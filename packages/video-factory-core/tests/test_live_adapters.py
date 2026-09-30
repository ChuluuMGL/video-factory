import copy
import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from video_factory.live_adapters import IsolatedStore, LarkTableBackend, N8nWorkflowBackend
from video_factory.provision import install_disabled, prepare_install
from test_provision import bundle


class SimulatedLark:
    def __init__(self):
        self.tables = {}
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        if "+base-get" in command:
            return {"data": {"name": "VF TEST new_brand"}}
        if "+table-create" in command:
            name = command[command.index("--name") + 1]
            fields = json.loads(command[command.index("--fields") + 1])
            identity = f"tbl{len(self.tables)+1}"
            self.tables[name] = {"id": identity, "fields": fields}
            return {"data": {"table_id": identity}}
        if "+table-list" in command:
            return {"data": {"items": [{"table_id": item["id"], "table_name": name}
                                      for name, item in self.tables.items()],
                             "has_more": False}}
        if "+field-list" in command:
            table_id = command[command.index("--table-id") + 1]
            table = next(item for item in self.tables.values() if item["id"] == table_id)
            return {"data": {"items": [{"field_id": f"fld{index}", "field_name": field["name"],
                                        **copy.deepcopy(field)}
                                       for index, field in enumerate(table["fields"])],
                             "has_more": False}}
        raise AssertionError("unexpected lark command")


class SimulatedN8n:
    def __init__(self):
        self.workflows = {}
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        if method == "POST":
            self.assert_disabled_request(body)
            identity = f"wf{len(self.workflows)+1}"
            self.workflows[identity] = {"id": identity, **copy.deepcopy(body), "active": False}
            return copy.deepcopy(self.workflows[identity])
        if "/workflows?" in url:
            return {"data": [{"id": item["id"], "name": item["name"],
                              "active": item["active"]} for item in self.workflows.values()],
                    "nextCursor": None}
        identity = url.rsplit("/", 1)[-1]
        return copy.deepcopy(self.workflows[identity])

    @staticmethod
    def assert_disabled_request(body):
        if "active" in body:
            raise AssertionError("active field must not be sent to n8n create")


class LiveAdapterSimulationTests(unittest.TestCase):
    def test_full_bundle_serial_readback_through_adapters(self):
        blueprint, templates, target = bundle()
        target["base_ref"] = "VF TEST new_brand"
        target["n8n_ref"] = "http://127.0.0.1:5678"
        prepared = prepare_install(blueprint, templates, target)
        lark_runner = SimulatedLark()
        n8n_transport = SimulatedN8n()
        store = IsolatedStore(
            target=target,
            base_backend=LarkTableBackend(base_token="baseTest",
                                          expected_base_name=target["base_ref"],
                                          runner=lark_runner),
            n8n_backend=N8nWorkflowBackend(instance_url=target["n8n_ref"],
                                           api_key="fake-test-key", transport=n8n_transport),
        )
        first = install_disabled(prepared, prepared["approval_sha256"], store)
        self.assertEqual(first["status"], "installed_disabled")
        self.assertEqual(len(first["receipts"]), 11)
        self.assertEqual(len(lark_runner.tables), 6)
        self.assertEqual(len(n8n_transport.workflows), 5)
        self.assertTrue(all(not item["active"] for item in n8n_transport.workflows.values()))
        second = install_disabled(prepared, prepared["approval_sha256"], store)
        self.assertEqual(second["status"], "installed_disabled")
        self.assertEqual(len(lark_runner.tables), 6)
        self.assertEqual(len(n8n_transport.workflows), 5)
        self.assertEqual({item["action"] for item in second["receipts"]},
                         {"reused_exact_match"})


if __name__ == "__main__":
    unittest.main()
