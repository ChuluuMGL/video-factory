"""Bounded-memory authenticated archives; v1 Fernet backups remain readable.

V2: magic + 96-bit random nonce + AES-256-GCM ciphertext + 128-bit tag.
The header is authenticated. Decrypted bytes stay in an unlinked private file
until authentication succeeds; no archive parsing or extraction precedes it.
"""
import base64
from contextlib import contextmanager
import hashlib
import os
import shutil
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .runtime_store import RuntimeFault

MAGIC=b'VFBACKUP2\n'
CHUNK=1024*1024
RESERVE=64*1024*1024
LEGACY_LIMIT=256*1024*1024


def encryption_key(key):
    Fernet(key)
    return HKDF(algorithm=hashes.SHA256(),length=32,salt=None,info=b'VideoFactory backup v2').derive(base64.urlsafe_b64decode(key))


def require_space(folder, needed):
    if shutil.disk_usage(folder).free < needed+RESERVE:
        raise RuntimeFault('BACKUP_INSUFFICIENT_DISK_SPACE')


class Writer:
    def __init__(self, stream, key, limit):
        Fernet(key)
        self.stream,self.limit,self.count=stream,limit,0
        self.hash=hashlib.sha256()
        nonce=os.urandom(12);header=MAGIC+nonce
        self.cipher=Cipher(algorithms.AES(encryption_key(key)),modes.GCM(nonce)).encryptor()
        self.cipher.authenticate_additional_data(header)
        self.emit(header)

    def emit(self, data):
        self.stream.write(data);self.hash.update(data)

    def write(self, data):
        self.count+=len(data)
        if self.count>self.limit: raise RuntimeFault('BACKUP_TOO_LARGE')
        for start in range(0,len(data),CHUNK):self.emit(self.cipher.update(data[start:start+CHUNK]))
        return len(data)

    def finish(self):
        self.emit(self.cipher.finalize());self.emit(self.cipher.tag)
        self.stream.flush();os.fsync(self.stream.fileno())


@contextmanager
def encrypted_output(destination, key, limit):
    # Publish atomically without overwriting an existing checkpoint. Failed or
    # interrupted writes leave no apparently complete backup at destination.
    with tempfile.NamedTemporaryFile(prefix='.vf-backup-',dir=destination.parent) as stream:
        writer=Writer(stream,key,limit)
        yield writer
        writer.finish()
        os.link(stream.name,destination)
        fd=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


@contextmanager
def decrypted_archive(source, key, folder, limit):
    Fernet(key)
    size=source.stat().st_size
    with source.open('rb') as stream, tempfile.TemporaryFile(dir=folder) as plain:
        prefix=stream.read(len(MAGIC))
        try:
            if prefix==MAGIC:
                header=prefix+stream.read(12)
                length=size-len(MAGIC)-12-16
                if length<0: raise InvalidTag
                if length>limit: raise RuntimeFault('BACKUP_TOO_LARGE')
                require_space(folder,length)
                stream.seek(-16,os.SEEK_END);tag=stream.read(16)
                cipher=Cipher(algorithms.AES(encryption_key(key)),modes.GCM(header[len(MAGIC):],tag)).decryptor()
                cipher.authenticate_additional_data(header)
                stream.seek(len(header))
                while length:
                    part=stream.read(min(CHUNK,length))
                    if not part: raise InvalidTag
                    plain.write(cipher.update(part));length-=len(part)
                plain.write(cipher.finalize())
            else:
                if size>LEGACY_LIMIT*2: raise RuntimeFault('BACKUP_TOO_LARGE')
                require_space(folder,size)
                stream.seek(0);raw=Fernet(key).decrypt(stream.read())
                if len(raw)>LEGACY_LIMIT: raise RuntimeFault('BACKUP_TOO_LARGE')
                plain.write(raw)
        except (InvalidTag,InvalidToken,ValueError,TypeError):
            raise RuntimeFault('BACKUP_AUTHENTICATION_FAILED') from None
        plain.seek(0)
        yield plain
