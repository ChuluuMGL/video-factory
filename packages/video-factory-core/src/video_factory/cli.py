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
    if args.command == 'base-results':
        return results_cli(args)
