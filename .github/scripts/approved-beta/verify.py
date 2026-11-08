"""Fresh cloud runner follows public installation without private repository/auth."""
import hashlib,json,os,platform,subprocess,sys,tempfile,urllib.request,zipfile,stat
from pathlib import Path
from urllib.parse import quote
trusted=Path(__file__).with_name('release-manifest.json').read_bytes()
manifest=json.loads(trusted);target=os.environ['VF_ASSET']
assert (sys.platform,platform.machine())==({'linux-x86_64':('linux','x86_64'),'macos-arm64':('darwin','arm64')}[target])
assert not any(os.environ.get(k) for k in ('GH_TOKEN','GITHUB_TOKEN','VF_BUSINESS_CREDENTIALS','DEEPSEEK_API_KEY'))
base='https://github.com/ChuluuMGL/video-factory/releases/download/v0.2.0-beta/'
def download(name):
    with urllib.request.urlopen(base+quote(name),timeout=120) as response:
        assert response.status==200
        return response.read(60*1024*1024)
assert download('release-manifest.json')==trusted
assert download('SHA256SUMS')==Path(__file__).with_name('SHA256SUMS').read_bytes()
selected={'video-factory-v0.2.0-beta-'+target+'.zip','video-factory-employee-client-v0.2.0-beta.zip','INSTALL.md','SKILL.md','PRIVACY_REVIEW.md'}
result={'passed':False,'target':target,'anonymous':True,'private_agent_checkout':False,'downloads':[],'model_calls':0,'production_writes':0}
def sockets():
    cmd=['lsof','-nP','-iTCP','-sTCP:LISTEN','-Fn']
    p=subprocess.run(cmd,text=True,capture_output=True)
    return sorted(set(l[1:] for l in p.stdout.splitlines() if l.startswith('n')))
try:
    with tempfile.TemporaryDirectory(prefix='vf-public-') as tmp:
        temp=Path(tmp).resolve();before=sockets()
        for a in manifest['assets']:
            if a['name'] not in selected:continue
            data=download(a['name']);assert len(data)==a['bytes'] and hashlib.sha256(data).hexdigest()==a['sha256']
            result['downloads'].append({'name':a['name'],'sha256':a['sha256'],'http':200})
            p=temp/a['name'];p.write_bytes(data)
            if p.suffix=='.zip':
                with zipfile.ZipFile(p) as z:
                    assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in z.namelist())
                    z.extractall(temp)
        bundle=temp/('video-factory-v0.2.0-beta-'+target);root=temp/'VideoFactory-Beta'
        env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','TMPDIR')}
        env.update(PIP_NO_INDEX='1',PYTHONNOUSERSITE='1')
        install=subprocess.run([sys.executable,str(bundle/'install.py'),'--mode','business','--root',str(root)],env=env,text=True,capture_output=True,check=True)
        installation=json.loads(install.stdout);assert installation['installed'] and not installation['automatically_started']
        receipt=json.loads((root/'install.json').read_text());assert receipt['phase']=='installed' and receipt['profile']=='business'
        for k,v in manifest['source'].items(): assert receipt[k]==v
        assert stat.S_IMODE(root.stat().st_mode)==0o700
        entry=json.loads(subprocess.check_output([str(root/'vf-business')],env=env,text=True))
        assert entry['profile']=='business' and not entry['automatically_started']
        assert all(x in entry['commands'] for x in ('setup','inspect','start','employee-suspend','employee-resume'))
        setup=subprocess.run([str(root/'vf-business'),'setup','--help'],env=env,text=True,capture_output=True,check=True)
        assert '--interactive' in setup.stdout and '--bind' in setup.stdout
        client=temp/'video-factory-employee-client-v0.2.0-beta/business-employee.mjs'
        subprocess.run(['node','--check',str(client)],env=env,check=True,capture_output=True)
        absent=subprocess.run(['node',str(client),'--mcp'],env=env,text=True,capture_output=True)
        assert absent.returncode!=0 and 'Explicit private credential path' in absent.stderr
        assert not (root/'kernel/business').exists()
        processes=subprocess.check_output(['ps','-axo','command='],text=True)
        assert not any(str(root) in line or str(client) in line for line in processes.splitlines())
        after=sockets();assert set(after)<=set(before), 'Unexpected listener after installation'
        result.update(passed=True,source=manifest['source'],python=platform.python_version(),node=subprocess.check_output(['node','--version'],text=True).strip(),offline_install=True,cli_help=True,employee_syntax=True,employee_without_credentials_denied=True,employee_help_supported=False,no_business_state_created=True,no_installed_processes=True,no_new_tcp_listeners=True,full_feishu_setup=False,physical_device=False)
finally:
    Path('public-install-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
