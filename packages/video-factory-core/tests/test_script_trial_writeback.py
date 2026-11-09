import copy
import time
import unittest
from unittest.mock import patch
import test_script_jobs as script_tests
from review_fixture import BINDING
from video_factory.runtime_store import RuntimeFault
from video_factory.script_trial import configure,activate
from video_factory.script_trial_writeback import TrialDraftWriter


class Client:
    def __init__(self,fields):self.fields=copy.deepcopy(fields);self.writes=[];self.fail=False
    def record(self,*args):return {'fields':copy.deepcopy(self.fields)}
    def update_record(self,base,table,record,fields):
        self.writes.append((base,table,record,fields));self.fields.update(fields)
        if self.fail:raise TimeoutError('unknown update result')


class WritebackTests(unittest.TestCase):
    setUp=script_tests.ScriptTests.setUp
    tearDown=script_tests.ScriptTests.tearDown
    queue=script_tests.ScriptTests.queue
    step=script_tests.ScriptTests.step

    def prepare(self):
        configure(self.f.store,self.f.admin,self.f.project,production_base='bascnProduction',
                  copy_base=BINDING['base_token'],copy_table=BINDING['table_id'])
        now=time.time()
        with patch('video_factory.script_trial.time.time',return_value=now):
            activate(self.f.store,self.f.admin,self.f.project,t0=now,notice_sha256='a'*64)
        self.queue()
        result={'script':'draft','structured_fields':{'video_script':'draft','caption_voiceover_script':'voice','generation_prompt':'prompt'},
                'provider_id':'synthetic-receipt','prepared_input_sha256':'a'*64,'prepared_source_revision':'source-v1',
                'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
        with patch.object(self.provider,'generate',return_value=result):self.step()
        self.names={k:k for k in ['task','input_digest','source_revision','candidate_version','video_script','caption_voiceover_script','generation_prompt']}
        self.client=Client({'task':'generated_one','input_digest':'a'*64,'source_revision':'source-v1',
                            'human_review':'accepted','human_score':2,'baseline':'preserved'})
        return self.writer()

    def writer(self,base=None):
        return TrialDraftWriter(self.f.store,self.client,base=base or BINDING['base_token'],
                                table=BINDING['table_id'],names=self.names)

    def write(self,w):return w.write(self.f.project,'generated_one',1,'recSynthetic')

    def test_exact_copy_readback_and_idempotence_preserve_human_and_baseline(self):
        writer=self.prepare();self.assertEqual(self.write(writer)['status'],'written')
        self.assertEqual(self.write(writer)['writes'],0);self.assertEqual(len(self.client.writes),1)
        self.assertEqual(set(self.client.writes[0][3]),{'video_script','caption_voiceover_script','generation_prompt','candidate_version'})
        self.assertEqual(self.client.fields['human_review'],'accepted');self.assertEqual(self.client.fields['baseline'],'preserved')

    def test_production_target_and_wrong_record_fail_before_write(self):
        self.prepare()
        with self.assertRaisesRegex(RuntimeFault,'TARGET_MISMATCH'):self.write(self.writer('bascnProduction'))
        self.client.fields['input_digest']='changed'
        with self.assertRaisesRegex(RuntimeFault,'REMOTE_INPUT_CHANGED'):self.write(self.writer())
        self.assertEqual(self.client.writes,[])

    def test_existing_changed_draft_is_never_overwritten(self):
        writer=self.prepare();self.client.fields['video_script']='human edit'
        with self.assertRaisesRegex(RuntimeFault,'REMOTE_DRAFT_CONFLICT'):self.write(writer)
        self.assertEqual(self.client.writes,[])

    def test_unknown_put_is_reconciled_read_only_without_second_write(self):
        writer=self.prepare();self.client.fail=True
        with self.assertRaises(TimeoutError):self.write(writer)
        self.assertEqual(self.write(writer)['writes'],0);self.assertEqual(len(self.client.writes),1)

    def test_old_revision_does_not_publish(self):
        writer=self.prepare()
        with self.assertRaisesRegex(RuntimeFault,'STALE_TASK_REVISION'):
            writer.write(self.f.project,'generated_one',2,'recSynthetic')
        self.assertEqual(self.client.writes,[])
