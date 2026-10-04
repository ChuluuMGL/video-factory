"""Keep raw diagnostics in the ephemeral runner; export only bounded metadata."""
import json,os,re,subprocess,sys,tempfile,time
from pathlib import Path
label=sys.argv[1]
assert re.fullmatch(r'[a-z0-9-]+',label)
script=sys.stdin.read();start=time.monotonic()
with tempfile.TemporaryFile() as log:
    result=subprocess.run(['bash','-e','-o','pipefail','-c',script],stdout=log,stderr=log)
    log.seek(0);raw=log.read().decode(errors='replace')
receipt={'stage':label,'status':'PASS' if result.returncode==0 else 'FAIL',
         'exit_code':result.returncode,'seconds':round(time.monotonic()-start,1),
         'unit_test_counts':[int(n) for n in re.findall(r'Ran (\d+) tests in',raw)]}
if result.returncode:
    # Codes only: no command text, payloads, credential values or exception messages.
    receipt['exception_types']=sorted(set(re.findall(r'^(?:[A-Za-z_][A-Za-z0-9_]*\.)*([A-Za-z]+(?:Error|Exception)|RuntimeFault):',raw,re.M)))
    frames=re.findall(r'File "([^"]+)", line (\d+)',raw)
    application=[(file,line) for file,line in frames if '/video_factory/' in file or '/.github/scripts/' in file or Path(file).name.startswith('test_')]
    # Keep the product call site even when TLS/HTTP adds many stdlib frames.
    receipt['frames']=[{'file':Path(file).name,'line':int(line)} for file,line in (application[-8:]+frames[-4:])]
print(json.dumps(receipt),flush=True)
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('```json\n'+json.dumps(receipt)+'\n```\n')
sys.exit(result.returncode)
