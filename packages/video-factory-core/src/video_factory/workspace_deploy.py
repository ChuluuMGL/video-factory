"""Journal a workspace replacement before downtime; retain private rollback files."""
import hashlib
import json
import os
import secrets
import stat

from .runtime_store import RuntimeFault, private_file
from .stack import write_json
from . import workspace

FILES = ('workspace.json', 'compose.json', 'nginx.conf', 'tls/certificate.pem', 'tls/key.pem')


def pending(root):
    path = root/'deployment.json'
    if not path.exists(): return False
    private_file(path)
    return json.loads(path.read_text())['status'] in ('in_flight', 'needs_attention')


def write_files(root, files):
    for name, data in files.items():
        path = root/name
        if path.resolve() != path: raise RuntimeFault('WORKSPACE_FILE_UNSAFE')
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeFault('WORKSPACE_FILE_UNSAFE')
        if data is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o644 if name == 'nginx.conf' else 0o600)
        if name.startswith('tls/'):
            os.chown(path.parent, 10001, 10001); os.chown(path, 10001, 10001)


def generated(stack, value, raw, keyraw):
    return {'workspace.json': json.dumps(value).encode(),
            'compose.json': json.dumps(workspace.document(stack, value)).encode(),
            'nginx.conf': workspace.nginx(value).encode(),
            'tls/certificate.pem': raw, 'tls/key.pem': keyraw}


def apply(stack, value, raw, keyraw):
    project = value['project']; root = workspace.directory(stack, project)
    previous = None; restart = False
    if (root/'workspace.json').exists():
        private_file(root/'workspace.json')
        previous = json.loads((root/'workspace.json').read_text())
        if previous['stack_instance'] == stack.config['instance'] and previous['runtime_image'] == stack.config['runtime_image']:
            state = workspace.status(stack, project)
            restart = any(row['State'] not in ('exited', 'dead', 'created') for row in state['components'])
    backup = root/('deployment-'+secrets.token_hex(16)); backup.mkdir(mode=0o700)
    files = {}
    for index, name in enumerate(FILES):
        path = root/name
        if path.resolve() != path: raise RuntimeFault('WORKSPACE_FILE_UNSAFE')
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 512*1024:
                raise RuntimeFault('WORKSPACE_FILE_UNSAFE')
            data = path.read_bytes(); target = backup/str(index)
            target.write_bytes(data); target.chmod(0o600)
            with target.open('rb') as stream: os.fsync(stream.fileno())
            files[name] = hashlib.sha256(data).hexdigest()
        else: files[name] = None
    receipt = {'status':'in_flight', 'project':project, 'candidate':value,
               'stack_instance':stack.config['instance'], 'backup':backup.name,
               'files':files, 'restart_previous':restart}
    write_json(root/'deployment.json', receipt)
    try:
        if previous and previous['stack_instance'] == stack.config['instance'] and previous['runtime_image'] == stack.config['runtime_image']:
            workspace.compose(stack, project, 'down', '--remove-orphans', '--timeout', '15')
        write_files(root, generated(stack, value, raw, keyraw))
        workspace.compose(stack, project, 'up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '90')
        # Compose success alone is not the final health readback.
        receipt['status'] = 'applied'; write_json(root/'deployment.json', receipt)
        result = workspace.status(stack, project)
        if result['status'] != 'running': raise RuntimeFault('WORKSPACE_CANDIDATE_UNHEALTHY')
        return result
    except BaseException:
        receipt['status'] = 'needs_attention'; write_json(root/'deployment.json', receipt)
        try: recover(stack, project)
        except Exception: raise RuntimeFault('WORKSPACE_DEPLOYMENT_NEEDS_ATTENTION') from None
        raise RuntimeFault('WORKSPACE_DEPLOYMENT_ROLLED_BACK') from None


def recover(stack, project):
    root = workspace.directory(stack, project); path = root/'deployment.json'
    private_file(path); receipt = json.loads(path.read_text())
    value = receipt['candidate']
    if (not pending(root) or receipt['project'] != project or value['project'] != project
            or receipt['stack_instance'] != stack.config['instance']
            or value['runtime_image'] != stack.config['runtime_image']):
        raise RuntimeFault('WORKSPACE_RECOVERY_CONTEXT_CHANGED')
    import re
    if not re.fullmatch(r'deployment-[a-f0-9]{32}', receipt['backup']) or set(receipt['files']) != set(FILES):
        raise RuntimeFault('WORKSPACE_RECOVERY_FILES_INVALID')
    backup = root/receipt['backup']; files = {}
    for index, name in enumerate(FILES):
        expected = receipt['files'][name]
        if expected is None: files[name] = None; continue
        source = backup/str(index); private_file(source)
        data = source.read_bytes()
        if source.resolve() != source or hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeFault('WORKSPACE_RECOVERY_FILES_CHANGED')
        files[name] = data
    try:
        # Rebuild only the fixed candidate Compose document, even after a partial
        # file write. Stop candidate executors before restoring the old entry.
        write_json(root/'workspace.json', value)
        write_json(root/'compose.json', workspace.document(stack, value))
        write_files(root, {'nginx.conf':workspace.nginx(value).encode()})
        workspace.compose(stack, project, 'down', '--remove-orphans', '--timeout', '15')
        write_files(root, files)
        if receipt['restart_previous']:
            workspace.check_companions(stack, json.loads(files['workspace.json']))
            workspace.compose(stack, project, 'up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '90')
        receipt['status'] = 'rolled_back'; write_json(path, receipt)
        try: state = workspace.status(stack, project)
        except RuntimeFault as error:
            if str(error) != 'WORKSPACE_REAPPLY_AFTER_STACK_CHANGE' or receipt['restart_previous']: raise
            state = {'status':'requires_reapply'}
        if receipt['restart_previous'] and state['status'] != 'running':
            raise RuntimeFault('WORKSPACE_ROLLBACK_UNHEALTHY')
        return {'status':'rolled_back', 'project':project, 'previous_status':state['status']}
    except BaseException:
        receipt['status'] = 'needs_attention'; write_json(path, receipt)
        raise
