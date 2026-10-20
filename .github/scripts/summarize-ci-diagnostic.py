"""Emit bounded traceback metadata from ephemeral CI diagnostics, never raw logs."""
import json
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
if not path.is_file():
    print(json.dumps({'diagnostic': 'unavailable'}))
    sys.exit(0)
raw = path.read_text(errors='replace')[-2_000_000:]
frames = re.findall(r'File "([^"]+)", line (\d+)', raw)
types = re.findall(r'^(?:[A-Za-z_][A-Za-z0-9_]*\.)*([A-Za-z]+(?:Error|Exception|Fault)):', raw, re.M)
stages = re.findall(r'"stage":\s*"([a-z0-9_-]{1,80})"', raw)
codes = re.findall(r'^(?:[A-Za-z_][A-Za-z0-9_]*\.)*(?:AssertionError|RuntimeFault): ([A-Z][A-Z0-9_]{3,80})$', raw, re.M)
states=[]
for line in raw.splitlines():
    try: item=json.loads(line)
    except (ValueError,TypeError): continue
    if not isinstance(item,dict) or item.get('stage')!='container_failure_components':continue
    allowed_names=('postgres','runtime','n8n','gateway','worker','egress','bootstrap')
    allowed_states=('running','exited','created','restarting','paused','dead','removing')
    components=item.get('components',{})
    if not isinstance(components,dict):continue
    states.append({name:{'state':value.get('state') if value.get('state') in allowed_states else 'unknown',
                         'exit_code':value.get('exit_code') if type(value.get('exit_code')) is int and 0<=value['exit_code']<=255 else None}
                   for name,value in components.items() if name in allowed_names and isinstance(value,dict)})
print(json.dumps({'diagnostic': 'sanitized', 'component_states': states[-5:],
                  'frames': [{'file': Path(name).name, 'line': int(line)} for name, line in frames[-8:]],
                  'exception_types': sorted(set(types))[-8:],
                  'stages': stages[-4:], 'codes': codes[-4:]}))
