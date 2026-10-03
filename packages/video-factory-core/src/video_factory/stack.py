"""Root-admin, single-customer Compose deployment and cold encrypted recovery.

Only generated, digest-checked Compose is executed. No arbitrary Compose files,
service names, hooks, or shell fragments are accepted from a config or backup.
"""
from contextlib import contextmanager
import base64
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import secrets
import shutil
import socket
import stat
import subprocess
import tarfile
import tempfile
from urllib.request import build_opener, ProxyHandler

from cryptography.fernet import Fernet, InvalidToken

from .runtime_store import RuntimeFault, canonical, exclusive_write, private_directory, private_file

ASSETS = Path(__file__).with_name('deployment')
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_UNPACKED = 1024 * 1024 * 1024


def tls_generation_link(name, target):
    # The sole allowed archive link is the product's atomic certificate pointer.
    return bool(re.fullmatch(r'data/workspaces/[A-Za-z0-9_-]{1,96}/tls/current', name)
                and re.fullmatch(r'generations/[a-f0-9]{32}', target))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def images(schema=2):
    return json.loads((ASSETS/('images.v1.json' if schema==1 else 'images.json')).read_text())


def template(name,schema):
    return ASSETS/('Dockerfile.v1' if name=='Dockerfile' and schema==1 else name)


