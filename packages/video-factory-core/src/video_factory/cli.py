"""Setup and customer-host alpha management; general paid routes stay disabled."""

import argparse
import json
import os
from pathlib import Path

from . import __version__
from .canary import CanaryRuntime, CanaryTask
from .config import ENV_NAME, draft_config, inspect_config
from .discovery import compare_discovery, discover_base, discover_n8n
from .setup import assess_brief, compile_blueprint, read_project_folder, reconcile
from .setup_cli import run_setup
from .runtime_cli import register_runtime, run_runtime
from .host_cli import register_host, run_host
from .stack_cli import register_stack, run_stack
from .setup_deploy import register_setup_deploy, run_setup_deploy
from .worker_cli import register_worker, run_worker
from .stack_worker import run_stack_worker
from .feishu_cli import register_feishu,run_feishu,run_stack_feishu
from .automation import template as queue_template
from .setup_feishu_cli import register as register_setup_feishu, run as run_setup_feishu
from .review_cli import register as register_review, run_review, run_stack as run_stack_review
from .setup_run import register as register_setup_run, run as run_setup_run
from .workflow_templates import compile_workflow_templates


def _print(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _load(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("CONFIG_FILE_NOT_REGULAR")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vfctl")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    from .workspace import register as register_workspace, cli as workspace_cli
    register_workspace(commands)
    from .base_results_cli import register as register_results, cli as results_cli
    register_results(commands)
    from .production_setup import register as register_production, cli as production_cli
    register_production(commands)
    register_runtime(commands)
    register_host(commands)
    register_stack(commands)
    register_worker(commands)
    register_worker(commands,container=True)
    register_setup_deploy(commands)
    register_setup_run(commands)
    register_setup_feishu(commands)
    register_review(commands)
    register_feishu(commands)
    register_feishu(commands,container=True)
    queue_parser=commands.add_parser("queue-template",help="disabled n8n project queue reader; no paid dispatch")
    queue_parser.add_argument("--project",required=True)
    queue_parser.add_argument("--credential-id",required=True)
    dispatch_template=commands.add_parser('dispatch-template',help='disabled n8n schedule for exact approved tasks only')
    dispatch_template.add_argument('--project',required=True)
    dispatch_template.add_argument('--credential-id',required=True)
    setup = commands.add_parser("setup", help="resume an offline customer Setup plan; does not deploy")
    setup.add_argument("--session", type=Path, required=True, help="absolute session file in an existing private directory")
    view = setup.add_mutually_exclusive_group()
    view.add_argument("--json", action="store_true", help="return the next question or plan without prompting")
    view.add_argument("--interactive", action="store_true", help="require a real interactive terminal")
    setup.add_argument("--answers", help="absolute JSON answer file, or - for stdin; no raw secrets")
    setup.add_argument("--expect-revision", type=int, help="revision returned by the last Setup response")
    setup.add_argument("--from-session", type=Path, help="copy organization/deployment configuration into a new project draft")
    init = commands.add_parser("init", help="create a disabled project config")
    init.add_argument("--project-id", required=True)
    init.add_argument("--output", type=Path, required=True)
    inspect = commands.add_parser("inspect", help="validate a config offline")
    inspect.add_argument("--config", type=Path, required=True)
    intake = commands.add_parser("intake", help="inspect a project folder and ask for missing setup facts")
    intake.add_argument("--project-dir", type=Path, required=True)
    plan = commands.add_parser("plan", help="preview Base and n8n resources without creating them")
    plan.add_argument("--project-dir", type=Path, required=True)
    plan.add_argument("--inventory", type=Path, help="optional resource snapshot for offline reconciliation")
    discover = commands.add_parser("discover", help="read only one explicit Base and n8n instance")
    discover.add_argument("--project-dir", type=Path, required=True)
    discover.add_argument("--base-token-env", required=True)
    discover.add_argument("--n8n-url", required=True)
    discover.add_argument("--n8n-api-key-env", required=True)
    template = commands.add_parser("template-preview", help="show disabled n8n route templates offline")
    template.add_argument("--project-dir", type=Path, required=True)
    template.add_argument("--worker-origin", required=True)
    template.add_argument("--credential-id", required=True)
    template.add_argument("--credential-name", default="Video Factory worker")
    canary = commands.add_parser("canary", help="rehearse normal and repair lanes without any provider")
    canary.add_argument("--project-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'production-setup':
        return production_cli(args)
    if args.command == 'base-results':
        return results_cli(args)
    if args.command == 'workspace':
        return workspace_cli(args)
    if args.command == 'setup-run':
        return run_setup_run(args)
    if args.command == 'review-ui':
        return run_review(args)
    if args.command == 'stack-review':
        return run_stack_review(args)
    if args.command == 'setup-feishu':
        return run_setup_feishu(args)
    if args.command == "setup-deploy":
        return run_setup_deploy(args)
    if args.command == 'dispatch-template':
        from .dispatch import template
        try: _print(template(args.project,args.credential_id)); return 0
        except ValueError as error: _print({'error':str(error)}); return 2
    if args.command == "queue-template":
        try:
            _print(queue_template(args.project,args.credential_id));return 0
        except ValueError as error:
            _print({"error":str(error)});return 2
    if args.command == "feishu":
        return run_feishu(args)
    if args.command == "stack-feishu":
        return run_stack_feishu(args)
    if args.command == "stack-worker":
        return run_stack_worker(args)
    if args.command == "worker":
        return run_worker(args)
    if args.command == "stack":
        return run_stack(args)
    if args.command == "host":
        return run_host(args)
    if args.command == "runtime":
        return run_runtime(args)
    if args.command == "setup":
        return run_setup(args)
    try:
        if args.command == "init":
            if not args.output.is_absolute() or not args.output.parent.is_dir() or args.output.parent.is_symlink():
                raise ValueError("EXPLICIT_EXISTING_OUTPUT_DIRECTORY_REQUIRED")
            config = draft_config(args.project_id)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(args.output, flags, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
            except BaseException:
                args.output.unlink(missing_ok=True)
                raise
            _print({"created": str(args.output), "project_id": args.project_id,
                    "runtime_status": "not_running", "execute_allowed": False})
            return 0
        if args.command == "inspect":
            result = inspect_config(_load(args.config))
            _print({"config": str(args.config), **result})
            return 0 if result["schema_valid"] else 2
        project = read_project_folder(args.project_dir)
        assessment = assess_brief(project["brief"])
        if args.command == "intake":
            _print({"project_dir": project["directory"], "manifest": project["manifest"],
                    "files_seen": project["files_seen"], **assessment,
                    "runtime_status": "not_running", "execute_allowed": False})
            return 0
        if not assessment["complete"]:
            _print({"project_dir": project["directory"], "error": "BRIEF_INCOMPLETE",
                    **assessment, "runtime_status": "not_running", "execute_allowed": False})
            return 2
        blueprint = compile_blueprint(project["brief"])
        if args.command == "canary":
            sku = project["brief"]["products"][0]
            task = CanaryTask("CANARY-001", sku["sku_id"], sku["truth_source"],
                              project["brief"]["business_goal"])
            simulation = CanaryRuntime([task])
            simulation.dispatch("script_production", task.task_id)
            simulation.review(task.task_id, task.script_version, "reject", "canary-human",
                              "Canary review: clarify one product claim")
            simulation.dispatch("script_repair", task.task_id)
            simulation.review(task.task_id, task.script_version, "pass", "canary-human")
            simulation.dispatch("video_production", task.task_id)
            simulation.review(task.task_id, task.video_version, "reject", "canary-human",
                              "Canary review: correct visual continuity")
            simulation.dispatch("video_repair", task.task_id)
            simulation.review(task.task_id, task.video_version, "pass", "canary-human")
            _print({"status": task.status, "routes": [row["route"] for row in task.executions],
                    "version_count": len(task.versions), "review_count": len(task.reviews),
                    "provider_requests": 0, "live_resources_touched": 0,
                    "runtime_status": "not_running", "execute_allowed": False})
            return 0
        if args.command == "template-preview":
            templates = compile_workflow_templates(
                blueprint, args.worker_origin, args.credential_id,
                args.credential_name)
            _print({"project_dir": project["directory"], "templates": templates,
                    "runtime_status": "not_running", "execute_allowed": False,
                    "blockers": ["WORKER_SERVICE_NOT_IMPLEMENTED",
                                 "N8N_IMPORT_AND_EXECUTION_NOT_VERIFIED"]})
            return 0
        if args.command == "discover":
            for name in (args.base_token_env, args.n8n_api_key_env):
                if not ENV_NAME.fullmatch(name):
                    raise ValueError("CREDENTIAL_ENV_NAME_INVALID")
            base_token = os.environ.get(args.base_token_env, "")
            n8n_key = os.environ.get(args.n8n_api_key_env, "")
            if not base_token or not n8n_key:
                raise ValueError("CREDENTIAL_ENV_MISSING")
            base = discover_base(base_token)
            n8n = discover_n8n(args.n8n_url, n8n_key)
            _print({"project_dir": project["directory"], "base": base, "n8n": n8n,
                    "comparison": compare_discovery(blueprint, base, n8n)})
            return 0
        inventory = _load(args.inventory) if args.inventory else None
        _print({"project_dir": project["directory"], "blueprint": blueprint,
                "reconciliation": reconcile(blueprint, inventory)})
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        _print({"error": str(error), "runtime_status": "not_running", "execute_allowed": False})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
