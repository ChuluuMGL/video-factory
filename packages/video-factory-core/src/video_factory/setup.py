"""Offline project intake and deterministic provisioning preview.

This module never contacts Feishu, n8n, or a model provider. A folder manifest is
data, not an instruction source. No resource may be activated from this plan.
"""

import hashlib
import json
from pathlib import Path

from .config import PROJECT_ID


BRIEF_NAME = "video-factory.brief.json"
BLUEPRINT_VERSION = 1
REQUIRED_TEXT = {
    "product_category": "商品属于什么品类？",
    "target_market": "面向哪个国家或地区？",
    "business_goal": "这条生产线优先服务什么业务目标？",
    "feishu_target": "使用哪个飞书空间，并且要新建 Base 还是复用现有 Base？",
    "n8n_target": "工作流准备部署到哪个 n8n 环境？",
    "script_reviewer": "谁负责人工审核脚本？",
    "video_reviewer": "谁负责人工审核视频？",
    "model_route": "视频与脚本分别走 API 还是本地模型？",
    "spend_policy": "单条费用、重试次数和预算上限是什么？",
    "category_rule_source": "该品类的商品真值和平台合规规则以什么资料为准？",
}
BRIEF_KEYS = {"project_id", "products", *REQUIRED_TEXT}
PRODUCT_KEYS = {"sku_id", "name", "variant", "truth_source"}


