"""Owned OS startup jobs. No credentials in units, command lines or receipts.

Desktop jobs start at user login; systemd system jobs start at boot. Existing
n8n instances are never adopted. Removing a job preserves all project data.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import signal
import subprocess
import sys
import time
import venv

from portable_n8n import Fault, private_write, locked, environment, health, ready, local_control, run_checked
from portable_events import secure_directory, receiver_lock

FILES=('portable_service.py','portable_n8n.py','portable_events.py','portable_approval.py','portable_upgrade.py','portable_models.py','portable_video_models.py','n8n_workflows.py','node_runtime.py')


def path_text(value):
    value=str(value)
    if any(ord(c)<32 for c in value): raise Fault('SERVICE_PATH_CONTROL_CHARACTER')
    return value


def ps(value):
    return "'"+path_text(value).replace("'","''")+"'"


def run(command, allow_failure=False):
    result=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,timeout=60)
    if result.returncode and not allow_failure: raise Fault('SERVICE_OS_COMMAND_FAILED_CHECK_SERVICE_STATUS')
    return result


def powershell(script, allow_failure=False):
    encoded=base64.b64encode(("$ErrorActionPreference='Stop';"+script).encode('utf-16le')).decode()
    return run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],allow_failure)


def system_quote(value):
    # systemd expands percent specifiers and dollar variables even without a shell.
    return '"'+path_text(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'


class Backend:
    def __init__(self, directory, command, system=None):
        self.directory=Path(directory).resolve()
        self.command=[path_text(x) for x in command]
        self.system=system or platform.system()
        self.name='tech.yueyu.vf.'+hashlib.sha256(str(self.directory).encode()).hexdigest()[:16]
        if self.system=='Linux':
            self.system_scope=os.geteuid()==0
            self.prefix=['systemctl']+([] if self.system_scope else ['--user'])
            self.path=(Path('/etc/systemd/system') if self.system_scope else Path.home()/'.config/systemd/user')/(self.name+'.service')
            self.startup='boot' if self.system_scope else 'user-manager (login unless linger is enabled)'
        elif self.system=='Darwin':
            self.path=Path.home()/'Library/LaunchAgents'/(self.name+'.plist')
            self.domain='gui/'+str(os.getuid())
            self.startup='user-login'
        elif self.system=='Windows':
            self.path=self.directory/'task-definition.json'
            self.startup='user-login'
        else: raise Fault('SERVICE_PLATFORM_UNSUPPORTED')

    def definition(self):
        if self.system=='Linux':
            return ('[Unit]\nDescription=Video Factory owned background process\n'
                '[Service]\nType=simple\nExecStart='+ ' '.join(map(system_quote,self.command))+
                '\nRestart=on-failure\nRestartSec=10\nTimeoutStopSec=45\nKillMode=control-group\n'
                'UMask=0077\nStandardOutput=null\nStandardError=null\n'
                '[Install]\nWantedBy='+('multi-user.target' if self.system_scope else 'default.target')+'\n').encode()
        if self.system=='Darwin':
            return plistlib.dumps({'Label':self.name,'ProgramArguments':self.command,
                'RunAtLoad':True,'KeepAlive':{'SuccessfulExit':False},'ThrottleInterval':10,
                'ExitTimeOut':45,'Umask':63,'StandardOutPath':'/dev/null','StandardErrorPath':'/dev/null'})
        return json.dumps({'execute':self.command[0],'arguments':subprocess.list2cmdline(self.command[1:])},sort_keys=True).encode()

    def task_check(self):
        return (f'$t=Get-ScheduledTask -TaskName {ps(self.name)} -ErrorAction SilentlyContinue;'
            f'if($t -and ($t.Actions.Count -ne 1 -or $t.Actions[0].Execute -cne {ps(self.command[0])} '
            f'-or $t.Actions[0].Arguments -cne {ps(subprocess.list2cmdline(self.command[1:]))}))'
            '{throw "SERVICE_TASK_CHANGED"};')

    def verify_file(self):
        if self.path.is_symlink(): raise Fault('SERVICE_DEFINITION_SYMLINK')
        if self.path.exists() and self.path.read_bytes()!=self.definition():
            raise Fault('SERVICE_DEFINITION_CHANGED_PRESERVED')

    def install(self):
        self.verify_file()
        if self.system=='Windows':
            # No Force: an existing task must have exactly this action before reuse.
            powershell(self.task_check()+
                f'if(!$t){{$u=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name;'
                f'$a=New-ScheduledTaskAction -Execute {ps(self.command[0])} -Argument {ps(subprocess.list2cmdline(self.command[1:]))};'
                '$g=New-ScheduledTaskTrigger -AtLogOn -User $u;'
                '$p=New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited;'
                '$s=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) '
                '-MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;'
                f'Register-ScheduledTask -TaskName {ps(self.name)} -Action $a -Trigger $g -Principal $p -Settings $s | Out-Null}}')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if not self.path.exists():
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(self.definition())
        if self.system=='Linux':run(self.prefix+['daemon-reload']);run(self.prefix+['enable',self.name])
        elif self.system=='Darwin':run(['launchctl','enable',self.domain+'/'+self.name])

    def control(self, action):
        self.verify_file()
        if not self.path.exists():raise Fault('SERVICE_NOT_INSTALLED')
        if self.system=='Linux':
            if action=='status':
                result=run(self.prefix+['is-active',self.name],True)
                return {'running':result.stdout.decode().strip()=='active','startup':self.startup}
            if action=='uninstall':
                run(self.prefix+['disable','--now',self.name]);self.path.unlink();run(self.prefix+['daemon-reload'])
            else:run(self.prefix+[action,self.name])
        elif self.system=='Darwin':
            target=self.domain+'/'+self.name
            present=run(['launchctl','print',target],True)
            if action=='status':return {'running':present.returncode==0 and b'state = running' in present.stdout,'startup':self.startup}
            if action in ('stop','uninstall') and present.returncode==0:
                run(['launchctl','bootout',target])
                # bootout returns before launchd has removed the job. Do not
                # bootstrap into that transient state or report removal early.
                for _ in range(45):
                    if run(['launchctl','print',target],True).returncode!=0:break
                    time.sleep(1)
                else:raise Fault('SERVICE_STOP_PENDING_PRESERVE_DEFINITION')
            if action in ('start','restart'):
                if present.returncode!=0:run(['launchctl','bootstrap',self.domain,str(self.path)])
                else:run(['launchctl','kickstart']+(['-k'] if action=='restart' else [])+[target])
            if action=='uninstall':self.path.unlink()
        else:
            check=self.task_check()+'if(!$t){throw "SERVICE_TASK_MISSING"};'
            if action=='status':
                result=powershell(check+'$t.State.ToString()')
                return {'running':result.stdout.decode().strip()=='Running','startup':self.startup}
            if action in ('stop','restart','uninstall'):powershell(check+f'Stop-ScheduledTask -TaskName {ps(self.name)};')
            if action in ('start','restart'):powershell(check+f'Start-ScheduledTask -TaskName {ps(self.name)};')
            if action=='uninstall':powershell(check+f'Unregister-ScheduledTask -TaskName {ps(self.name)} -Confirm:$false;');self.path.unlink()
        return {'action':action,'data_preserved':True,'startup':self.startup}


def location(root, component):
    if component not in ('n8n','events'):raise Fault('SERVICE_COMPONENT_INVALID')
    root=Path(root)
    if root.is_symlink():raise Fault('SERVICE_ROOT_SYMLINK')
    directory=root.resolve()/('.vf-'+component+'-service')
    if directory.is_symlink():raise Fault('SERVICE_DIRECTORY_SYMLINK')
    return directory


def install(root, component):
    directory=location(root,component);root=Path(root).resolve()
    if not (root/('local.json' if component=='n8n' else 'events.json')).is_file():
        raise Fault('SERVICE_REQUIRES_EXISTING_INSTALLATION_OR_EVENT_CONFIGURATION')
    # The containing installation/event directory must already be private/owned.
    secure_directory(root)
    with locked(directory):
        receipt=directory/'service.json'
        if receipt.is_symlink():raise Fault('SERVICE_RECEIPT_SYMLINK')
        if receipt.exists():
            spec=json.loads(receipt.read_text(encoding='utf-8'))
            if spec['root']!=str(root) or spec['component']!=component:raise Fault('SERVICE_RECEIPT_MISMATCH')
            backend=Backend(directory,spec['command']);backend.verify_file()
            if spec.get('installed'):
                if not backend.control('status')['running']:backend.control('start')
                return {**wait_running(root,component),'reused':True}
        if component=='n8n':
            saved=json.loads((root/'local.json').read_text(encoding='utf-8'))
            if saved['mode']!='node':raise Fault('DOCKER_USES_RESTART_POLICY_ENABLE_DOCKER_STARTUP')
            if not Path(saved['node_executable']).is_file() or not (root/'node_modules/n8n/bin/n8n').is_file():raise Fault('LOCAL_N8N_NOT_INSTALLED')
        else:
            from portable_events import load_secrets
            load_secrets(root)
            with receiver_lock(root):pass
        runtime=directory/'runtime'
        if runtime.is_symlink():raise Fault('SERVICE_RUNTIME_SYMLINK')
        runtime.mkdir(exist_ok=True)
        for name in FILES:
            destination=runtime/name
            if destination.is_symlink():raise Fault('SERVICE_RUNTIME_SYMLINK')
            shutil.copyfile(Path(__file__).parent/name,destination)
        python=sys.executable
        if component=='events':
            env=directory/'python'
            if env.is_symlink():raise Fault('SERVICE_PYTHON_SYMLINK')
            venv.EnvBuilder(with_pip=True).create(env)
            python=str(env/('Scripts/python.exe' if os.name=='nt' else 'bin/python'))
            run_checked([python,'-m','pip','install','cryptography==46.0.5','lark-oapi==1.7.3','websockets==15.0.1'],directory)
        command=[python,str(runtime/'portable_service.py'),'--root',str(root),'--component',component,'run']
        spec={'root':str(root),'component':component,'command':command,'installed':False}
        private_write(receipt,spec)
        backend=Backend(directory,command);backend.verify_file()
        # Stop only the process matching this installer's saved PID + creation time.
        if component=='n8n':local_control(root,'stop')
        backend.install()
        spec['installed']=True;private_write(receipt,spec)
        backend.control('start')
        return wait_running(root,component)


def backend_for(root,component):
    directory=location(root,component)
    receipt=directory/'service.json'
    if receipt.is_symlink():raise Fault('SERVICE_RECEIPT_SYMLINK')
    spec=json.loads(receipt.read_text(encoding='utf-8'))
    if spec['root']!=str(Path(root).resolve()) or spec['component']!=component:raise Fault('SERVICE_RECEIPT_MISMATCH')
    return Backend(directory,spec['command'])


def wait_running(root,component):
    backend=backend_for(root,component)
    for _ in range(60):
        state=backend.control('status')
        if state['running']:
            if component!='n8n' or ready(json.loads((Path(root)/'local.json').read_text(encoding='utf-8'))['origin']):
                return {**state,'installed':True,'provider_calls_by_setup':0,'production_ready':False}
        time.sleep(2)
    raise Fault('SERVICE_START_NOT_VERIFIED_DATA_PRESERVED_RUN_STATUS')


def control(root,component,action):
    with locked(location(root,component)):
        backend=backend_for(root,component)
        result=backend.control(action)
        if action in ('start','restart'):return wait_running(root,component)
        if action=='uninstall':
            receipt=location(root,component)/'service.json'
            spec=json.loads(receipt.read_text(encoding='utf-8'));spec['installed']=False;private_write(receipt,spec)
        return result


def windows_child_job():
    """Kill the child tree if Task Scheduler force-terminates its wrapper."""
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),
            ('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),
            ('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
    class IO(ctypes.Structure):_fields_=[(n,ctypes.c_ulonglong) for n in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
    class Limits(ctypes.Structure):_fields_=[('BasicLimitInformation',Basic),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateJobObjectW.restype=wintypes.HANDLE
    k.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    handle=k.CreateJobObjectW(None,None);limits=Limits();limits.BasicLimitInformation.LimitFlags=0x2000
    if not handle or not k.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
        raise Fault('SERVICE_CHILD_JOB_UNAVAILABLE')
    return k,handle


def serve(root,component):
    root=Path(root).resolve()
    if component=='events':
        from portable_events import serve as events
        url=json.loads((root/'events.json').read_text(encoding='utf-8'))['origin']
        # Boot may involve database recovery after the HTTP listener is alive.
        # Wait before opening the SDK receiver or dispatching the durable inbox.
        for _ in range(150):
            if ready(url):break
            time.sleep(2)
        else:raise Fault('SERVICE_N8N_NOT_READY_NO_EVENT_DISPATCH')
        return events(root)
    saved=json.loads((root/'local.json').read_text(encoding='utf-8'))
    if saved['mode']!='node':raise Fault('SERVICE_OWNED_NODE_ONLY')
    with receiver_lock(location(root,component)/'process'):
        if health(saved['origin']):raise Fault('SERVICE_PORT_ALREADY_IN_USE')
        job=windows_child_job()
        child=subprocess.Popen([saved['node_executable'],str(root/'node_modules/n8n/bin/n8n'),'start'],
            cwd=root,env=environment(root,saved['port']),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            if job and not job[0].AssignProcessToJobObject(job[1],int(child._handle)):
                raise Fault('SERVICE_CHILD_JOB_ASSIGN_FAILED')
            def stop(*_):
                child.terminate()
            signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
            return child.wait()
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=30)
                except subprocess.TimeoutExpired:child.kill();child.wait()
            if job:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(job[1]))


def main():
    from portable_n8n import console_utf8
    console_utf8()
    parser=argparse.ArgumentParser(description='Video Factory 后台运行：保留数据，不自动启用收费工作流')
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--component',required=True,choices=['n8n','events'])
    parser.add_argument('--exclusive-app',action='store_true',help='已核对同一飞书应用没有其他接收器；仅事件服务首次安装需要')
    parser.add_argument('action',choices=['install','status','start','stop','restart','uninstall','run'])
    args=parser.parse_args()
    try:
        if args.action=='run':return serve(args.root,args.component)
        if args.action=='install' and args.component=='events' and not args.exclusive_app:
            raise Fault('CONFIRM_NO_OTHER_APP_RECEIVER_THEN_PASS_EXCLUSIVE_APP')
        value=install(args.root,args.component) if args.action=='install' else control(args.root,args.component,args.action)
        print(json.dumps(value,ensure_ascii=False));return 0
    except Exception as error:
        print(str(error) if isinstance(error,Fault) else 'SERVICE_FAILED_DATA_PRESERVED');return 2


if __name__=='__main__':sys.exit(main())