def run(command, *, timeout=300, data=None):
    try:
        result = subprocess.run(command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeFault('STACK_COMMAND_UNCERTAIN_CHECK_STATUS') from None
    if result.returncode:
        # Provider passwords and Docker env/connection strings must not leak.
        raise RuntimeFault('STACK_COMMAND_FAILED')
    return result.stdout


def admin_host():
    if platform.system()!='Linux' or platform.machine()!='x86_64' or os.getuid()!=0:
        raise RuntimeFault('LINUX_X86_64_ROOT_ADMIN_REQUIRED')


def local_engine():
    if os.environ.get('DOCKER_HOST') not in (None,'','unix:///var/run/docker.sock'):
        raise RuntimeFault('LOCAL_DOCKER_ENGINE_REQUIRED')
    endpoint=run(['docker','context','inspect','--format','{{.Endpoints.docker.Host}}']).decode().strip()
    if endpoint!='unix:///var/run/docker.sock':
        raise RuntimeFault('LOCAL_DOCKER_ENGINE_REQUIRED')


def preflight(root,runtime_port=8787,n8n_port=5678):
    admin_host()
    root=private_directory(root)
    local_engine()
    info=json.loads(run(['docker','info','--format','{{json .}}']))
    version=run(['docker','compose','version','--short']).decode().strip()
    if info.get('OSType')!='linux' or info.get('Architecture') not in ('x86_64','amd64'):
        raise RuntimeFault('DOCKER_LINUX_AMD64_REQUIRED')
    if info.get('MemTotal',0)<4*1024**3 or shutil.disk_usage(root).free<4*1024**3:
        raise RuntimeFault('STACK_REQUIRES_4_GIB_MEMORY_AND_FREE_DISK')
    match=re.fullmatch(r'v?(\d+)\.(\d+)\..*',version)
    if not match or (int(match[1]),int(match[2]))<(2,24):
        raise RuntimeFault('DOCKER_COMPOSE_224_OR_NEWER_REQUIRED')
    if runtime_port==n8n_port:
        raise RuntimeFault('STACK_PORT_INVALID')
    for port in (runtime_port,n8n_port):
        if type(port) is not int or not 1024<=port<=65535:
            raise RuntimeFault('STACK_PORT_INVALID')
        with socket.socket() as sock:
            # Match the gateway's restart semantics: closed connections in
            # TIME_WAIT must not look like an active service. A live listener
            # still rejects this bind; SO_REUSEPORT is deliberately not used.
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try:sock.bind(('127.0.0.1',port))
            except OSError:raise RuntimeFault('STACK_PORT_ALREADY_IN_USE') from None
    return {'platform':'linux/amd64','docker_version':info.get('ServerVersion'),'compose_version':version,
            'capacity_minimum_passed':True,'loopback_ports_available':True,'customer_resources_changed':False}


def validate_config(config):
    if (not isinstance(config,dict) or set(config)-{'image_bundle','resolved_images'}!={'schema','deployment','instance','runtime_port','n8n_port','images','wheels','runtime_image'}
            or type(config['schema']) is not int or config['schema'] not in (1,2) or config['images']!=images(config['schema'])
            or not re.fullmatch(r'[0-9a-f]{12}',str(config['instance']))
            or not re.fullmatch(r'[a-z][a-z0-9_-]{2,47}',str(config['deployment']))):
        raise RuntimeFault('STACK_CONFIG_UNSUPPORTED')
    if 'image_bundle' in config:
        from .image_bundle import validate_manifest
        validate_manifest(config['image_bundle'], config['wheels'], config['images'])
        bundle=config['image_bundle']
        allowed={role:{image_id,bundle.get('oci_images',{}).get(role,image_id)} for role,image_id in bundle['images'].items()}
        if 'resolved_images' in config:
            resolved=config['resolved_images']
            if (not isinstance(resolved,dict) or set(resolved)!=set(allowed)
                    or any(not isinstance(v,str) or v not in allowed[k] for k,v in resolved.items())):
                raise RuntimeFault('IMAGE_BUNDLE_RESOLVED_IDS_INVALID')
            allowed['runtime']={resolved['runtime']}
        if config['runtime_image'] is not None and config['runtime_image'] not in allowed['runtime']:
            raise RuntimeFault('IMAGE_BUNDLE_RUNTIME_MISMATCH')
    elif 'resolved_images' in config:
        raise RuntimeFault('IMAGE_BUNDLE_RESOLVED_IDS_INVALID')
    ports=[config['runtime_port'],config['n8n_port']]
    if any(type(p) is not int or not 1024<=p<=65535 for p in ports) or len(set(ports))!=2:
        raise RuntimeFault('STACK_PORT_INVALID')
    if (not isinstance(config['wheels'],dict) or not 1<=len(config['wheels'])<=30
            or any(not re.fullmatch(r'[A-Za-z0-9_.+-]+\.whl',k) or not re.fullmatch(r'[0-9a-f]{64}',str(v)) for k,v in config['wheels'].items())
            or sum(k.startswith('video_factory_core-') for k in config['wheels'])!=1):
        raise RuntimeFault('STACK_RELEASE_INVALID')
    if config['runtime_image'] is not None and not re.fullmatch(r'sha256:[0-9a-f]{64}',str(config['runtime_image'])):
        raise RuntimeFault('STACK_IMAGE_ID_INVALID')


def write_json(path,value):
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    temp=Path(name)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(canonical(value).encode());stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def compose_document(config):
    validate_config(config)
    service_images=config.get('resolved_images',config['image_bundle']['images']) if 'image_bundle' in config else config['images']
    common={'platform':'linux/amd64','restart':'unless-stopped','security_opt':['no-new-privileges:true'],
            'networks':['private'],'logging':{'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}}
    if 'image_bundle' in config:common['pull_policy']='never'
    pg={**common,'image':service_images['postgres'],
        'environment':{'POSTGRES_USER':'postgres','POSTGRES_PASSWORD_FILE':'/run/secrets/postgres_password'},
        'secrets':['postgres_password','product_db_password','n8n_db_password'],
        'volumes':['./data/postgres:/var/lib/postgresql/data','./release/init-databases.sh:/docker-entrypoint-initdb.d/10-vf.sh:ro'],
        'healthcheck':{'test':['CMD-SHELL','pg_isready -U postgres -d postgres'],'interval':'3s','timeout':'3s','retries':40},
        'stop_grace_period':'60s'}
    runtime={**common,'image':config['runtime_image'] or 'vf-runtime-not-built',
             'pull_policy':'never','read_only':True,'init':True,'tmpfs':['/tmp:rw,noexec,nosuid,size=64m'],
             'environment':{'VF_DATABASE_URL_FILE':'/run/secrets/runtime_dsn','VF_CONTAINER_MODE':'1'},
             'secrets':['runtime_dsn','runtime_master'], 'volumes':['./data/runtime:/state','./data/media:/media'],
             'depends_on':{'postgres':{'condition':'service_healthy'}},

             'command':['runtime','serve','--root','/state','--port','8787','--container-network'],
             'healthcheck':{'test':['CMD','python','-c',"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8787/healthz',timeout=2)"],
                            'interval':'3s','timeout':'4s','retries':30}}
    bootstrap={**runtime,'profiles':['bootstrap'],'restart':'no','ports':[],
               'environment':{**runtime['environment'],'VF_DEPLOYMENT':config['deployment']},
               'secrets':runtime['secrets']+['bootstrap_password'],
               'entrypoint':['python','-m','video_factory.container_entry'],'command':[],
               'healthcheck':{'disable':True}}
    n8n={**common,'image':service_images['n8n'],'user':'1000:1000',
         'depends_on':{'postgres':{'condition':'service_healthy'},'runtime':{'condition':'service_healthy'}},

         'volumes':['./data/n8n:/home/node/.n8n'], 'secrets':['n8n_db_password','n8n_encryption'],
         'environment':{'DB_TYPE':'postgresdb','DB_POSTGRESDB_HOST':'postgres','DB_POSTGRESDB_PORT':'5432',
                        'DB_POSTGRESDB_DATABASE':'vf_n8n','DB_POSTGRESDB_USER':'vf_n8n',
                        'DB_POSTGRESDB_PASSWORD_FILE':'/run/secrets/n8n_db_password',
                        'N8N_ENCRYPTION_KEY_FILE':'/run/secrets/n8n_encryption',
                        'N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS':'true','N8N_DIAGNOSTICS_ENABLED':'false',
                        'N8N_VERSION_NOTIFICATIONS_ENABLED':'false','N8N_TEMPLATES_ENABLED':'false',
                        'N8N_PERSONALIZATION_ENABLED':'false','N8N_COMMUNITY_PACKAGES_ENABLED':'false',
                        'N8N_UNVERIFIED_PACKAGES_ENABLED':'false','N8N_PROXY_HOPS':'1',
                        'N8N_EDITOR_BASE_URL':f"http://localhost:{config['n8n_port']}",
                        'GENERIC_TIMEZONE':'Asia/Shanghai','TZ':'Asia/Shanghai'},
         'healthcheck':{'test':['CMD','node','-e',"fetch('http://127.0.0.1:5678/healthz/readiness').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"],
                        'interval':'5s','timeout':'5s','retries':48},'stop_grace_period':'60s'}
    gateway={**common,'image':service_images['gateway'],'user':'101:101','read_only':True,
             'cap_drop':['ALL'],'tmpfs':['/tmp:rw,noexec,nosuid,size=64m'],
             'networks':['private','management'],
             'entrypoint':['nginx','-g','daemon off;'],
             'volumes':['./release/nginx.conf:/etc/nginx/nginx.conf:ro'],
             'ports':[f"127.0.0.1:{config['runtime_port']}:8080",f"127.0.0.1:{config['n8n_port']}:8081"],
             'depends_on':{'runtime':{'condition':'service_healthy'},'n8n':{'condition':'service_healthy'}},
             'healthcheck':{'test':['CMD','wget','-q','-O','/dev/null','http://127.0.0.1:8080/healthz'],
                            'interval':'3s','timeout':'3s','retries':20}}
    names=('postgres_password','product_db_password','n8n_db_password','n8n_encryption','runtime_dsn','runtime_master','bootstrap_password')
    document={'name':'vf-'+config['deployment']+'-'+config['instance'],'services':{'postgres':pg,'runtime':runtime,'n8n':n8n,'bootstrap':bootstrap,'gateway':gateway},
            'networks':{'private':{'internal':True},'management':{}},'secrets':{name:{'file':'./secrets/'+name} for name in names}}
    if config['schema']==2:
        # Only an explicitly invoked, short-lived worker can reach the relay.
        document['networks'].update({'worker_link':{'internal':True},'outbound':{}})
        document['services']['worker']={**runtime,'profiles':['worker'],'restart':'no',
            'networks':['private','worker_link'],'cap_drop':['ALL'],
            'environment':{**runtime['environment'],'VF_WORKER_EGRESS':'1'},
            'volumes':runtime['volumes']+['./data/worker:/work:ro'],
            'entrypoint':['python','-m','video_factory.worker_container'],
            'command':['--help'],'healthcheck':{'disable':True}}
        document['services']['egress']={**common,'image':runtime['image'],'profiles':['worker'],
            'restart':'no','read_only':True,'cap_drop':['ALL'],'init':True,
            'networks':['worker_link','outbound'],'pids_limit':64,'mem_limit':'128m',
            'entrypoint':['python','-m','video_factory.worker_egress'],
            'healthcheck':{'test':['CMD','python','-c',"import socket; socket.create_connection(('127.0.0.1',8443),2).close()"],
                           'interval':'2s','timeout':'3s','retries':15}}
    return document


def install_release(root,wheelhouse, schema=2):
    wheelhouse=Path(wheelhouse)
    paths=sorted(wheelhouse.glob('*.whl'))
    if not paths or sum(p.stat().st_size for p in paths)>100*1024*1024:
        raise RuntimeFault('RELEASE_WHEELHOUSE_REQUIRED_OR_TOO_LARGE')
    release=root/'release';release.mkdir(mode=0o700)
    dest=release/'wheels';dest.mkdir(mode=0o700)
    hashes={}
    for path in paths:
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r'[A-Za-z0-9_.+-]+\.whl',path.name):
            raise RuntimeFault('RELEASE_WHEEL_UNSAFE')
        raw=path.read_bytes();hashes[path.name]=digest(raw);exclusive_write(dest/path.name,raw)
    for name in ('Dockerfile','init-databases.sh','nginx.conf'):
        exclusive_write(release/name,template(name,schema).read_bytes())
    (release/'init-databases.sh').chmod(0o644)
    (release/'nginx.conf').chmod(0o644)
    return hashes


def read_stack_config(root):
    admin_host()
    root=private_directory(root)
    private_file(root/'stack.json')
    config=json.loads((root/'stack.json').read_text())
    validate_config(config)
    for name,checksum in config['wheels'].items():
        path=root/'release/wheels'/name
        private_file(path)
        if digest(path.read_bytes())!=checksum:
            raise RuntimeFault('RELEASE_WHEEL_CHANGED')
    for name in ('Dockerfile','init-databases.sh','nginx.conf'):
        path=root/'release'/name
        if path.is_symlink() or path.read_bytes()!=template(name,config['schema']).read_bytes():
            raise RuntimeFault('RELEASE_TEMPLATE_CHANGED')
    return config


class Stack:
    def __init__(self,root,*,repair_derived=False):
        admin_host()
        self.root=private_directory(root)
        self.config=read_stack_config(self.root)
        expected=compose_document(self.config)
        path=self.root/'compose.json'
        if path.is_symlink():
            raise RuntimeFault('COMPOSE_CHANGED_REGENERATE_FROM_TRUSTED_RELEASE')
        if not path.exists() or json.loads(path.read_text())!=expected:
            if not repair_derived:
                raise RuntimeFault('COMPOSE_CHANGED_REGENERATE_FROM_TRUSTED_RELEASE')
            write_json(path,expected)

    @classmethod
    def prepare(cls,root,deployment,wheelhouse,password,runtime_port=8787,n8n_port=5678):
        admin_host()
        root=private_directory(root)
        if any(root.iterdir()):
            raise RuntimeFault('STACK_PREPARE_REQUIRES_EMPTY_DIRECTORY')
        with tempfile.TemporaryDirectory(prefix='.vf-prepare-',dir=root.parent) as temporary:
            staged=Path(temporary)
            cls._prepare_in_place(staged,deployment,wheelhouse,password,runtime_port,n8n_port)
            # Replaces only an empty directory; a concurrent completed installer
            # makes rename fail instead of overwriting customer state.
            os.rename(staged,root)
        return cls(root)

    @classmethod
    def _prepare_in_place(cls,root,deployment,wheelhouse,password,runtime_port=8787,n8n_port=5678):
        admin_host()
        from .runtime_store import RuntimeStore
        RuntimeStore.validate_password(password)
        root=private_directory(root)
        if any(root.iterdir()):
            raise RuntimeFault('STACK_PREPARE_REQUIRES_EMPTY_DIRECTORY')
        config={'schema':2,'instance':secrets.token_hex(6),'deployment':deployment,'runtime_port':runtime_port,'n8n_port':n8n_port,
                'images':images(),'wheels':{'video_factory_core-placeholder.whl':'0'*64},'runtime_image':None}
        validate_config(config)
        config['wheels']=install_release(root,wheelhouse)
        validate_config(config)
        sec=root/'secrets';sec.mkdir(mode=0o700)
        values={name:secrets.token_hex(32) for name in ('postgres_password','product_db_password','n8n_db_password','n8n_encryption')}
        values['runtime_dsn']='postgresql://vf_runtime:'+values['product_db_password']+'@postgres:5432/vf_runtime'
        values['runtime_master']=Fernet.generate_key().decode()
        values['bootstrap_password']=password
        for name,value in values.items():
            path=sec/name;exclusive_write(path,value.encode())
            if name in ('runtime_dsn','runtime_master','bootstrap_password'):
                os.chown(path,10001,10001)
            else:
                path.chmod(0o444)  # Host parent is root-only; only named services mount each file.
        data=root/'data';data.mkdir(mode=0o700)
        for name,uid in (('postgres',999),('runtime',10001),('media',10001),('worker',10001),('n8n',1000)):
            path=data/name;path.mkdir(mode=0o700);os.chown(path,uid,uid)
        write_json(root/'stack.json',config)
        write_json(root/'compose.json',compose_document(config))
        return cls(root)

    @contextmanager
    def lock(self):
        fd=os.open(self.root/'stack.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            private_file(self.root/'stack.lock')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield
        except BlockingIOError:
            raise RuntimeFault('STACK_OPERATION_ALREADY_RUNNING') from None
        finally:
            os.close(fd)

    def compose(self,*args,timeout=300,data=None):
        local_engine()
        profiles=['--profile','worker'] if self.config['schema']==2 else []
        return run(['docker','compose',*profiles,'--project-directory',str(self.root),'-f',str(self.root/'compose.json'),*args],timeout=timeout,data=data)

    def build(self):
        local_engine()
        with self.lock():
            if 'image_bundle' in self.config:
                from .image_bundle import verify_loaded
                self.config['resolved_images']=verify_loaded(self.config['image_bundle'])
                self.config['runtime_image']=self.config['resolved_images']['runtime']
                write_json(self.root/'stack.json',self.config)
                write_json(self.root/'compose.json',compose_document(self.config))
                return {'built':False,'offline_image_reused':True,'runtime_image':self.config['runtime_image']}
            tag='vf-runtime:'+digest(canonical(self.config['wheels']).encode())[:24]
            args=['docker','build','--network=none','--build-arg','PYTHON_IMAGE='+self.config['images']['python']]
            if self.config['schema']==2:args+=['--build-arg','FFMPEG_IMAGE='+self.config['images']['ffmpeg']]
            run([*args,'-t',tag,str(self.root/'release')],timeout=600)
            image=run(['docker','image','inspect',tag,'--format','{{.Id}}']).decode().strip()
            self.config['runtime_image']=image;validate_config(self.config)
            write_json(self.root/'stack.json',self.config)
            write_json(self.root/'compose.json',compose_document(self.config))
        return {'built':True,'runtime_image':image,'wheel_hashes':self.config['wheels']}

    def up(self):
        with self.lock():
            return self._up_unlocked()

    def _up_unlocked(self):
        if not self.config['runtime_image']:
            raise RuntimeFault('STACK_BUILD_REQUIRED')
        # Use only already fetched pinned images during runtime operations.
        self.compose('up','-d','--pull','never','--wait','--wait-timeout','120','postgres')
        if not (self.root/'initialized.json').exists():
            self.compose('run','--rm','--no-deps','bootstrap')
            write_json(self.root/'initialized.json',{'deployment':self.config['deployment'],'schema':1})
            (self.root/'secrets/bootstrap_password').unlink(missing_ok=True)
        if (self.root/'restored.json').exists():
            self.compose('run','--rm','--no-deps','--entrypoint','python','runtime','-m','video_factory.container_entry','--revoke-sessions')
            (self.root/'restored.json').unlink()
        self.compose('up','-d','--pull','never','--wait','--wait-timeout','240','runtime','n8n','gateway',timeout=300)
        return self.status()


    def status(self):
        raw=self.compose('ps','--all','--format','json').decode()
        rows=json.loads(raw) if raw.lstrip().startswith('[') else [json.loads(line) for line in raw.splitlines() if line]
        status={row['Service']:{'state':row['State'],'health':row.get('Health',''),'exit_code':row.get('ExitCode')} for row in rows}
        ready=all(status.get(name,{}).get('health')=='healthy' and status[name]['state']=='running' for name in ('postgres','runtime','n8n','gateway'))
        routes_ready=False
        if ready:
            try:
                opener=build_opener(ProxyHandler({}))
                with opener.open(f"http://127.0.0.1:{self.config['runtime_port']}/healthz",timeout=3) as response:
                    routes_ready=json.loads(response.read(2048)).get('status')=='up'
                with opener.open(f"http://127.0.0.1:{self.config['n8n_port']}/healthz/readiness",timeout=3) as response:
                    routes_ready=routes_ready and response.status==200
            except (OSError,ValueError):
                routes_ready=False
        return {'deployment':self.config['deployment'],'components':status,'infrastructure_ready':ready and routes_ready,'host_routes_ready':routes_ready,
                'egress':'worker_allowlist_only' if status.get('egress',{}).get('state')=='running' else 'disabled',
                'paid_generation':'explicit_task_approval_required','human_acceptance':'not_run',
                'worker_container_available':self.config['schema']==2}

    def stop(self):
        with self.lock():
            from .workspace import stop_all
            stop_all(self)
            self.compose('stop','--timeout','60',timeout=200)
            return self.status()

    def backup(self,destination,key):
        with self.lock():
            return self._backup_unlocked(destination,key)

    def _backup_unlocked(self,destination,key):
        Fernet(key)  # Validate before stopping any running service.
        destination=Path(destination)
        private_directory(destination.parent)
        if destination.exists() or destination.is_symlink() or destination.is_relative_to(self.root):
            raise RuntimeFault('BACKUP_REQUIRES_NEW_PATH_OUTSIDE_STACK')
        from .workspace import stop_all
        stop_all(self)
        # Explicit cold backup; services deliberately remain stopped afterward.
        self.compose('stop','--timeout','60',timeout=200)
        components=self.status()['components']
        if any(v['state']=='running' for v in components.values()):
            raise RuntimeFault('BACKUP_REQUIRES_STOPPED_COMPONENTS')
        if components.get('postgres',{}).get('state')!='exited' or components['postgres'].get('exit_code')!=0:
            raise RuntimeFault('BACKUP_REQUIRES_CLEAN_POSTGRES_SHUTDOWN')
        selected=[self.root/'stack.json',self.root/'initialized.json',self.root/'release',self.root/'secrets',self.root/'data']
        total=0
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
            for parent in selected:
                if not parent.exists():
                    raise RuntimeFault('BACKUP_COMPONENT_MISSING')
                for path in [parent]+(sorted(parent.rglob('*')) if parent.is_dir() else []):
                    info=path.lstat()
                    link = stat.S_ISLNK(info.st_mode)
                    if link:
                        target = os.readlink(path)
                        if (not tls_generation_link(path.relative_to(self.root).as_posix(), target)
                                or (path.parent/target).resolve() != path.parent/target
                                or not (path.parent/target).is_dir()):
                            raise RuntimeFault('BACKUP_SPECIAL_FILE_REJECTED')
                        for name in ('certificate.pem', 'key.pem'):
                            part = (path.parent/target/name).lstat()
                            if not stat.S_ISREG(part.st_mode) or part.st_nlink != 1:
                                raise RuntimeFault('BACKUP_SPECIAL_FILE_REJECTED')
                    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) or link) or (stat.S_ISREG(info.st_mode) and info.st_nlink!=1):
                        raise RuntimeFault('BACKUP_SPECIAL_FILE_REJECTED')
                    total+=info.st_size
                    if total>MAX_UNPACKED:
                        raise RuntimeFault('BACKUP_TOO_LARGE')
                    archive.add(path,arcname=str(path.relative_to(self.root)),recursive=False)
                    if buffer.tell()>MAX_ARCHIVE:
                        raise RuntimeFault('BACKUP_TOO_LARGE')
        encrypted=Fernet(key).encrypt(buffer.getvalue())
        exclusive_write(destination,encrypted)
        return {'backup':str(destination),'sha256':digest(encrypted),'encrypted':True,'components_stopped':True,
                'includes':['postgres_databases','runtime_state','n8n_state','media','secret_keys','release_wheels'],
                'backup_key_included':False,'portability':'same_pinned_images_linux_amd64'}


    def upgrade(self,candidate_root,wheelhouse,checkpoint,key,*,image_bundle=None):
        """Stage a product-wheel upgrade on a cold clone; recover original on failure.

        Component digests and DB schema remain fixed. The original data directory
        is never migrated in place. Only a healthy candidate becomes the new root.
        """
        candidate_root=private_directory(candidate_root)
        if (any(candidate_root.iterdir()) or candidate_root.is_relative_to(self.root)
                or self.root.is_relative_to(candidate_root)):
            raise RuntimeFault('UPGRADE_REQUIRES_SEPARATE_EMPTY_DIRECTORY')
        if 'image_bundle' in self.config and image_bundle is None:
            raise RuntimeFault('OFFLINE_UPGRADE_REQUIRES_CANDIDATE_IMAGE_BUNDLE')
        if image_bundle is not None:
            from .image_bundle import validate_manifest, verify_loaded
            from .setup_deploy import release_manifest
            validate_manifest(image_bundle,release_manifest(wheelhouse),images())
            verify_loaded(image_bundle)
        Fernet(key)
        candidate=None
        with self.lock():
            if not self.status()['infrastructure_ready']:
                raise RuntimeFault('UPGRADE_REQUIRES_HEALTHY_SOURCE')
            try:
                self._backup_unlocked(checkpoint,key)
                candidate=Stack.restore(checkpoint,candidate_root,key)
                # Only the new clone's release is replaced. The original and the
                # encrypted checkpoint retain the previous release and data.
                shutil.rmtree(candidate_root/'release')
                hashes=install_release(candidate_root,wheelhouse)
                if hashes==self.config['wheels']:
                    raise RuntimeFault('UPGRADE_RELEASE_UNCHANGED')
                candidate.config['schema']=2
                candidate.config['images']=images(2)
                work=candidate_root/'data/worker'
                if not work.exists():work.mkdir(mode=0o700);os.chown(work,10001,10001)
                candidate.config.pop('image_bundle',None)
                candidate.config.pop('resolved_images',None)
                if image_bundle is not None:candidate.config['image_bundle']=image_bundle
                candidate.config['wheels']=hashes
                candidate.config['runtime_image']=None
                validate_config(candidate.config)
                write_json(candidate_root/'stack.json',candidate.config)
                write_json(candidate_root/'compose.json',compose_document(candidate.config))
                candidate=Stack(candidate_root)
                if image_bundle is None:
                    run(['docker','pull','--platform','linux/amd64',candidate.config['images']['ffmpeg']],timeout=600)
                candidate.build()
                status=candidate.up()
                if not status['infrastructure_ready']:
                    raise RuntimeFault('UPGRADE_CANDIDATE_NOT_HEALTHY')
                return {'status':'upgraded','active_root':str(candidate_root),'previous_root':str(self.root),
                        'checkpoint':str(checkpoint),'previous_data_preserved':True,'candidate':status}
            except Exception:
                if candidate is not None:
                    try:candidate.compose('down','--timeout','30',timeout=120)
                    except RuntimeFault:pass
                try:
                    restored=self._up_unlocked()
                except Exception:
                    return {'status':'needs_attention','error':'UPGRADE_AND_ROLLBACK_FAILED',
                            'previous_root':str(self.root),'checkpoint':str(checkpoint)}
                return {'status':'rolled_back' if restored['infrastructure_ready'] else 'needs_attention',
                        'error':'UPGRADE_FAILED','active_root':str(self.root),'checkpoint':str(checkpoint),
                        'previous_data_preserved':True,'original':restored}

    @classmethod
    def restore(cls,source,root,key,*,deployment=None,runtime_port=None,n8n_port=None):
        admin_host()
        source=Path(source);private_file(source)
        root=private_directory(root)
        if any(root.iterdir()):
            raise RuntimeFault('RESTORE_REQUIRES_EMPTY_DIRECTORY')
        if source.stat().st_size>MAX_ARCHIVE*2:
            raise RuntimeFault('BACKUP_TOO_LARGE')
        try:
            raw=Fernet(key).decrypt(source.read_bytes())
        except (InvalidToken,ValueError,TypeError):
            raise RuntimeFault('BACKUP_AUTHENTICATION_FAILED') from None
        if len(raw)>MAX_ARCHIVE:
            raise RuntimeFault('BACKUP_TOO_LARGE')
        # Validate all members before writing; never use tar.extractall().
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
            members=archive.getmembers();seen=set();total=0
            for member in members:
                path=PurePosixPath(member.name)
                if (path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] not in {'stack.json','initialized.json','release','secrets','data'}
                        or member.name in seen or not (member.isfile() or member.isdir() or (member.issym() and tls_generation_link(member.name, member.linkname))) or member.mode & 0o7000
                        or member.uid not in (0,999,1000,10001) or member.gid not in (0,999,1000,10001)):
                    raise RuntimeFault('BACKUP_MEMBER_UNSAFE')
                seen.add(member.name);total+=member.size
                if total>MAX_UNPACKED or len(members)>100000:
                    raise RuntimeFault('BACKUP_TOO_LARGE')
            indexed = {member.name: member for member in members}
            links = [member for member in members if member.issym()]
            for member in links:
                target = (PurePosixPath(member.name).parent/member.linkname).as_posix()
                if (target not in indexed or not indexed[target].isdir()
                        or any(name.startswith(member.name+'/') for name in indexed)
                        or any(target+'/'+name not in indexed or not indexed[target+'/'+name].isfile()
                               for name in ('certificate.pem', 'key.pem'))):
                    raise RuntimeFault('BACKUP_MEMBER_UNSAFE')
            config=json.load(archive.extractfile('stack.json'));validate_config(config)
            # Deployment identity is immutable; use a new compose name through the
            # explicit clone option only, preserving product ledger identity.
            if deployment is not None and deployment!=config['deployment']:
                raise RuntimeFault('RESTORE_DEPLOYMENT_IDENTITY_MUST_MATCH')
            if runtime_port is not None:config['runtime_port']=runtime_port
            if n8n_port is not None:config['n8n_port']=n8n_port
            validate_config(config)
            for member in sorted((m for m in members if not m.issym()),key=lambda m:(len(PurePosixPath(m.name).parts),m.name)):
                path=root/member.name
                if member.isdir():
                    path.mkdir(mode=0o700,exist_ok=True)
                else:
                    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                    with archive.extractfile(member) as stream:
                        exclusive_write(path,stream.read())
                path.chmod(member.mode & 0o777);os.chown(path,member.uid,member.gid)
            # All regular members are validated and written before any link exists.
            for member in links:
                path = root/member.name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.symlink(member.linkname, path)
                os.lchown(path, member.uid, member.gid)
        # Rebuild product image from pinned base and unchanged wheel hashes on a
        # new host; registry images must be pulled by their locked digests.
        config['runtime_image']=None
        config['instance']=secrets.token_hex(6)
        write_json(root/'restored.json',{'revoke_sessions_before_start':True})
        write_json(root/'stack.json',config)
        write_json(root/'compose.json',compose_document(config))
        return cls(root)
