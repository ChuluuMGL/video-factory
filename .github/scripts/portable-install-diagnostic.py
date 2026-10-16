"""Ephemeral Windows CI installation diagnostic. No customer credentials."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'skills/video-factory-setup/scripts'))
import portable_n8n as portable
root=Path(tempfile.mkdtemp(prefix='vf-install-diagnostic-'))
original=portable.run_checked
strategy=os.environ.get('VF_INSTALL_STRATEGY','hoisted')
if strategy not in ('hoisted','shallow'): raise ValueError('unexpected diagnostic strategy')

def bounded(command,cwd,env=None,**kwargs):
    if any('npm-cli.js' in str(part) for part in command):
        env={**(env or os.environ),'npm_config_cache':str(root/'ci-npm-cache')}
        command=[*command,'--loglevel=verbose','--timing','--install-strategy='+strategy]
        stopped=threading.Event()
        def progress():
            while not stopped.wait(60):
                logs=list((root/'ci-npm-cache/_logs').glob('*-debug-0.log'))
                if logs:
                    with max(logs,key=lambda p:p.stat().st_mtime).open('rb') as stream:
                        stream.seek(max(0,os.fstat(stream.fileno()).st_size-2500))
                        print(stream.read().decode(errors='replace').splitlines()[-3:],flush=True)
        thread=threading.Thread(target=progress,daemon=True); thread.start()
        try:return original(command,cwd,env=env,timeout=720)
        finally:stopped.set();thread.join(5)
    return original(command,cwd,env=env,**kwargs)
portable.run_checked=bounded
try:
    started=time.monotonic()
    portable.install_local(root,'node',15678,managed_node=True)
    print('NATIVE_INSTALL_COMPLETED',strategy,round(time.monotonic()-started),flush=True)
    print(portable.local_control(root,'start'),flush=True)
    print(portable.local_control(root,'stop'),flush=True)
except Exception as error:
    print(type(error).__name__,str(error))
    if hasattr(error,'diagnostics'):print(error.diagnostics.decode(errors='replace')[-7000:])
    logs=sorted((root/'ci-npm-cache/_logs').glob('*-debug-0.log'),key=lambda path:path.stat().st_mtime)
    if logs:print(logs[-1].read_text(encoding='utf-8',errors='replace')[-10000:])
    raise
