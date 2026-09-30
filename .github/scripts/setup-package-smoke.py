"""Exercise the installed wheel through separate CLI processes, on cloud CI."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile


def call(*args):
    completed = subprocess.run([sys.executable, "-m", "video_factory.cli", "setup", *args],
                               capture_output=True, text=True, check=True, timeout=15)
    return json.loads(completed.stdout)


with tempfile.TemporaryDirectory(prefix="vf-setup-wheel-") as folder:
    session = Path(folder) / "customer.setup.json"
    first = call("--session", str(session), "--json")
    assert first["revision"] == 0 and first["status"] == "needs_input"
    planned = call("--session", str(session), "--json", "--answers", sys.argv[1], "--expect-revision", "0")
    assert planned["status"] == "plan_ready" and not planned["execute_allowed"]
    resumed = call("--session", str(session), "--json")
    assert resumed["plan"]["plan_sha256"] == planned["plan"]["plan_sha256"]
    second = call("--session", str(Path(folder) / "second.setup.json"),
                  "--from-session", str(session), "--json")
    assert second["next_question"]["field"] == "project.id"
    assert second["source_session_id"] == first["session_id"]
    assert all(value == "not_run" for value in resumed["verification"].values())
    # Follow the installed CLI's own question contract, not just a bulk fixture.
    fixture = json.loads(Path(sys.argv[1]).read_text())
    incremental = second
    answered = 0
    answer_file = Path(folder) / "answer.json"
    while incremental["next_question"]:
        question = incremental["next_question"]
        value = fixture[question["field"]]
        if question["input_schema"]["type"] == "array":
            assert isinstance(value, list) and "路径" not in question["question"]
        else:
            assert isinstance(value, str)
        answer_file.write_text(json.dumps({question["field"]: value}))
        incremental = call("--session", str(Path(folder) / "second.setup.json"), "--json",
                           "--answers", str(answer_file), "--expect-revision", str(incremental["revision"]))
        answered += 1
    assert incremental["plan"]["plan_sha256"] == planned["plan"]["plan_sha256"]
    print(json.dumps({"scope": "installed_wheel_setup_planning", "status": "PASS",
                      "separate_cli_processes": 4 + answered, "question_driven_answers": answered,
                      "plan_sha256": planned["plan"]["plan_sha256"],
                      "resume_verified": True, "new_project_draft_verified": True,
                      "provider_requests": 0, "remote_installation": "not_run"}))
