"""Owner-controlled media relocation; immutable artifact/review descriptors survive.

Only an exact (original location, sha256) entry can resolve into this media root.
The caller must still open without following symlinks and verify the file hash.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

from .runtime_store import RuntimeFault, private_directory

INDEX = '.vf-media-relocation.json'
SCHEMA = 'vf-media-relocation-v1'


def entry_key(location, digest):
    return hashlib.sha256(json.dumps([location, digest], ensure_ascii=True,
                                     separators=(',', ':')).encode()).hexdigest()


def read_index(root):
    path = root / INDEX
    if not path.exists() and not path.is_symlink():
        return {'schema': SCHEMA, 'entries': {}}
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 8*1024*1024):
            raise RuntimeFault('REVIEW_MEDIA_RELOCATION_INVALID')
        try:
            index = json.load(stream)
        except (ValueError, UnicodeError):
            raise RuntimeFault('REVIEW_MEDIA_RELOCATION_INVALID') from None
    if (not isinstance(index, dict) or set(index) != {'schema', 'entries'}
            or index['schema'] != SCHEMA or not isinstance(index['entries'], dict)):
        raise RuntimeFault('REVIEW_MEDIA_RELOCATION_INVALID')
    return index


def resolve_media_path(media_root, artifact, expected_hash):
    root = private_directory(media_root)
    location = artifact.get('location')
    if (not isinstance(location, str) or not Path(location).is_absolute()
            or '..' in Path(location).parts or artifact.get('sha256') != expected_hash):
        raise RuntimeFault('REVIEW_MEDIA_PATH_INVALID')
    path = Path(location)
    if not path.is_relative_to(root):
        item = read_index(root)['entries'].get(entry_key(location, expected_hash))
        if (not isinstance(item, dict) or set(item) != {'location', 'sha256', 'path'}
                or item['location'] != location or item['sha256'] != expected_hash
                or not isinstance(item['path'], str)):
            raise RuntimeFault('REVIEW_MEDIA_PATH_INVALID')
        relative = Path(item['path'])
        if (relative.is_absolute() or not relative.parts or '..' in relative.parts
                or relative.as_posix() != item['path'] or relative.name == INDEX):
            raise RuntimeFault('REVIEW_MEDIA_PATH_INVALID')
        path = root / relative
    if path.resolve() != path or not path.is_relative_to(root):
        raise RuntimeFault('REVIEW_MEDIA_PATH_INVALID')
    # Reject even an owned file reached through a permissive nested directory.
    for parent in path.parents:
        if parent == root:
            break
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeFault('REVIEW_MEDIA_PATH_INVALID')
    return path
