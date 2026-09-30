"""No-network rehearsal of the human-review production contract.

This is deliberately not a Feishu or n8n executor. It makes the state machine
testable before any paid provider, live Base, or active workflow is involved.
"""

from dataclasses import dataclass, field
import hashlib
import json


ROUTES = {
    "script_production": ("脚本待生产", "脚本待审核", "script"),
    "video_production": ("视频待生产", "视频待审核", "video"),
    "script_repair": ("脚本返修", "脚本待审核", "script"),
    "video_repair": ("视频返修", "视频待审核", "video"),
}


class CanaryError(ValueError):
    pass


def _sha(value):
    body = json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


@dataclass
class CanaryTask:
    task_id: str
    sku_id: str
    truth_source: str
    direction: str
    status: str = "脚本待生产"
    script_version: str | None = None
    video_version: str | None = None
    approved_script_version: str | None = None
    review_feedback: str = ""
    review_target_version: str | None = None
    versions: list = field(default_factory=list)
    reviews: list = field(default_factory=list)
    executions: list = field(default_factory=list)


class CanaryRuntime:
    """Single-process, single-owner rehearsal with deterministic fake outputs."""

    def __init__(self, tasks):
        self.tasks = {}
        for task in tasks:
            if not isinstance(task, CanaryTask) or task.task_id in self.tasks:
                raise CanaryError("TASK_ID_DUPLICATE_OR_INVALID")
            self.tasks[task.task_id] = task
        self._claims = set()

    def dispatch(self, route, task_id):
        if route not in ROUTES or task_id not in self.tasks:
            raise CanaryError("UNKNOWN_ROUTE_OR_TASK")
        task = self.tasks[task_id]
        expected, next_status, kind = ROUTES[route]
        if task.status != expected:
            raise CanaryError("TASK_NOT_IN_ROUTE_QUEUE")
        if not all((task.sku_id, task.truth_source, task.direction)):
            raise CanaryError("PRODUCT_TRUTH_OR_DIRECTION_MISSING")
        if kind == "video" and task.approved_script_version != task.script_version:
            raise CanaryError("CURRENT_SCRIPT_NOT_APPROVED")
        if route.endswith("repair"):
            current = task.script_version if kind == "script" else task.video_version
            if not current or task.review_target_version != current or not task.review_feedback:
                raise CanaryError("BOUND_HUMAN_REPAIR_FEEDBACK_REQUIRED")
        frozen = {
            "route": route, "task_id": task_id, "sku_id": task.sku_id,
            "truth_source": task.truth_source, "direction": task.direction,
            "script_version": task.script_version if route != "script_production" else None,
            "video_version": task.video_version if route == "video_repair" else None,
            "feedback": task.review_feedback if route.endswith("repair") else "",
        }
        claim = _sha(frozen)
        if claim in self._claims:
            raise CanaryError("DUPLICATE_INPUT_CLAIM")
        self._claims.add(claim)
        version_id = f"{task_id}-{kind}-{len(task.versions) + 1:03d}"
        # A receipt and a placeholder are created; no model is contacted.
        output = f"CANARY_ONLY:{route}:{task_id}:{claim[:12]}"
        previous = task.script_version if kind == "script" else task.video_version
        task.versions.append({"version_id": version_id, "kind": kind,
                              "content": output, "previous_version": previous,
                              "input_sha256": claim})
        if kind == "script":
            task.script_version = version_id
            task.approved_script_version = None
        else:
            task.video_version = version_id
        task.review_feedback = ""
        task.review_target_version = None
        task.status = next_status
        task.executions.append({"route": route, "input_sha256": claim,
                                "version_id": version_id,
                                "provider_request_id": None,
                                "reuse_evidence": "CANARY_ONLY_NO_PROVIDER"})
        return task.executions[-1]

    def review(self, task_id, target_version, decision, reviewer, feedback=""):
        if task_id not in self.tasks:
            raise CanaryError("UNKNOWN_TASK")
        task = self.tasks[task_id]
        if task.status not in {"脚本待审核", "视频待审核"}:
            raise CanaryError("TASK_NOT_AWAITING_REVIEW")
        kind = "script" if task.status == "脚本待审核" else "video"
        current = task.script_version if kind == "script" else task.video_version
        if target_version != current:
            raise CanaryError("STALE_REVIEW_VERSION")
        if decision not in {"pass", "reject"} or not reviewer:
            raise CanaryError("HUMAN_REVIEW_DECISION_REQUIRED")
        if decision == "reject" and not feedback.strip():
            raise CanaryError("REJECTION_FEEDBACK_REQUIRED")
        event_id = f"{task_id}-review-{len(task.reviews) + 1:03d}"
        task.reviews.append({"event_id": event_id, "target_version": current,
                             "decision": decision, "reviewer": reviewer,
                             "feedback": feedback})
        if decision == "pass":
            if kind == "script":
                task.approved_script_version = current
                task.status = "视频待生产"
            else:
                task.status = "视频审核通过"
            task.review_feedback = ""
            task.review_target_version = None
        else:
            task.review_feedback = feedback
            task.review_target_version = current
            task.status = "脚本返修" if kind == "script" else "视频返修"
        return task.reviews[-1]
