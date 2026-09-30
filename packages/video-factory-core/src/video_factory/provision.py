"""Fail-closed, serial installation contract for a new isolated test project.

The module has no live adapter or CLI entry point. It can only operate through
an injected store, and it never activates workflows or creates task records.
"""

import hashlib
import json


class ProvisionError(ValueError):
    pass


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _field_shape(field):
    if not isinstance(field, dict):
        raise ProvisionError("FIELD_SHAPE_INVALID")
    name, kind = field.get("name"), field.get("type")
    if not isinstance(name, str) or not name or not isinstance(kind, str):
        raise ProvisionError("FIELD_NAME_OR_TYPE_MISSING")
    result = {"name": name, "type": kind}
    if kind == "select":
        options = field.get("options")
        if not isinstance(options, list):
            raise ProvisionError("SELECT_OPTIONS_MISSING")
        result["multiple"] = field.get("multiple", False)
        result["options"] = [item.get("name") for item in options]
        if any(not isinstance(name, str) or not name for name in result["options"]):
            raise ProvisionError("SELECT_OPTION_INVALID")
    if kind == "number" and "style" in field:
        style = field["style"]
        if not isinstance(style, dict):
            raise ProvisionError("NUMBER_STYLE_INVALID")
        result["style"] = {key: style[key] for key in ("type", "precision") if key in style}
    return result


def _table_shape(resource):
    fields = resource.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ProvisionError("TABLE_FIELDS_MISSING")
    normalized = [_field_shape(field) for field in fields]
    if len({field["name"] for field in normalized}) != len(normalized):
        raise ProvisionError("DUPLICATE_FIELD_NAME")
    return normalized


def _workflow_shape(workflow):
    if not isinstance(workflow, dict):
        raise ProvisionError("WORKFLOW_TEMPLATE_INVALID")
    if workflow.get("active") is not False:
        raise ProvisionError("WORKFLOW_MUST_BE_DISABLED")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or len(nodes) < 2:
        raise ProvisionError("WORKFLOW_NODES_NOT_IMPLEMENTED")
    names = [node.get("name") for node in nodes if isinstance(node, dict)]
    if (len(names) != len(nodes) or
            any(not isinstance(name, str) or not name for name in names) or
            len(names) != len(set(names))):
        raise ProvisionError("WORKFLOW_NODE_NAMES_INVALID")
    if any(not isinstance(node.get("type"), str) or not node["type"] for node in nodes):
        raise ProvisionError("WORKFLOW_NODE_TYPES_INVALID")
    if not isinstance(workflow.get("connections"), dict) or not workflow["connections"]:
        raise ProvisionError("WORKFLOW_CONNECTIONS_MISSING")
    if not isinstance(workflow.get("settings"), dict):
        raise ProvisionError("WORKFLOW_SETTINGS_MISSING")
    return {key: workflow[key] for key in ("name", "nodes", "connections", "settings", "active")}


def prepare_install(blueprint, templates, target):
    """Validate the complete bundle and return an immutable approval digest."""
    if (not isinstance(blueprint, dict) or not isinstance(templates, dict) or
            not isinstance(target, dict)):
        raise ProvisionError("INSTALL_INPUT_INVALID")
    project = blueprint.get("project_id")
    if blueprint.get("installation_state") != "preview_only" or blueprint.get("execute_allowed") is not False:
        raise ProvisionError("UNSAFE_BLUEPRINT_STATE")
    if (target.get("kind") != "isolated_test" or target.get("project_id") != project or
            not target.get("base_ref") or not target.get("n8n_ref")):
        raise ProvisionError("EXPLICIT_ISOLATED_TARGET_REQUIRED")
    resources = blueprint.get("resources")
    if not isinstance(resources, list) or not resources:
        raise ProvisionError("BLUEPRINT_RESOURCES_MISSING")
    keys = [item.get("key") for item in resources]
    if len(keys) != len(set(keys)):
        raise ProvisionError("DUPLICATE_RESOURCE_KEY")
    expected_workflows = {item["key"] for item in resources
                          if item.get("kind") == "n8n_workflow"}
    if set(templates) != expected_workflows:
        raise ProvisionError("WORKFLOW_TEMPLATES_INCOMPLETE")
    operations = []
    for item in resources:
        kind, key, spec = item.get("kind"), item.get("key"), item.get("spec")
        if not isinstance(spec, dict) or not isinstance(key, str):
            raise ProvisionError("RESOURCE_INVALID")
        if spec.get("active") is not False:
            raise ProvisionError("RESOURCE_MUST_BE_DISABLED")
        if kind == "feishu_table":
            _table_shape(spec)
            payload = {"name": spec["name"], "fields": spec["fields"]}
        elif kind == "n8n_workflow":
            payload = templates[key]
            _workflow_shape(payload)
            if payload["name"] != spec["name"]:
                raise ProvisionError("WORKFLOW_NAME_MISMATCH")
        else:
            raise ProvisionError("RESOURCE_KIND_UNSUPPORTED")
        operations.append({"kind": kind, "key": key, "name": spec["name"],
                           "payload": payload})
    return {"project_id": project, "target": target,
            "operations": operations, "approval_sha256": _digest({"target": target,
                                                                     "operations": operations}),
            "execute_allowed": False, "activation_allowed": False}


