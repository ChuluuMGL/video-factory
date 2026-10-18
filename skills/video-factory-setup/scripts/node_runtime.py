"""Pinned official Node runtime, private to an installation; no global changes."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import tarfile
from urllib.request import Request, build_opener
import zipfile

from portable_n8n import Fault, NoRedirect

VERSION='24.22.0'
# Official https://nodejs.org/dist/v24.22.0/SHASUMS256.txt, checked 2026-10-16.
HASHES={
 'darwin-arm64':'6a9724d9b50a5f22b4816cded441b5a8d9ddae546191a5e5b7efa5a5e2ee26d5',
 'darwin-x64':'495e3f273c645f3c4677d438304cdfc701729ea5dec5728f5c8e76a2dece4b91',
 'linux-arm64':'b7e7991d5407815adfa74a2a9cd4db683552c8ce32ff4a295c22e972849c3f5d',
 'linux-x64':'28ea0a438de9a13bd7891fd758eee6c7819ee8af5442dfd8d45bd65e56d4228c',
 'win-arm64':'3a5c209a5bc59e65b3bc0fa13ed5e166a26bd3d55332847f498b76b037408d61',
 'win-x64':'f9683a19877e12a9b167305c8db71040d812876359de21f0d2e97c3f321f80c3',
}


def target():
    system={'Darwin':'darwin','Windows':'win','Linux':'linux'}.get(platform.system())
    arch={'arm64':'arm64','aarch64':'arm64','amd64':'x64','x86_64':'x64'}.get(platform.machine().lower())
    key=f'{system}-{arch}'
    if key not in HASHES: raise Fault('NODE_PLATFORM_NOT_IN_PINNED_DISTRIBUTIONS')
    return key


def destination(root, name, prefix):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!=prefix or '\\' in name or ':' in name:
        raise Fault('NODE_ARCHIVE_PATH_INVALID')
    value=root.joinpath(*path.parts)
    if value.is_symlink() or not value.resolve().is_relative_to(root.resolve()):
        raise Fault('NODE_ARCHIVE_SYMLINK_REJECTED')
    return value


def prepare(root):
    key=target(); prefix='node-v'+VERSION+'-'+key
    suffix='.zip' if key.startswith('win-') else '.tar.gz'
    root=Path(root).resolve(); root.mkdir(mode=0o700,parents=True,exist_ok=True)
    archive=root/(prefix+suffix)
    if archive.is_symlink(): raise Fault('NODE_ARCHIVE_SYMLINK_REJECTED')
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest()!=HASHES[key]:
        part=archive.with_suffix(archive.suffix+'.partial')
        if part.is_symlink(): raise Fault('NODE_DOWNLOAD_SYMLINK_REJECTED')
        with build_opener(NoRedirect).open(Request('https://nodejs.org/dist/v'+VERSION+'/'+archive.name),timeout=60) as response:
            total=0; digest=hashlib.sha256()
            with part.open('wb') as output:
                while chunk:=response.read(1024*1024):
                    total+=len(chunk)
                    if total>100*1024*1024: raise Fault('NODE_ARCHIVE_TOO_LARGE')
                    digest.update(chunk); output.write(chunk)
        if digest.hexdigest()!=HASHES[key]:
            raise Fault('NODE_DOWNLOAD_CHECKSUM_MISMATCH')
        os.replace(part,archive)
    total=0
    if suffix=='.zip':
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                path=destination(root,member.filename,prefix)
                total+=member.file_size
                if total>400*1024*1024: raise Fault('NODE_EXTRACT_TOO_LARGE')
                if member.is_dir(): path.mkdir(parents=True,exist_ok=True); continue
                path.parent.mkdir(parents=True,exist_ok=True)
                with bundle.open(member) as source, path.open('wb') as output: shutil.copyfileobj(source,output)
    else:
        with tarfile.open(archive,'r:gz') as bundle:
            for member in bundle:
                path=destination(root,member.name,prefix)
                if member.isdir(): path.mkdir(parents=True,exist_ok=True); continue
                # npm's JS entry is called directly, so archive symlinks are unnecessary.
                if not member.isfile(): continue
                total+=member.size
                if total>400*1024*1024: raise Fault('NODE_EXTRACT_TOO_LARGE')
                path.parent.mkdir(parents=True,exist_ok=True)
                with bundle.extractfile(member) as source, path.open('wb') as output: shutil.copyfileobj(source,output)
                path.chmod(member.mode & 0o755)
    directory=root/prefix
    return str(directory/'node.exe' if key.startswith('win-') else directory/'bin/node')
