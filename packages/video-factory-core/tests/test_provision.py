import copy
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from video_factory.provision import ProvisionError, install_disabled, prepare_install
from video_factory.setup import compile_blueprint
from test_setup import sample_brief


def bundle():
    blueprint = compile_blueprint(sample_brief())
    templates = {}
    for resource in blueprint["resources"]:
        if resource["kind"] != "n8n_workflow":
            continue
        templates[resource["key"]] = {
            "name": resource["spec"]["name"],
            "nodes": [{"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
                      {"name": "Worker", "type": "example.testWorker"}],
            "connections": {"Start": {"main": [[{"node": "Worker", "type": "main",
                                                  "index": 0}]]}},
            "settings": {}, "active": False,
        }
    target = {"kind": "isolated_test", "project_id": "new_brand",
              "base_ref": "sandbox-base", "n8n_ref": "sandbox-n8n"}
    return blueprint, templates, target


class FakeStore:
    def __init__(self, target):
        self.target = target
        self.resources = {}
        self.creates = []
        self.fail_after_create_name = None
        self.fail_before_create_name = None

    def find(self, kind, name):
        value = self.resources.get((kind, name))
        return [] if value is None else [copy.deepcopy(value)]

    def create(self, kind, payload):
        key = kind, payload["name"]
        self.creates.append(key)
        if payload["name"] == self.fail_before_create_name:
            raise TimeoutError("simulated write failure before remote acceptance")
        if key in self.resources:
            raise AssertionError("duplicate create")
        self.resources[key] = {"id": f"resource-{len(self.resources)+1}",
                               **copy.deepcopy(payload)}
        if payload["name"] == self.fail_after_create_name:
            raise TimeoutError("simulated response loss after remote success")


class ProvisionTests(unittest.TestCase):
    def test_prep_refuses_production_target_and_placeholder_templates(self):
        blueprint, templates, target = bundle()
        with self.assertRaisesRegex(ProvisionError, "EXPLICIT_ISOLATED_TARGET_REQUIRED"):
            prepare_install(blueprint, templates, {**target, "kind": "production"})
        with self.assertRaisesRegex(ProvisionError, "WORKFLOW_TEMPLATES_INCOMPLETE"):
            prepare_install(blueprint, {}, target)
        templates["new_brand.video_production"]["nodes"] = []
        with self.assertRaisesRegex(ProvisionError, "WORKFLOW_NODES_NOT_IMPLEMENTED"):
            prepare_install(blueprint, templates, target)

    def test_prep_refuses_active_workflow_and_active_blueprint(self):
        blueprint, templates, target = bundle()
        templates["new_brand.video_repair"]["active"] = True
        with self.assertRaisesRegex(ProvisionError, "WORKFLOW_MUST_BE_DISABLED"):
            prepare_install(blueprint, templates, target)
        templates["new_brand.video_repair"]["active"] = False
        blueprint["execute_allowed"] = True
        with self.assertRaisesRegex(ProvisionError, "UNSAFE_BLUEPRINT_STATE"):
            prepare_install(blueprint, templates, target)

    def test_serial_creation_readback_and_second_run_reuse(self):
        prepared = prepare_install(*bundle())
        store = FakeStore(prepared["target"])
        first = install_disabled(prepared, prepared["approval_sha256"], store)
        self.assertEqual(first["status"], "installed_disabled")
        self.assertEqual(len(first["receipts"]), 11)
        self.assertEqual(len(store.creates), 11)
        self.assertEqual({row["action"] for row in first["receipts"]},
                         {"created_and_read_back"})
        self.assertFalse(first["activation_allowed"])
        second = install_disabled(prepared, prepared["approval_sha256"], store)
        self.assertEqual(second["status"], "installed_disabled")
        self.assertEqual(len(store.creates), 11)
        self.assertEqual({row["action"] for row in second["receipts"]},
                         {"reused_exact_match"})

    def test_conflict_stops_before_any_new_write(self):
        prepared = prepare_install(*bundle())
        store = FakeStore(prepared["target"])
        first = prepared["operations"][0]
        store.resources[(first["kind"], first["name"])] = {
            "id": "existing-1", "name": first["name"], "fields": [{"name": "Wrong", "type": "text"}]}
        result = install_disabled(prepared, prepared["approval_sha256"], store)
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(result["reason"], "EXISTING_RESOURCE_CONFLICT")
        self.assertEqual(store.creates, [])

    def test_timeout_after_remote_create_is_read_back_without_duplicate(self):
        prepared = prepare_install(*bundle())
        store = FakeStore(prepared["target"])
        store.fail_after_create_name = prepared["operations"][0]["name"]
        result = install_disabled(prepared, prepared["approval_sha256"], store)
        self.assertEqual(result["status"], "installed_disabled")
        self.assertEqual(result["receipts"][0]["action"],
                         "recovered_after_uncertain_create")
        self.assertEqual(len(store.creates), 11)

    def test_plan_hash_mismatch_never_touches_store(self):
        prepared = prepare_install(*bundle())
        store = FakeStore(prepared["target"])
        with self.assertRaisesRegex(ProvisionError, "PLAN_APPROVAL_MISMATCH"):
            install_disabled(prepared, "incorrect", store)
        self.assertEqual(store.creates, [])

    def test_partial_install_stops_then_reuses_completed_resources(self):
        prepared = prepare_install(*bundle())
        store = FakeStore(prepared["target"])
        store.fail_before_create_name = prepared["operations"][3]["name"]
        first = install_disabled(prepared, prepared["approval_sha256"], store)
        self.assertEqual(first["status"], "needs_attention")
        self.assertEqual(first["next_resource"], prepared["operations"][3]["key"])
        self.assertEqual(first["reason"], "CREATE_UNCERTAIN_NO_RETRY")
        self.assertEqual(len(first["receipts"]), 3)
        store.fail_before_create_name = None
        second = install_disabled(prepared, prepared["approval_sha256"], store)
        self.assertEqual(second["status"], "installed_disabled")
        self.assertEqual([item["action"] for item in second["receipts"][:3]],
                         ["reused_exact_match"] * 3)

    def test_wrong_store_target_cannot_receive_any_write(self):
        prepared = prepare_install(*bundle())
        store = FakeStore({**prepared["target"], "n8n_ref": "other-server"})
        with self.assertRaisesRegex(ProvisionError, "STORE_TARGET_MISMATCH"):
            install_disabled(prepared, prepared["approval_sha256"], store)
        self.assertEqual(store.creates, [])


if __name__ == "__main__":
    unittest.main()
