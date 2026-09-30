from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from video_factory.canary import CanaryError, CanaryRuntime, CanaryTask
from video_factory.setup import compile_blueprint
from test_setup import sample_brief


def runtime():
    return CanaryRuntime([CanaryTask("VID-001", "SKU-001", "packaging-approved",
                                     "one clear product result")])


class CanaryTests(unittest.TestCase):
    def test_every_canary_status_exists_in_task_table_schema(self):
        blueprint = compile_blueprint(sample_brief())
        task_table = next(item["spec"] for item in blueprint["resources"]
                          if item["key"] == "new_brand.tasks")
        status_field = next(field for field in task_table["fields"] if field["name"] == "状态")
        configured = {option["name"] for option in status_field["options"]}
        simulated = {"脚本待生产", "脚本待审核", "脚本返修", "视频待生产",
                     "视频待审核", "视频返修", "视频审核通过"}
        self.assertTrue(simulated <= configured)

    def test_normal_and_repair_routes_with_human_review(self):
        factory = runtime()
        task = factory.tasks["VID-001"]
        first = factory.dispatch("script_production", task.task_id)
        self.assertEqual(task.status, "脚本待审核")
        self.assertIsNone(first["provider_request_id"])
        with self.assertRaisesRegex(CanaryError, "TASK_NOT_IN_ROUTE_QUEUE"):
            factory.dispatch("video_production", task.task_id)
        factory.review(task.task_id, task.script_version, "reject", "reviewer-a",
                       "The product claim is too broad")
        factory.dispatch("script_repair", task.task_id)
        with self.assertRaisesRegex(CanaryError, "STALE_REVIEW_VERSION"):
            factory.review(task.task_id, first["version_id"], "pass", "reviewer-a")
        factory.review(task.task_id, task.script_version, "pass", "reviewer-a")
        factory.dispatch("video_production", task.task_id)
        factory.review(task.task_id, task.video_version, "reject", "reviewer-b",
                       "The product label is unreadable")
        factory.dispatch("video_repair", task.task_id)
        factory.review(task.task_id, task.video_version, "pass", "reviewer-b")
        self.assertEqual(task.status, "视频审核通过")
        self.assertEqual([row["route"] for row in task.executions],
                         ["script_production", "script_repair", "video_production",
                          "video_repair"])
        self.assertEqual(len(task.reviews), 4)
        self.assertTrue(all(row["reuse_evidence"] == "CANARY_ONLY_NO_PROVIDER"
                            for row in task.executions))

    def test_repair_requires_bound_feedback_and_no_duplicate_claim(self):
        factory = runtime()
        task = factory.tasks["VID-001"]
        factory.dispatch("script_production", task.task_id)
        task.status = "脚本待生产"
        with self.assertRaisesRegex(CanaryError, "DUPLICATE_INPUT_CLAIM"):
            factory.dispatch("script_production", task.task_id)
        task.status = "脚本待审核"
        with self.assertRaisesRegex(CanaryError, "REJECTION_FEEDBACK_REQUIRED"):
            factory.review(task.task_id, task.script_version, "reject", "reviewer", "")
        factory.review(task.task_id, task.script_version, "reject", "reviewer", "rewrite")
        task.review_target_version = "old-version"
        with self.assertRaisesRegex(CanaryError, "BOUND_HUMAN_REPAIR_FEEDBACK_REQUIRED"):
            factory.dispatch("script_repair", task.task_id)
        task.review_target_version = task.script_version
        factory.dispatch("script_repair", task.task_id)

    def test_missing_truth_and_duplicate_task_id_fail_closed(self):
        with self.assertRaisesRegex(CanaryError, "TASK_ID_DUPLICATE_OR_INVALID"):
            CanaryRuntime([CanaryTask("same", "SKU", "truth", "direction"),
                           CanaryTask("same", "SKU", "truth", "direction")])
        factory = CanaryRuntime([CanaryTask("VID", "SKU", "", "direction")])
        with self.assertRaisesRegex(CanaryError, "PRODUCT_TRUTH_OR_DIRECTION_MISSING"):
            factory.dispatch("script_production", "VID")


if __name__ == "__main__":
    unittest.main()
