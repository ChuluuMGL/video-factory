"""Single-host alpha ledger. OS owner is trusted; no public network listener.

SQLite transactions fence claims before any provider I/O. An interrupted submit
is deliberately left uncertain, never returned to the automatic submit queue.
"""
from contextlib import contextmanager
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

from cryptography.fernet import Fernet, InvalidToken


class RuntimeFault(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value):
        raise RuntimeFault("IDENTIFIER_INVALID")
    return value


def private_directory(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or not path.is_dir():
        raise RuntimeFault("PRIVATE_ABSOLUTE_DIRECTORY_REQUIRED")
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeFault("DIRECTORY_OWNER_ONLY_REQUIRED")
    return path


def private_file(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise RuntimeFault("FILE_OWNER_ONLY_REGULAR_REQUIRED")


def exclusive_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE users (name TEXT PRIMARY KEY, salt BLOB NOT NULL, password BLOB NOT NULL,
 role TEXT NOT NULL, project TEXT, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE sessions (digest TEXT PRIMARY KEY, name TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE login_limits (name TEXT PRIMARY KEY, failures INTEGER NOT NULL, until REAL NOT NULL);
CREATE TABLE vault (alias TEXT PRIMARY KEY, ciphertext BLOB NOT NULL, revision INTEGER NOT NULL);
CREATE TABLE projects (id TEXT PRIMARY KEY, configuration TEXT NOT NULL, digest TEXT NOT NULL);
CREATE TABLE tasks (project TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
 input TEXT NOT NULL, input_digest TEXT NOT NULL, configuration_digest TEXT NOT NULL,
 state TEXT NOT NULL, provider_id TEXT, artifact TEXT, PRIMARY KEY(project,id,revision));
CREATE TABLE events (id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, receipt TEXT NOT NULL);
CREATE TABLE audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor TEXT NOT NULL,
 action TEXT NOT NULL, project TEXT, task TEXT, revision INTEGER);
"""


class RuntimeStore:
    def __init__(self, root):
        self.root = private_directory(root)
        self.path = self.root / "runtime.sqlite3"
        for name in ("runtime.sqlite3", "runtime.sqlite3-journal", "runtime.sqlite3-wal", "runtime.sqlite3-shm"):
            path = self.root / name
            if path.exists() or path.is_symlink():
                private_file(path)
        if not self.path.exists():
            raise RuntimeFault("RUNTIME_NOT_INSTALLED")
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if row is None or row[0] != "1":
                raise RuntimeFault("SCHEMA_UNSUPPORTED")

    @classmethod
    def install(cls, root, deployment, password):
        root = private_directory(root)
        identifier(deployment)
        cls.validate_password(password)
        path = root / "runtime.sqlite3"
        exclusive_write(path, b"")
        # A partial install stays visible and is not overwritten by a retry.
        db = sqlite3.connect(path)
        try:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO meta VALUES('schema','1')")
            db.execute("INSERT INTO meta VALUES('deployment',?)", (deployment,))
            salt = secrets.token_bytes(16)
            db.execute("INSERT INTO users(name,salt,password,role) VALUES(?,?,?,'admin')",
                       ("admin", salt, cls.password_hash(password, salt)))
            db.commit()
        finally:
            db.close()
        return cls(root)

    @staticmethod
    def validate_password(password):
        if not isinstance(password, str) or not 14 <= len(password) <= 256:
            raise RuntimeFault("PASSWORD_LENGTH_14_TO_256_REQUIRED")

    @staticmethod
    def password_hash(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, dklen=32, maxmem=64*1024*1024)

    @contextmanager
    def connect(self):
        private_file(self.path)
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def audit(db, actor, action, project=None, task=None, revision=None):
        db.execute("INSERT INTO audit(at,actor,action,project,task,revision) VALUES(?,?,?,?,?,?)",
                   (time.time(), actor, action, project, task, revision))

    @staticmethod
    def invalidate_worker_approvals(db):
        # Restoring old ready rows cannot carry an old paid permission forward.
        # Keep receipts intact so submitted/unknown jobs can still be reconciled.
        for row in db.execute("SELECT key,value FROM meta WHERE key LIKE 'worker:%'").fetchall():
            value=json.loads(row['value'])
            value['expires_at']=0
            value['approval_revoked']='recovery'
            db.execute('UPDATE meta SET value=? WHERE key=?',(canonical(value),row['key']))

        for row in db.execute("SELECT key,value FROM meta WHERE key LIKE 'automation:key:%'").fetchall():
            value=json.loads(row['value']);value['expires_at']=0
            db.execute('UPDATE meta SET value=? WHERE key=?',(canonical(value),row['key']))

        for row in db.execute("SELECT key,value FROM meta WHERE key LIKE 'script:job:%'").fetchall():
            value=json.loads(row['value']);value['expires_at']=0
            db.execute('UPDATE meta SET value=? WHERE key=?',(canonical(value),row['key']))

        # A restored checkpoint may predate a successful external Base write.
        # Fence every create project, even if the snapshot has no journal yet.
        for row in db.execute("SELECT key,value FROM meta WHERE key LIKE 'setup:project:%'").fetchall():
            value=json.loads(row['value'])
            if value['configuration']['project']['base_mode']=='create':
                key='setup:provision-recovery:'+value['configuration']['project']['id']
                db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,'{"required":true}'))

        for row in db.execute("SELECT key FROM meta WHERE key LIKE 'feishu:binding:%'").fetchall():
            key='feishu:reconfirm:'+row['key'][len('feishu:binding:'):]
            db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,'{"required":true}'))

    def login(self, name, password):
        identifier(name)
        if not isinstance(password, str) or len(password) > 256:
            raise RuntimeFault("AUTH_FAILED")
        failed = False
        with self.connect() as db:
            now = time.time()
            limit = db.execute("SELECT * FROM login_limits WHERE name=?", (name,)).fetchone()
            if limit and limit["until"] > now:
                raise RuntimeFault("AUTH_RATE_LIMITED")
            user = db.execute("SELECT * FROM users WHERE name=? AND active=1", (name,)).fetchone()
            calculated = self.password_hash(password, user["salt"] if user else b"0"*16)
            if not user or not hmac.compare_digest(calculated, user["password"]):
                failures = (limit["failures"] if limit else 0) + 1
                db.execute("INSERT INTO login_limits VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET failures=excluded.failures,until=excluded.until",
                           (name, failures, now + 300 if failures >= 5 else 0))
                failed = True
                result = None
            else:
                db.execute("DELETE FROM login_limits WHERE name=?", (name,))
                db.execute("DELETE FROM sessions WHERE expires<?", (now,))
                token = secrets.token_urlsafe(32)
                db.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), name, now+3600))
                self.audit(db, name, "login")
                result = {"token": token, "expires_at": now+3600}
        if failed:
            raise RuntimeFault("AUTH_FAILED")
        return result

    def authorize(self, db, token, role=None, project=None):
        if not isinstance(token, str) or len(token) > 256:
            raise RuntimeFault("AUTH_REQUIRED")
        user = db.execute("SELECT u.* FROM sessions s JOIN users u ON u.name=s.name WHERE s.digest=? AND s.expires>? AND u.active=1",
                          (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if not user:
            raise RuntimeFault("AUTH_REQUIRED")
        if user["role"] != "admin" and (role == "admin" or user["project"] != project):
            raise RuntimeFault("ROLE_OR_PROJECT_DENIED")
        return user["name"]

    def set_user(self, token, name, password, project):
        identifier(name); identifier(project); self.validate_password(password)
        if name == "admin":
            raise RuntimeFault("ADMIN_RESET_REQUIRES_LOCAL_RECOVERY")
        with self.connect() as db:
            actor = self.authorize(db, token, "admin")
            if not db.execute("SELECT 1 FROM projects WHERE id=?", (project,)).fetchone():
                raise RuntimeFault("PROJECT_MISSING")
            salt = secrets.token_bytes(16)
            db.execute("INSERT INTO users VALUES(?,?,?,'reviewer',?,1) ON CONFLICT(name) DO UPDATE SET salt=excluded.salt,password=excluded.password,role=excluded.role,project=excluded.project,active=excluded.active",
                       (name, salt, self.password_hash(password, salt), project))
            db.execute("DELETE FROM sessions WHERE name=?", (name,))
            self.audit(db, actor, "set_reviewer", project)
        return {"user": name, "role": "reviewer", "project": project, "identity_source": "customer_local_account"}

    def revoke_user(self, token, name):
        if name == "admin":
            raise RuntimeFault("CANNOT_REVOKE_ONLY_ADMIN")
        with self.connect() as db:
            actor = self.authorize(db, token, "admin")
            db.execute("UPDATE users SET active=0 WHERE name=?", (name,))
            db.execute("DELETE FROM sessions WHERE name=?", (name,))
            self.audit(db, actor, "revoke_user")
        return {"revoked": name}

    def recover_admin(self, password):
        """Local OS-owner recovery only; never exposed through HTTP."""
        self.validate_password(password)
        with self.connect() as db:
            salt = secrets.token_bytes(16)
            db.execute("UPDATE users SET salt=?,password=? WHERE name='admin'", (salt, self.password_hash(password, salt)))
            db.execute("DELETE FROM sessions")
            db.execute("DELETE FROM login_limits")
            self.invalidate_worker_approvals(db)
            self.audit(db, "os_owner", "recover_admin_revoke_all_sessions")
        return {"admin_recovered": True, "all_sessions_revoked": True}

    def put_secret(self, token, alias, value, master_key):
        identifier(alias)
        if not isinstance(value, str) or not 1 <= len(value.encode()) <= 65536:
            raise RuntimeFault("SECRET_SIZE_INVALID")
        try:
            cipher = Fernet(master_key).encrypt(value.encode())
        except (ValueError, TypeError):
            raise RuntimeFault("MASTER_KEY_INVALID") from None
        with self.connect() as db:
            actor = self.authorize(db, token, "admin")
            key_id = hashlib.sha256(base64.urlsafe_b64decode(master_key)).hexdigest()
            current = db.execute("SELECT value FROM meta WHERE key='vault_key_id'").fetchone()
            if current and not hmac.compare_digest(current[0], key_id):
                raise RuntimeFault("MASTER_KEY_DOES_NOT_MATCH_VAULT")
            db.execute("INSERT INTO meta VALUES('vault_key_id',?) ON CONFLICT(key) DO NOTHING", (key_id,))
            db.execute("INSERT INTO vault VALUES(?,?,1) ON CONFLICT(alias) DO UPDATE SET ciphertext=excluded.ciphertext,revision=vault.revision+1", (alias,cipher))
            revision = db.execute("SELECT revision FROM vault WHERE alias=?", (alias,)).fetchone()[0]
            self.audit(db, actor, "secret_write")
        return {"reference": "secret:"+alias, "revision": revision}

    def resolve_secret(self, alias, master_key):
        """Internal worker/OS-owner only; never available over HTTP."""
        with self.connect() as db:
            row = db.execute("SELECT ciphertext FROM vault WHERE alias=?", (alias,)).fetchone()
        if row is None:
            raise RuntimeFault("SECRET_MISSING")
        try:
            return Fernet(master_key).decrypt(row[0]).decode()
        except (InvalidToken, ValueError, TypeError):
            raise RuntimeFault("SECRET_DECRYPT_FAILED") from None

    def rotate_key(self, token, old_key, new_key):
        try:
            old, new = Fernet(old_key), Fernet(new_key)
            with self.connect() as db:
                actor = self.authorize(db, token, "admin")
                current = db.execute("SELECT value FROM meta WHERE key='vault_key_id'").fetchone()
                if current and not hmac.compare_digest(current[0], hashlib.sha256(base64.urlsafe_b64decode(old_key)).hexdigest()):
                    raise RuntimeFault("KEY_ROTATION_FAILED")
                for row in db.execute("SELECT * FROM vault").fetchall():
                    db.execute("UPDATE vault SET ciphertext=?,revision=revision+1 WHERE alias=?", (new.encrypt(old.decrypt(row["ciphertext"])),row["alias"]))
                db.execute("INSERT INTO meta VALUES('vault_key_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (hashlib.sha256(base64.urlsafe_b64decode(new_key)).hexdigest(),))
                self.audit(db, actor, "rotate_master_key")
        except (InvalidToken, ValueError, TypeError) as error:
            if isinstance(error, RuntimeFault):
                raise
            raise RuntimeFault("KEY_ROTATION_FAILED") from None
        return {"rotated": True}

    def put_project(self, token, project, configuration, expected_digest=None):
        identifier(project)
        if not isinstance(configuration, dict) or set(configuration) != {"video_route", "credential_ref", "billing_owner"}:
            raise RuntimeFault("PROJECT_CONFIGURATION_INVALID")
        if configuration["video_route"] != "deferred":
            raise RuntimeFault("GENERAL_PAID_ADAPTER_NOT_VERIFIED")
        if not re.fullmatch(r"secret:[A-Za-z0-9_-]{1,96}", configuration["credential_ref"]):
            raise RuntimeFault("SECRET_REFERENCE_REQUIRED")
        identifier(configuration["billing_owner"])
        digest = fingerprint(configuration)
        with self.connect() as db:
            actor = self.authorize(db, token, "admin")
            old = db.execute("SELECT digest FROM projects WHERE id=?",(project,)).fetchone()
            if (old[0] if old else None) != expected_digest:
                raise RuntimeFault("PROJECT_CONFIGURATION_CONFLICT")
            db.execute("INSERT INTO projects VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET configuration=excluded.configuration,digest=excluded.digest", (project,canonical(configuration),digest))
            self.audit(db, actor, "project_configuration", project)
        return {"project": project, "configuration_digest": digest, "paid_execution_enabled": False}

    def create_task(self, token, project, task, payload, expected_revision=0):
        with self.connect() as db:
            actor = self.authorize(db, token, "admin")
            return self._create_task(db, actor, project, task, payload, expected_revision)

    def _create_task(self, db, actor, project, task, payload, expected_revision=0):
        identifier(project); identifier(task)
        if not isinstance(payload, dict) or set(payload) != {"sku_id", "script", "source_revision"}:
            raise RuntimeFault("TASK_INPUT_INVALID")
        if any(not isinstance(v,str) or not 1 <= len(v) <= 12000 for v in payload.values()):
            raise RuntimeFault("TASK_INPUT_INVALID")
        if type(expected_revision) is not int or expected_revision < 0:
            raise RuntimeFault("REVISION_INVALID")
        configuration = db.execute("SELECT digest FROM projects WHERE id=?", (project,)).fetchone()
        if not configuration:
            raise RuntimeFault("PROJECT_MISSING")
        previous = db.execute("SELECT revision,state FROM tasks WHERE project=? AND id=? ORDER BY revision DESC LIMIT 1", (project,task)).fetchone()
        if (previous[0] if previous else 0) != expected_revision:
            raise RuntimeFault("TASK_REVISION_CONFLICT")
        if previous and previous[1] not in ("rejected", "accepted", "failed"):
            raise RuntimeFault("PREVIOUS_REVISION_NOT_CLOSED")
        revision = expected_revision+1
        db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?, 'awaiting_script_review',NULL,NULL)",
                   (project,task,revision,canonical(payload),fingerprint(payload),configuration[0]))
        self.audit(db, actor, "task_created",project,task,revision)
        return {"project":project,"task":task,"revision":revision,"state":"awaiting_script_review"}

    def _current(self, db, project, task, revision):
        row = db.execute("SELECT * FROM tasks WHERE project=? AND id=? ORDER BY revision DESC LIMIT 1", (project,task)).fetchone()
        if not row or type(revision) is not int or row["revision"] != revision:
            raise RuntimeFault("STALE_TASK_REVISION")
        config = db.execute("SELECT digest FROM projects WHERE id=?", (project,)).fetchone()
        if config[0] != row["configuration_digest"]:
            raise RuntimeFault("PROJECT_CONFIGURATION_CHANGED")
        return row

    def review(self, token, event, project, task, revision, stage, decision, feedback=""):
        with self.connect() as db:
            actor = self.authorize(db, token, project=project)
            return self._review(db, actor, event, project, task, revision, stage, decision, feedback)

    def _review(self, db, actor, event, project, task, revision, stage, decision, feedback=""):
        identifier(event)
        if stage not in ("script", "video") or decision not in ("accept", "reject"):
            raise RuntimeFault("REVIEW_INVALID")
        if not isinstance(feedback,str) or len(feedback)>8000 or (decision=="reject" and not feedback.strip()):
            raise RuntimeFault("REVIEW_FEEDBACK_REQUIRED")
        request_digest = fingerprint([actor, project,task,revision,stage,decision,feedback])
        old = db.execute("SELECT * FROM events WHERE id=?",(event,)).fetchone()
        if old:
            if old["request_digest"] != request_digest:
                raise RuntimeFault("EVENT_ID_CONFLICT")
            return json.loads(old["receipt"])
        row = self._current(db,project,task,revision)
        if row["state"] != "awaiting_"+stage+"_review":
            raise RuntimeFault("REVIEW_STATE_CONFLICT")
        state = "rejected" if decision=="reject" else ("ready" if stage=="script" else "accepted")
        receipt = {"actor":actor,"project":project,"task":task,"revision":revision,"state":state,"feedback":feedback}
        db.execute("UPDATE tasks SET state=? WHERE project=? AND id=? AND revision=?",(state,project,task,revision))
        db.execute("INSERT INTO events VALUES(?,?,?)",(event,request_digest,canonical(receipt)))
        self.audit(db,actor,"review_"+stage+"_"+decision,project,task,revision)
        return receipt

    def claim(self, token, project, task, revision):
        with self.connect() as db:
            actor = self.authorize(db,token,"admin")
            row = self._current(db,project,task,revision)
            if row["state"] != "ready":
                raise RuntimeFault("TASK_NOT_READY_NO_RESUBMIT")
            # Caller may crash after commit and before I/O. Unknown remains
            # unknown until the existing provider job is reconciled explicitly.
            db.execute("UPDATE tasks SET state='submission_unknown' WHERE project=? AND id=? AND revision=?",(project,task,revision))
            self.audit(db,actor,"submission_intent",project,task,revision)
        return {"state":"submission_unknown","input_digest":row["input_digest"],"automatic_resubmit":False}

    def attach_provider(self, token, project, task, revision, provider_id):
        identifier(provider_id)
        with self.connect() as db:
            actor = self.authorize(db,token,"admin")
            row = self._current(db,project,task,revision)
            if row["state"] not in ("submission_unknown","submitted") or row["provider_id"] not in (None,provider_id):
                raise RuntimeFault("PROVIDER_RECEIPT_CONFLICT")
            if db.execute('SELECT 1 FROM tasks WHERE provider_id=? AND NOT (project=? AND id=? AND revision=?)', (provider_id,project,task,revision)).fetchone():
                raise RuntimeFault("PROVIDER_RECEIPT_ALREADY_BOUND")
            db.execute("UPDATE tasks SET state='submitted',provider_id=? WHERE project=? AND id=? AND revision=?", (provider_id,project,task,revision))
            self.audit(db,actor,"provider_receipt",project,task,revision)
        return {"state":"submitted","provider_id":provider_id}

    def record_artifact(self, token, project, task, revision, provider_id, artifact):
        # This is an evidence ledger, not an automatic media verifier. A worker
        # must separately decode and hash bytes before calling it.
        if (not isinstance(artifact,dict) or set(artifact)!={"sha256","location","verification"}
                or not re.fullmatch(r"[0-9a-f]{64}",str(artifact.get("sha256")))
                or artifact.get("verification") != "full_decode_passed"
                or not isinstance(artifact.get("location"),str) or not 1<=len(artifact["location"])<=2048):
            raise RuntimeFault("ARTIFACT_EVIDENCE_INVALID")
        with self.connect() as db:
            actor=self.authorize(db,token,"admin")
            row=self._current(db,project,task,revision)
            if row["provider_id"]==provider_id and row["artifact"]==canonical(artifact) and row["state"] in ("awaiting_video_review","accepted","rejected"):
                return {"state":row["state"],"replayed":True,"human_acceptance":"separate_review_required"}
            if row["state"]!="submitted" or row["provider_id"]!=provider_id:
                raise RuntimeFault("ARTIFACT_PROVIDER_CONFLICT")
            db.execute("UPDATE tasks SET state='awaiting_video_review',artifact=? WHERE project=? AND id=? AND revision=?",(canonical(artifact),project,task,revision))
            self.audit(db,actor,"artifact_recorded",project,task,revision)
        return {"state":"awaiting_video_review","human_acceptance":"pending"}

    def inspect_task(self, token, project, task):
        with self.connect() as db:
            self.authorize(db,token,project=project)
            rows=db.execute("SELECT * FROM tasks WHERE project=? AND id=? ORDER BY revision",(project,task)).fetchall()
            receipts = [json.loads(row[0]) for row in db.execute("SELECT receipt FROM events")]
        return {"versions":[dict(row) for row in rows],
                "reviews":[item for item in receipts if item["project"]==project and item["task"]==task]}

    def doctor(self):
        with self.connect() as db:
            integrity=db.execute("PRAGMA quick_check").fetchone()[0]
            states={row[0]:row[1] for row in db.execute("SELECT state,count(*) FROM tasks GROUP BY state")}
            deployment=db.execute("SELECT value FROM meta WHERE key='deployment'").fetchone()[0]
        return {"database_integrity":integrity,"deployment":deployment,"schema":1,
                "task_states":states,"paid_adapter":"disabled","feishu_identity":"not_connected"}
