"""Read-only discovery of an explicitly selected Feishu Base and n8n instance.

The returned inventory is evidence for a later installation decision, not an
authorization to create, update, activate, or run any resource.
"""

import json
import subprocess
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class DiscoveryError(ValueError):
    """A discovery response was incomplete, unsafe, or ambiguous."""


def _items(payload):
    if not isinstance(payload, dict):
        raise DiscoveryError("DISCOVERY_RESPONSE_OBJECT_REQUIRED")
    data = payload.get("data", payload)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise DiscoveryError("DISCOVERY_ITEMS_MISSING")
    return data["items"], data


def _more(data, count, limit):
    flag = data.get("has_more")
    if flag is not None:
        if not isinstance(flag, bool):
            raise DiscoveryError("DISCOVERY_HAS_MORE_INVALID")
        return flag
    return count == limit


def _lark_json(args, runner=None):
    command = ["lark-cli", "base", *args, "--as", "user", "--format", "json"]
    if runner is None:
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DiscoveryError("LARK_CLI_UNAVAILABLE_OR_TIMEOUT") from error
        if result.returncode != 0:
            # CLI stderr can contain resource identifiers or context. Never
            # echo it together with an auth-bearing command line.
            raise DiscoveryError("LARK_READ_FAILED")
        output = result.stdout
    else:
        output = runner(command)
    try:
        payload = json.loads(output) if isinstance(output, str) else output
    except (TypeError, json.JSONDecodeError) as error:
        raise DiscoveryError("LARK_JSON_INVALID") from error
    if not isinstance(payload, dict):
        raise DiscoveryError("LARK_JSON_OBJECT_REQUIRED")
    if payload.get("code") not in (None, 0) or payload.get("success") is False:
        raise DiscoveryError("LARK_READ_FAILED")
    return payload


def _paged_lark(base_token, shortcut, table_id=None, runner=None):
    limit = 100 if shortcut == "+table-list" else 200
    collected = []
    seen = set()
    offset = 0
    for _ in range(100):
        args = [shortcut, "--base-token", base_token, "--limit", str(limit),
                "--offset", str(offset)]
        if table_id is not None:
            args += ["--table-id", table_id]
        items, data = _items(_lark_json(args, runner))
        for item in items:
            if not isinstance(item, dict):
                raise DiscoveryError("LARK_ITEM_INVALID")
            identity = item.get("table_id") if table_id is None else item.get("field_id")
            if not isinstance(identity, str) or not identity or identity in seen:
                raise DiscoveryError("LARK_ITEM_ID_MISSING_OR_DUPLICATE")
            seen.add(identity)
            collected.append(item)
        if not _more(data, len(items), limit):
            return collected
        if not items:
            raise DiscoveryError("LARK_PAGINATION_STALLED")
        offset += len(items)
    raise DiscoveryError("LARK_PAGINATION_LIMIT")


def discover_base(base_token, runner=None):
    if not isinstance(base_token, str) or not base_token or any(c.isspace() for c in base_token):
        raise DiscoveryError("BASE_TOKEN_INVALID")
    details = _lark_json(["+base-get", "--base-token", base_token], runner)
    if not isinstance(details, dict):
        raise DiscoveryError("BASE_DETAILS_INVALID")
    data = details.get("data", details)
    if not isinstance(data, dict):
        raise DiscoveryError("BASE_DETAILS_INVALID")
    tables = []
    for item in _paged_lark(base_token, "+table-list", runner=runner):
        table_id = item["table_id"]
        name = item.get("table_name") or item.get("name")
        if not isinstance(name, str) or not name:
            raise DiscoveryError("TABLE_NAME_MISSING")
        fields = []
        for field in _paged_lark(base_token, "+field-list", table_id, runner):
            field_name = field.get("field_name") or field.get("name")
            if not isinstance(field_name, str) or not field_name:
                raise DiscoveryError("FIELD_NAME_MISSING")
            normalized = {"id": field["field_id"], "name": field_name,
                          "type": field.get("type")}
            details = field.get("property") if isinstance(field.get("property"), dict) else field
            for optional in ("options", "multiple", "style"):
                if optional in details:
                    normalized[optional] = details[optional]
            fields.append(normalized)
        tables.append({"id": table_id, "name": name, "fields": fields})
    return {"base_name": data.get("name") or data.get("app_name"), "tables": tables,
            "read_only": True, "pagination_complete": True}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise DiscoveryError("N8N_REDIRECT_REFUSED")


