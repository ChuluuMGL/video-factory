"""Transfer hash-approved original archives in CI. Never rebuild or publish draft."""
import hashlib, io, json, os, subprocess, tempfile, urllib.request, urllib.parse, zipfile
from pathlib import Path
manifest=json.loads(Path(__file__).with_name('release-manifest.json').read_text())
expected={a['name']:a for a in manifest['assets'] if a['name'].endswith('.zip')}
urls=json.loads(os.environ.pop('SOURCE_URLS'))
assert set(urls)=={'linux-x86_64','macos-arm64'}
with tempfile.TemporaryDirectory() as tmp:
    found={}
    for target,url in urls.items():
        parsed=urllib.parse.urlsplit(url)
        assert parsed.scheme=='https' and parsed.hostname.endswith('.blob.core.windows.net')
        print('::add-mask::'+url)
        with urllib.request.urlopen(url,timeout=120) as response: raw=response.read(60*1024*1024)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            wanted={'video-factory-v0.2.0-beta-'+target+'.zip','video-factory-employee-client-v0.2.0-beta.zip'}
            assert set(archive.namelist())==wanted
            for name in archive.namelist():
                data=archive.read(name);spec=expected[name]
                assert len(data)==spec['bytes'] and hashlib.sha256(data).hexdigest()==spec['sha256']
                if name in found: assert found[name]==spec['sha256']
                else: (Path(tmp)/name).write_bytes(data)
                found[name]=spec['sha256']
    assert set(found)==set(expected)
    release=json.loads(subprocess.check_output(['gh','release','view','v0.2.0-beta','-R','ChuluuMGL/video-factory','--json','isDraft,isPrerelease']))
    assert release['isDraft'] and release['isPrerelease']
    subprocess.run(['gh','release','upload','v0.2.0-beta','-R','ChuluuMGL/video-factory',*[str(Path(tmp)/name) for name in sorted(found)]],check=True)
    print(json.dumps({'original_assets_verified':found,'rebuilt':False,'draft_published':False}))
