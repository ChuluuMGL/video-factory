"""Static, offline configuration contract for a new Video Factory project."""

import re
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
PROJECT_ID = re.compile(r"[a-z][a-z0-9_-]{2,47}\Z")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")
STATUS_KEYS = (
    "script_queued", "script_review", "video_queued", "video_review",
    "script_repair", "video_repair",
)


def draft_config(project_id):
    if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
        raise ValueError("PROJECT_ID_INVALID")
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "installation_state": "draft",
        "task_source": {
            "kind": "feishu_base", "base_token": "", "tasks_table_id": "",
            "credential_env": "", "business_id_field": "", "status_field": "",
        },
        "scheduler": {"kind": "n8n", "url": "", "credential_env": ""},
        "human_review": {
            "required": True, "script_field": "", "feedback_field": "",
            "current_video_field": "", "video_history_field": "",
        },
        "status_map": {key: "" for key in STATUS_KEYS},
        "model_route": {"kind": "none", "endpoint": "", "credential_env": ""},
        "execution": {"enabled": False, "mode": "disabled"},
    }


def _object(value, name, errors):
    if not isinstance(value, dict):
        errors.append(name + "_OBJECT_REQUIRED")
        return {}
    return value


def inspect_config(value):
    """Assess only static configuration; never contacts a live integration."""
    errors = []
    missing = []
    if not isinstance(value, dict):
        return {"schema_valid": False, "errors": ["CONFIG_OBJECT_REQUIRED"],
                "missing": [], "configuration_complete": False,
                "runtime_status": "not_running", "execute_allowed": False}
    expected = {"schema_version", "project_id", "installation_state", "task_source",
                "scheduler", "human_review", "status_map", "model_route", "execution"}
    if set(value) != expected:
        errors.append("TOP_LEVEL_FIELDS_CHANGED")
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append("SCHEMA_VERSION_UNSUPPORTED")
    if not isinstance(value.get("project_id"), str) or not PROJECT_ID.fullmatch(value["project_id"]):
        errors.append("PROJECT_ID_INVALID")
    if value.get("installation_state") != "draft":
        errors.append("INSTALLATION_STATE_UNSUPPORTED")

    task = _object(value.get("task_source"), "TASK_SOURCE", errors)
    scheduler = _object(value.get("scheduler"), "SCHEDULER", errors)
    review = _object(value.get("human_review"), "HUMAN_REVIEW", errors)
    statuses = _object(value.get("status_map"), "STATUS_MAP", errors)
    model = _object(value.get("model_route"), "MODEL_ROUTE", errors)
    execution = _object(value.get("execution"), "EXECUTION", errors)
    if set(task) != {"kind", "base_token", "tasks_table_id", "credential_env",
                     "business_id_field", "status_field"}:
        errors.append("TASK_SOURCE_FIELDS_CHANGED")
    if set(scheduler) != {"kind", "url", "credential_env"}:
        errors.append("SCHEDULER_FIELDS_CHANGED")
    if set(review) != {"required", "script_field", "feedback_field",
                       "current_video_field", "video_history_field"}:
        errors.append("HUMAN_REVIEW_FIELDS_CHANGED")
    if set(model) != {"kind", "endpoint", "credential_env"}:
        errors.append("MODEL_ROUTE_FIELDS_CHANGED")
    if task.get("kind") != "feishu_base":
        errors.append("TASK_SOURCE_KIND_UNSUPPORTED")
    if scheduler.get("kind") != "n8n":
        errors.append("SCHEDULER_KIND_UNSUPPORTED")
    if model.get("kind") not in ("none", "api", "self_hosted"):
        errors.append("MODEL_ROUTE_KIND_UNSUPPORTED")
    if review.get("required") is not True:
        errors.append("HUMAN_REVIEW_MUST_BE_REQUIRED")
    if execution != {"enabled": False, "mode": "disabled"}:
        errors.append("EXECUTION_MUST_REMAIN_DISABLED_IN_BOOTSTRAP")
    if set(statuses) != set(STATUS_KEYS):
        errors.append("STATUS_MAP_KEYS_CHANGED")

    for group_name, group, keys in (
        ("task_source", task, ("base_token", "tasks_table_id", "business_id_field", "status_field")),
        ("scheduler", scheduler, ("url",)),
        ("human_review", review, ("script_field", "feedback_field", "current_video_field", "video_history_field")),
        ("status_map", statuses, STATUS_KEYS),
    ):
        for key in keys:
            cell = group.get(key)
            if not isinstance(cell, str):
                errors.append(group_name.upper() + "_" + key.upper() + "_STRING_REQUIRED")
            elif not cell.strip():
                missing.append(group_name + "." + key)
    for group_name, group in (("task_source", task), ("scheduler", scheduler)):
        cell = group.get("credential_env")
        if not isinstance(cell, str):
            errors.append(group_name.upper() + "_CREDENTIAL_ENV_STRING_REQUIRED")
        elif not cell:
            missing.append(group_name + ".credential_env")
        elif not ENV_NAME.fullmatch(cell):
            errors.append(group_name.upper() + "_CREDENTIAL_ENV_NAME_INVALID")
    for group_name, endpoint in (("scheduler", scheduler.get("url")),
                                 ("model_route", model.get("endpoint"))):
        if not isinstance(endpoint, str):
            errors.append(group_name.upper() + "_ENDPOINT_STRING_REQUIRED")
        elif endpoint:
            try:
                parsed = urlsplit(endpoint)
                unsafe = (parsed.scheme not in ("http", "https") or not parsed.netloc
                          or parsed.username or parsed.password or parsed.query or parsed.fragment)
            except ValueError:
                unsafe = True
            if unsafe:
                errors.append(group_name.upper() + "_ENDPOINT_UNSAFE")
    if model.get("kind") == "none":
        missing.append("model_route.kind")
    elif not model.get("endpoint"):
        missing.append("model_route.endpoint")
    model_env = model.get("credential_env")
    if not isinstance(model_env, str) or (model_env and not ENV_NAME.fullmatch(model_env)):
        errors.append("MODEL_ROUTE_CREDENTIAL_ENV_NAME_INVALID")
    if model.get("kind") == "api" and not model_env:
        missing.append("model_route.credential_env")

    return {
        "schema_valid": not errors,
        "errors": sorted(set(errors)),
        "missing": sorted(set(missing)),
        "configuration_complete": not errors and not missing,
        "runtime_status": "not_running",
        "execute_allowed": False,
        "reason": "ADAPTERS_NOT_WIRED_OR_ACCEPTED",
    }
