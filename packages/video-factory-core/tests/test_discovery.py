import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from video_factory.cli import main
from video_factory.discovery import DiscoveryError, compare_discovery, discover_base, discover_n8n
from video_factory.setup import compile_blueprint
from test_setup import sample_brief


class DiscoveryTests(unittest.TestCase):
    def test_lark_discovery_reads_all_pages_and_no_mutation(self):
        calls = []

        def runner(command):
            calls.append(command)
            self.assertEqual(command[:2], ["lark-cli", "base"])
            self.assertIn("--as", command)
            self.assertEqual(command[command.index("--as") + 1], "user")
            if "+base-get" in command:
                return {"data": {"name": "Isolated Test"}}
            if "+table-list" in command:
                offset = command[command.index("--offset") + 1]
                if offset == "0":
                    return {"data": {"items": [{"table_id": "tblA", "table_name": "A"}],
                                     "has_more": True}}
                self.assertEqual(offset, "1")
                return {"data": {"items": [{"table_id": "tblB", "table_name": "B"}],
                                 "has_more": False}}
            table = command[command.index("--table-id") + 1]
            return {"data": {"items": [{"field_id": "fld" + table,
                                          "field_name": "ID", "type": "text"}],
                             "has_more": False}}

        result = discover_base("baseTest", runner)
        self.assertEqual([table["id"] for table in result["tables"]], ["tblA", "tblB"])
        self.assertEqual(result["tables"][0]["fields"][0]["name"], "ID")
        self.assertTrue(all("create" not in " ".join(call) for call in calls))

    def test_n8n_cursor_pagination_and_header(self):
        calls = []

        def getter(url, headers):
            calls.append(url)
            self.assertEqual(headers["X-N8N-API-KEY"], "test-key")
            if "cursor=" not in url:
                return {"data": [{"id": "wf1", "name": "One", "active": False}],
                        "nextCursor": "next"}
            return {"data": [{"id": "wf2", "name": "Two", "active": True}],
                    "nextCursor": None}

        result = discover_n8n("https://n8n.example.invalid/api/v1", "test-key", getter)
        self.assertEqual([item["id"] for item in result["workflows"]], ["wf1", "wf2"])
        self.assertTrue(result["pagination_complete"])
        self.assertEqual(len(calls), 2)
        self.assertIn("/api/v1/workflows?", calls[0])
        self.assertNotIn("test-key", " ".join(calls))

    def test_n8n_repeated_cursor_fails_closed(self):
        def getter(url, headers):
            return {"data": [], "nextCursor": "repeat"}

        with self.assertRaisesRegex(DiscoveryError, "N8N_CURSOR_INVALID_OR_REPEATED"):
            discover_n8n("https://n8n.example.invalid", "test-key", getter)

    def test_real_local_http_get_is_read_only_and_handles_cursor(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append((self.path, self.headers.get("X-N8N-API-KEY")))
                if "cursor=" in self.path:
                    body = {"data": [{"id": "wf2", "name": "Second", "active": False}],
                            "nextCursor": None}
                else:
                    body = {"data": [{"id": "wf1", "name": "First", "active": False}],
                            "nextCursor": "second"}
                content = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = discover_n8n(f"http://127.0.0.1:{server.server_port}", "test-key")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(len(result["workflows"]), 2)
        self.assertEqual(len(received), 2)
        self.assertTrue(all(key == "test-key" for _, key in received))

    def test_n8n_rejects_credentialed_or_insecure_url(self):
        for url in ("http://n8n.example.invalid", "https://u:p@n8n.example.invalid",
                    "https://n8n.example.invalid?key=value"):
            with self.assertRaises(DiscoveryError):
                discover_n8n(url, "test-key", lambda *_: {})

    def test_existing_name_never_auto_reused(self):
        blueprint = compile_blueprint(sample_brief())
        base = {"tables": [{"id": "tbl1", "name": "VF new_brand products", "fields": []}],
                "pagination_complete": True}
        n8n = {"workflows": [{"id": "wf1", "name": "VF new_brand script_production",
                              "active": False}], "pagination_complete": True}
        result = compare_discovery(blueprint, base, n8n)
        self.assertEqual(result["resources"][0]["verdict"],
                         "exists_requires_structure_review")
        self.assertFalse(result["execute_allowed"])

    def test_cli_discover_with_missing_env_stops_before_network(self):
        with tempfile.TemporaryDirectory(prefix="vf-discovery-") as folder:
            (Path(folder) / "video-factory.brief.json").write_text(json.dumps(sample_brief()))
            with contextlib.redirect_stdout(output := io.StringIO()):
                code = main(["discover", "--project-dir", folder,
                             "--base-token-env", "UNSET_VF_BASE_TOKEN",
                             "--n8n-url", "https://n8n.example.invalid",
                             "--n8n-api-key-env", "UNSET_VF_N8N_API_KEY"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())["error"], "CREDENTIAL_ENV_MISSING")


if __name__ == "__main__":
    unittest.main()
