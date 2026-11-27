"""Synthetic contracts for the read-only first-install route chooser."""

import importlib.util
from pathlib import Path
import unittest


SCRIPT = (Path(__file__).resolve().parents[3] / "skills" /
          "video-factory-setup" / "scripts" / "choose_install_path.py")
SPEC = importlib.util.spec_from_file_location("choose_install_path", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InstallPathTests(unittest.TestCase):
    def assess(self, n8n, feishu="ready", runtime="ubuntu"):
        observed = {"os": "Linux", "remote_instances_scanned": False}
        return MODULE.assess({"n8n": n8n, "feishu": feishu,
                              "runtime": runtime}, observed=observed)

    def test_empty_ubuntu_uses_current_managed_release(self):
        result = self.assess("none")
        self.assertEqual(result["route"], "managed_ubuntu_current_release")
        self.assertTrue(result["current_release_can_complete"])
        self.assertFalse(result["software_installed"])
        self.assertEqual(result["entry"], "start.py")

    def test_existing_n8n_routes_to_portable_without_modifying_instance(self):
        for mode in ("local-node", "local-docker", "self-hosted"):
            with self.subTest(mode=mode):
                result = self.assess(mode)
                self.assertEqual(result["route"], "reuse_existing_n8n")
                self.assertTrue(result["current_release_can_complete"])
                self.assertFalse(result["existing_n8n_modified"])
                self.assertFalse(result["instance_verified"])
                self.assertEqual(result["entry"], "scripts/portable_n8n.py welcome")
                self.assertEqual(result["provider_requests"], 0)

    def test_empty_desktop_routes_to_local_install_without_server(self):
        result = self.assess("none", "partial", "local-desktop")
        self.assertEqual(result["route"], "portable_n8n_install_method")
        self.assertTrue(result["current_release_can_complete"])
        self.assertTrue(any("飞书" in step for step in result["next_steps"]))

    def test_n8n_free_local_resident_route_is_first_class_on_macos_and_linux(self):
        for n8n, runtime, os_name in (
            ("not-needed", "local-kernel", "Darwin"),
            ("not-needed", "local-desktop", "Linux"),
            ("none", "local-kernel", "Darwin"),
        ):
            with self.subTest(n8n=n8n, runtime=runtime, os_name=os_name):
                result = MODULE.assess(
                    {"n8n": n8n, "feishu": "partial", "runtime": runtime},
                    observed={"os": os_name, "remote_instances_scanned": False},
                )
                self.assertEqual(result["route"], "local_resident_kernel_v03")
                self.assertTrue(result["current_release_can_complete"])
                self.assertEqual(result["entry"], "scripts/install_local_kernel.py --mode business")
                self.assertFalse(result["software_installed"])
                self.assertFalse(result["configuration_saved"])
                self.assertFalse(result["feishu_verified"])
                self.assertEqual(result["provider_requests"], 0)
                self.assertFalse(result["production_accepted"])

    def test_n8n_free_local_resident_route_declines_windows_native(self):
        result = MODULE.assess(
            {"n8n": "not-needed", "feishu": "ready", "runtime": "local-kernel"},
            observed={"os": "Windows", "remote_instances_scanned": False},
        )
        self.assertEqual(result["route"], "local_resident_kernel_v03")
        self.assertFalse(result["current_release_can_complete"])
        self.assertIsNone(result["entry"])
        self.assertTrue(any("Windows" in step for step in result["next_steps"]))

    def test_cloud_and_unknown_are_not_mislabeled_as_verified(self):
        for mode in ("cloud", "unknown"):
            result = self.assess(mode)
            self.assertFalse(result["current_release_can_complete"])
            self.assertFalse(result["instance_verified"])
            self.assertFalse(result["production_accepted"])
            self.assertEqual(result["provider_requests"], 0)
        self.assertTrue(any("未实测" in step for step in self.assess("cloud")["next_steps"]))
        self.assertIsNone(self.assess("unknown")["entry"])

    def test_malformed_answers_do_not_crash_or_accept_secrets(self):
        for answers in ({"n8n": [], "feishu": "ready", "runtime": "ubuntu"},
                        {"n8n": "none", "feishu": "ready", "runtime": "ubuntu", "api_key": "secret"}):
            with self.subTest(answers=answers):
                with self.assertRaises(ValueError):
                    MODULE.assess(answers, observed={})


if __name__ == "__main__":
    unittest.main()