def _equivalent(operation, actual):
    if not isinstance(actual, dict) or actual.get("name") != operation["name"]:
        return False
    if not isinstance(actual.get("id"), str) or not actual["id"]:
        return False
    if operation["kind"] == "feishu_table":
        return _table_shape(actual) == _table_shape(operation["payload"])
    return _workflow_shape(actual) == _workflow_shape(operation["payload"])


def install_disabled(prepared, approved_sha256, store):
    """Create only missing resources, read back after each, never retry blindly.

    The store must implement ``find(kind, name) -> list`` and
    ``create(kind, payload)``. A real store must normalize API readbacks to the
    same shapes before this function can be used against a live target.
    """
    if prepared.get("approval_sha256") != approved_sha256:
        raise ProvisionError("PLAN_APPROVAL_MISMATCH")
    if getattr(store, "target", None) != prepared.get("target"):
        raise ProvisionError("STORE_TARGET_MISMATCH")
    receipts = []
    for operation in prepared["operations"]:
        kind, name = operation["kind"], operation["name"]
        try:
            matches = store.find(kind, name)
        except Exception:
            return _halt(prepared, receipts, operation, "DISCOVERY_FAILED")
        if not isinstance(matches, list) or len(matches) > 1:
            return _halt(prepared, receipts, operation, "AMBIGUOUS_EXISTING_RESOURCE")
        if matches:
            try:
                equivalent = _equivalent(operation, matches[0])
            except ProvisionError:
                equivalent = False
            if not equivalent:
                return _halt(prepared, receipts, operation, "EXISTING_RESOURCE_CONFLICT")
            receipts.append(_receipt(operation, matches[0], "reused_exact_match"))
            continue
        try:
            store.create(kind, operation["payload"])
        except Exception:
            # The remote service may have accepted a timed-out write. Read
            # back once; never issue a second create on uncertainty.
            try:
                after_error = store.find(kind, name)
                if len(after_error) == 1 and _equivalent(operation, after_error[0]):
                    receipts.append(_receipt(operation, after_error[0],
                                             "recovered_after_uncertain_create"))
                    continue
            except Exception:
                pass
            return _halt(prepared, receipts, operation, "CREATE_UNCERTAIN_NO_RETRY")
        try:
            readback = store.find(kind, name)
            if len(readback) != 1 or not _equivalent(operation, readback[0]):
                return _halt(prepared, receipts, operation, "READBACK_MISMATCH")
            receipts.append(_receipt(operation, readback[0], "created_and_read_back"))
        except Exception:
            return _halt(prepared, receipts, operation, "READBACK_FAILED")
    return {"project_id": prepared["project_id"], "approval_sha256": approved_sha256,
            "status": "installed_disabled", "receipts": receipts,
            "runtime_status": "not_running", "activation_allowed": False}


def _receipt(operation, actual, action):
    identity = actual.get("id")
    if not isinstance(identity, str) or not identity:
        raise ProvisionError("RESOURCE_ID_MISSING")
    return {"key": operation["key"], "kind": operation["kind"], "id": identity,
            "action": action, "readback_sha256": _digest(actual)}


def _halt(prepared, receipts, operation, reason):
    return {"project_id": prepared["project_id"],
            "approval_sha256": prepared["approval_sha256"],
            "status": "needs_attention", "receipts": receipts,
            "next_resource": operation["key"], "reason": reason,
            "runtime_status": "not_running", "activation_allowed": False}
