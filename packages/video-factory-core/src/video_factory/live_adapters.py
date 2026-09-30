"""Unexposed live adapters for an explicitly selected isolated test target.

No CLI command currently calls these adapters. They must be validated against
real sandbox response shapes before a public installer command is added.
"""

import json
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener

from .discovery import _NoRedirect, _lark_json, discover_base, discover_n8n
from .provision import ProvisionError


def _n8n_root(instance_url):
    parsed = urlsplit(instance_url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and
                                          parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ProvisionError("N8N_URL_MUST_BE_HTTPS_OR_LOOPBACK")
    if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProvisionError("N8N_URL_UNSAFE")
    root = instance_url.rstrip("/")
    return root[:-7] if root.endswith("/api/v1") else root


def _http_json(method, url, api_key, body=None, transport=None):
    headers = {"Accept": "application/json", "X-N8N-API-KEY": api_key}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if transport is not None:
        return transport(method, url, headers, body)
    request = Request(url, headers=headers, data=data, method=method)
    try:
        with build_opener(_NoRedirect).open(request, timeout=30) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
    except Exception as error:
        raise ProvisionError("N8N_REQUEST_UNCERTAIN") from error
    if len(raw) > 4 * 1024 * 1024:
        raise ProvisionError("N8N_RESPONSE_TOO_LARGE")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ProvisionError("N8N_RESPONSE_INVALID") from error
    if not isinstance(result, dict):
        raise ProvisionError("N8N_RESPONSE_OBJECT_REQUIRED")
    return result


class LarkTableBackend:
    def __init__(self, *, base_token, expected_base_name, runner=None):
        if not base_token or not expected_base_name:
            raise ProvisionError("EXPLICIT_BASE_TARGET_REQUIRED")
        self.base_token = base_token
        self.expected_base_name = expected_base_name
        self.runner = runner

    def _snapshot(self):
        snapshot = discover_base(self.base_token, self.runner)
        if snapshot["base_name"] != self.expected_base_name or not snapshot["pagination_complete"]:
            raise ProvisionError("BASE_TARGET_IDENTITY_MISMATCH")
        return snapshot

    def find(self, name):
        return [table for table in self._snapshot()["tables"] if table["name"] == name]

    def create(self, payload):
        if not isinstance(payload, dict) or not payload.get("name") or not payload.get("fields"):
            raise ProvisionError("TABLE_CREATE_PAYLOAD_INVALID")
        # Discovery immediately precedes each create in the serial installer.
        # This command creates schema only; it never writes SKU/task records.
        _lark_json(["+table-create", "--base-token", self.base_token,
                    "--name", payload["name"],
                    "--fields", json.dumps(payload["fields"], ensure_ascii=False)],
                   self.runner)


class N8nWorkflowBackend:
    def __init__(self, *, instance_url, api_key, transport=None, list_getter=None):
        if not isinstance(api_key, str) or not api_key:
            raise ProvisionError("N8N_API_KEY_REQUIRED")
        self.root = _n8n_root(instance_url)
        self.api_key = api_key
        self.transport = transport
        self.list_getter = list_getter

    def find(self, name):
        # Full-pagination list is repeated before each create to detect a name
        # collision introduced by another installer between resources.
        if self.list_getter is None and self.transport is not None:
            getter = lambda url, headers: self.transport("GET", url, headers, None)
        else:
            getter = self.list_getter
        snapshot = discover_n8n(self.root, self.api_key, getter)
        if not snapshot["pagination_complete"]:
            raise ProvisionError("N8N_DISCOVERY_INCOMPLETE")
        matches = []
        for item in snapshot["workflows"]:
            if item["name"] != name:
                continue
            url = self.root + "/api/v1/workflows/" + quote(item["id"], safe="")
            workflow = _http_json("GET", url, self.api_key, transport=self.transport)
            if workflow.get("id") != item["id"] or workflow.get("name") != name:
                raise ProvisionError("N8N_DETAIL_LIST_MISMATCH")
            matches.append(workflow)
        return matches

    def create(self, payload):
        if not isinstance(payload, dict) or payload.get("active") is not False:
            raise ProvisionError("N8N_CREATE_MUST_BE_DISABLED")
        body = {key: payload[key] for key in ("name", "nodes", "connections", "settings")}
        # The public API creates a saved workflow; active is omitted from the
        # request and must be verified false by the installer's readback.
        _http_json("POST", self.root + "/api/v1/workflows", self.api_key,
                   body=body, transport=self.transport)


class IsolatedStore:
    def __init__(self, *, target, base_backend, n8n_backend):
        if target.get("kind") != "isolated_test":
            raise ProvisionError("EXPLICIT_ISOLATED_TARGET_REQUIRED")
        if target.get("base_ref") != base_backend.expected_base_name:
            raise ProvisionError("BASE_TARGET_IDENTITY_MISMATCH")
        if target.get("n8n_ref") != n8n_backend.root:
            raise ProvisionError("N8N_TARGET_IDENTITY_MISMATCH")
        self.target = target
        self.base = base_backend
        self.n8n = n8n_backend

    def find(self, kind, name):
        if kind == "feishu_table":
            return self.base.find(name)
        if kind == "n8n_workflow":
            return self.n8n.find(name)
        raise ProvisionError("RESOURCE_KIND_UNSUPPORTED")

    def create(self, kind, payload):
        if kind == "feishu_table":
            return self.base.create(payload)
        if kind == "n8n_workflow":
            return self.n8n.create(payload)
        raise ProvisionError("RESOURCE_KIND_UNSUPPORTED")
