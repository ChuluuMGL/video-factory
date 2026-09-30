"""Resumable, offline Setup planning. No remote action or secret resolution.

Terminal and Agent callers share validation, optimistic revisions and the same
plan. A plan is never evidence of an installed or authenticated deployment.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from uuid import uuid4

from .setup import compile_blueprint


SCHEMA_VERSION = 1
MAX_BYTES = 256 * 1024
GROUPS = ("organization", "deployment", "project")
ID = re.compile(r"[a-z][a-z0-9_-]{2,47}\Z")
REFERENCE = re.compile(r"(?:env:[A-Z][A-Z0-9_]{1,95}|secret:[a-z][a-z0-9_-]{1,63})\Z")
IDENTITY = re.compile(r"feishu:[A-Za-z0-9_-]{2,96}\Z")
SECRET = re.compile(
    r"-----BEGIN .*PRIVATE KEY|\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{16,}|"
    r"\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|"
    r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"(?:api[_ -]?key|password|token)\s*[=:]\s*\S+|secret\s*=\s*\S+",
    re.IGNORECASE,
)


class SetupError(ValueError):
    """Only fixed codes and known field identifiers are exposed to callers."""


@dataclass(frozen=True)
class Question:
    field: str
    step: str
    label: str
    kind: str = "text"
    choices: tuple = ()
    default: str | None = None

    def public(self):
        schema = {"type": "string"}
        if self.choices:
            schema["enum"] = [value for value, _ in self.choices]
        if self.kind == "products":
            names = ("sku_id", "name", "variant", "truth_source")
            schema = {"type": "array", "minItems": 1, "maxItems": 100,
                      "items": {"type": "object", "required": list(names),
                                "additionalProperties": False,
                                "properties": {name: {"type": "string", "minLength": 1,
                                                      "maxLength": 1024} for name in names}}}
        return {"field": self.field, "step": self.step, "question": self.label,
                "kind": self.kind, "choices": list(self.choices),
                "default": self.default,
                "input_schema": schema,
                "accepts_secret_value": False}


QUESTIONS = (
    Question("organization.id", "organization", "客户组织的英文短名称", "id"),
    Question("organization.name", "organization", "客户组织名称"),
    Question("organization.admin_ref", "organization", "管理员飞书身份引用（feishu:用户ID，身份尚未验证）", "identity"),
    Question("deployment.id", "server", "部署环境的英文短名称", "id"),
    Question("deployment.host", "server", "客户云主机 IP 或域名（不带协议、密码或端口）", "host"),
    Question("deployment.ssh_user", "server", "SSH 登录用户名", "ssh_user"),
    Question("deployment.ssh_identity_ref", "server", "SSH 身份引用，例如 secret:customer_ssh；不要输入私钥", "reference"),
    Question("deployment.feishu_tenant", "integrations", "客户飞书租户标识", "identifier"),
    Question("deployment.feishu_credential_ref", "integrations", "飞书凭据引用，例如 env:FEISHU_CREDENTIAL；不要输入 Key", "reference"),
    Question("deployment.n8n_mode", "integrations", "n8n 部署方式", "choice",
             (("bundled", "随本产品独立部署；不会接管已有 n8n"),), "bundled"),
    Question("project.id", "project", "新项目的英文短名称", "id"),
    Question("project.name", "project", "项目显示名称"),
    Question("project.product_category", "project", "商品品类"),
    Question("project.target_market", "project", "目标市场"),
    Question("project.language", "project", "内容语言"),
    Question("project.business_goal", "project", "内容目标"),
    Question("project.category_rule_source", "project", "商品事实及品类规则的资料来源"),
    Question("project.base_mode", "project", "飞书 Base 设置", "choice",
             (("create", "在客户租户内规划新 Base"), ("bind", "规划绑定明确的已有 Base")), "create"),
    Question("project.base_target", "project", "新 Base 名称或已有 Base 标识（稍后需实际验证）"),
    Question("project.script_reviewer", "project", "脚本审核人飞书身份引用（feishu:用户ID）", "identity"),
    Question("project.video_reviewer", "project", "视频审核人飞书身份引用（feishu:用户ID）", "identity"),
    Question("project.products", "project", "提供 SKU 数组，每项包含 sku_id、name、variant、truth_source", "products"),
    Question("project.video_route", "models", "视频路线规划（本向导不调用模型）", "choice",
             (("deferred", "暂不选择，等待通用适配器"),
              ("minimax_h3_canary", "MiniMax H3：仅四样本 canary 已验证，通用路线待接入")), "deferred"),
    Question("project.video_credential_ref", "models", "视频凭据引用，例如 env:VIDEO_API_KEY；不要输入 Key", "reference"),
    Question("project.billing_owner", "models", "视频费用账户标签（必须由本客户明确持有或授权）", "identifier"),
    Question("project.spend_policy", "models", "付费策略", "choice",
             (("manual_per_run", "实际执行前明确每次任务与费用策略；不自动重发付费提交"),), "manual_per_run"),
)
FIELDS = {question.field: question for question in QUESTIONS}
VIDEO_FIELDS = ("project.video_credential_ref", "project.billing_owner")
VERIFICATION = ("runtime_ready", "integrations_verified", "project_ready",
                "technical_canary_passed", "human_acceptance_passed")


def _get(config, field):
    group, name = field.split(".")
    return config[group].get(name)


def _put(config, field, value):
    group, name = field.split(".")
    config[group][name] = value


def _remove(config, fields):
    removed = []
    for field in fields:
        group, name = field.split(".")
        if name in config[group]:
            del config[group][name]
            removed.append(field)
    return removed


def _active(config, question):
    return question.field not in VIDEO_FIELDS or _get(config, "project.video_route") == "minimax_h3_canary"


def _text(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 1024:
        raise SetupError("SETUP_TEXT_REQUIRED")
    value = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise SetupError("SETUP_CONTROL_CHARACTER_REJECTED")
    if SECRET.search(value):
        raise SetupError("SETUP_SECRET_VALUE_REJECTED_USE_REFERENCE")
    return value


def _validate(question, value):
    if question.kind == "products":
        if not isinstance(value, list) or not 1 <= len(value) <= 100:
            raise SetupError("SETUP_PRODUCTS_ARRAY_REQUIRED_MAX_100")
        products, seen = [], set()
        for item in value:
            if not isinstance(item, dict) or set(item) != {"sku_id", "name", "variant", "truth_source"}:
                raise SetupError("SETUP_PRODUCT_FIELDS_INVALID")
            product = {key: _text(item[key]) for key in ("sku_id", "name", "variant", "truth_source")}
            if product["sku_id"] in seen:
                raise SetupError("SETUP_DUPLICATE_SKU_ID")
            seen.add(product["sku_id"])
            products.append(product)
        return products
    value = _text(value)
    kind = question.kind
    if kind == "id" and not ID.fullmatch(value):
        raise SetupError("SETUP_ID_INVALID")
    if kind == "identity" and not IDENTITY.fullmatch(value):
        raise SetupError("SETUP_FEISHU_IDENTITY_REFERENCE_REQUIRED")
    if kind == "reference" and not REFERENCE.fullmatch(value):
        raise SetupError("SETUP_SECRET_REFERENCE_REQUIRED")
    if kind == "ssh_user" and not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value):
        raise SetupError("SETUP_SSH_USER_INVALID")
    if kind == "identifier" and not re.fullmatch(r"[A-Za-z0-9_-]{2,96}", value):
        raise SetupError("SETUP_IDENTIFIER_INVALID")
    if kind == "choice" and value not in dict(question.choices):
        raise SetupError("SETUP_CHOICE_UNSUPPORTED")
    if kind == "host":
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if (len(value) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", value)
                    or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                           for label in value.split("."))):
                raise SetupError("SETUP_HOST_INVALID") from None
    return value


def _validate_config(config):
    if not isinstance(config, dict) or set(config) != set(GROUPS):
        raise SetupError("SETUP_CONFIGURATION_INVALID")
    for group, values in config.items():
        if not isinstance(values, dict):
            raise SetupError("SETUP_CONFIGURATION_INVALID")
        for name, value in values.items():
            field = group + "." + name
            if field not in FIELDS:
                raise SetupError("SETUP_UNKNOWN_FIELD")
            if not _active(config, FIELDS[field]):
                raise SetupError("SETUP_INACTIVE_FIELD")
            if _validate(FIELDS[field], value) != value:
                raise SetupError("SETUP_CONFIGURATION_NOT_CANONICAL")


def _now():
    return datetime.now(timezone.utc).isoformat()


def new_session(source=None):
    configuration = {group: {} for group in GROUPS}
    if source is not None:
        validate_session(source)
        # Reuse only configuration, never evidence, passwords or a running state.
        if any(_get(source["configuration"], q.field) is None for q in QUESTIONS
               if q.field.split(".")[0] in ("organization", "deployment")):
            raise SetupError("SETUP_SOURCE_DEPLOYMENT_INCOMPLETE")
        for group in ("organization", "deployment"):
            configuration[group] = copy.deepcopy(source["configuration"][group])
    now = _now()
    return {"schema_version": SCHEMA_VERSION, "session_id": str(uuid4()),
            "revision": 0, "created_at": now, "updated_at": now,
            "source_session_id": source["session_id"] if source else None,
            "configuration": configuration}


def validate_session(session):
    if (not isinstance(session, dict) or type(session.get("schema_version")) is not int
            or session.get("schema_version") != SCHEMA_VERSION):
        raise SetupError("SETUP_SCHEMA_UNSUPPORTED")
    if set(session) != {"schema_version", "session_id", "revision", "created_at", "updated_at",
                        "source_session_id", "configuration"}:
        raise SetupError("SETUP_SESSION_FIELDS_INVALID")
    if type(session["revision"]) is not int or session["revision"] < 0:
        raise SetupError("SETUP_REVISION_INVALID")
    for name in ("session_id", "source_session_id"):
        value = session[name]
        if name == "source_session_id" and value is None:
            continue
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9-]{36}", value):
            raise SetupError("SETUP_SESSION_ID_INVALID")
    for field in ("created_at", "updated_at"):
        try:
            date = datetime.fromisoformat(session[field])
            if date.tzinfo is None:
                raise ValueError
        except (ValueError, TypeError):
            raise SetupError("SETUP_TIMESTAMP_INVALID") from None
    _validate_config(session["configuration"])


def apply_answers(session, answers, expected_revision):
    validate_session(session)
    if type(expected_revision) is not int or expected_revision != session["revision"]:
        raise SetupError("SETUP_REVISION_CONFLICT")
    if not isinstance(answers, dict) or not answers:
        raise SetupError("SETUP_ANSWERS_OBJECT_REQUIRED")
    if any(field not in FIELDS for field in answers):
        raise SetupError("SETUP_UNKNOWN_FIELD")
    checked = {field: _validate(FIELDS[field], value) for field, value in answers.items()}
    updated = copy.deepcopy(session)
    config = updated["configuration"]
    changed = {field for field, value in checked.items()
               if _get(config, field) is not None and _get(config, field) != value}
    invalidated = set()
    if "organization.id" in changed:
        invalidated.update(FIELDS)
    if "project.id" in changed:
        invalidated.update(field for field in FIELDS if field.startswith("project."))
    if changed & {"deployment.id", "deployment.host"}:
        invalidated.update(("deployment.ssh_identity_ref", "deployment.feishu_credential_ref", *VIDEO_FIELDS))
    if "deployment.feishu_tenant" in changed:
        invalidated.update(("organization.admin_ref", "deployment.feishu_credential_ref", "project.base_target",
                            "project.script_reviewer", "project.video_reviewer"))
    if "project.base_mode" in changed:
        invalidated.add("project.base_target")
    removed = _remove(config, invalidated - checked.keys())
    for field, value in checked.items():
        _put(config, field, value)
    if _get(config, "project.video_route") != "minimax_h3_canary":
        if checked.keys() & set(VIDEO_FIELDS):
            raise SetupError("SETUP_INACTIVE_FIELD")
        removed += _remove(config, VIDEO_FIELDS)
    _validate_config(config)
    if config != session["configuration"]:
        updated["revision"] += 1
        updated["updated_at"] = _now()
    return updated, sorted(set(removed))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _plan(config):
    org, deployment, project = (config[group] for group in GROUPS)
    brief = {key: project[key] for key in ("product_category", "target_market", "business_goal",
                                          "category_rule_source", "products")}
    brief.update(project_id=project["id"],
                 feishu_target=project["base_mode"] + ":" + project["base_target"],
                 n8n_target="deployment:" + deployment["id"],
                 script_reviewer=project["script_reviewer"], video_reviewer=project["video_reviewer"],
                 model_route="script:deferred; video:" + project["video_route"] + "; voice:disabled",
                 spend_policy=project["spend_policy"])
    blueprint = compile_blueprint(brief)
    target = {"organization_id": org["id"], "deployment_id": deployment["id"],
              "host": deployment["host"], "feishu_tenant": deployment["feishu_tenant"],
              "project_id": project["id"], "base_mode": project["base_mode"],
              "base_target": project["base_target"]}
    return {"plan_sha256": _digest({"schema_version": SCHEMA_VERSION, "configuration": config}),
            "target_sha256": _digest(target), "target": target,
            "configuration": copy.deepcopy(config),
            "components": ["product_runtime", "n8n", "postgresql", "persistent_storage"],
            "resource_intents": [{"key": item["key"], "kind": item["kind"],
                                  "action": "discover_before_proposing_create_or_reuse"}
                                 for item in blueprint["resources"]],
            "model_capabilities": {"script": "not_implemented", "voice": "disabled",
                                   "video": "scoped_canary_only" if project["video_route"] == "minimax_h3_canary" else "deferred"},
            "paid_requests_allowed": False, "activation_allowed": False,
            "blockers": ["REMOTE_PREFLIGHT_NOT_RUN", "SETUP_DEPLOY_NOT_APPLIED",
                         "IDENTITY_NOT_VERIFIED", "SECRET_STORE_NOT_CONNECTED",
                         "GENERAL_MODEL_ADAPTER_NOT_IMPLEMENTED", "LIVE_RESOURCE_DISCOVERY_REQUIRED",
                         "CUSTOMER_END_TO_END_ACCEPTANCE_NOT_RUN"]}


def describe(session):
    validate_session(session)
    config = session["configuration"]
    missing = [q for q in QUESTIONS if _active(config, q) and _get(config, q.field) is None]
    question = missing[0] if missing else None
    return {"schema_version": SCHEMA_VERSION, "session_id": session["session_id"],
            "revision": session["revision"], "source_session_id": session["source_session_id"],
            "status": ("needs_secret" if question.kind == "reference" else "needs_input") if question else "plan_ready",
            "next_question": question.public() if question else None,
            "remaining_questions": len(missing),
            "secret_input": {"mode": "reference_only", "accepts_secret_value": False,
                             "message": "仅填写 env:变量名 或 secret:别名；P1 不接收、读取或保存原始 Key。"},
            "installation_state": "draft", "runtime_status": "not_running", "execute_allowed": False,
            "verification": {name: "not_run" for name in VERIFICATION},
            "plan": _plan(config) if not missing else None}


def read_json(stream):
    data = stream.read(MAX_BYTES + 1)
    if len(data.encode("utf-8")) > MAX_BYTES:
        raise SetupError("SETUP_INPUT_TOO_LARGE")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SetupError("SETUP_DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid_constant(value):
        raise SetupError("SETUP_JSON_INVALID")

    try:
        return json.loads(data, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except SetupError:
        raise
    except (ValueError, RecursionError):
        raise SetupError("SETUP_JSON_INVALID") from None


def read_input_file(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise SetupError("SETUP_REGULAR_ABSOLUTE_INPUT_REQUIRED")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, encoding="utf-8") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise SetupError("SETUP_REGULAR_ABSOLUTE_INPUT_REQUIRED")
        return read_json(stream)


class SessionStore:
    """Owner-only files, nonblocking flock, revision CAS and atomic replacement.

    This protects concurrent cooperative installers, not hostile code running
    as the same OS user. No configuration file can assert remote verification.
    """

    validate = staticmethod(validate_session)

    def __init__(self, path):
        self.path = Path(path)
        if (not self.path.is_absolute() or self.path.parent.resolve() != self.path.parent
                or not self.path.parent.is_dir()):
            raise SetupError("SETUP_EXISTING_ABSOLUTE_DIRECTORY_REQUIRED")
        parent = self.path.parent.stat()
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o022:
            raise SetupError("SETUP_DIRECTORY_MUST_BE_OWNED_AND_NOT_SHARED_WRITABLE")
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @staticmethod
    def _private_regular(descriptor):
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
            raise SetupError("SETUP_PRIVATE_REGULAR_FILE_REQUIRED")

    @contextmanager
    def locked(self):
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            self._private_regular(descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SetupError("SETUP_SESSION_BUSY") from None
            yield
        finally:
            os.close(descriptor)

    def _read(self):
        descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            self._private_regular(stream.fileno())
            session = read_json(stream)
        self.validate(session)
        return session

    def _save(self, session):
        self.validate(session)
        data = json.dumps(session, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if len(data.encode()) > MAX_BYTES:
            raise SetupError("SETUP_INPUT_TOO_LARGE")
        descriptor, temporary = tempfile.mkstemp(prefix="." + self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def start(self, source=None):
        with self.locked():
            if self.path.exists() or self.path.is_symlink():
                if source is not None:
                    raise SetupError("SETUP_SOURCE_ONLY_FOR_NEW_SESSION")
                return self._read()
            session = new_session(source)
            self._save(session)
            return session

    def read(self):
        with self.locked():
            return self._read()

    def answer(self, answers, expected_revision):
        with self.locked():
            current = self._read()
            updated, invalidated = apply_answers(current, answers, expected_revision)
            if updated != current:
                self._save(updated)
            result = describe(updated)
            result["invalidated_fields"] = invalidated
            return result
