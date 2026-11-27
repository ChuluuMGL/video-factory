#!/usr/bin/env python3
"""Read-only first-step inventory for a Video Factory installation.

This script does not collect credentials, install software or contact a remote
n8n instance. An Agent may ask the same questions when Python is unavailable.
"""

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener


N8N = ("not-needed", "none", "local-node", "local-docker", "self-hosted", "cloud", "unknown")
FEISHU = ("ready", "partial", "none", "unknown")
RUNTIME = ("local-kernel", "ubuntu", "local-desktop", "other-server", "unknown")
FIELDS = {"n8n": N8N, "feishu": FEISHU, "runtime": RUNTIME}
LABELS = {
    "n8n": (
        "不装 n8n，直接用无 n8n 本机常驻引擎（v0.3，macOS / Linux）",
        "还没有 n8n（准备安装便携 n8n 或服务器完整栈）",
        "本机 Node.js / npx",
        "本机 Docker",
        "已有服务器上的 n8n",
        "n8n Cloud",
        "不确定",
    ),
    "feishu": ("已有应用和 Base", "只有应用或 Base", "都没有", "不确定"),
    "runtime": (
        "本机 macOS / Linux 无 n8n 常驻引擎（v0.3）",
        "已有 Ubuntu 24.04 x86_64 主机（服务器完整栈）",
        "希望运行在本机 Windows / Mac / Linux（便携 n8n）",
        "其他服务器",
        "还没决定",
    ),
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
    os_name = platform.system()
    return {"os": os_name, "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "local_kernel_host_eligible": os_name in ("Darwin", "Linux") and sys.version_info >= (3, 11),
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
    entry = None
    if n8n in ("local-node", "local-docker", "self-hosted", "cloud"):
        route = "reuse_existing_n8n"
        next_steps = ["保留现有 n8n 工作流和凭据；先只读核对实例、API 权限和飞书应用/Base。",
                      "运行完整 Skill 内的 scripts/portable_n8n.py welcome，接入原实例并配置关闭状态的飞书与模型工作流；先核对该实例 API、版本与权限，再做本项目验收。"]
        supported = n8n != "cloud"
        entry = "scripts/portable_n8n.py welcome"
    elif n8n == "not-needed" or (n8n == "none" and runtime == "local-kernel"):
        route = "local_resident_kernel_v03"
        if observed.get("os") == "Windows":
            next_steps = ["v0.3 无 n8n 本机常驻安装器（scripts/install_local_kernel.py）当前要求 macOS 或 Linux（Python 3.11+），暂不支持 Windows 原生。",
                          "若希望在 Windows 本机运行，请改选便携 n8n 路线（scripts/portable_n8n.py welcome）；或改在 macOS / Linux 电脑上执行无 n8n 一键安装。"]
            supported = False
        else:
            next_steps = ["无需安装 n8n 或 Docker、无需另租服务器：在 macOS/Linux（Python 3.11+）使用 v0.3 的 scripts/install_local_kernel.py --mode business --root <全新私有目录> 一键创建独立环境与 vf-setup / vf-run / vf-agent。",
                          "按 references/local-business.md 分步推进：项目配置与参考图自动托管 → 飞书官方身份授权与原表接线 → 只读常驻或 launchd/systemd 用户服务启停；单次付费模型执行与真人表内验收单独确认。"]
            supported = True
            entry = "scripts/install_local_kernel.py --mode business"
    elif n8n == "none" and runtime == "ubuntu":
        route = "managed_ubuntu_current_release"
        next_steps = ["在这台 Ubuntu 主机核对版本和依赖；固定安装包会部署自己的 n8n，不需预装。",
                      "随后按 Skill 配置飞书、首个项目，并逐项验收。"]
        supported = True
        entry = "start.py"
    elif n8n == "none" and runtime in ("local-desktop", "other-server"):
        route = "portable_n8n_install_method"
        next_steps = ["先选择在本机 Node.js、Docker、现有服务器或 n8n Cloud 使用 n8n；Agent 按所选方式检查官方安装要求。若为 macOS 或 Linux 且不想搭 n8n，也可改走 local-kernel（scripts/install_local_kernel.py --mode business）无 n8n 常驻路线。",
                      "运行完整 Skill 内的 scripts/portable_n8n.py welcome，选择 Node.js 或 Docker 安装并配置飞书与模型；无需另租 Ubuntu，安装完成后仍需本项目验收。"]
        supported = True
        entry = "scripts/portable_n8n.py welcome"
    else:
        route = "host_or_n8n_choice_needed"
        next_steps = ["确认选择无 n8n 本机常驻引擎（macOS/Linux）、接入已有 n8n，还是部署 Ubuntu 服务器完整栈。",
                      "不要因为不会搭 n8n 就默认购买服务器：macOS/Linux 可直接走 v0.3 无 n8n 常驻路线，已有 Ubuntu 主机时固定包可自带 n8n。"]
        supported = False
    if answers["feishu"] != "ready":
        next_steps.append("继续核对飞书组织、应用和 Base；缺项由 Setup 引导准备，不复用其他客户凭据。")
    if n8n == "cloud":
        next_steps.append("Cloud SaaS 实例本轮未实测；先核对当前套餐的公共 API 和执行端点权限，再由实例操作者验证，不能标记已验收。")
    return {"answers": answers, "observed_local": observed, "route": route,
            "current_release_can_complete": supported,
            "entry": entry,
            "candidate_entry": entry or "scripts/portable_n8n.py welcome",  # legacy reader compatibility
            "software_installed": False,
            "configuration_saved": False,
            "feishu_verified": False,
            "instance_verified": False,
            "production_accepted": False,
            "candidate_production_accepted": False,
            "existing_n8n_modified": False,
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
        print("Video Factory · 先识别已有环境或无 n8n 常驻选择；此步骤不安装软件、不读取密钥。")
        answers = {"n8n": _ask("你打算怎样处理 n8n / 工作流运行方式？", N8N, LABELS["n8n"]),
                   "feishu": _ask("飞书应用和 Base 准备情况？", FEISHU, LABELS["feishu"]),
                   "runtime": _ask("希望产品执行服务运行在哪里？", RUNTIME, LABELS["runtime"])}
    print(json.dumps(assess(answers), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
