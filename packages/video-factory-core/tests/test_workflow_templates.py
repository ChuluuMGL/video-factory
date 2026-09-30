from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import json
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from video_factory.setup import compile_blueprint
from video_factory.cli import main
from video_factory.workflow_templates import ROUTES, compile_workflow_templates
from test_setup import sample_brief


class WorkflowTemplateTests(unittest.TestCase):
    def test_cli_preview_and_canary_are_offline(self):
        project_dir = Path(__file__).resolve().parents[1] / "examples" / "new-brand"
        output = StringIO()
        with redirect_stdout(output):
            code = main(["template-preview", "--project-dir", str(project_dir),
                         "--worker-origin", "https://worker.example.test",
                         "--credential-id", "credential-001"])
        self.assertEqual(code, 0)
        preview = json.loads(output.getvalue())
        self.assertEqual(len(preview["templates"]), 5)
        self.assertEqual(preview["runtime_status"], "not_running")
        output = StringIO()
        with redirect_stdout(output):
            code = main(["canary", "--project-dir", str(project_dir)])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "视频审核通过")
        self.assertEqual(result["provider_requests"], 0)
        self.assertEqual(result["live_resources_touched"], 0)

    def test_five_disabled_separate_routes_with_credential_reference(self):
        templates = compile_workflow_templates(
            compile_blueprint(sample_brief()), "https://worker.example.test", "credential-001")
        self.assertEqual(len(templates), 5)
        for route in ROUTES:
            workflow = templates[f"new_brand.{route}"]
            self.assertFalse(workflow["active"])
            schedule, dispatch = workflow["nodes"]
            self.assertEqual(schedule["type"], "n8n-nodes-base.scheduleTrigger")
            self.assertEqual(schedule["parameters"]["rule"]["interval"][0]
                             ["minutesInterval"], 15 if route == "health_notify" else 3)
            self.assertEqual(dispatch["type"], "n8n-nodes-base.httpRequest")
            self.assertTrue(dispatch["parameters"]["url"].endswith("/" + route))
            self.assertEqual(dispatch["credentials"]["httpHeaderAuth"]["id"],
                             "credential-001")
            self.assertEqual(json.loads(dispatch["parameters"]["jsonBody"])["route"], route)
            self.assertNotIn("secret", json.dumps(workflow).lower())

    def test_refuses_url_secrets_and_missing_credential(self):
        blueprint = compile_blueprint(sample_brief())
        for origin in ("http://worker.example.test", "https://user:pass@worker.example.test",
                       "https://worker.example.test/path", "https://worker.example.test?a=b"):
            with self.assertRaisesRegex(ValueError, "HTTPS_WORKER_ORIGIN_REQUIRED"):
                compile_workflow_templates(blueprint, origin, "credential-001")
        with self.assertRaisesRegex(ValueError, "CREDENTIAL_REFERENCE_REQUIRED"):
            compile_workflow_templates(blueprint, "https://worker.example.test", "")


if __name__ == "__main__":
    unittest.main()
