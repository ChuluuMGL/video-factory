"""Ephemeral Windows CI installation diagnostic. No customer credentials."""
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'skills/video-factory-setup/scripts'))
import portable_n8n as portable
root=Path(tempfile.mkdtemp(prefix='vf-install-diagnostic-'))
original=portable.run_checked

def bounded(command,cwd,env=None,**kwargs):
    if any('npm-cli.js' in str(part) for part in command):
        env={**(env or os.environ),'npm_config_cache':str(root/'ci-npm-cache')}
        command=[*command,'--loglevel=verbose','--timing']
        return original(command,cwd,env=env,timeout=240)
    return original(command,cwd,env=env,**kwargs)
portable.run_checked=bounded
try:
    portable.install_local(root,'node',15678,managed_node=True)
    print('NATIVE_INSTALL_COMPLETED')
except Exception as error:
    print(type(error).__name__,str(error))
    if hasattr(error,'diagnostics'):print(error.diagnostics.decode(errors='replace')[-7000:])
    logs=sorted((root/'ci-npm-cache/_logs').glob('*-debug-0.log'),key=lambda path:path.stat().st_mtime)
    if logs:print(logs[-1].read_text(encoding='utf-8',errors='replace')[-10000:])
    raise
