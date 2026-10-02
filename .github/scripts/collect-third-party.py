"""Preserve wheel licenses and the pinned Psycopg source in a public bundle."""
from email.parser import BytesParser
import hashlib,json,re,shutil,urllib.request,zipfile
from pathlib import Path

PSYCOPG_COMMIT='a67654d1e7afbf9b3a619557838f62de1c790e7c' # upstream 3.3.6 tag
PSYCOPG_URL='https://codeload.github.com/psycopg/psycopg/tar.gz/'+PSYCOPG_COMMIT

def collect(root):
    root=Path(root); notices=root/'third-party-licenses';notices.mkdir()
    entries=[]
    for wheel in sorted((root/'wheels').glob('*.whl')):
        with zipfile.ZipFile(wheel) as archive:
            metadata=next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))
            info=BytesParser().parsebytes(archive.read(metadata))
            name=re.sub(r'[-_.]+','-',info['Name']).lower()
            if name=='video-factory-core':continue
            version=info['Version'];assert re.fullmatch(r'[A-Za-z0-9.+]+',version)
            license_paths=[n for n in archive.namelist() if '.dist-info/' in n and not n.endswith('/') and (
                '/licenses/' in n.lower() or Path(n).name.upper().startswith(('LICENSE','COPYING','NOTICE','AUTHORS')))]
            assert license_paths,'DEPENDENCY_LICENSE_TEXT_MISSING: '+name
            dest=notices/(name+'-'+version);dest.mkdir()
            for index,path in enumerate(license_paths):
                (dest/f'{index:02d}-{Path(path).name}').write_bytes(archive.read(path))
            entries.append({'name':name,'version':version,'wheel':wheel.name,
                'wheel_sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),
                'license_expression':info.get('License-Expression'),
                'license_metadata':info.get('License'),'license_files':[p.relative_to(root).as_posix() for p in sorted(dest.iterdir())]})
    versions={e['name']:e['version'] for e in entries}
    assert versions.get('psycopg')==versions.get('psycopg-binary')=='3.3.6','REVIEW_NEW_PSYCOPG_SOURCE_VERSION'
    sources=root/'third-party-source';sources.mkdir()
    source=sources/'psycopg-3.3.6.tar.gz'
    # Source only, never executed. Fixed commit avoids mutable-tag downloads.
    with urllib.request.urlopen(PSYCOPG_URL,timeout=120) as response,source.open('wb') as dest:
        shutil.copyfileobj(response,dest)
    assert 1024<source.stat().st_size<20*1024*1024
    (root/'third-party-manifest.json').write_text(json.dumps({'schema':1,'dependencies':entries,
        'corresponding_source':[{'packages':['psycopg','psycopg-binary'],'version':'3.3.6',
            'upstream_commit':PSYCOPG_COMMIT,'url':PSYCOPG_URL,'file':source.relative_to(root).as_posix(),
            'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}],
        'third_party_images':'fetched directly from pinned upstream registries; not included'},indent=2)+'\n')
