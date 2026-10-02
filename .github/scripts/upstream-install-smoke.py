"""Actual anonymous PyPI acquisition on an isolated cloud host, with cache reuse."""
import hashlib,json,subprocess,sys,tarfile,tempfile,shutil
from pathlib import Path
release=Path(sys.argv[1]);r=json.loads((release/'release.json').read_text());archive=release/r['archive']
assert hashlib.sha256(archive.read_bytes()).hexdigest()==r['archive_sha256']
with tempfile.TemporaryDirectory(prefix='vf-upstream-',dir='/root') as temp:
    root=Path(temp)
    with tarfile.open(archive) as t:
        wheels=[m.name for m in t.getmembers() if m.name.endswith('.whl')]
        assert len(wheels)==1 and '/video_factory_core-' in wheels[0]
    subprocess.run(['tar','-xzf',str(archive),'-C',str(root)],check=True)
    bundle=root/archive.name.removesuffix('.tar.gz')
    base=[sys.executable,'-I',str(bundle/'install.py'),'--prefix',str(root/'cli'),'--manifest-sha256']
    # Wrong trust hash must fail before upstream requests or local materialization.
    p=subprocess.run(base+['0'*64],capture_output=True,text=True);assert p.returncode==2
    assert json.loads(p.stdout)['error']=='MANIFEST_HASH_MISMATCH'
    assert len(list((bundle/'wheels').glob('*.whl')))==1
    bad=root/'bad';shutil.copytree(bundle,bad)
    plan=json.loads((bad/'dependency-downloads.json').read_text());first=next(iter(plan));plan[first]['url']='https://example.com/forbidden.whl'
    (bad/'dependency-downloads.json').write_text(json.dumps(plan))
    manifest=json.loads((bad/'manifest.json').read_text());manifest['files']['dependency-downloads.json']=hashlib.sha256((bad/'dependency-downloads.json').read_bytes()).hexdigest()
    (bad/'manifest.json').write_text(json.dumps(manifest));bad_sha=hashlib.sha256((bad/'manifest.json').read_bytes()).hexdigest()
    p=subprocess.run([sys.executable,'-I',str(bad/'install.py'),'--prefix',str(root/'bad-cli'),'--manifest-sha256',bad_sha],capture_output=True,text=True)
    assert p.returncode==2 and json.loads(p.stdout)['error']=='DEPENDENCY_ORIGIN_INVALID'
    assert not (root/'bad-cli').exists()
    p=subprocess.run(base+[r['manifest_sha256']],capture_output=True,text=True,timeout=600)
    assert p.returncode==0,'UPSTREAM_INSTALL_FAILED'
    assert json.loads(p.stdout)['version']==r['version']
    for name,item in json.loads((bundle/'dependency-downloads.json').read_text()).items():
        assert hashlib.sha256((bundle/name).read_bytes()).hexdigest()==item['sha256']
    p=subprocess.run(['unshare','--net','--',*base,r['manifest_sha256']],capture_output=True,text=True,timeout=300)
    assert p.returncode==0 and json.loads(p.stdout)['reused']
print(json.dumps({'status':'PASS','anonymous_upstream_download':True,'offline_reuse':True,'wrong_digest_rejected_before_download':True,'third_party_binaries_in_archive':False}))
