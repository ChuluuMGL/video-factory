"""Adversarial contract and security tests for v0.3 operational workbench actions.

Purely specification-driven (v0.3运营工作台接入规范.md) using synthetic data
and a simulated client/gateway, plus auto-activating live AgentAPI probes once
M1-Part2 routes land. No real network, Feishu, or paid provider I/O occurs here.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unittest


ALL_SIX_SKILLS = (
    "project_stats",
    "list_failures",
    "repair_and_schedule",
    "list_reviews",
    "review_or_edit_script",
    "submit_review_decision",
)

NATURAL_LANGUAGE_NON_CONFIRMATIONS = (
    "好",
    "好的",
    "确认",
    "确认执行",
    "批准",
    "同意",
    "ok",
    "yes",
    "approve",
)

FORBIDDEN_VALUE_PATTERNS = (
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)),
    ("secret_key", re.compile(r"\b(?:sk|vf|t)-[A-Za-z0-9_-]{10,}")),
    ("feishu_app_id", re.compile(r"\bcli_[A-Za-z0-9]{8,}\b")),
    ("feishu_open_id", re.compile(r"\bou_[A-Za-z0-9_-]{4,}\b")),
    ("secret_ref", re.compile(r"\bsecret:[A-Za-z0-9_-]{3,}")),
    (
        "abs_local_path",
        re.compile(
            r"(?:/Users/|/home/|/var/|/tmp/|/etc/|/private/|/opt/|/work/|[A-Za-z]:\\)"
        ),
    ),
    ("ssh_uri", re.compile(r"\bssh://", re.IGNORECASE)),
    ("ssh_key_file", re.compile(r"\b(?:id_rsa|id_ed25519|authorized_keys)\b|\.pem\b")),
    ("ssh_user_host", re.compile(r"\b[a-zA-Z0-9._-]+@\d{1,3}(?:\.\d{1,3}){3}\b")),
    ("ssh_env", re.compile(r"\bWORKFLOW_REMOTE_SSH(?:_TARGET|_KEY)?\b")),
)

FORBIDDEN_KEY_NAMES = frozenset(
    {
        "token",
        "access_token",
        "user_token",
        "admin_token",
        "api_key",
        "app_secret",
        "master_key",
        "authorization",
        "open_id",
        "reviewer_open_id",
        "submitter_open_id",
        "local_path",
        "assets_root",
        "ssh_target",
        "ssh_key",
        "raw_provider_response",
        "provider_raw",
        "upstream_body",
        "upstream_headers",
    }
)


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_digest(value):
    raw = value.encode("utf-8") if isinstance(value, str) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def assert_redacted(test_case, payload, *, context=""):
    """Recursively verify that no secrets, paths, SSH details, raw provider bodies or open_ids leak."""
    stack = [("$", payload)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            for key, val in node.items():
                key_lower = str(key).lower()
                test_case.assertNotIn(
                    key_lower,
                    FORBIDDEN_KEY_NAMES,
                    f"[{context}] forbidden key leaked at {path}.{key}",
                )
                stack.append((f"{path}.{key}", val))
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                stack.append((f"{path}[{idx}]", item))
        elif isinstance(node, str):
            for label, pattern in FORBIDDEN_VALUE_PATTERNS:
                test_case.assertIsNone(
                    pattern.search(node),
                    f"[{context}] forbidden {label} leaked at {path}: {node!r}",
                )


def assert_task_brief_v1(test_case, brief, *, context=""):
    test_case.assertIsInstance(brief, dict, f"[{context}] brief must be a dict")
    test_case.assertEqual(brief.get("schema"), "task-brief-v1", f"[{context}] brief schema mismatch")
    for field in ("conclusion", "nextAction", "blocker", "details"):
        value = brief.get(field)
        test_case.assertIsInstance(value, str, f"[{context}] brief.{field} must be str")
        test_case.assertTrue(value.strip(), f"[{context}] brief.{field} must be non-empty")


class ContractFault(Exception):
    def __init__(self, status, code, message=""):
        super().__init__(f"{status}:{code}:{message}")
        self.status = status
        self.code = code
        self.message = message or code


@dataclass
class ConfirmationRecord:
    confirmation_id: str
    skill: str
    project_id: str
    principal: str
    task_key: str
    proposal_sha256: str
    expected_version: str
    expires_at: float
    may_incur_charges: bool
    payload: dict
    consumed: bool = False


class SimulatedWorkbenchGateway:
    """Reference in-memory implementation of the v0.3 6-action A2A/Tool contract.

    Intentionally stores sensitive backend fields (local paths, Feishu open_ids,
    SSH targets, raw provider HTTP bodies, bearer tokens) inside internal records
    so redaction tests prove none of them escape through any of the 6 actions.
    """

    def __init__(self, *, clock=None):
        self._now = clock or (lambda: 1_764_150_000.0)
        self.principals = {
            "auth-" + "a" * 64: {"projects": {"brand_alpha"}, "cost_owners": {"brand_alpha"}},
            "auth-" + "b" * 64: {"projects": {"brand_alpha"}, "cost_owners": set()},
            "auth-" + "c" * 64: {"projects": {"brand_beta"}, "cost_owners": {"brand_beta"}},
        }
        self.idempotency = {}
        self.confirmations = {}
        self.reschedule_calls = []
        self.provider_charge_calls = []

        # Internal backend records deliberately contain sensitive fields that
        # must NEVER appear in any serialized response.
        self.tasks = {
            "brand_alpha:VID-201": {
                "project_id": "brand_alpha",
                "task_id": "VID-201",
                "sku_id": "sku_white_cup",
                "sku_name": "极简白瓷保温杯",
                "revision": 1,
                "stage": "video",
                "canonical_status": "submission_unknown",
                "raw_status": "submission_unknown",
                "error_code": "PROVIDER_SUBMISSION_UNCERTAIN",
                "diagnosis": "视频提交响应中断，供应商侧状态未知，必须先核对回执。",
                "script": "开场 3 秒展示哑光白瓷杯身，12 小时实测保温。",
                "unresolved_notes_count": 0,
                "local_path": "/Users/operator/VideoFactory/media/VID-201.mp4",
                "open_id": "ou_secret_operator_201",
                "ssh_target": "ops@10.0.0.21",
                "api_key": "sk-" + "synthetic-fixture-201",
                "raw_provider_response": {"Authorization": "Bearer " + "sk-" + "synthetic-fixture-201", "body": "raw"},
            },
            "brand_alpha:VID-202": {
                "project_id": "brand_alpha",
                "task_id": "VID-202",
                "sku_id": "sku_white_cup",
                "sku_name": "极简白瓷保温杯",
                "revision": 1,
                "stage": "script",
                "canonical_status": "submission_unknown",
                "raw_status": "script_submission_unknown",
                "error_code": "SCRIPT_SUBMISSION_UNCERTAIN_NO_RETRY",
                "diagnosis": "脚本模型提交结果待核实，禁止自动重发。",
                "script": "",
                "unresolved_notes_count": 0,
                "local_path": "/private/var/vf/scripts/VID-202.txt",
                "open_id": "ou_secret_operator_202",
                "ssh_key": "/Users/operator/.ssh/id_ed25519",
                "user_token": "t-feishu-user-secret-202",
                "raw_provider_response": {"upstream": "timeout"},
            },
            "brand_alpha:VID-212": {
                "project_id": "brand_alpha",
                "task_id": "VID-212",
                "sku_id": "sku_white_cup",
                "sku_name": "极简白瓷保温杯",
                "revision": 2,
                "stage": "video",
                "canonical_status": "failed",
                "raw_status": "failed",
                "error_code": "TRANSCRIBE_ASR_TIME_INVALID",
                "diagnosis": "原片已生成，转写词时间戳异常导致后期暂停，可修复重排。",
                "script": "通勤常备极简保温杯，12 小时实测保温。",
                "unresolved_notes_count": 0,
                "local_path": "/Users/operator/VideoFactory/media/VID-212.mp4",
                "open_id": "ou_secret_operator_212",
                "ssh_target": "ops@10.0.0.21",
                "app_secret": "cli_a1b2c3d4e5f6",
                "raw_provider_response": {"error": "asr_overlap"},
            },
            "brand_alpha:VID-219": {
                "project_id": "brand_alpha",
                "task_id": "VID-219",
                "sku_id": "sku_white_cup",
                "sku_name": "极简白瓷保温杯",
                "revision": 2,
                "stage": "script",
                "canonical_status": "awaiting_review",
                "raw_status": "awaiting_script_review",
                "error_code": None,
                "diagnosis": None,
                "script": "全网绝对第一保温杯，长达 24 小时保温。",
                "unresolved_notes_count": 0,
                "local_path": "/Users/operator/VideoFactory/assets/cup.png",
                "open_id": "ou_secret_reviewer_219",
                "raw_provider_response": {"id": "chatcmpl-secret"},
            },
            "brand_alpha:VID-220": {
                "project_id": "brand_alpha",
                "task_id": "VID-220",
                "sku_id": "sku_white_cup",
                "sku_name": "极简白瓷保温杯",
                "revision": 1,
                "stage": "storyboard",
                "canonical_status": "awaiting_review",
                "raw_status": "awaiting_storyboard_review",
                "error_code": None,
                "diagnosis": None,
                "script": "分镜1：杯身特写；分镜2：倒水演示。",
                "unresolved_notes_count": 0,
                "local_path": "/Users/operator/VideoFactory/storyboard/VID-220.png",
                "open_id": "ou_secret_reviewer_220",
                "raw_provider_response": {},
            },
            "brand_alpha:VID-221": {
                "project_id": "brand_alpha",
                "task_id": "VID-221",
                "sku_id": "sku_white_cup",
                "sku_name": "极简白瓷保温杯",
                "revision": 1,
                "stage": "video",
                "canonical_status": "awaiting_review",
                "raw_status": "awaiting_video_review",
                "error_code": None,
                "diagnosis": None,
                "script": "成片审核样例脚本。",
                "unresolved_notes_count": 0,
                "artifact_sha256": "d" * 64,
                "local_path": "/Users/operator/VideoFactory/output/VID-221.mp4",
                "open_id": "ou_secret_reviewer_221",
                "raw_provider_response": {"download_url": "https://secret.example/raw"},
            },
            "brand_alpha:VID-222": {
                "project_id": "brand_alpha",
                "task_id": "VID-222",
                "sku_id": "sku_white_cup",
                "sku_name": "极简白瓷保温杯",
                "revision": 2,
                "stage": "script",
                "canonical_status": "awaiting_review",
                "raw_status": "awaiting_script_review",
                "error_code": None,
                "diagnosis": None,
                "script": "仍有未解决历史批注的脚本。",
                "unresolved_notes_count": 2,
                "local_path": "/Users/operator/VideoFactory/assets/cup2.png",
                "open_id": "ou_secret_reviewer_222",
                "raw_provider_response": {},
            },
            "brand_beta:VID-901": {
                "project_id": "brand_beta",
                "task_id": "VID-901",
                "sku_id": "sku_other",
                "sku_name": "竞品测试品",
                "revision": 1,
                "stage": "script",
                "canonical_status": "awaiting_review",
                "raw_status": "awaiting_script_review",
                "error_code": None,
                "diagnosis": None,
                "script": "外部项目隔离脚本。",
                "unresolved_notes_count": 0,
                "local_path": "/home/beta/secret.mp4",
                "open_id": "ou_beta_owner_901",
                "raw_provider_response": {},
            },
        }

    def version_token(self, task_key):
        task = self.tasks[task_key]
        return f"rev-{task['revision']}-{sha256_digest([task_key, task['revision'], task['canonical_status'], task['script']])[:16]}"

    def _authorize(self, principal, project_id):
        acl = self.principals.get(principal)
        if not acl or project_id not in acl["projects"]:
            raise ContractFault(403, "AUTH_PROJECT_DENIED", "Cross-project access denied")
        return acl

    def _validate_request_id(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", request_id):
            raise ContractFault(400, "REQUEST_ID_INVALID")

    def _require_task(self, project_id, task_key):
        task = self.tasks.get(task_key)
        if not task or task["project_id"] != project_id:
            raise ContractFault(403, "TASK_PROJECT_DENIED")
        return task

    def _reject_natural_language_confirmation(self, body):
        for field in ("confirmation_id", "proposal_sha256", "draft_sha256", "confirm_text"):
            val = body.get(field)
            if isinstance(val, str) and val.strip().lower() in NATURAL_LANGUAGE_NON_CONFIRMATIONS:
                raise ContractFault(400, "EXPLICIT_STRUCTURED_CONFIRMATION_REQUIRED")

    def _verify_confirmation(self, *, principal, project_id, skill, body, digest_field="proposal_sha256"):
        self._reject_natural_language_confirmation(body)
        cid = body.get("confirmation_id")
        digest = body.get(digest_field)
        expected_version = body.get("expected_version")
        if not isinstance(cid, str) or not cid.startswith("cf_"):
            raise ContractFault(409, "CONFIRMATION_REQUIRED")
        record = self.confirmations.get(cid)
        if not record or record.skill != skill or record.project_id != project_id:
            raise ContractFault(409, "CONFIRMATION_INVALID")
        if record.principal != principal:
            raise ContractFault(403, "CONFIRMATION_PRINCIPAL_MISMATCH")
        if record.consumed:
            raise ContractFault(409, "CONFIRMATION_ALREADY_USED")
        if self._now() >= record.expires_at:
            raise ContractFault(409, "CONFIRMATION_EXPIRED")
        if digest != record.proposal_sha256:
            raise ContractFault(409, "CONFIRMATION_DIGEST_MISMATCH")
        current_version = self.version_token(record.task_key)
        if expected_version != record.expected_version or current_version != record.expected_version:
            raise ContractFault(409, "OPTIMISTIC_LOCK_VERSION_CONFLICT")
        return record

    def invoke(self, principal, request_id, body):
        self._validate_request_id(request_id)
        if not isinstance(body, dict):
            raise ContractFault(400, "INVALID_BODY")
        skill = body.get("skill")
        project_id = body.get("project_id")
        if skill not in ALL_SIX_SKILLS:
            raise ContractFault(400, "UNSUPPORTED_SKILL")
        acl = self._authorize(principal, project_id)

        fingerprint = sha256_digest(body)
        idem_key = (principal, request_id)
        if idem_key in self.idempotency:
            prev_fp, prev_resp = self.idempotency[idem_key]
            if prev_fp != fingerprint:
                raise ContractFault(409, "IDEMPOTENCY_CONFLICT")
            replayed = json.loads(json.dumps(prev_resp))
            replayed["replayed"] = True
            return replayed

        handler = getattr(self, f"_skill_{skill}")
        result = handler(principal, acl, project_id, body)
        self.idempotency[idem_key] = (fingerprint, json.loads(json.dumps(result)))
        return result

    def _skill_project_stats(self, principal, acl, project_id, body):
        project_tasks = [t for t in self.tasks.values() if t["project_id"] == project_id]
        by_status = {
            "queued": 0,
            "running": 0,
            "awaiting_review": 0,
            "approved_waiting_next": 0,
            "rejected": 0,
            "failed": 0,
            "submission_unknown": 0,
            "completed": 0,
        }
        for t in project_tasks:
            by_status[t["canonical_status"]] = by_status.get(t["canonical_status"], 0) + 1
        total = len(project_tasks)
        completed = by_status["completed"]
        return {
            "schema": "op-workbench-v1",
            "skill": "project_stats",
            "project_id": project_id,
            "engine": "video_factory",
            "board": "feishu_bitable",
            "source": {"read_at": "2026-11-26T11:40:00Z", "complete": True, "stale": False, "total_tasks": total},
            "brief": {
                "schema": "task-brief-v1",
                "conclusion": f"项目 {project_id} 共 {total} 条任务，完成 {completed} 条。",
                "nextAction": "核对失败与待核实任务，处理待审队列。",
                "blocker": "存在 submission_unknown 任务需人工核对回执。",
                "details": f"待审 {by_status['awaiting_review']} 条，失败 {by_status['failed']} 条。",
            },
            "metrics": {
                "window": body.get("window", "7d"),
                "total": total,
                "completed": completed,
                "success_rate": round(completed / total, 4) if total else 0.0,
                "by_status": by_status,
            },
        }

    def _skill_list_failures(self, principal, acl, project_id, body):
        items = []
        for task_key, t in sorted(self.tasks.items()):
            if t["project_id"] != project_id:
                continue
            if t["canonical_status"] not in ("failed", "submission_unknown"):
                continue
            is_unknown = t["canonical_status"] == "submission_unknown"
            items.append(
                {
                    "task_key": task_key,
                    "task_id": t["task_id"],
                    "revision": t["revision"],
                    "version_token": self.version_token(task_key),
                    "stage": t["stage"],
                    "canonical_status": t["canonical_status"],
                    "raw_status": t["raw_status"],
                    "failure_kind": "submission_unknown" if is_unknown else "failed",
                    "error_code": t["error_code"],
                    "diagnosis": t["diagnosis"],
                    "reconciliation_required": is_unknown,
                    "automatic_resubmit": False,
                    "repairable": not is_unknown,
                    "suggested_repair_type": None if is_unknown else "requeue_failed_task",
                }
            )
        return {
            "schema": "op-workbench-v1",
            "skill": "list_failures",
            "project_id": project_id,
            "source": {"read_at": "2026-11-26T11:40:00Z", "complete": True},
            "brief": {
                "schema": "task-brief-v1",
                "conclusion": f"发现 {len(items)} 条异常任务。",
                "nextAction": "可对 failed 任务预览修复；submission_unknown 禁止自动重排产。",
                "blocker": "submission_unknown 需先核对供应商回执。",
                "details": "列表已脱敏，不含本机路径或供应商原始响应。",
            },
            "items": items,
            "has_more": False,
            "next_cursor": None,
        }

    def _skill_repair_and_schedule(self, principal, acl, project_id, body):
        phase = body.get("phase")
        if phase == "preview":
            task_key = body.get("target_id")
            task = self._require_task(project_id, task_key)
            if task["canonical_status"] == "submission_unknown" or task["raw_status"] in (
                "submission_unknown",
                "script_submission_unknown",
            ):
                raise ContractFault(409, "SUBMISSION_UNKNOWN_REQUIRES_RECONCILIATION")
            expected_version = body.get("expected_version")
            if expected_version != self.version_token(task_key):
                raise ContractFault(409, "OPTIMISTIC_LOCK_VERSION_CONFLICT")
            proposal_sha256 = sha256_digest(
                {
                    "task_key": task_key,
                    "expected_version": expected_version,
                    "repair_action": body.get("repair_action", "requeue_failed_task"),
                    "reschedule": bool(body.get("reschedule", True)),
                }
            )
            cid = "cf_rep_" + proposal_sha256[:24]
            expires_at = self._now() + 600.0
            may_charge = bool(body.get("reschedule", True))
            self.confirmations[cid] = ConfirmationRecord(
                confirmation_id=cid,
                skill="repair_and_schedule",
                project_id=project_id,
                principal=principal,
                task_key=task_key,
                proposal_sha256=proposal_sha256,
                expected_version=expected_version,
                expires_at=expires_at,
                may_incur_charges=may_charge,
                payload={"reschedule": may_charge},
            )
            return {
                "schema": "op-workbench-v1",
                "skill": "repair_and_schedule",
                "phase": "preview",
                "brief": {
                    "schema": "task-brief-v1",
                    "conclusion": f"已生成 {task['task_id']} 修复与重排产预案，尚未写入。",
                    "nextAction": "核对 Diff 后点击确认执行。",
                    "blocker": "等待人工确认。",
                    "details": "状态将从 failed 重置为 queued。",
                },
                "diff": {
                    "format": "unified",
                    "summary": "任务状态 failed -> queued",
                    "patch": "- state: failed\n+ state: queued",
                },
                "confirmation": {
                    "required": True,
                    "confirmation_id": cid,
                    "proposal_sha256": proposal_sha256,
                    "expected_version": expected_version,
                    "expires_at": expires_at,
                    "summary": f"修复 {task['task_id']} 并重新排产",
                    "cost_gate": {
                        "may_incur_charges": may_charge,
                        "quote_available": False,
                        "amount": None,
                        "currency": None,
                        "approval_basis": "one_submission_at_configured_provider_rate",
                    },
                },
            }

        if phase == "confirm":
            record = self._verify_confirmation(
                principal=principal,
                project_id=project_id,
                skill="repair_and_schedule",
                body=body,
            )
            task = self._require_task(project_id, record.task_key)
            if task["canonical_status"] == "submission_unknown":
                raise ContractFault(409, "SUBMISSION_UNKNOWN_REQUIRES_RECONCILIATION")
            if record.may_incur_charges:
                if body.get("accept_provider_charges") is not True:
                    raise ContractFault(409, "EXPLICIT_PROVIDER_CHARGES_REQUIRED")
                if project_id not in acl["cost_owners"]:
                    raise ContractFault(403, "COST_OWNER_AUTHORITY_REQUIRED")
                self.provider_charge_calls.append((project_id, record.task_key))
            record.consumed = True
            task["canonical_status"] = "queued"
            task["raw_status"] = "ready"
            self.reschedule_calls.append(record.task_key)
            return {
                "schema": "op-workbench-v1",
                "skill": "repair_and_schedule",
                "phase": "confirm",
                "applied": True,
                "scheduled": record.payload["reschedule"],
                "new_version": self.version_token(record.task_key),
                "automatic_resubmit": False,
                "brief": {
                    "schema": "task-brief-v1",
                    "conclusion": f"{task['task_id']} 已修复并重新排产。",
                    "nextAction": "等待调度器推进并读取回执。",
                    "blocker": "无阻塞。",
                    "details": "状态已变更为 queued。",
                },
            }

        raise ContractFault(400, "INVALID_PHASE")

    def _skill_list_reviews(self, principal, acl, project_id, body):
        stage = body.get("stage", "all")
        if stage not in ("all", "script", "storyboard", "video"):
            raise ContractFault(400, "INVALID_STAGE")
        items = []
        for task_key, t in sorted(self.tasks.items()):
            if t["project_id"] != project_id or t["canonical_status"] != "awaiting_review":
                continue
            if stage != "all" and t["stage"] != stage:
                continue
            is_media = t["stage"] in ("storyboard", "video")
            items.append(
                {
                    "task_key": task_key,
                    "task_id": t["task_id"],
                    "sku_id": t["sku_id"],
                    "sku_name": t["sku_name"],
                    "revision": t["revision"],
                    "version_token": self.version_token(task_key),
                    "stage": t["stage"],
                    "canonical_status": t["canonical_status"],
                    "unresolved_notes_count": t["unresolved_notes_count"],
                    "content_is_instruction": False,
                    "preview": {
                        "kind": "media_token" if is_media else "text_excerpt",
                        "excerpt": None if is_media else t["script"][:80],
                        "media_preview_token": f"mpt_{sha256_digest(task_key)[:24]}" if is_media else None,
                        "artifact_sha256": t.get("artifact_sha256"),
                        "decode_verified": True if t["stage"] == "video" else None,
                    },
                }
            )
        return {
            "schema": "op-workbench-v1",
            "skill": "list_reviews",
            "project_id": project_id,
            "source": {"read_at": "2026-11-26T11:40:00Z", "complete": True},
            "brief": {
                "schema": "task-brief-v1",
                "conclusion": f"共 {len(items)} 条待审任务（stage={stage}）。",
                "nextAction": "选择任务执行 AI 预审、改稿或审核提交。",
                "blocker": "无阻塞。",
                "details": "覆盖脚本、分镜与成片待审节点。",
            },
            "items": items,
            "has_more": False,
            "next_cursor": None,
        }

    def _skill_review_or_edit_script(self, principal, acl, project_id, body):
        mode = body.get("mode")
        if mode == "ai_prereview":
            task_key = body.get("task_key")
            task = self._require_task(project_id, task_key)
            if body.get("expected_version") != self.version_token(task_key):
                raise ContractFault(409, "OPTIMISTIC_LOCK_VERSION_CONFLICT")
            return {
                "schema": "op-workbench-v1",
                "skill": "review_or_edit_script",
                "mode": "ai_prereview",
                "task_key": task_key,
                "revision": task["revision"],
                "is_human_approval": False,
                "content_is_instruction": False,
                "brief": {
                    "schema": "task-brief-v1",
                    "conclusion": "发现 1 处违禁词风险，建议修改。",
                    "nextAction": "发起 edit_preview 生成改稿 Diff。",
                    "blocker": "AI 预审不等于人工通过。",
                    "details": "第 1 行存在绝对化用语。",
                },
                "risks": [
                    {
                        "severity": "high",
                        "line": 1,
                        "quote": "全网绝对第一保温杯",
                        "reason": "绝对化违禁词",
                        "suggestion": "改为通勤常备极简保温杯",
                    }
                ],
            }

        if mode == "edit_preview":
            task_key = body.get("task_key")
            task = self._require_task(project_id, task_key)
            expected_version = body.get("expected_version")
            if expected_version != self.version_token(task_key):
                raise ContractFault(409, "OPTIMISTIC_LOCK_VERSION_CONFLICT")
            instruction = body.get("instruction", "")
            if not isinstance(instruction, str) or not instruction.strip():
                raise ContractFault(400, "INSTRUCTION_REQUIRED")
            new_script = "通勤常备极简保温杯，12 小时实测保温。"
            draft_sha256 = sha256_digest(new_script)
            cid = "cf_edit_" + draft_sha256[:24]
            expires_at = self._now() + 600.0
            self.confirmations[cid] = ConfirmationRecord(
                confirmation_id=cid,
                skill="review_or_edit_script",
                project_id=project_id,
                principal=principal,
                task_key=task_key,
                proposal_sha256=draft_sha256,
                expected_version=expected_version,
                expires_at=expires_at,
                may_incur_charges=False,
                payload={"new_script": new_script},
            )
            return {
                "schema": "op-workbench-v1",
                "skill": "review_or_edit_script",
                "mode": "edit_preview",
                "task_key": task_key,
                "base_revision": task["revision"],
                "target_revision": task["revision"] + 1,
                "content_is_instruction": False,
                "brief": {
                    "schema": "task-brief-v1",
                    "conclusion": "已生成改稿 Diff，尚未落库。",
                    "nextAction": "确认后调用 edit_commit 落新版本。",
                    "blocker": "等待人工确认。",
                    "details": "不触发付费视频生成。",
                },
                "diff": {
                    "format": "unified",
                    "base_sha256": sha256_digest(task["script"]),
                    "draft_sha256": draft_sha256,
                    "patch": f"-{task['script']}\n+{new_script}",
                },
                "confirmation": {
                    "required": True,
                    "confirmation_id": cid,
                    "proposal_sha256": draft_sha256,
                    "expected_version": expected_version,
                    "expires_at": expires_at,
                    "summary": f"保存 {task['task_id']} 新版脚本并回写任务表",
                    "cost_gate": {"may_incur_charges": False, "quote_available": False, "amount": None, "currency": None},
                },
            }

        if mode == "edit_commit":
            record = self._verify_confirmation(
                principal=principal,
                project_id=project_id,
                skill="review_or_edit_script",
                body=body,
                digest_field="draft_sha256",
            )
            record.consumed = True
            task = self._require_task(project_id, record.task_key)
            task["revision"] += 1
            task["script"] = record.payload["new_script"]
            return {
                "schema": "op-workbench-v1",
                "skill": "review_or_edit_script",
                "mode": "edit_commit",
                "task_key": record.task_key,
                "new_revision": task["revision"],
                "new_version_token": self.version_token(record.task_key),
                "draft_sha256": record.proposal_sha256,
                "board_writeback": "synced",
                "video_spending_approved": False,
                "brief": {
                    "schema": "task-brief-v1",
                    "conclusion": f"{task['task_id']} 已落第 {task['revision']} 版脚本并回写。",
                    "nextAction": "可继续发起人工审核。",
                    "blocker": "尚未批准视频费用。",
                    "details": "新版本已保存。",
                },
            }

        raise ContractFault(400, "INVALID_MODE")

    def _skill_submit_review_decision(self, principal, acl, project_id, body):
        phase = body.get("phase")
        if phase == "prepare":
            task_key = body.get("task_key")
            task = self._require_task(project_id, task_key)
            expected_version = body.get("expected_version")
            if expected_version != self.version_token(task_key):
                raise ContractFault(409, "OPTIMISTIC_LOCK_VERSION_CONFLICT")
            decision = body.get("decision")
            if decision not in ("accept", "reject"):
                raise ContractFault(400, "INVALID_DECISION")
            if decision == "accept" and task["unresolved_notes_count"] > 0:
                raise ContractFault(409, "UNRESOLVED_NOTES_EXIST")
            advance = bool(body.get("advance_next_stage", False)) and decision == "accept"
            may_charge = advance and task["stage"] in ("script", "storyboard")
            proposal_sha256 = sha256_digest(
                {
                    "task_key": task_key,
                    "expected_version": expected_version,
                    "stage": task["stage"],
                    "decision": decision,
                    "advance_next_stage": advance,
                }
            )
            cid = "cf_rev_" + proposal_sha256[:24]
            expires_at = self._now() + 600.0
            self.confirmations[cid] = ConfirmationRecord(
                confirmation_id=cid,
                skill="submit_review_decision",
                project_id=project_id,
                principal=principal,
                task_key=task_key,
                proposal_sha256=proposal_sha256,
                expected_version=expected_version,
                expires_at=expires_at,
                may_incur_charges=may_charge,
                payload={"decision": decision, "advance": advance},
            )
            return {
                "schema": "op-workbench-v1",
                "skill": "submit_review_decision",
                "phase": "prepare",
                "brief": {
                    "schema": "task-brief-v1",
                    "conclusion": f"已准备 {task['task_id']} 审核结论（{decision}）。",
                    "nextAction": "核对费用门禁后点击确认提交。",
                    "blocker": "涉及付费生成时必须显式确认费用。" if may_charge else "等待人工确认。",
                    "details": f"当前阶段：{task['stage']}。",
                },
                "confirmation": {
                    "required": True,
                    "confirmation_id": cid,
                    "proposal_sha256": proposal_sha256,
                    "expected_version": expected_version,
                    "expires_at": expires_at,
                    "summary": f"提交 {task['task_id']} 审核结论 ({decision})",
                    "cost_gate": {
                        "may_incur_charges": may_charge,
                        "quote_available": False,
                        "amount": None,
                        "currency": None,
                        "approval_basis": "one_submission_at_configured_provider_rate" if may_charge else None,
                    },
                },
            }

        if phase == "commit":
            record = self._verify_confirmation(
                principal=principal,
                project_id=project_id,
                skill="submit_review_decision",
                body=body,
            )
            if record.may_incur_charges:
                if body.get("accept_provider_charges") is not True:
                    raise ContractFault(409, "EXPLICIT_PROVIDER_CHARGES_REQUIRED")
                if project_id not in acl["cost_owners"]:
                    raise ContractFault(403, "COST_OWNER_AUTHORITY_REQUIRED")
                self.provider_charge_calls.append((project_id, record.task_key))
            record.consumed = True
            task = self._require_task(project_id, record.task_key)
            decision = record.payload["decision"]
            task["canonical_status"] = (
                "queued"
                if (decision == "accept" and record.payload["advance"])
                else ("approved_waiting_next" if decision == "accept" else "rejected")
            )
            return {
                "schema": "op-workbench-v1",
                "skill": "submit_review_decision",
                "phase": "commit",
                "review_recorded": True,
                "new_canonical_status": task["canonical_status"],
                "board_writeback": "synced",
                "video_spending_approved": bool(record.may_incur_charges),
                "brief": {
                    "schema": "task-brief-v1",
                    "conclusion": f"{task['task_id']} 审核结论已提交（{decision}）。",
                    "nextAction": "读取回执确认后续状态。",
                    "blocker": "无阻塞。",
                    "details": f"状态更新为 {task['canonical_status']}。",
                },
            }

        raise ContractFault(400, "INVALID_PHASE")


class BaseAdversarialCase(unittest.TestCase):
    def setUp(self):
        self.now = 1_764_150_000.0
        self.gateway = SimulatedWorkbenchGateway(clock=lambda: self.now)
        self.owner = "auth-" + "a" * 64
        self.non_cost_member = "auth-" + "b" * 64
        self.other_project_user = "auth-" + "c" * 64
        self._seq = 0

    def next_req_id(self, prefix="req"):
        self._seq += 1
        return f"{prefix}-00000000000000{self._seq:04d}"


class SubmissionUnknownHardRejectTests(BaseAdversarialCase):
    """submission_unknown / script_submission_unknown must NEVER be auto-rescheduled."""

    def test_repair_and_schedule_preview_rejects_video_submission_unknown(self):
        with self.assertRaises(ContractFault) as ctx:
            self.gateway.invoke(
                self.owner,
                self.next_req_id("unk-vid"),
                {
                    "skill": "repair_and_schedule",
                    "phase": "preview",
                    "project_id": "brand_alpha",
                    "target_type": "task_state",
                    "target_id": "brand_alpha:VID-201",
                    "expected_version": self.gateway.version_token("brand_alpha:VID-201"),
                    "repair_action": "requeue_failed_task",
                    "reschedule": True,
                },
            )
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.code, "SUBMISSION_UNKNOWN_REQUIRES_RECONCILIATION")
        self.assertEqual(self.gateway.reschedule_calls, [])
        self.assertEqual(self.gateway.provider_charge_calls, [])

    def test_repair_and_schedule_preview_rejects_script_submission_unknown(self):
        with self.assertRaises(ContractFault) as ctx:
            self.gateway.invoke(
                self.owner,
                self.next_req_id("unk-scr"),
                {
                    "skill": "repair_and_schedule",
                    "phase": "preview",
                    "project_id": "brand_alpha",
                    "target_type": "task_state",
                    "target_id": "brand_alpha:VID-202",
                    "expected_version": self.gateway.version_token("brand_alpha:VID-202"),
                    "repair_action": "requeue_failed_task",
                    "reschedule": True,
                },
            )
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.code, "SUBMISSION_UNKNOWN_REQUIRES_RECONCILIATION")
        self.assertEqual(self.gateway.reschedule_calls, [])

    def test_repair_confirm_blocks_if_task_drifted_to_submission_unknown_after_preview(self):
        v = self.gateway.version_token("brand_alpha:VID-212")
        preview = self.gateway.invoke(
            self.owner,
            self.next_req_id("rep-prev"),
            {
                "skill": "repair_and_schedule",
                "phase": "preview",
                "project_id": "brand_alpha",
                "target_type": "task_state",
                "target_id": "brand_alpha:VID-212",
                "expected_version": v,
                "repair_action": "requeue_failed_task",
                "reschedule": True,
            },
        )
        # Simulate concurrent state transition into submission_unknown.
        self.gateway.tasks["brand_alpha:VID-212"]["canonical_status"] = "submission_unknown"
        conf = preview["confirmation"]
        with self.assertRaises(ContractFault) as ctx:
            self.gateway.invoke(
                self.owner,
                self.next_req_id("rep-conf"),
                {
                    "skill": "repair_and_schedule",
                    "phase": "confirm",
                    "project_id": "brand_alpha",
                    "confirmation_id": conf["confirmation_id"],
                    "proposal_sha256": conf["proposal_sha256"],
                    "expected_version": conf["expected_version"],
                    "accept_provider_charges": True,
                },
            )
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(self.gateway.reschedule_calls, [])

    def test_list_failures_flags_submission_unknown_non_repairable_and_no_auto_resubmit(self):
        res = self.gateway.invoke(
            self.owner,
            self.next_req_id("fail-lst"),
            {"skill": "list_failures", "project_id": "brand_alpha"},
        )
        unknowns = [i for i in res["items"] if i["canonical_status"] == "submission_unknown"]
        self.assertEqual(len(unknowns), 2)
        for item in unknowns:
            self.assertTrue(item["reconciliation_required"])
            self.assertFalse(item["automatic_resubmit"])
            self.assertFalse(item["repairable"])


class TwoPhaseConfirmationAndCostGateTests(BaseAdversarialCase):
    """Every mutating or paid action must enforce two-phase structured confirmation."""

    def _prepare_repair(self):
        v = self.gateway.version_token("brand_alpha:VID-212")
        return self.gateway.invoke(
            self.owner,
            self.next_req_id("prep-rep"),
            {
                "skill": "repair_and_schedule",
                "phase": "preview",
                "project_id": "brand_alpha",
                "target_type": "task_state",
                "target_id": "brand_alpha:VID-212",
                "expected_version": v,
                "repair_action": "requeue_failed_task",
                "reschedule": True,
            },
        )["confirmation"]

    def test_repair_confirm_rejects_missing_confirmation_id(self):
        conf = self._prepare_repair()
        with self.assertRaises(ContractFault) as ctx:
            self.gateway.invoke(
                self.owner,
                self.next_req_id("no-cid"),
                {
                    "skill": "repair_and_schedule",
                    "phase": "confirm",
                    "project_id": "brand_alpha",
                    "proposal_sha256": conf["proposal_sha256"],
                    "expected_version": conf["expected_version"],
                    "accept_provider_charges": True,
                },
            )
        self.assertEqual(ctx.exception.code, "CONFIRMATION_REQUIRED")
        self.assertEqual(self.gateway.reschedule_calls, [])

    def test_repair_confirm_rejects_mismatched_digest(self):
        conf = self._prepare_repair()
        with self.assertRaises(ContractFault) as ctx:
            self.gateway.invoke(
                self.owner,
                self.next_req_id("bad-dig"),
                {
                    "skill": "repair_and_schedule",
                    "phase": "confirm",
                    "project_id": "brand_alpha",
                    "confirmation_id": conf["confirmation_id"],
                    "proposal_sha256": "0" * 64,
                    "expected_version": conf["expected_version"],
                    "accept_provider_charges": True,
                },
            )
        self.assertEqual(ctx.exception.code, "CONFIRMATION_DIGEST_MISMATCH")
        self.assertEqual(self.gateway.reschedule_calls, [])

    def test_repair_confirm_rejects_expired_confirmation(self):
        conf = self._prepare_repair()
        self.now += 601.0
        with self.assertRaises(ContractFault) as ctx:
            self.gateway.invoke(
                self.owner,
                self.next_req_id("exp-cid"),
                {
                    "skill": "repair_and_schedule",
                    "phase": "confirm",
                    "project_id": "brand_alpha",
                    "confirmation_id": conf["confirmation_id"],
                    "proposal_sha256": conf["proposal_sha256"],
                    "expected_version": conf["expected_version"],
                    "accept_provider_charges": True,
                },
            )
        self.assertEqual(ctx.exception.code, "CONFIRMATION_EXPIRED")
        self.assertEqual(self.gateway.reschedule_calls, [])

    def test_natural_language_confirmation_rejected_for_all_write_skills(self):
        for phrase in NATURAL_LANGUAGE_NON_CONFIRMATIONS:
            with self.assertRaises(ContractFault) as ctx:
                self.gateway.invoke(
                    self.owner,
                    self.next_req_id("nl-rep"),
                    {
                        "skill": "repair_and_schedule",
                        "phase": "confirm",
                        "project_id": "brand_alpha",
                        "confirmation_id": phrase,
                        "proposal_sha256": phrase,
                        "expected_version": self.gateway.version_token("brand_alpha:VID-212"),
                        "accept_provider_charges": True,
                    },
                )
            self.assertEqual(ctx.exception.code, "EXPLICIT_STRUCTURED_CONFIRMATION_REQUIRED")

            with self.assertRaises(ContractFault) as ctx2:
                self.gateway.invoke(
                    self.owner,
                    self.next_req_id("nl-rev"),
                    {
                        "skill": "submit_review_decision",
                        "phase": "commit",
                        "project_id": "brand_alpha",
                        "confirmation_id": phrase,
                        "proposal_sha256": phrase,
                        "expected_version": self.gateway.version_token("brand_alpha:VID-219"),
                        "accept_provider_charges": True,
                    },
                )
            self.assertEqual(ctx2.exception.code, "EXPLICIT_STRUCTURED_CONFIRMATION_REQUIRED")
        self.assertEqual(self.gateway.reschedule_calls, [])
        self.assertEqual(self.gateway.provider_charge_calls, [])

    def test_paid_review_advance_rejects_without_explicit_charge_consent_or_cost_owner(self):
        v = self.gateway.version_token("brand_alpha:VID-219")
        prep = self.gateway.invoke(
            self.owner,
            self.next_req_id("rev-prep"),
            {
                "skill": "submit_review_decision",
                "phase": "prepare",
                "project_id": "brand_alpha",
                "task_key": "brand_alpha:VID-219",
                "expected_version": v,
                "stage": "script",
                "decision": "accept",
                "feedback": "通过并推进视频",
                "advance_next_stage": True,
            },
        )
        conf = prep["confirmation"]
        self.assertTrue(conf["cost_gate"]["may_incur_charges"])

        # Missing accept_provider_charges must fail.
        with self.assertRaises(ContractFault) as ctx:
            self.gateway.invoke(
                self.owner,
                self.next_req_id("rev-nocharge"),
                {
                    "skill": "submit_review_decision",
                    "phase": "commit",
                    "project_id": "brand_alpha",
                    "confirmation_id": conf["confirmation_id"],
                    "proposal_sha256": conf["proposal_sha256"],
                    "expected_version": conf["expected_version"],
                    "accept_provider_charges": False,
                },
            )
        self.assertEqual(ctx.exception.code, "EXPLICIT_PROVIDER_CHARGES_REQUIRED")
        self.assertEqual(self.gateway.provider_charge_calls, [])

        # Non-cost-owner preparing and attempting to commit paid advance must fail.
        prep_non_owner = self.gateway.invoke(
            self.non_cost_member,
            self.next_req_id("rev-nonown-p"),
            {
                "skill": "submit_review_decision",
                "phase": "prepare",
                "project_id": "brand_alpha",
                "task_key": "brand_alpha:VID-219",
                "expected_version": v,
                "stage": "script",
                "decision": "accept",
                "feedback": "成员尝试批准费用",
                "advance_next_stage": True,
            },
        )["confirmation"]
        with self.assertRaises(ContractFault) as ctx2:
            self.gateway.invoke(
                self.non_cost_member,
                self.next_req_id("rev-nonown-c"),
                {
                    "skill": "submit_review_decision",
                    "phase": "commit",
                    "project_id": "brand_alpha",
                    "confirmation_id": prep_non_owner["confirmation_id"],
                    "proposal_sha256": prep_non_owner["proposal_sha256"],
                    "expected_version": prep_non_owner["expected_version"],
                    "accept_provider_charges": True,
                },
            )
        self.assertEqual(ctx2.exception.status, 403)
        self.assertEqual(ctx2.exception.code, "COST_OWNER_AUTHORITY_REQUIRED")
        self.assertEqual(self.gateway.provider_charge_calls, [])

    def test_script_edit_commit_rejects_mismatched_draft_sha256_and_expired(self):
        v = self.gateway.version_token("brand_alpha:VID-219")
        preview = self.gateway.invoke(
            self.owner,
            self.next_req_id("ed-prev"),
            {
                "skill": "review_or_edit_script",
                "mode": "edit_preview",
                "project_id": "brand_alpha",
                "task_key": "brand_alpha:VID-219",
                "expected_version": v,
                "instruction": "去掉违禁词",
            },
        )
        conf = preview["confirmation"]
        with self.assertRaises(ContractFault) as ctx:
            self.gateway.invoke(
                self.owner,
                self.next_req_id("ed-badsha"),
                {
                    "skill": "review_or_edit_script",
                    "mode": "edit_commit",
                    "project_id": "brand_alpha",
                    "confirmation_id": conf["confirmation_id"],
                    "draft_sha256": "f" * 64,
                    "expected_version": conf["expected_version"],
                },
            )
        self.assertEqual(ctx.exception.code, "CONFIRMATION_DIGEST_MISMATCH")

        self.now += 601.0
        with self.assertRaises(ContractFault) as ctx2:
            self.gateway.invoke(
                self.owner,
                self.next_req_id("ed-exp"),
                {
                    "skill": "review_or_edit_script",
                    "mode": "edit_commit",
                    "project_id": "brand_alpha",
                    "confirmation_id": conf["confirmation_id"],
                    "draft_sha256": preview["diff"]["draft_sha256"],
                    "expected_version": conf["expected_version"],
                },
            )
        self.assertEqual(ctx2.exception.code, "CONFIRMATION_EXPIRED")

    def test_accept_script_rejected_when_unresolved_notes_exist(self):
        v = self.gateway.version_token("brand_alpha:VID-222")
        with self.assertRaises(ContractFault) as ctx:
            self.gateway.invoke(
                self.owner,
                self.next_req_id("unres"),
                {
                    "skill": "submit_review_decision",
                    "phase": "prepare",
                    "project_id": "brand_alpha",
                    "task_key": "brand_alpha:VID-222",
                    "expected_version": v,
                    "stage": "script",
                    "decision": "accept",
                    "feedback": "直接通过",
                },
            )
        self.assertEqual(ctx.exception.code, "UNRESOLVED_NOTES_EXIST")


class IsolationOptimisticLockAndIdempotencyTests(BaseAdversarialCase):
    """Cross-project isolation, optimistic locking, and request_id idempotency."""

    def test_cross_project_access_denied_for_all_six_actions(self):
        payloads = [
            {"skill": "project_stats", "project_id": "brand_alpha"},
            {"skill": "list_failures", "project_id": "brand_alpha"},
            {
                "skill": "repair_and_schedule",
                "phase": "preview",
                "project_id": "brand_alpha",
                "target_type": "task_state",
                "target_id": "brand_alpha:VID-212",
                "expected_version": self.gateway.version_token("brand_alpha:VID-212"),
            },
            {"skill": "list_reviews", "project_id": "brand_alpha", "stage": "all"},
            {
                "skill": "review_or_edit_script",
                "mode": "ai_prereview",
                "project_id": "brand_alpha",
                "task_key": "brand_alpha:VID-219",
                "expected_version": self.gateway.version_token("brand_alpha:VID-219"),
            },
            {
                "skill": "submit_review_decision",
                "phase": "prepare",
                "project_id": "brand_alpha",
                "task_key": "brand_alpha:VID-219",
                "expected_version": self.gateway.version_token("brand_alpha:VID-219"),
                "stage": "script",
                "decision": "reject",
                "feedback": "越权退回",
            },
        ]
        for body in payloads:
            with self.assertRaises(ContractFault) as ctx:
                self.gateway.invoke(self.other_project_user, self.next_req_id("xproj"), body)
            self.assertEqual(ctx.exception.status, 403, f"Cross-project leak on {body['skill']}")
            self.assertEqual(ctx.exception.code, "AUTH_PROJECT_DENIED")

    def test_cross_project_task_key_injection_denied(self):
        with self.assertRaises(ContractFault) as ctx:
            self.gateway.invoke(
                self.owner,
                self.next_req_id("xtkey"),
                {
                    "skill": "review_or_edit_script",
                    "mode": "ai_prereview",
                    "project_id": "brand_alpha",
                    "task_key": "brand_beta:VID-901",
                    "expected_version": self.gateway.version_token("brand_beta:VID-901"),
                },
            )
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(ctx.exception.code, "TASK_PROJECT_DENIED")

    def test_stale_version_token_rejected_on_all_versioned_actions(self):
        stale = "rev-1-0000000000000000"
        for body in (
            {
                "skill": "repair_and_schedule",
                "phase": "preview",
                "project_id": "brand_alpha",
                "target_type": "task_state",
                "target_id": "brand_alpha:VID-212",
                "expected_version": stale,
            },
            {
                "skill": "review_or_edit_script",
                "mode": "edit_preview",
                "project_id": "brand_alpha",
                "task_key": "brand_alpha:VID-219",
                "expected_version": stale,
                "instruction": "修改开头",
            },
            {
                "skill": "submit_review_decision",
                "phase": "prepare",
                "project_id": "brand_alpha",
                "task_key": "brand_alpha:VID-219",
                "expected_version": stale,
                "stage": "script",
                "decision": "accept",
                "feedback": "通过",
            },
        ):
            with self.assertRaises(ContractFault) as ctx:
                self.gateway.invoke(self.owner, self.next_req_id("stale"), body)
            self.assertEqual(ctx.exception.status, 409)
            self.assertEqual(ctx.exception.code, "OPTIMISTIC_LOCK_VERSION_CONFLICT")

    def test_duplicate_request_id_conflict_vs_identical_replay(self):
        req_id = self.next_req_id("idem-same")
        body_a = {"skill": "project_stats", "project_id": "brand_alpha", "window": "7d"}
        body_b = {"skill": "project_stats", "project_id": "brand_alpha", "window": "30d"}

        first = self.gateway.invoke(self.owner, req_id, body_a)
        replay = self.gateway.invoke(self.owner, req_id, body_a)
        self.assertTrue(replay.get("replayed"))
        self.assertEqual(first["metrics"], replay["metrics"])

        with self.assertRaises(ContractFault) as ctx:
            self.gateway.invoke(self.owner, req_id, body_b)
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_cross_principal_confirmation_hijack_rejected(self):
        v = self.gateway.version_token("brand_alpha:VID-219")
        conf = self.gateway.invoke(
            self.owner,
            self.next_req_id("hij-prep"),
            {
                "skill": "review_or_edit_script",
                "mode": "edit_preview",
                "project_id": "brand_alpha",
                "task_key": "brand_alpha:VID-219",
                "expected_version": v,
                "instruction": "修改第一句",
            },
        )["confirmation"]
        with self.assertRaises(ContractFault) as ctx:
            self.gateway.invoke(
                self.non_cost_member,
                self.next_req_id("hij-comm"),
                {
                    "skill": "review_or_edit_script",
                    "mode": "edit_commit",
                    "project_id": "brand_alpha",
                    "confirmation_id": conf["confirmation_id"],
                    "draft_sha256": conf["proposal_sha256"],
                    "expected_version": conf["expected_version"],
                },
            )
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(ctx.exception.code, "CONFIRMATION_PRINCIPAL_MISMATCH")


class AllSixActionsRedactionTests(BaseAdversarialCase):
    """Verify all 6 actions return valid op-workbench-v1 briefs and leak zero sensitive fields."""

    def test_all_six_actions_redacted_and_contract_valid(self):
        # 1. project_stats
        stats = self.gateway.invoke(
            self.owner,
            self.next_req_id("red-stat"),
            {"skill": "project_stats", "project_id": "brand_alpha", "window": "7d"},
        )
        assert_task_brief_v1(self, stats["brief"], context="project_stats")
        assert_redacted(self, stats, context="project_stats")

        # 2. list_failures
        failures = self.gateway.invoke(
            self.owner,
            self.next_req_id("red-fail"),
            {"skill": "list_failures", "project_id": "brand_alpha"},
        )
        assert_task_brief_v1(self, failures["brief"], context="list_failures")
        assert_redacted(self, failures, context="list_failures")

        # 3. repair_and_schedule (preview + confirm)
        v_212 = self.gateway.version_token("brand_alpha:VID-212")
        rep_prev = self.gateway.invoke(
            self.owner,
            self.next_req_id("red-repp"),
            {
                "skill": "repair_and_schedule",
                "phase": "preview",
                "project_id": "brand_alpha",
                "target_type": "task_state",
                "target_id": "brand_alpha:VID-212",
                "expected_version": v_212,
                "repair_action": "requeue_failed_task",
                "reschedule": True,
            },
        )
        assert_task_brief_v1(self, rep_prev["brief"], context="repair_preview")
        assert_redacted(self, rep_prev, context="repair_preview")

        rep_conf = self.gateway.invoke(
            self.owner,
            self.next_req_id("red-repc"),
            {
                "skill": "repair_and_schedule",
                "phase": "confirm",
                "project_id": "brand_alpha",
                "confirmation_id": rep_prev["confirmation"]["confirmation_id"],
                "proposal_sha256": rep_prev["confirmation"]["proposal_sha256"],
                "expected_version": rep_prev["confirmation"]["expected_version"],
                "accept_provider_charges": True,
            },
        )
        assert_task_brief_v1(self, rep_conf["brief"], context="repair_confirm")
        assert_redacted(self, rep_conf, context="repair_confirm")

        # 4. list_reviews (script + storyboard + video)
        for stage in ("all", "script", "storyboard", "video"):
            reviews = self.gateway.invoke(
                self.owner,
                self.next_req_id(f"red-rv-{stage}"),
                {"skill": "list_reviews", "project_id": "brand_alpha", "stage": stage},
            )
            assert_task_brief_v1(self, reviews["brief"], context=f"list_reviews:{stage}")
            assert_redacted(self, reviews, context=f"list_reviews:{stage}")
            if stage == "all":
                stages_seen = {item["stage"] for item in reviews["items"]}
                self.assertEqual(stages_seen, {"script", "storyboard", "video"})

        # 5. review_or_edit_script (ai_prereview + edit_preview + edit_commit)
        v_219 = self.gateway.version_token("brand_alpha:VID-219")
        prereview = self.gateway.invoke(
            self.owner,
            self.next_req_id("red-pre"),
            {
                "skill": "review_or_edit_script",
                "mode": "ai_prereview",
                "project_id": "brand_alpha",
                "task_key": "brand_alpha:VID-219",
                "expected_version": v_219,
            },
        )
        self.assertFalse(prereview["is_human_approval"])
        self.assertFalse(prereview["content_is_instruction"])
        assert_task_brief_v1(self, prereview["brief"], context="ai_prereview")
        assert_redacted(self, prereview, context="ai_prereview")

        ed_prev = self.gateway.invoke(
            self.owner,
            self.next_req_id("red-edp"),
            {
                "skill": "review_or_edit_script",
                "mode": "edit_preview",
                "project_id": "brand_alpha",
                "task_key": "brand_alpha:VID-219",
                "expected_version": v_219,
                "instruction": "去掉违禁词并修正保温时长",
            },
        )
        assert_task_brief_v1(self, ed_prev["brief"], context="edit_preview")
        assert_redacted(self, ed_prev, context="edit_preview")

        ed_comm = self.gateway.invoke(
            self.owner,
            self.next_req_id("red-edc"),
            {
                "skill": "review_or_edit_script",
                "mode": "edit_commit",
                "project_id": "brand_alpha",
                "confirmation_id": ed_prev["confirmation"]["confirmation_id"],
                "draft_sha256": ed_prev["diff"]["draft_sha256"],
                "expected_version": ed_prev["confirmation"]["expected_version"],
            },
        )
        self.assertFalse(ed_comm["video_spending_approved"])
        assert_task_brief_v1(self, ed_comm["brief"], context="edit_commit")
        assert_redacted(self, ed_comm, context="edit_commit")

        # 6. submit_review_decision (prepare + commit)
        v_219_new = ed_comm["new_version_token"]
        dec_prep = self.gateway.invoke(
            self.owner,
            self.next_req_id("red-decp"),
            {
                "skill": "submit_review_decision",
                "phase": "prepare",
                "project_id": "brand_alpha",
                "task_key": "brand_alpha:VID-219",
                "expected_version": v_219_new,
                "stage": "script",
                "decision": "accept",
                "feedback": "合规通过并推进视频",
                "advance_next_stage": True,
            },
        )
        assert_task_brief_v1(self, dec_prep["brief"], context="review_prepare")
        assert_redacted(self, dec_prep, context="review_prepare")

        dec_comm = self.gateway.invoke(
            self.owner,
            self.next_req_id("red-decc"),
            {
                "skill": "submit_review_decision",
                "phase": "commit",
                "project_id": "brand_alpha",
                "confirmation_id": dec_prep["confirmation"]["confirmation_id"],
                "proposal_sha256": dec_prep["confirmation"]["proposal_sha256"],
                "expected_version": dec_prep["confirmation"]["expected_version"],
                "accept_provider_charges": True,
            },
        )
        assert_task_brief_v1(self, dec_comm["brief"], context="review_commit")
        assert_redacted(self, dec_comm, context="review_commit")


class LiveV03AgentAPIAdversarialTests(unittest.TestCase):
    """Auto-activating adversarial checks against video_factory.agent_api.AgentAPI.

    Before Codex lands M1-Part2 routes, routes returning AGENT_ROUTE_NOT_AVAILABLE
    are cleanly skipped unless VF_REQUIRE_V03_ROUTES=1 is set in CI.
    Once Codex merges M1-Part2, these tests automatically execute against the real
    AgentAPI and synthetic SQLite RuntimeStore.
    """

    def setUp(self):
        from video_factory import automation
        from video_factory.agent_api import AgentAPI
        from video_factory.feishu_bridge import meta, save
        from video_factory.runtime_store import RuntimeStore, canonical, fingerprint
        from video_factory.scheduler import Scheduler

        self._tmp = tempfile.TemporaryDirectory(prefix="vf-v03-adv-")
        root = Path(self._tmp.name)
        root.chmod(0o700)
        self.store = RuntimeStore.install(root, "v03_adv_deploy", "synthetic-admin-password-14+")
        self.admin = self.store.login("admin", "synthetic-admin-password-14+")["token"]
        project_cfg = {"video_route": "deferred", "credential_ref": "secret:fixture", "billing_owner": "fixture"}
        self.store.put_project(self.admin, "brand_alpha", project_cfg)
        self.store.put_project(self.admin, "brand_beta", project_cfg)
        self.alpha_token = automation.issue(self.store, self.admin, "brand_alpha", 2, scope="agent_plan")["token"]
        self.beta_token = automation.issue(self.store, self.admin, "brand_beta", 2, scope="agent_plan")["token"]
        self.principal = "auth-" + "a" * 64
        self.other_principal = "auth-" + "b" * 64
        self.delegated_token = automation.issue(self.store, self.admin, "brand_alpha", 2, scope="agent_plan")["token"]
        key_id = hashlib.sha256(self.delegated_token.encode()).hexdigest()
        with self.store.connect() as db:
            grant = meta(db, "automation:key:" + key_id)
            grant["workbench_principal"] = self.principal
            grant["workbench_roles"] = ["repair", "review", "edit", "cost", "video_admin"]
            save(db, "automation:key:" + key_id, grant)
            save(db, "workbench:delegation:brand_alpha", {self.principal: ["repair", "review", "edit", "cost", "video_admin"]})
            cfg_digest = db.execute("SELECT digest FROM projects WHERE id='brand_alpha'").fetchone()[0]
            for tid, state, script in (
                ("VID_UNK_V", "submission_unknown", "未知视频提交脚本"),
                ("VID_UNK_S", "script_submission_unknown", "未知脚本提交"),
                ("VID_FAIL", "failed", "失败可修复任务"),
                ("VID_REV", "awaiting_script_review", "全网绝对第一保温杯，长达 24 小时保温。"),
            ):
                payload = canonical({"sku_id": "sku_white_cup", "script": script})
                db.execute(
                    "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,NULL,NULL)",
                    ("brand_alpha", tid, 1, payload, fingerprint(payload), cfg_digest, state),
                )
        self.api = AgentAPI(self.store, "brand_alpha", Scheduler(self.store, "brand_alpha"), plans_enabled=True)
        self.require_routes = os.environ.get("VF_REQUIRE_V03_ROUTES") == "1"

    def tearDown(self):
        self._tmp.cleanup()

    def _call_or_skip_unlanded(self, route, token, body, *, principal=None):
        from video_factory.runtime_store import RuntimeFault

        try:
            return self.api.call(route, token, body, principal=principal)
        except RuntimeFault as err:
            if str(err) == "AGENT_ROUTE_NOT_AVAILABLE" and not self.require_routes:
                self.skipTest(f"v0.3 route {route} not yet landed by M1-Part2")
            raise

    def test_live_cross_project_token_denied_on_v03_routes(self):
        from video_factory.runtime_store import RuntimeFault

        for route, body in (
            ("/v1/agent/stats", {"project": "brand_alpha"}),
            ("/v1/agent/failures", {"project": "brand_alpha"}),
            ("/v1/agent/reviews", {"project": "brand_alpha"}),
        ):
            # First probe whether route exists using valid alpha token.
            result = self._call_or_skip_unlanded(route, self.alpha_token, body)
            assert_redacted(self, result, context=f"live:{route}")
            # Cross-project token must be denied.
            with self.assertRaises(RuntimeFault) as ctx:
                self.api.call(route, self.beta_token, body)
            self.assertTrue(str(ctx.exception).startswith("AUTH_"))

    def test_live_submission_unknown_hard_rejected_on_repair_preview(self):
        from video_factory.runtime_store import RuntimeFault

        failures = self._call_or_skip_unlanded("/v1/agent/failures", self.alpha_token, {"project": "brand_alpha"})
        assert_redacted(self, failures, context="live:failures")
        by_id = {item["task_id"]: item for item in failures.get("items", [])}
        for tid in ("VID_UNK_V", "VID_UNK_S"):
            self.assertIn(tid, by_id)
            item = by_id[tid]
            self.assertTrue(item["reconciliation_required"])
            self.assertFalse(item["automatic_resubmit"])
            with self.assertRaises(RuntimeFault) as ctx:
                self._call_or_skip_unlanded(
                    "/v1/agent/repair/preview",
                    self.delegated_token,
                    {
                        "project": "brand_alpha",
                        "target_type": "task_state",
                        "target_id": item["task_key"],
                        "expected_version": item["version_token"],
                        "repair_action": "requeue_failed_task",
                        "reschedule": True,
                    },
                    principal=self.principal,
                )
            self.assertNotEqual(str(ctx.exception), "AGENT_ROUTE_NOT_AVAILABLE")

    def test_live_repair_and_review_commit_reject_missing_or_nl_confirmation(self):
        from video_factory.runtime_store import RuntimeFault

        stale_version = "rev-1-" + "0" * 64
        for route, body in (
            (
                "/v1/agent/repair/apply",
                {
                    "project": "brand_alpha",
                    "confirmation_id": "确认",
                    "proposal_sha256": "0" * 64,
                    "expected_version": stale_version,
                    "accept_provider_charges": True,
                },
            ),
            (
                "/v1/agent/review/commit",
                {
                    "project": "brand_alpha",
                    "confirmation_id": "好",
                    "proposal_sha256": "0" * 64,
                    "expected_version": stale_version,
                    "accept_provider_charges": True,
                },
            ),
            (
                "/v1/agent/script/edit-commit",
                {
                    "project": "brand_alpha",
                    "confirmation_id": "批准",
                    "draft_sha256": "0" * 64,
                    "expected_version": stale_version,
                },
            ),
            (
                "/v1/agent/plan/approve",
                {
                    "project": "brand_alpha",
                    "phase": "commit",
                    "plan_type": "video",
                    "plan_id": "0" * 64,
                    "plan_sha256": "0" * 64,
                    "confirmation_id": "cf_" + "0" * 48,
                    "expected_digest": "0" * 64,
                    "expected_version": stale_version,
                    "accept_provider_charges": False,
                },
            ),
        ):
            with self.assertRaises(RuntimeFault) as ctx:
                self._call_or_skip_unlanded(route, self.delegated_token, body, principal=self.principal)
            self.assertNotEqual(str(ctx.exception), "AGENT_ROUTE_NOT_AVAILABLE")

            # Undelegated principal must be rejected with AUTH_WORKBENCH_ROLE_DENIED.
            with self.assertRaises(RuntimeFault) as ctx2:
                self._call_or_skip_unlanded(route, self.delegated_token, body, principal=self.other_principal)
            self.assertEqual(str(ctx2.exception), "AUTH_WORKBENCH_ROLE_DENIED")

    def test_live_stale_version_conflict_rejected(self):
        from video_factory.runtime_store import RuntimeFault

        stale_version = "rev-1-" + "f" * 64
        for route, body, principal in (
            (
                "/v1/agent/script/prereview",
                {"project": "brand_alpha", "task_key": "brand_alpha:VID_REV", "expected_version": stale_version},
                None,
            ),
            (
                "/v1/agent/review/prepare",
                {
                    "project": "brand_alpha",
                    "task_key": "brand_alpha:VID_REV",
                    "expected_version": stale_version,
                    "stage": "script",
                    "decision": "accept",
                    "feedback": "ok",
                },
                self.principal,
            ),
        ):
            with self.assertRaises(RuntimeFault) as ctx:
                self._call_or_skip_unlanded(
                    route,
                    self.delegated_token if principal else self.alpha_token,
                    body,
                    principal=principal,
                )
            self.assertEqual(str(ctx.exception), "WORKBENCH_VERSION_CHANGED")


if __name__ == "__main__":
    unittest.main()
