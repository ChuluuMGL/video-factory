"""Cloud-only relocation contracts. Synthetic media, no provider/Feishu requests."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from video_factory.media_relocation import INDEX, SCHEMA, entry_key
from video_factory.review_service import open_media
from video_factory.runtime_store import RuntimeFault, canonical
from video_factory.feishu_bridge import save
from video_factory.feishu_native_sync import item_key
from test_feishu_native_review import NativeReviewTests


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.media = self.root/'new-media'; self.media.mkdir(mode=0o700)
        self.data = b'synthetic-media-contract'
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.file = self.media/'clip.mp4'; self.file.write_bytes(self.data); self.file.chmod(0o600)
        self.artifact = {'location': '/unavailable/old-root/media/clip.mp4',
                         'sha256': self.digest, 'verification': 'full_decode_passed'}
        self.item = {**{k:self.artifact[k] for k in ('location','sha256')}, 'path':'clip.mp4'}
        self.write_index()
    def tearDown(self): self.tmp.cleanup()
    def write_index(self):
        path=self.media/INDEX
        path.write_text(json.dumps({'schema':SCHEMA,'entries':{
            entry_key(self.artifact['location'],self.digest):self.item}})); path.chmod(0o600)
    def read(self):
        stream,size=open_media(self.media,self.artifact,self.digest)
        with stream: self.assertEqual(stream.read(),self.data)
        self.assertEqual(size,len(self.data))
    def test_relocation_preserves_descriptor_and_can_move_twice(self):
        before=canonical(self.artifact); self.read()
        self.media.rename(self.root/'third-media'); self.media=self.root/'third-media'
        self.read(); self.assertEqual(canonical(self.artifact),before)
    def test_no_basename_fallback_or_wrong_hash(self):
        (self.media/INDEX).unlink()
        with self.assertRaises(RuntimeFault): self.read()
        self.write_index(); self.file.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeFault,'HASH_CHANGED'): self.read()
        with self.assertRaises(RuntimeFault): open_media(self.media,self.artifact,'0'*64)
    def test_traversal_absolute_and_mismatched_descriptors_rejected(self):
        for field,value in [('path','../clip.mp4'),('path','/tmp/clip.mp4'),
                            ('path','./clip.mp4'),('location','/different/clip.mp4'),('sha256','0'*64)]:
            with self.subTest(field=field,value=value):
                original=self.item[field];self.item[field]=value;self.write_index()
                with self.assertRaises(RuntimeFault): self.read()
                self.item[field]=original
    def test_symlinks_hardlinks_and_public_index_rejected(self):
        outside=self.root/'outside.mp4';self.file.rename(outside);self.file.symlink_to(outside)
        with self.assertRaises(RuntimeFault): self.read()
        self.file.unlink();os.link(outside,self.file)
        with self.assertRaises(RuntimeFault): self.read()
        self.file.unlink();outside.rename(self.file)
        (self.media/INDEX).chmod(0o644)
        with self.assertRaises(RuntimeFault): self.read()
    def test_malformed_index_and_permissive_nested_directory_rejected(self):
        (self.media/INDEX).write_text('[]')
        with self.assertRaises(RuntimeFault): self.read()
        folder=self.media/'nested';folder.mkdir(mode=0o755)
        self.file.rename(folder/'clip.mp4');self.item['path']='nested/clip.mp4';self.write_index()
        with self.assertRaises(RuntimeFault): self.read()


class ReviewReplayAfterRelocation(NativeReviewTests):
    def test_unchanged_artifact_keeps_accepted_video_event_idempotent(self):
        media=self.store.root/'media';media.mkdir(mode=0o700)
        data=b'synthetic-review-media';path=media/'clip.mp4';path.write_bytes(data);path.chmod(0o600)
        digest=hashlib.sha256(data).hexdigest()
        artifact={'sha256':digest,'location':str(path),'verification':'full_decode_passed'}
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET state='awaiting_video_review',artifact=?",(canonical(artifact),))
            save(db,item_key('brand','task_one',1,'awaiting_video_review'),
                 {'complete':True,'steps':{'finish':{'status':'done','receipt':'fileFixture'}},'snapshot':{}})
        self.remote.update({'状态':'视频通过','视频摘要':digest,'视频':[{'file_token':'fileFixture'}]})
        event=self.event(before='视频待审核',after='视频通过',event_id='evt_relocation')
        self.reducer.enqueue_verified('brand',event)
        first=self.reducer.process_one('brand')
        self.assertEqual(first['result']['reviewed'][0]['state'],'accepted')
        with self.store.connect() as db:
            before=[tuple(row) for row in db.execute('SELECT key,value FROM meta ORDER BY key')]
        new=self.store.root/'relocated';media.rename(new)
        index=new/INDEX;index.write_text(json.dumps({'schema':SCHEMA,'entries':{
            entry_key(str(path),digest):{'location':str(path),'sha256':digest,'path':'clip.mp4'}}}));index.chmod(0o600)
        stream,_=open_media(new,artifact,digest)
        with stream:self.assertEqual(stream.read(),data)
        self.assertEqual(self.reducer.enqueue_verified('brand',event), {'status':'done','replayed':True})
        self.assertEqual(self.reducer.process_one('brand')['status'],'idle')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT artifact FROM tasks').fetchone()[0],canonical(artifact))
            self.assertEqual([tuple(row) for row in db.execute('SELECT key,value FROM meta ORDER BY key')],before)


if __name__=='__main__': unittest.main()
