"""Allowlist the exact tested bundle and guide; never package raw CI evidence."""
import hashlib,json,os,shutil,subprocess,zipfile
from pathlib import Path
root=Path('public-release');root.mkdir()
receipt=json.loads(Path('ci-release/release.json').read_text());source=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
assert receipt['public_release'] is True and receipt['source_commit']==source
for name in (receipt['archive'],'release.json','SHA256SUMS'):shutil.copyfile(Path('ci-release')/name,root/name)
assert hashlib.sha256((root/receipt['archive']).read_bytes()).hexdigest()==receipt['archive_sha256']
shutil.copyfile('ci-delivery/video-factory-setup.zip',root/'video-factory-setup.zip')
with zipfile.ZipFile(root/'installation-guide.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(Path('ci-delivery').rglob('*')):
        if p.is_file():z.write(p,p.relative_to('ci-delivery'))
for name in ('LICENSE','NOTICE','THIRD_PARTY_NOTICES.md'):shutil.copyfile(name,root/name)
handoff={**receipt,'checks':f'https://github.com/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{os.environ["GITHUB_RUN_ID"]}',
    'scope':'cloud synthetic tests; real n8n and PostgreSQL; fixed archive installation; browser and cold restore',
    'customer_independent_install':'not_run','paid_model_generation':'not_run','human_acceptance':'not_run','business_ready':False}
(root/'handoff.json').write_text(json.dumps(handoff,indent=2)+'\n')
(root/'ASSET_SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(root.iterdir()) if p.is_file()))
print(json.dumps({'status':'PASS','version':receipt['version'],'source_commit':source,'assets':len(list(root.iterdir()))}))
