"""Deterministic, disabled n8n dispatch templates for an isolated project.

The HTTP endpoint is a *required future worker service*, not implemented here.
Generating these JSON objects never imports or activates workflows.
"""

import json
import re
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5


ROUTES = ("script_production", "video_production", "script_repair",
          "video_repair", "health_notify")
_REF = re.compile(r"[A-Za-z0-9_-]{2,128}\Z")


def _worker_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("HTTPS_WORKER_ORIGIN_REQUIRED")
    return value.rstrip("/")


def compile_workflow_templates(blueprint, worker_origin, credential_id,
                               credential_name="Video Factory worker", minutes=3):
    """Return import-shaped JSON; caller must independently validate in n8n.

    No credential value is accepted. The given n8n credential *reference* must
    already exist in the isolated target and be scoped to this worker.
    """
    origin = _worker_url(worker_origin)
    if not isinstance(credential_id, str) or not _REF.fullmatch(credential_id):
        raise ValueError("CREDENTIAL_REFERENCE_REQUIRED")
    if not isinstance(credential_name, str) or not credential_name.strip():
        raise ValueError("CREDENTIAL_NAME_REQUIRED")
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 59:
        raise ValueError("SCAN_INTERVAL_INVALID")
    project_id = blueprint.get("project_id")
    if not isinstance(project_id, str) or not _REF.fullmatch(project_id):
        raise ValueError("PROJECT_ID_INVALID")
    resources = {row["key"]: row["spec"] for row in blueprint.get("resources", [])
                 if row.get("kind") == "n8n_workflow"}
    if set(resources) != {f"{project_id}.{route}" for route in ROUTES}:
        raise ValueError("WORKFLOW_BLUEPRINT_INCOMPLETE")
    templates = {}
    for route in ROUTES:
        key = f"{project_id}.{route}"
        start, dispatch = "Scan queue", f"Dispatch {route}"
        interval = minutes if route != "health_notify" else max(minutes, 15)
        payload = {"project_id": project_id, "route": route,
                   "max_claims": 1, "mode": "normal" if route.endswith("production")
                   else "repair" if route.endswith("repair") else "health"}
        templates[key] = {
            "name": resources[key]["name"],
            "nodes": [
                {"id": str(uuid5(NAMESPACE_URL, key + ":schedule")),
                 "name": start, "type": "n8n-nodes-base.scheduleTrigger",
                 "typeVersion": 1.2, "position": [0, 0],
                 "parameters": {"rule": {"interval": [{"field": "minutes",
                                                      "minutesInterval": interval}]}}},
                {"id": str(uuid5(NAMESPACE_URL, key + ":dispatch")),
                 "name": dispatch, "type": "n8n-nodes-base.httpRequest",
                 "typeVersion": 4.2, "position": [280, 0],
                 "parameters": {"method": "POST",
                                "url": f"{origin}/v1/projects/{project_id}/dispatch/{route}",
                                "authentication": "genericCredentialType",
                                "genericAuthType": "httpHeaderAuth",
                                "sendBody": True, "specifyBody": "json",
                                "jsonBody": json.dumps(payload, ensure_ascii=False,
                                                       separators=(",", ":")),
                                "options": {"timeout": 30000}},
                 "credentials": {"httpHeaderAuth": {"id": credential_id,
                                                    "name": credential_name}}},
            ],
            "connections": {start: {"main": [[{"node": dispatch, "type": "main",
                                                "index": 0}]]}},
            "settings": {"executionOrder": "v1"}, "active": False,
        }
    return templates
