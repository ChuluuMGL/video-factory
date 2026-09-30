import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from video_factory.cli import main
from video_factory.setup import assess_brief, compile_blueprint, read_project_folder, reconcile


def sample_brief():
    return {
        "project_id": "new_brand", "product_category": "hair care",
        "target_market": "US", "business_goal": "qualified sales",
        "feishu_target": "new Base in test space", "n8n_target": "test server",
        "script_reviewer": "content lead", "video_reviewer": "QA lead",
        "model_route": "script API; video local", "spend_policy": "no retry without approval",
        "category_rule_source": "approved product sheets and platform policy",
        "products": [{"sku_id": "SKU-001", "name": "Example Color", "variant": "single pack",
                      "truth_source": "approved packaging photo"}],
    }


class SetupTests(unittest.TestCase):
    def test_empty_folder_asks_instead_of_guessing(self):
        with tempfile.TemporaryDirectory(prefix="vf-intake-") as folder:
            project = read_project_folder(Path(folder))
            result = assess_brief(project["brief"])
            self.assertFalse(result["complete"])
            self.assertIn("products", [item["field"] for item in result["questions"]])

    def test_rejects_duplicate_skus(self):
        brief = sample_brief()
        brief["products"].append(dict(brief["products"][0]))
        with self.assertRaisesRegex(ValueError, "DUPLICATE_SKU_ID"):
            assess_brief(brief)

    def test_unrecognized_secret_or_instruction_fields_are_rejected(self):
        brief = sample_brief()
        brief["api_key"] = "must not enter credential values here"
        with self.assertRaisesRegex(ValueError, "UNKNOWN_BRIEF_FIELDS"):
            assess_brief(brief)

    def test_blueprint_is_disabled_and_has_separate_lanes(self):
        blueprint = compile_blueprint(sample_brief())
        self.assertFalse(blueprint["execute_allowed"])
        self.assertEqual(blueprint["installation_state"], "preview_only")
        self.assertEqual(len(blueprint["resources"]), 11)
        keys = {item["key"] for item in blueprint["resources"]}
        self.assertIn("new_brand.script_production", keys)
        self.assertIn("new_brand.script_repair", keys)
        self.assertIn("new_brand.video_production", keys)
        self.assertIn("new_brand.video_repair", keys)
        self.assertIn("new_brand.review_events", keys)
        self.assertIn("new_brand.execution_ledger", keys)
        for item in blueprint["resources"]:
            self.assertFalse(item["spec"]["active"])
        tasks = next(item for item in blueprint["resources"] if item["key"] == "new_brand.tasks")
        products = next(item for item in blueprint["resources"] if item["key"] == "new_brand.products")
        self.assertEqual(products["spec"]["proposed_records"][0]["可售状态"], "待核验")
        status = next(field for field in tasks["spec"]["fields"] if field["name"] == "状态")
        self.assertEqual(status["type"], "select")
        self.assertFalse(status["multiple"])
        self.assertTrue(all(isinstance(option, dict) and "name" in option
                            for option in status["options"]))
        repairs = next(item for item in blueprint["resources"]
                       if item["key"] == "new_brand.video_repair")
        self.assertEqual(repairs["spec"]["input_status"], "视频返修")
        self.assertEqual(repairs["spec"]["output_status"], "视频待审核")
        self.assertEqual(repairs["spec"]["implementation_status"], "template_required")

    def test_reconcile_is_idempotent_and_refuses_schema_conflict(self):
        blueprint = compile_blueprint(sample_brief())
        self.assertEqual({item["action"] for item in reconcile(blueprint)["actions"]},
                         {"discover_live_resource"})
        self.assertEqual({item["action"] for item in reconcile(blueprint, {"resources": []})["actions"]},
                         {"propose_create"})
        inventory = {"resources": [
            {"kind": item["kind"], "key": item["key"], "fingerprint": item["fingerprint"]}
            for item in blueprint["resources"]]}
        self.assertEqual({item["action"] for item in reconcile(blueprint, inventory)["actions"]},
                         {"reuse"})
        inventory["resources"][0]["fingerprint"] = "changed"
        self.assertEqual(reconcile(blueprint, inventory)["actions"][0]["action"], "conflict_review")

    def test_cli_intake_and_plan_are_local_only(self):
        with tempfile.TemporaryDirectory(prefix="vf-intake-") as folder:
            project = Path(folder)
            with contextlib.redirect_stdout(output := io.StringIO()):
                self.assertEqual(main(["intake", "--project-dir", folder]), 0)
            intake = json.loads(output.getvalue())
            self.assertFalse(intake["complete"])
            with contextlib.redirect_stdout(output := io.StringIO()):
                self.assertEqual(main(["plan", "--project-dir", folder]), 2)
            self.assertEqual(json.loads(output.getvalue())["error"], "BRIEF_INCOMPLETE")
            (project / "video-factory.brief.json").write_text(json.dumps(sample_brief()))
            with contextlib.redirect_stdout(output := io.StringIO()):
                self.assertEqual(main(["plan", "--project-dir", folder]), 0)
            plan = json.loads(output.getvalue())
            self.assertFalse(plan["blueprint"]["execute_allowed"])
            self.assertEqual({item["action"] for item in plan["reconciliation"]["actions"]},
                             {"discover_live_resource"})

    def test_manifest_symlink_is_not_read(self):
        with tempfile.TemporaryDirectory(prefix="vf-intake-") as folder:
            path = Path(folder)
            target = path / "elsewhere.json"
            target.write_text(json.dumps(sample_brief()))
            (path / "video-factory.brief.json").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "MANIFEST_FILE_NOT_REGULAR"):
                read_project_folder(path)


if __name__ == "__main__":
    unittest.main()