def _http_get_json(url, headers):
    request = Request(url, headers=headers, method="GET")
    try:
        with build_opener(_NoRedirect).open(request, timeout=20) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
    except DiscoveryError:
        raise
    except Exception as error:
        raise DiscoveryError("N8N_READ_FAILED") from error
    if len(raw) > 4 * 1024 * 1024:
        raise DiscoveryError("N8N_RESPONSE_TOO_LARGE")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise DiscoveryError("N8N_JSON_INVALID") from error


def discover_n8n(instance_url, api_key, getter=None):
    parsed = urlsplit(instance_url)
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback)) or not parsed.netloc:
        raise DiscoveryError("N8N_URL_MUST_BE_HTTPS_OR_LOOPBACK")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise DiscoveryError("N8N_URL_UNSAFE")
    if not isinstance(api_key, str) or not api_key.strip():
        raise DiscoveryError("N8N_API_KEY_MISSING")
    base = instance_url.rstrip("/")
    if base.endswith("/api/v1"):
        base = base[:-7]
    headers = {"accept": "application/json", "X-N8N-API-KEY": api_key}
    getter = getter or _http_get_json
    workflows = []
    ids = set()
    cursors = set()
    cursor = None
    for _ in range(100):
        query = {"limit": 250}
        if cursor:
            query["cursor"] = cursor
        payload = getter(base + "/api/v1/workflows?" + urlencode(query), headers)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise DiscoveryError("N8N_WORKFLOWS_DATA_MISSING")
        for item in payload["data"]:
            if not isinstance(item, dict):
                raise DiscoveryError("N8N_WORKFLOW_INVALID")
            identity, name = item.get("id"), item.get("name")
            if not isinstance(identity, str) or not identity or identity in ids:
                raise DiscoveryError("N8N_WORKFLOW_ID_MISSING_OR_DUPLICATE")
            if not isinstance(name, str) or not name:
                raise DiscoveryError("N8N_WORKFLOW_NAME_MISSING")
            if not isinstance(item.get("active"), bool):
                raise DiscoveryError("N8N_WORKFLOW_ACTIVE_INVALID")
            ids.add(identity)
            workflows.append({"id": identity, "name": name, "active": item.get("active")})
        cursor = payload.get("nextCursor")
        if not cursor:
            return {"workflows": workflows, "read_only": True,
                    "pagination_complete": True}
        if not isinstance(cursor, str) or cursor in cursors:
            raise DiscoveryError("N8N_CURSOR_INVALID_OR_REPEATED")
        cursors.add(cursor)
    raise DiscoveryError("N8N_PAGINATION_LIMIT")


def compare_discovery(blueprint, base, n8n):
    """Name collisions are never treated as safe re-use without schema review."""
    if not base.get("pagination_complete") or not n8n.get("pagination_complete"):
        raise DiscoveryError("FULL_DISCOVERY_REQUIRED")
    output = []
    for resource in blueprint["resources"]:
        if resource["kind"] == "feishu_table":
            matches = [item for item in base["tables"] if item["name"] == resource["spec"]["name"]]
        else:
            matches = [item for item in n8n["workflows"] if item["name"] == resource["spec"]["name"]]
        if len(matches) > 1:
            verdict = "duplicate_name_conflict"
        elif matches:
            verdict = "exists_requires_structure_review"
        else:
            verdict = "not_found_in_selected_targets"
        output.append({"kind": resource["kind"], "key": resource["key"],
                       "verdict": verdict, "existing_ids": [item["id"] for item in matches]})
    return {"project_id": blueprint["project_id"], "resources": output,
            "runtime_status": "not_running", "execute_allowed": False}
