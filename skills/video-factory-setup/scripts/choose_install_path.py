#!/usr/bin/env python3
"""Read-only first-step inventory for a Video Factory installation.

This script does not collect credentials, install software or contact a remote
n8n instance. An Agent may ask the same questions when Python is unavailable.
"""

import argparse
import json
import platform
import shutil
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener


N8N = ("none", "local-node", "local-docker", "self-hosted", "cloud", "unknown")
FEISHU = ("ready", "partial", "none", "unknown")
RUNTIME = ("ubuntu", "local-desktop", "other-server", "unknown")
FIELDS = {"n8n": N8N, "feishu": FEISHU, "runtime": RUNTIME}
LABELS = {
    "n8n": ("还没有 n8n", "本机 Node.js / npx", "本机 Docker", "已有服务器上的 n8n", "n8n Cloud", "不确定"),
    "feishu": ("已有应用和 Base", "只有应用或 Base", "都没有", "不确定"),
    "runtime": ("已有 Ubuntu 24.04 x86_64 主机", "希望运行在本机 Windows / Mac", "其他服务器", "还没决定"),
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def local_inventory():
    """Observe only this computer; a listening port is not an API check."""
    running = False
    try:
        request = Request("http://127.0.0.1:5678/healthz", method="GET")
        with build_opener(_NoRedirect).open(request, timeout=1) as response:
            running = response.status == 200
    except Exception:
        pass
    return {"os": platform.system(), "architecture": platform.machine(),
            "n8n_command_found": shutil.which("n8n") is not None,
            "docker_command_found": shutil.which("docker") is not None,
            "localhost_5678_health_candidate": running,
            "remote_instances_scanned": False}


def assess(answers, observed=None):
    if not isinstance(answers, dict) or set(answers) != set(FIELDS):
        raise ValueError("INSTALL_CHOICES_REQUIRED_NO_SECRETS")
    for field, options in FIELDS.items():
        if not isinstance(answers[field], str) or answers[field] not in options:
            raise ValueError("INSTALL_CHOICE_INVALID_" + field.upper())
    observed = local_inventory() if observed is None else observed
    n8n, runtime = answers["n8n"], answers["runtime"]
    if n8n == "none" and runtime == "ubuntu":
        route = "managed_ubuntu_current_release"
        next_steps = ["在这台 Ubuntu 主机核对版本和依赖；固定安装包会部署自己的 n8n，不需预装。",
                      "随后按 Skill 配置飞书、首个项目，并逐项验收。"]
        supported = True
    elif n8n in ("local-node", "local-docker", "self-hosted", "cloud"):
        route = "reuse_existing_n8n"
        next_steps = ["保留现有 n8n 工作流和凭据；先只读核对实例、API 权限和飞书应用/Base。",
                      "运行完整 Skill 内的 scripts/portable_n8n.py welcome，接入原实例并配置关闭状态的飞书与模型工作流；先核对该实例 API、版本与权限，再做本项目验收。"]
        supported = n8n != "cloud"
    elif n8n == "none" and runtime in ("local-desktop", "other-server"):
        route = "portable_n8n_install_method"
        next_steps = ["先选择在本机 Node.js、Docker、现有服务器或 n8n Cloud 使用 n8n；Agent 按所选方式检查官方安装要求。",
                      "运行完整 Skill 内的 scripts/portable_n8n.py welcome，选择 Node.js 或 Docker 安装并配置飞书与模型；无需另租 Ubuntu，安装完成后仍需本项目验收。"]
        supported = True
    else:
        route = "host_or_n8n_choice_needed"
        next_steps = ["确认 n8n 位于本机、其他服务器还是 n8n Cloud，以及产品执行服务打算运行的位置。",
                      "不要因为本机没有 n8n 就默认购买服务器；已有 Ubuntu 主机时固定包可自带 n8n。"]
        supported = False
    if answers["feishu"] != "ready":
        next_steps.append("继续核对飞书组织、应用和 Base；缺项由 Setup 引导准备，不复用其他客户凭据。")
    if n8n == "cloud":
        next_steps.append("Cloud SaaS 实例本轮未实测；先核对当前套餐的公共 API 和执行端点权限，再由实例操作者验证，不能标记已验收。")
    return {"answers": answers, "observed_local": observed, "route": route,
            "current_release_can_complete": supported,
            "entry": "scripts/portable_n8n.py welcome",
            "candidate_entry": "scripts/portable_n8n.py welcome",  # legacy reader compatibility
            "instance_verified": False,
            "production_accepted": False,
            "candidate_production_accepted": False,
            "existing_n8n_modified": False, "software_installed": False,
            "provider_requests": 0, "next_steps": next_steps}


def _ask(label, options, descriptions):
    print(label)
    for index, (option, description) in enumerate(zip(options, descriptions), 1):
        print(f"  {index}. {description} ({option})")
    while True:
        value = input("请选择编号> ").strip()
        if value.isdigit() and 1 <= int(value) <= len(options):
            return options[int(value) - 1]
        print("请输入列出的编号。")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Video Factory 安装方式识别（只读）")
    parser.add_argument("--answers", type=Path, help="仅含 n8n/feishu/runtime 三项的非秘密 JSON")
    args = parser.parse_args(argv)
    if args.answers:
        if args.answers.is_symlink() or not args.answers.is_file() or args.answers.stat().st_size > 4096:
            parser.error("ANSWERS_FILE_INVALID")
        answers = json.loads(args.answers.read_text(encoding="utf-8"))
    else:
        print("Video Factory · 先识别已有环境；此步骤不安装或修改 n8n。")
        answers = {"n8n": _ask("你现在的 n8n 在哪里？", N8N, LABELS["n8n"]),
                   "feishu": _ask("飞书应用和 Base 准备情况？", FEISHU, LABELS["feishu"]),
                   "runtime": _ask("希望产品执行服务运行在哪里？", RUNTIME, LABELS["runtime"])}
    print(json.dumps(assess(answers), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