def _regular_file(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("MANIFEST_FILE_NOT_REGULAR")


def read_project_folder(directory):
    """Read only the explicitly named manifest; list direct files as hints."""
    directory = Path(directory)
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        raise ValueError("EXPLICIT_REGULAR_PROJECT_DIRECTORY_REQUIRED")
    manifest = directory / BRIEF_NAME
    if manifest.exists() or manifest.is_symlink():
        _regular_file(manifest)
        if manifest.stat().st_size > 64 * 1024:
            raise ValueError("MANIFEST_TOO_LARGE")
        brief = json.loads(manifest.read_text(encoding="utf-8"))
    else:
        brief = {}
    if not isinstance(brief, dict):
        raise ValueError("BRIEF_OBJECT_REQUIRED")
    files = sorted(item.name for item in directory.iterdir()
                   if item.is_file() and not item.is_symlink() and item.name != BRIEF_NAME)
    return {"directory": str(directory), "manifest": str(manifest) if manifest.exists() else None,
            "files_seen": files, "brief": brief}


def assess_brief(brief):
    if not isinstance(brief, dict):
        raise ValueError("BRIEF_OBJECT_REQUIRED")
    if set(brief) - BRIEF_KEYS:
        raise ValueError("UNKNOWN_BRIEF_FIELDS")
    project_id = brief.get("project_id")
    if project_id is not None and (not isinstance(project_id, str)
                                   or not PROJECT_ID.fullmatch(project_id)):
        raise ValueError("PROJECT_ID_INVALID")
    questions = []
    if project_id is None:
        questions.append({"field": "project_id", "question": "这个项目的唯一英文短名称是什么？"})
    for field, question in REQUIRED_TEXT.items():
        value = brief.get(field)
        if not isinstance(value, str) or not value.strip():
            questions.append({"field": field, "question": question})
    products = brief.get("products")
    if not isinstance(products, list) or not products:
        questions.append({"field": "products", "question": "有哪些可售 SKU？请逐个给出商品 ID、规格与商品真值来源。"})
    else:
        seen = set()
        for index, product in enumerate(products):
            if not isinstance(product, dict):
                raise ValueError(f"PRODUCT_{index}_OBJECT_REQUIRED")
            if set(product) - PRODUCT_KEYS:
                raise ValueError(f"PRODUCT_{index}_UNKNOWN_FIELDS")
            for field in ("sku_id", "name", "variant", "truth_source"):
                value = product.get(field)
                if not isinstance(value, str) or not value.strip():
                    questions.append({"field": f"products[{index}].{field}",
                                      "question": f"第 {index + 1} 个 SKU 的 {field} 是什么？"})
            sku_id = product.get("sku_id")
            if isinstance(sku_id, str) and sku_id:
                if sku_id in seen:
                    raise ValueError("DUPLICATE_SKU_ID")
                seen.add(sku_id)
    return {"complete": not questions, "questions": questions}


def _field(name, kind, **options):
    if kind == "select":
        options = {"multiple": False,
                   "options": [{"name": value} for value in options["options"]]}
    return {"name": name, "type": kind, **options}


def _resource(kind, key, spec):
    body = json.dumps(spec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {"kind": kind, "key": key, "fingerprint": hashlib.sha256(body.encode()).hexdigest(),
            "spec": spec}


def compile_blueprint(brief):
    assessment = assess_brief(brief)
    if not assessment["complete"]:
        raise ValueError("BRIEF_INCOMPLETE")
    project_id = brief["project_id"]
    # The field definitions use the lark-base field JSON contract. Linked
    # records are intentionally deferred until table IDs exist in a live plan.
    tables = {
        "products": [
            _field("SKU ID", "text"), _field("商品名称", "text"),
            _field("规格", "text"), _field("真值来源", "text"),
            _field("可售状态", "select", options=["待核验", "可售", "暂停"]),
        ],
        "assets": [
            _field("素材 ID", "text"), _field("SKU ID", "text"),
            _field("素材文件", "attachment"), _field("权利与真实性证据", "text"),
            _field("可使用状态", "select", options=["待核验", "可使用", "禁止使用"]),
        ],
        "tasks": [
            _field("业务任务 ID", "text"), _field("SKU ID", "text"),
            _field("计划日期", "datetime"), _field("账号", "text"),
            _field("场景与方向", "text"), _field("脚本", "text"),
            _field("当前脚本版本 ID", "text"), _field("当前视频版本 ID", "text"),
            _field("当前视频", "attachment"), _field("人工审核意见", "text"),
            _field("审核目标版本 ID", "text"), _field("输入指纹", "text"),
            _field("状态", "select", options=["规划待核验", "脚本待生产", "脚本待审核",
                                               "视频待生产", "视频待审核", "视频审核通过",
                                               "脚本返修", "视频返修", "暂停"]),
        ],
        "versions": [
            _field("版本 ID", "text"), _field("业务任务 ID", "text"),
            _field("版本类型", "select", options=["脚本", "视频"]),
            _field("内容或地址", "text"), _field("审核意见快照", "text"),
            _field("来源执行 ID", "text"), _field("内容 SHA-256", "text"),
            _field("上一版本 ID", "text"),
        ],
        "review_events": [
            _field("审核事件 ID", "text"), _field("业务任务 ID", "text"),
            _field("审核目标版本 ID", "text"),
            _field("审核阶段", "select", options=["脚本", "视频"]),
            _field("审核决定", "select", options=["通过", "退回", "待澄清"]),
            _field("人工意见", "text"), _field("审核人", "text"),
            _field("审核时间", "datetime"), _field("来源记录修订", "text"),
        ],
        "execution_ledger": [
            _field("关联 ID", "text"), _field("业务任务 ID", "text"),
            _field("审核事件 ID", "text"), _field("冻结输入 SHA-256", "text"),
            _field("执行路线", "select", options=["脚本正常", "视频正常", "脚本返修", "视频返修"]),
            _field("n8n 执行 ID", "text"), _field("Provider 请求 ID", "text"),
            _field("执行状态", "select", options=["已领取", "已受理", "结果未知", "已交付", "失败"]),
            _field("尝试次数", "number", style={"type": "plain", "precision": 0}),
            _field("实际费用", "number"), _field("费用币种", "text"),
        ],
    }
    resources = []
    for table, fields in tables.items():
        proposed_records = ([{"SKU ID": item["sku_id"], "商品名称": item["name"],
                              "规格": item["variant"], "真值来源": item["truth_source"],
                              "可售状态": "待核验"} for item in brief["products"]]
                            if table == "products" else [])
        resources.append(_resource("feishu_table", f"{project_id}.{table}",
                                   {"name": f"VF {project_id} {table}", "fields": fields,
                                    "proposed_records": proposed_records, "active": False}))
    workflows = {
        "script_production": ("脚本待生产", "脚本待审核", "只领取商品真值和输入齐全的任务"),
        "video_production": ("视频待生产", "视频待审核", "必须绑定人工通过的当前脚本版本"),
        "script_repair": ("脚本返修", "脚本待审核", "必须绑定脚本版本和人工退回意见"),
        "video_repair": ("视频返修", "视频待审核", "必须绑定视频版本和人工退回意见"),
        "health_notify": (None, None, "仅提醒卡住、失败和待审核事件，不代替人工决定"),
    }
    for key, (input_status, output_status, purpose) in workflows.items():
        receipts = ([] if key == "health_notify" else
                    ["审核事件 ID", "关联 ID", "输入 SHA-256", "n8n 执行 ID"])
        resources.append(_resource("n8n_workflow", f"{project_id}.{key}",
                                   {"name": f"VF {project_id} {key}", "purpose": purpose,
                                    "input_status": input_status, "output_status": output_status,
                                    "same_task_version_single_owner": True,
                                    "required_receipts": receipts,
                                    "provider_request_or_reuse_evidence": key != "health_notify",
                                    "active": False, "nodes": [], "credentials": [],
                                    "implementation_status": "template_required"}))
    return {"blueprint_version": BLUEPRINT_VERSION, "project_id": project_id,
            "project_context": {key: brief[key] for key in REQUIRED_TEXT},
            "resources": resources, "installation_state": "preview_only",
            "runtime_status": "not_running", "execute_allowed": False,
            "blockers": ["LIVE_RESOURCE_DISCOVERY_REQUIRED", "LIVE_ADAPTERS_NOT_VERIFIED",
                         "WORKFLOW_TEMPLATES_NOT_IMPORTED_OR_VERIFIED",
                         "WORKER_SERVICE_NOT_IMPLEMENTED",
                         "CATEGORY_RULES_AND_PRODUCT_TRUTH_NOT_ACCEPTED",
                         "END_TO_END_CANARY_NOT_ACCEPTED"]}


def reconcile(blueprint, inventory=None):
    """Pure preview. An absent inventory means unknown, not an empty server."""
    known = {}
    if inventory is not None:
        if not isinstance(inventory, dict) or not isinstance(inventory.get("resources"), list):
            raise ValueError("INVENTORY_RESOURCES_REQUIRED")
        for item in inventory["resources"]:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str)
                                                     for k in ("kind", "key", "fingerprint")):
                raise ValueError("INVENTORY_ITEM_INVALID")
            identity = (item["kind"], item["key"])
            if identity in known:
                raise ValueError("DUPLICATE_INVENTORY_IDENTITY")
            known[identity] = item
    actions = []
    for resource in blueprint["resources"]:
        identity = (resource["kind"], resource["key"])
        actual = known.get(identity)
        if inventory is None:
            action = "discover_live_resource"
        elif actual is None:
            action = "propose_create"
        elif actual["fingerprint"] == resource["fingerprint"]:
            action = "reuse"
        else:
            action = "conflict_review"
        actions.append({"kind": resource["kind"], "key": resource["key"],
                        "fingerprint": resource["fingerprint"], "action": action})
    return {"project_id": blueprint["project_id"], "actions": actions,
            "runtime_status": "not_running", "execute_allowed": False}
