"""Download privately and fully decode before registering a video for human review."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from .runtime_store import RuntimeFault, private_directory, private_file


def toolchain():
    paths={name:shutil.which(name) for name in ('ffmpeg','ffprobe')}
    if not all(paths.values()):raise RuntimeFault('MEDIA_FFMPEG_AND_FFPROBE_REQUIRED')
    return paths


def verify(path,duration):
    tools=toolchain();private_file(path)
    try:
        result=subprocess.run([tools['ffprobe'],'-v','error','-protocol_whitelist','file,pipe','-show_streams','-show_format','-of','json',str(path)],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=True)
        probe=json.loads(result.stdout)
        video=[s for s in probe['streams'] if s['codec_type']=='video'];audio=[s for s in probe['streams'] if s['codec_type']=='audio']
        length=float(probe['format']['duration'])
        if (len(video)!=1 or not audio or not math.isfinite(length) or abs(length-duration)>1
                or video[0]['width']>=video[0]['height'] or abs(video[0]['width']/video[0]['height']-9/16)>0.03):
            raise RuntimeFault('MEDIA_CONTRACT_FAILED')
        subprocess.run([tools['ffmpeg'],'-nostdin','-v','error','-xerror','-protocol_whitelist','file,pipe','-i',str(path),
                        '-map','0:v:0','-map','0:a:0','-f','null','-'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120,check=True)
    except RuntimeFault:raise
    except Exception:raise RuntimeFault('MEDIA_FULL_DECODE_FAILED') from None
    return {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'location':str(path),'verification':'full_decode_passed'}


def collect(provider,url,root,identity,duration):
    root=private_directory(root)
    # Identity is an application-generated hash, not a remote filename.
    if len(identity)!=64 or any(c not in '0123456789abcdef' for c in identity):raise RuntimeFault('MEDIA_ID_INVALID')
    dest=root/(identity+'.mp4')
    if dest.exists() or dest.is_symlink():return verify(dest,duration)
    descriptor,name=tempfile.mkstemp(prefix='.'+identity+'-',dir=root)
    temp=Path(name)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            provider.download(url,stream);stream.flush();os.fsync(stream.fileno())
        result=verify(temp,duration)
        os.replace(temp,dest)
        return {**result,'location':str(dest)}
    finally:temp.unlink(missing_ok=True)
