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
types = re.findall(r'^([A-Za-z]+(?:Error|Exception)):', raw, re.M)
stages = re.findall(r'"stage":\s*"([a-z0-9_-]{1,80})"', raw)
codes = re.findall(r'^(?:AssertionError|RuntimeFault): ([A-Z][A-Z0-9_]{3,80})$', raw, re.M)
print(json.dumps({'diagnostic': 'sanitized',
                  'frames': [{'file': Path(name).name, 'line': int(line)} for name, line in frames[-8:]],
                  'exception_types': sorted(set(types))[-8:],
                  'stages': stages[-4:], 'codes': codes[-4:]}))
