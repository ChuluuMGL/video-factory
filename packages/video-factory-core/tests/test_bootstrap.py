import contextlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from video_factory.cli import main
from video_factory.config import draft_config, inspect_config


def filled(project_id):
    value = draft_config(project_id)
    value["task_source"].update(base_token="base_example", tasks_table_id="tblExample",
                                credential_env="LARK_TOKEN", business_id_field="VID",
                                status_field="Status")
    value["scheduler"].update(url="https://n8n.example.invalid", credential_env="N8N_API_KEY")
    value["human_review"].update(script_field="Script", feedback_field="Feedback",
                                  current_video_field="Current", video_history_field="History")
    for key in value["status_map"]:
        value["status_map"][key] = project_id + "_" + key
    value["model_route"].update(kind="api", endpoint="https://model.example.invalid",
                                credential_env="MODEL_API_KEY")
    return value


class BootstrapTests(unittest.TestCase):
    def test_new_project_draft_is_safe_and_incomplete(self):
        result = inspect_config(draft_config("new_brand"))
        self.assertTrue(result["schema_valid"])
        self.assertFalse(result["configuration_complete"])
        self.assertFalse(result["execute_allowed"])
        self.assertEqual(result["runtime_status"], "not_running")
        self.assertIn("task_source.tasks_table_id", result["missing"])

    def test_two_projects_use_same_schema_without_shared_identity(self):
        first = filled("brand_a")
        second = filled("brand_b")
        for config in (first, second):
            result = inspect_config(config)
            self.assertTrue(result["configuration_complete"])
            self.assertFalse(result["execute_allowed"])
        self.assertNotEqual(first["status_map"], second["status_map"])

    def test_bootstrap_refuses_enabled_execution(self):
        config = filled("brand_a")
        config["execution"]["enabled"] = True
        result = inspect_config(config)
        self.assertFalse(result["schema_valid"])
        self.assertIn("EXECUTION_MUST_REMAIN_DISABLED_IN_BOOTSTRAP", result["errors"])

    def test_human_review_cannot_be_disabled(self):
        config = filled("brand_a")
        config["human_review"]["required"] = False
        self.assertIn("HUMAN_REVIEW_MUST_BE_REQUIRED", inspect_config(config)["errors"])

    def test_unknown_fields_and_secret_like_env_are_rejected(self):
        config = filled("brand_a")
        config["task_source"]["credential_env"] = "secret-value"
        config["task_source"]["token_value"] = "secret"
        result = inspect_config(config)
        self.assertIn("TASK_SOURCE_FIELDS_CHANGED", result["errors"])
        self.assertIn("TASK_SOURCE_CREDENTIAL_ENV_NAME_INVALID", result["errors"])

    def test_endpoint_cannot_embed_credentials(self):
        config = filled("brand_a")
        config["scheduler"]["url"] = "https://user:password@n8n.example.invalid"
        config["model_route"]["endpoint"] = "https://model.example.invalid?key=secret"
        result = inspect_config(config)
        self.assertIn("SCHEDULER_ENDPOINT_UNSAFE", result["errors"])
        self.assertIn("MODEL_ROUTE_ENDPOINT_UNSAFE", result["errors"])
        config["scheduler"]["url"] = "https://[broken"
        self.assertIn("SCHEDULER_ENDPOINT_UNSAFE", inspect_config(config)["errors"])

    def test_init_is_explicit_non_overwriting_and_private(self):
        with tempfile.TemporaryDirectory(prefix="vf-install-test-") as temp:
            target = Path(temp) / "video-factory.project.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["init", "--project-id", "brand_a", "--output", str(target)]), 0)
            self.assertEqual(json.loads(output.getvalue())["runtime_status"], "not_running")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            original = target.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["init", "--project-id", "brand_b", "--output", str(target)]), 2)
            self.assertEqual(target.read_bytes(), original)
            with contextlib.redirect_stdout(output := io.StringIO()):
                self.assertEqual(main(["inspect", "--config", str(target)]), 0)
            self.assertFalse(json.loads(output.getvalue())["execute_allowed"])

    def test_init_requires_absolute_target_and_inspect_rejects_symlink(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["init", "--project-id", "brand_a",
                                   "--output", "video-factory.project.json"]), 2)
        with tempfile.TemporaryDirectory(prefix="vf-install-test-") as temp:
            target = Path(temp) / "real.json"
            target.write_text(json.dumps(draft_config("brand_a")))
            link = Path(temp) / "link.json"
            link.symlink_to(target)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["inspect", "--config", str(link)]), 2)


if __name__ == "__main__":
    unittest.main()
