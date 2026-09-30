"""Setup integrity and actual terminal checks; execute on the cloud Linux runner."""

from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import select
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from video_factory.cli import main
from video_factory.onboarding import (QUESTIONS, SessionStore, SetupError, apply_answers,
                                      describe, new_session, read_json)
from video_factory.setup_cli import interactive


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "setup"


def answers():
    return json.loads((EXAMPLES / "answers.json").read_text())


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "customer.setup.json"
        self.store = SessionStore(self.path)

    def test_new_and_resumed_session_are_private_and_stable(self):
        initial = self.store.start()
        first = describe(initial)
        self.assertEqual(first["status"], "needs_input")
        self.assertEqual(first["next_question"]["field"], "organization.id")
        self.assertEqual(self.store.start(), initial)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.store.lock_path.stat().st_mode), 0o600)

    def test_complete_plan_has_no_remote_or_verification_claim(self):
        self.store.start()
        result = self.store.answer(answers(), 0)
        self.assertEqual(result["status"], "plan_ready")
        self.assertEqual(len(result["plan"]["resource_intents"]), 11)
        self.assertEqual(result["installation_state"], "draft")
        self.assertFalse(result["execute_allowed"])
        self.assertFalse(result["plan"]["paid_requests_allowed"])
        self.assertTrue(all(value == "not_run" for value in result["verification"].values()))
        self.assertEqual(result["plan"]["model_capabilities"]["video"], "scoped_canary_only")

    def test_restart_and_repeat_preserve_plan_and_revision(self):
        self.store.start()
        first = self.store.answer(answers(), 0)
        reopened = SessionStore(self.path)
        self.assertEqual(describe(reopened.start()), {k: v for k, v in first.items() if k != "invalidated_fields"})
        self.assertEqual(reopened.answer(answers(), 1)["revision"], 1)

    def test_stale_revision_and_two_writers_do_not_lose_updates(self):
        self.store.start()
        def write(value):
            try:
                return SessionStore(self.path).answer({"organization.id": value}, 0)["revision"]
            except SetupError as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ["org_one", "org_two"]))
        self.assertEqual(results.count(1), 1)
        self.assertTrue(any(value in ("SETUP_SESSION_BUSY", "SETUP_REVISION_CONFLICT") for value in results))
        before = self.path.read_bytes()
        with self.assertRaisesRegex(SetupError, "REVISION_CONFLICT"):
            self.store.answer({"organization.name": "stale"}, 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_existing_lock_is_not_deleted_or_bypassed(self):
        self.store.start()
        with self.store.locked():
            with self.assertRaisesRegex(SetupError, "SESSION_BUSY"):
                SessionStore(self.path).read()
        self.assertEqual(self.store.read()["revision"], 0)

    def test_atomic_save_failure_keeps_checkpoint_and_cleans_temporary(self):
        self.store.start()
        before = self.path.read_bytes()
        with patch("video_factory.onboarding.os.replace", side_effect=OSError("synthetic interrupted write")):
            with self.assertRaises(OSError):
                self.store.answer({"organization.id": "new_org"}, 0)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".customer.setup.json.*")), [])

    def test_target_change_invalidates_credentials_and_plan(self):
        self.store.start()
        first = self.store.answer(answers(), 0)
        changed = self.store.answer({"deployment.host": "different.example.invalid"}, 1)
        self.assertIsNone(changed["plan"])
        self.assertIn("deployment.ssh_identity_ref", changed["invalidated_fields"])
        self.assertIn("project.video_credential_ref", changed["invalidated_fields"])
        refreshed = answers()
        refreshed["deployment.host"] = "different.example.invalid"
        second = self.store.answer(refreshed, 2)
        self.assertNotEqual(first["plan"]["plan_sha256"], second["plan"]["plan_sha256"])
        self.assertNotEqual(first["plan"]["target_sha256"], second["plan"]["target_sha256"])

    def test_tenant_change_invalidates_people_and_base(self):
        self.store.start()
        self.store.answer(answers(), 0)
        result = self.store.answer({"deployment.feishu_tenant": "different_tenant"}, 1)
        self.assertTrue({"organization.admin_ref", "project.script_reviewer", "project.video_reviewer",
                         "project.base_target"}.issubset(result["invalidated_fields"]))

    def test_organization_change_does_not_inherit_other_customer_data(self):
        self.store.start()
        self.store.answer(answers(), 0)
        self.store.answer({"organization.id": "other_customer"}, 1)
        config = self.store.read()["configuration"]
        self.assertEqual(config, {"organization": {"id": "other_customer"}, "deployment": {}, "project": {}})

    def test_new_project_copies_configuration_only(self):
        self.store.start()
        self.store.answer(answers(), 0)
        source = self.store.read()
        target = SessionStore(self.root / "second.setup.json")
        child = target.start(source)
        self.assertEqual(child["configuration"]["organization"], source["configuration"]["organization"])
        self.assertEqual(child["configuration"]["project"], {})
        self.assertEqual(child["revision"], 0)
        self.assertEqual(child["source_session_id"], source["session_id"])
        self.assertNotEqual(child["session_id"], source["session_id"])
        self.assertTrue(all(v == "not_run" for v in describe(child)["verification"].values()))
        with self.assertRaisesRegex(SetupError, "SOURCE_ONLY_FOR_NEW_SESSION"):
            target.start(source)

    def test_partial_source_cannot_create_project(self):
        with self.assertRaisesRegex(SetupError, "SOURCE_DEPLOYMENT_INCOMPLETE"):
            new_session(new_session())

    def test_route_change_removes_old_billing_and_does_not_offer_seedance(self):
        self.store.start()
        self.store.answer(answers(), 0)
        result = self.store.answer({"project.video_route": "deferred"}, 1)
        self.assertEqual(result["status"], "plan_ready")
        self.assertNotIn("video_credential_ref", result["plan"]["configuration"]["project"])
        with self.assertRaisesRegex(SetupError, "CHOICE_UNSUPPORTED"):
            self.store.answer({"project.video_route": "seedance"}, 2)

    def test_raw_secrets_and_unknown_fields_never_enter_checkpoint_or_errors(self):
        self.store.start()
        secret = "sk-" + "SyntheticCredentialOnly" * 3
        for payload in ({"api_key": secret}, {"organization.name": secret},
                        {"deployment.feishu_credential_ref": secret},
                        {"project.products": [{"sku_id": "TEST", "name": secret,
                                               "variant": "test", "truth_source": "test"}]}):
            before = self.path.read_bytes()
            with self.assertRaises(SetupError) as caught:
                self.store.answer(payload, 0)
            self.assertNotIn(secret, str(caught.exception))
            self.assertNotIn(secret, self.path.read_text())
            self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_values_do_not_partially_save_valid_fields(self):
        self.store.start()
        bad_values = [{"deployment.host": "https://user:pass@host.invalid"},
                      {"project.products": answers()["project.products"] * 2},
                      {"project.name": "bad\x1b[31m"}, {"project.video_credential_ref": "env:VIDEO_KEY"}]
        for bad in bad_values:
            before = self.path.read_bytes()
            with self.assertRaises(SetupError):
                self.store.answer({"organization.id": "valid_org", **bad}, 0)
            self.assertEqual(self.path.read_bytes(), before)

    def test_tampered_session_cannot_claim_verification_or_unknown_schema(self):
        original = self.store.start()
        for modified in (dict(original, schema_version=2), dict(original, schema_version=True),
                         dict(original, runtime_ready=True), dict(original, revision=-1)):
            self.path.write_text(json.dumps(modified))
            with self.assertRaises(SetupError):
                self.store.read()

    def test_symlinks_hardlinks_and_shared_permissions_are_rejected(self):
        other = self.root / "other.json"
        other.write_text("do not overwrite")
        self.path.symlink_to(other)
        with self.assertRaises(OSError):
            self.store.start()
        self.assertEqual(other.read_text(), "do not overwrite")
        self.path.unlink()
        self.store.start()
        os.link(self.path, self.root / "hardlink.json")
        with self.assertRaisesRegex(SetupError, "PRIVATE_REGULAR"):
            self.store.read()
        (self.root / "hardlink.json").unlink()
        self.path.chmod(0o644)
        with self.assertRaisesRegex(SetupError, "PRIVATE_REGULAR"):
            self.store.read()
        shared = self.root / "shared"
        shared.mkdir(mode=0o777)
        shared.chmod(0o777)
        with self.assertRaisesRegex(SetupError, "DIRECTORY_MUST_BE_OWNED"):
            SessionStore(shared / "state.json")

    def test_duplicate_json_keys_invalid_json_and_size_limits(self):
        for data in ('{"organization.id":"first","organization.id":"second"}', '{broken}',
                     '{"organization.id":NaN}', ' ' * (256 * 1024 + 1)):
            with self.assertRaises(SetupError):
                read_json(io.StringIO(data))

    def test_secret_reference_handoff_does_not_resolve_environment(self):
        partial = {q.field: answers()[q.field] for q in QUESTIONS[:6]}
        self.store.start()
        result = self.store.answer(partial, 0)
        self.assertEqual(result["status"], "needs_secret")
        self.assertFalse(result["next_question"]["accepts_secret_value"])
        with patch.dict(os.environ, {"EXAMPLE_FEISHU_CREDENTIAL": "never_copy_this_secret"}):
            result = self.store.answer(answers(), 1)
        self.assertNotIn("never_copy_this_secret", self.path.read_text())
        self.assertNotIn("never_copy_this_secret", json.dumps(result))

    def test_terminal_and_agent_produce_identical_plan(self):
        self.store.start()
        pending = iter(QUESTIONS)
        fields = answers()
        def reply(prompt):
            question = next(pending)
            value = fields[question.field]
            return str(EXAMPLES / "products.json") if question.kind == "products" else value
        terminal = interactive(self.store, read=reply, read_reference=reply, write=lambda _: None)
        agent, _ = apply_answers(new_session(), fields, 0)
        self.assertEqual(terminal["plan"], describe(agent)["plan"])

    def test_interrupted_terminal_resumes_last_saved_answer(self):
        self.store.start()
        calls = iter(["example_org"])
        def reply(prompt):
            try:
                return next(calls)
            except StopIteration:
                raise EOFError
        result = interactive(self.store, read=reply, write=lambda _: None)
        self.assertTrue(result["interrupted"])
        self.assertEqual(result["revision"], 1)
        self.assertEqual(describe(SessionStore(self.path).read())["next_question"]["field"], "organization.name")

    def test_json_cli_batch_and_stdin_never_prompt(self):
        with contextlib.redirect_stdout(output := io.StringIO()):
            code = main(["setup", "--session", str(self.path), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["revision"], 0)
        with patch("sys.stdin", io.StringIO(json.dumps(answers()))), contextlib.redirect_stdout(output := io.StringIO()):
            code = main(["setup", "--session", str(self.path), "--json", "--answers", "-", "--expect-revision", "0"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "plan_ready")

    def test_agent_answers_returned_question_contract_one_field_at_a_time(self):
        result = describe(self.store.start())
        expected = answers()
        visited = []
        while result["next_question"]:
            question = result["next_question"]
            field = question["field"]
            schema = question["input_schema"]
            value = expected[field]
            if schema["type"] == "array":
                self.assertEqual(field, "project.products")
                self.assertNotIn("路径", question["question"])
                self.assertEqual(set(schema["items"]["required"]), set(value[0]))
                self.assertFalse(schema["items"]["additionalProperties"])
                before = self.path.read_bytes()
                with self.assertRaisesRegex(SetupError, "PRODUCTS_ARRAY_REQUIRED"):
                    self.store.answer({field: str(EXAMPLES / "products.json")}, result["revision"])
                self.assertEqual(self.path.read_bytes(), before)
            else:
                self.assertEqual(schema["type"], "string")
                self.assertIsInstance(value, str)
            if "enum" in schema:
                self.assertIn(value, schema["enum"])
            with patch("sys.stdin", io.StringIO(json.dumps({field: value}))), contextlib.redirect_stdout(output := io.StringIO()):
                code = main(["setup", "--session", str(self.path), "--json", "--answers", "-",
                             "--expect-revision", str(result["revision"])])
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            visited.append(field)
        self.assertEqual(len(visited), len(QUESTIONS))
        self.assertEqual(result["status"], "plan_ready")
        baseline, _ = apply_answers(new_session(), expected, 0)
        self.assertEqual(result["plan"], describe(baseline)["plan"])

    def test_terminal_back_edits_the_saved_answer(self):
        self.store.start()
        replies = iter(["first_org", ":back", "changed_org", "Customer", ":quit"])
        reply = lambda _: next(replies)
        result = interactive(self.store, read=reply, read_reference=reply, write=lambda _: None)
        self.assertEqual(result["revision"], 3)
        self.assertEqual(self.store.read()["configuration"]["organization"],
                         {"id": "changed_org", "name": "Customer"})

    def test_agent_mutation_requires_revision_before_creating_session(self):
        with patch("sys.stdin", io.StringIO(json.dumps(answers()))), contextlib.redirect_stdout(output := io.StringIO()):
            code = main(["setup", "--session", str(self.path), "--json", "--answers", "-"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["error"], "SETUP_EXPECT_REVISION_REQUIRED")
        self.assertFalse(self.path.exists())

    def test_cli_invalid_secret_and_malformed_json_do_not_echo_input(self):
        secret = "sk-" + "OnlySyntheticForTests" * 3
        for data in (json.dumps({"api_key": secret}), '{"api_key": "' + secret):
            with patch("sys.stdin", io.StringIO(data)), contextlib.redirect_stdout(output := io.StringIO()):
                code = main(["setup", "--session", str(self.path), "--json", "--answers", "-", "--expect-revision", "0"])
            self.assertEqual(code, 2)
            self.assertNotIn(secret, output.getvalue())
            if self.path.exists():
                self.assertNotIn(secret, self.path.read_text())

    def test_explicit_interactive_without_tty_returns_actionable_error(self):
        with patch("sys.stdin", io.StringIO()), contextlib.redirect_stdout(output := io.StringIO()):
            code = main(["setup", "--session", str(self.path), "--interactive"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["error"], "SETUP_TTY_REQUIRED_USE_JSON")
        self.assertFalse(self.path.exists())

    def test_actual_pty_welcome_reference_masking_and_resume(self):
        """A real Linux PTY verifies prompts/getpass, not just mocked input()."""
        partial = {q.field: answers()[q.field] for q in QUESTIONS[:6]}
        self.store.start()
        self.store.answer(partial, 0)
        master, slave = pty.openpty()
        process = subprocess.Popen([sys.executable, "-m", "video_factory.cli", "setup",
                                    "--session", str(self.path), "--interactive"],
                                   stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        received = b""
        def until(marker):
            nonlocal received
            end = time.monotonic() + 10
            while marker not in received:
                if time.monotonic() > end:
                    self.fail("terminal prompt timeout")
                readable, _, _ = select.select([master], [], [], .2)
                if readable:
                    received += os.read(master, 65536)
        try:
            until("引用> ".encode())
            self.assertIn("欢迎使用".encode(), received)
            received = b""
            os.write(master, b"secret:example_ssh\n")
            until("输入> ".encode())
            self.assertNotIn(b"secret:example_ssh", received)
            os.write(master, b":quit\n")
            self.assertEqual(process.wait(timeout=10), 0)
            resumed = describe(self.store.read())
            self.assertEqual(resumed["revision"], 2)
            self.assertEqual(resumed["next_question"]["field"], "deployment.feishu_tenant")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)


if __name__ == "__main__":
    unittest.main()
