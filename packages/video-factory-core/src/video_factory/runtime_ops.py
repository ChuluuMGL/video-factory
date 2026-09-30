"""Encrypted, bounded ledger backups; restore only to an empty destination."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from cryptography.fernet import Fernet, InvalidToken

from .runtime_store import RuntimeFault, RuntimeStore, canonical, exclusive_write, private_directory, private_file

MAX_DATABASE = 64 * 1024 * 1024


def backup(store, destination, backup_key):
    destination = Path(destination)
    private_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        raise RuntimeFault("BACKUP_DESTINATION_EXISTS")
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=store.root) as temp:
        snapshot = Path(temp) / "ledger.sqlite3"
        exclusive_write(snapshot, b"")
        source = sqlite3.connect(store.path)
        target = sqlite3.connect(snapshot)
        try:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeFault("BACKUP_INTEGRITY_FAILED")
        finally:
            target.close(); source.close()
        if snapshot.stat().st_size > MAX_DATABASE:
            raise RuntimeFault("BACKUP_TOO_LARGE")
        data = snapshot.read_bytes()
    envelope = {"format": 1, "kind": "runtime_ledger_only", "sha256": hashlib.sha256(data).hexdigest(),
                "database": base64.b64encode(data).decode()}
    try:
        encrypted = Fernet(backup_key).encrypt(canonical(envelope).encode())
    except (TypeError, ValueError):
        raise RuntimeFault("BACKUP_KEY_INVALID") from None
    exclusive_write(destination, encrypted)
    return {"backup": str(destination), "sha256": hashlib.sha256(encrypted).hexdigest(),
            "scope": "runtime_ledger_including_encrypted_secrets", "master_key_included": False,
            "external_media_and_n8n_included": False}


def restore(source, root, backup_key):
    source, root = Path(source), private_directory(root)
    private_file(source)
    if source.stat().st_size > MAX_DATABASE*2:
        raise RuntimeFault("BACKUP_TOO_LARGE")
    if any(root.iterdir()):
        raise RuntimeFault("RESTORE_REQUIRES_EMPTY_DIRECTORY")
    try:
        envelope = json.loads(Fernet(backup_key).decrypt(source.read_bytes()))
        if envelope["format"] != 1 or envelope["kind"] != "runtime_ledger_only":
            raise ValueError()
        data = base64.b64decode(envelope["database"], validate=True)
        if len(data)>MAX_DATABASE or hashlib.sha256(data).hexdigest()!=envelope["sha256"]:
            raise ValueError()
    except (InvalidToken, ValueError, TypeError, KeyError):
        raise RuntimeFault("BACKUP_AUTHENTICATION_OR_FORMAT_FAILED") from None
    # Validate in a staging directory. Invalid input never creates the target DB.
    with tempfile.TemporaryDirectory(prefix=".restore-",dir=root.parent) as temp:
        staging=Path(temp)
        exclusive_write(staging / "runtime.sqlite3", data)
        candidate=RuntimeStore(staging)
        if candidate.doctor()["database_integrity"]!="ok":
            raise RuntimeFault("RESTORE_INTEGRITY_FAILED")
        with candidate.connect() as db:
            db.execute("DELETE FROM sessions")
            candidate.invalidate_worker_approvals(db)
            candidate.audit(db,"os_owner","restore_revoke_sessions")
        # link is exclusive and atomic; no replacement of concurrently installed DB.
        os.link(candidate.path,root / "runtime.sqlite3")
        candidate.path.unlink()
    return {"restored": True, "scope":"runtime_ledger_only", "sessions_revoked":True,
            "master_key_required_separately":True, "doctor":RuntimeStore(root).doctor()}
