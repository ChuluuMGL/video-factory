"""Atomic certificate generations for a product-owned workspace only.

No global nginx edits, imported production keys, hooks, or Docker socket mounts.
The certificate/key pair switches through one symlink; running nginx retains the
old pair until validation and reload. Retained generations support rollback.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import stat
import time
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from .runtime_store import RuntimeFault, fingerprint, private_file
from .stack import Stack, write_json
from . import workspace


def register(commands):
    parser = commands.add_parser('workspace-tls', help='inspect or atomically rotate a project workspace certificate')
    parser.add_argument('action', choices=['plan', 'apply', 'status', 'recover'])
    parser.add_argument('--stack-root', type=Path, required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--certificate', type=Path)
    parser.add_argument('--private-key', type=Path)
    parser.add_argument('--expect-plan')


def current(stack, project):
    root = workspace.directory(stack, project)
    if root.resolve() != root or (root/'tls').resolve() != root/'tls':
        raise RuntimeFault('TLS_WORKSPACE_DIRECTORY_UNSAFE')
    private_file(root/'workspace.json')
    value = json.loads((root/'workspace.json').read_text())
    if value['stack_instance'] != stack.config['instance'] or value['runtime_image'] != stack.config['runtime_image']:
        raise RuntimeFault('TLS_RECONFIRM_AFTER_STACK_CHANGE')
    if json.loads((root/'compose.json').read_text()) != workspace.document(stack, value):
        raise RuntimeFault('WORKSPACE_GENERATED_FILES_CHANGED')
    if (root/'nginx.conf').read_text() != workspace.nginx(value):
        raise RuntimeFault('WORKSPACE_GENERATED_FILES_CHANGED')
    return root, value


def managed_bytes(path, *, key=False):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 10001
            or info.st_mode & 0o077 or not 0 < info.st_size <= 128*1024):
        raise RuntimeFault('TLS_MANAGED_FILE_UNSAFE')
    return path.read_bytes()


def generation_target(root):
    link = root/'tls/current'
    if not link.is_symlink(): raise RuntimeFault('TLS_GENERATION_LINK_REQUIRED')
    target = os.readlink(link)
    if not re.fullmatch(r'generations/[a-f0-9]{32}', target): raise RuntimeFault('TLS_GENERATION_LINK_UNSAFE')
    directory = root/'tls'/target
    if directory.resolve() != directory or not directory.is_dir(): raise RuntimeFault('TLS_GENERATION_DIRECTORY_UNSAFE')
    return target


def pair(root, value):
    directory = root/'tls'
    if value.get('tls_layout') == 'generations': directory = directory/generation_target(root)
    return managed_bytes(directory/'certificate.pem'), managed_bytes(directory/'key.pem', key=True)


def describe(raw):
    cert = x509.load_pem_x509_certificate(raw)
    return {'sha256': hashlib.sha256(raw).hexdigest(),
            'leaf_sha256': hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest(),
            'expires_at': cert.not_valid_after_utc.isoformat(),
            'days_remaining': (cert.not_valid_after_utc-datetime.now(timezone.utc)).total_seconds()/86400}


def status(stack, project):
    root = workspace.directory(stack, project); receipt = root/'tls-rotation.json'
    if receipt.exists():
        private_file(receipt)
        pending = json.loads(receipt.read_text())
        if pending['status'] in ('in_flight', 'needs_attention'):
            return {'project': project, 'status': pending['status'], 'recovery_required': True, 'external_https_verified': False}
    root, value = current(stack, project); raw, _ = pair(root, value)
    return {'project': project, 'url': value['origin'], 'certificate': describe(raw),
            'renewal_due': describe(raw)['days_remaining'] < 30,
            'last_rotation': json.loads(receipt.read_text()) if receipt.exists() else None,
            'automatic_acme': False, 'external_https_verified': False}


def plan(stack, project, cert, key):
    root, value = current(stack, project)
    receipt = root/'tls-rotation.json'
    if receipt.exists():
        private_file(receipt)
        if json.loads(receipt.read_text())['status'] in ('in_flight', 'needs_attention'):
            raise RuntimeFault('TLS_RECOVERY_REQUIRED')
    previous, previous_key = pair(root, value)
    raw, keyraw = workspace.certificate(cert, key, urlsplit(value['origin']).hostname)
    return {'project': project, 'origin': value['origin'], 'workspace_sha256': fingerprint(value),
            'previous_certificate_sha256': hashlib.sha256(previous).hexdigest(),
            'previous_key_sha256': hashlib.sha256(previous_key).hexdigest(),
            'certificate_sha256': hashlib.sha256(raw).hexdigest(),
            'private_key_sha256': hashlib.sha256(keyraw).hexdigest(),
            'stack_instance': stack.config['instance'], 'reload_only': True}


def write_generation(root, raw, keyraw):
    directory = root/'tls/generations'
    if directory.exists():
        if directory.resolve() != directory or not directory.is_dir(): raise RuntimeFault('TLS_GENERATION_DIRECTORY_UNSAFE')
    else:
        directory.mkdir(mode=0o700); os.chown(directory, 10001, 10001)
    name = secrets.token_hex(16); folder = directory/name
    folder.mkdir(mode=0o700); os.chown(folder, 10001, 10001)
    for name, content in [('certificate.pem', raw), ('key.pem', keyraw)]:
        fd = os.open(folder/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno()); os.fchown(stream.fileno(), 10001, 10001)
    folder.chmod(0o500)
    return 'generations/'+folder.name


def switch(root, target):
    if not re.fullmatch(r'generations/[a-f0-9]{32}', target): raise RuntimeFault('TLS_GENERATION_LINK_UNSAFE')
    link = root/'tls/current'
    if link.exists() and not link.is_symlink(): raise RuntimeFault('TLS_GENERATION_LINK_UNSAFE')
    temporary = root/'tls'/('.current-'+secrets.token_hex(12))
    try:
        os.symlink(target, temporary); os.replace(temporary, link)
        fd = os.open(root/'tls', os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally: temporary.unlink(missing_ok=True)


def served_leaf(value, expected):
    # Exact local port and SNI; this proves the new leaf is served, not public
    # DNS reachability or CA trust. Those remain independent acceptance checks.
    parsed = urlsplit(value['origin'])
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False; context.verify_mode = ssl.CERT_NONE
    for _ in range(15):
        try:
            with socket.create_connection(('127.0.0.1', parsed.port or 443), timeout=2) as connection:
                with context.wrap_socket(connection, server_hostname=parsed.hostname) as stream:
                    if hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest() == expected: return True
        except (OSError, ssl.SSLError): pass
        time.sleep(.2)
    return False


def apply(stack, value, cert, key, *, readback=served_leaf):
    project = value['project']
    if plan(stack, project, cert, key) != value: raise RuntimeFault('TLS_REVIEWED_PLAN_CHANGED')
    root, previous = current(stack, project); oldraw, oldkey = pair(root, previous)
    raw, keyraw = workspace.certificate(cert, key, urlsplit(previous['origin']).hostname)
    oldtarget = generation_target(root) if previous.get('tls_layout') == 'generations' else write_generation(root, oldraw, oldkey)
    target = write_generation(root, raw, keyraw)
    updated = {**previous, 'tls_layout': 'generations', 'certificate_sha256': hashlib.sha256(raw).hexdigest(),
               'private_key_sha256': hashlib.sha256(keyraw).hexdigest()}
    receipt = {'status': 'in_flight', 'project': project, 'previous': oldtarget, 'candidate': target,
               'plan_sha256': fingerprint(value), 'previous_workspace': previous, 'started_at': datetime.now(timezone.utc).isoformat()}
    write_json(root/'tls-rotation.json', receipt)
    try:
        switch(root, target)
        # Rewrite only this workspace's generated nginx file. No broad reload.
        (root/'nginx.conf').write_text(workspace.nginx(updated)); (root/'nginx.conf').chmod(0o644)
        write_json(root/'workspace.json', updated)
        workspace.compose(stack, project, 'exec', '-T', 'edge', 'nginx', '-t')
        workspace.compose(stack, project, 'exec', '-T', 'edge', 'nginx', '-s', 'reload')
        if not readback(updated, describe(raw)['leaf_sha256']): raise RuntimeFault('TLS_NEW_LEAF_NOT_SERVED')
        receipt.update(status='rotated', served_leaf_sha256=describe(raw)['leaf_sha256'], external_https_verified=False)
        write_json(root/'tls-rotation.json', receipt)
        return receipt
    except BaseException:
        try:
            switch(root, oldtarget)
            (root/'nginx.conf').write_text(workspace.nginx(previous)); (root/'nginx.conf').chmod(0o644)
            write_json(root/'workspace.json', previous)
            workspace.compose(stack, project, 'exec', '-T', 'edge', 'nginx', '-t')
            workspace.compose(stack, project, 'exec', '-T', 'edge', 'nginx', '-s', 'reload')
            restored = readback(previous, describe(oldraw)['leaf_sha256'])
            receipt['status'] = 'rolled_back' if restored else 'needs_attention'
        except Exception: receipt['status'] = 'needs_attention'
        write_json(root/'tls-rotation.json', receipt)
        raise RuntimeFault('TLS_ROTATION_'+receipt['status'].upper()) from None


def recover(stack, project, *, readback=served_leaf):
    root = workspace.directory(stack, project); path = root/'tls-rotation.json'
    if root.resolve() != root or (root/'tls').resolve() != root/'tls':
        raise RuntimeFault('TLS_WORKSPACE_DIRECTORY_UNSAFE')
    private_file(path); receipt = json.loads(path.read_text()); previous = receipt['previous_workspace']
    if (receipt['status'] not in ('in_flight', 'needs_attention') or receipt['project'] != project
            or previous['project'] != project or previous['stack_instance'] != stack.config['instance']
            or previous['runtime_image'] != stack.config['runtime_image']):
        raise RuntimeFault('TLS_RECOVERY_CONTEXT_CHANGED')
    if json.loads((root/'compose.json').read_text()) != workspace.document(stack, previous):
        raise RuntimeFault('TLS_RECOVERY_COMPOSE_CHANGED')
    target = receipt['previous']
    if not re.fullmatch(r'generations/[a-f0-9]{32}', target): raise RuntimeFault('TLS_GENERATION_LINK_UNSAFE')
    folder = root/'tls'/target
    if folder.resolve() != folder: raise RuntimeFault('TLS_GENERATION_DIRECTORY_UNSAFE')
    oldraw = managed_bytes(folder/'certificate.pem'); oldkey = managed_bytes(folder/'key.pem')
    if (hashlib.sha256(oldraw).hexdigest() != previous['certificate_sha256']
            or hashlib.sha256(oldkey).hexdigest() != previous['private_key_sha256']):
        raise RuntimeFault('TLS_RECOVERY_PREVIOUS_HASH_CHANGED')
    switch(root, target)
    (root/'nginx.conf').write_text(workspace.nginx(previous)); (root/'nginx.conf').chmod(0o644)
    write_json(root/'workspace.json', previous)
    workspace.compose(stack, project, 'exec', '-T', 'edge', 'nginx', '-t')
    workspace.compose(stack, project, 'exec', '-T', 'edge', 'nginx', '-s', 'reload')
    if not readback(previous, describe(oldraw)['leaf_sha256']): raise RuntimeFault('TLS_RECOVERY_OLD_LEAF_NOT_SERVED')
    receipt['status'] = 'rolled_back'; write_json(path, receipt)
    return {'project': project, 'status': 'rolled_back', 'external_https_verified': False}


def cli(args):
    try:
        stack = Stack(args.stack_root)
        with stack.lock():
            if args.action == 'status': result = status(stack, args.project)
            elif args.action == 'recover': result = recover(stack, args.project)
            else:
                value = plan(stack, args.project, args.certificate, args.private_key)
                result = {'plan': value, 'plan_sha256': fingerprint(value)}
                if args.action == 'apply':
                    if args.expect_plan != result['plan_sha256']: raise RuntimeFault('TLS_REVIEWED_PLAN_REQUIRED')
                    result = apply(stack, value, args.certificate, args.private_key)
        print(json.dumps(result)); return 0
    except Exception as error:
        print(json.dumps({'error': str(error) if isinstance(error, RuntimeFault) else 'TLS_OPERATION_FAILED'})); return 2
