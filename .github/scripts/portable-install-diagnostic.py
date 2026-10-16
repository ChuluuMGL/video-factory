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
if strategy not in ('hoisted','shallow','pnpm'): raise ValueError('unexpected diagnostic strategy')

def bounded(command,cwd,env=None,**kwargs):
    if any('npm-cli.js' in str(part) for part in command):
        env={**(env or os.environ),'npm_config_cache':str(root/'ci-npm-cache')}
        if strategy=='pnpm':
            pm=root/'tools/package-manager'; pm.mkdir(parents=True,exist_ok=True)
            portable.private_write(pm/'package.json',{'name':'vf-package-manager','private':True,'dependencies':{'pnpm':'12.3.4'}})
            original([command[0],command[1],'install','--global=false','--prefix',str(pm),'--no-audit','--no-fund'],pm,env=env,timeout=180)
            allowed=['@confluentinc/kafka-javascript','@vscode/ripgrep','isolated-vm','sqlite3']
            ignored=['@biomejs/biome','@jitsi/robotjs','@parcel/watcher','@sentry-internal/node-cpu-profiler','@sentry-internal/node-native-stacktrace','@sentry/cli','@sentry/node-cpu-profiler','@sentry/node-native-stacktrace','@swc/core','agent-browser','bufferutil','core-js','cpu-features','electron','electron-winstaller','es5-ext','esbuild','eslint-plugin-n8n-nodes-base','lefthook','libpq','msgpackr-extract','oracledb','protobufjs','puppeteer','sleep','ssh2','tree-sitter','tree-sitter-bash','unrs-resolver','utf-8-validate','v-code-diff','vue-demi']
            portable.private_write(root/'pnpm-workspace.yaml',{'allowBuilds':{**dict.fromkeys(allowed,True),**dict.fromkeys(ignored,False)},'strictDepBuilds':True})
            exe=pm/'node_modules/pnpm'/('pnpm.exe' if os.name=='nt' else 'pnpm')
            return original([str(exe),'install','--store-dir',str(root/'tools/pnpm-store'),'--no-frozen-lockfile','--reporter=append-only'],root,env=env,timeout=720)

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
