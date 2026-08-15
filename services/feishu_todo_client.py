# -*- coding: utf-8 -*-
"""
飞书待办（多维表格 / bitable）读取 —— tenant_access_token 版本。
云端自洽，不再依赖本机 lark-cli 用户授权。
字段映射与原 lark-cli 解析结果保持一致，便于 _sync_feishu_todos 直接复用。
"""
import logging
import requests
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://open.feishu.cn"


def _get_tenant_token(app_id: str, app_secret: str) -> str:
    """获取 tenant_access_token（应用身份，长效）"""
    resp = requests.post(
        f"{BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    d = resp.json()
    if d.get("code") == 0:
        return d["tenant_access_token"]
    raise Exception(f"获取tenant_token失败: {d.get('msg')} (code={d.get('code')})")


def _map_record(rec: dict) -> dict:
    """将 bitable 单条 record 映射为与 lark-cli 解析一致的字段结构。"""
    f = rec.get("fields", {}) or {}
    rid = rec.get("record_id", "")

    execs = f.get("任务执行人") or []
    if isinstance(execs, list):
        executor = [u.get("name", "") if isinstance(u, dict) else str(u) for u in execs]
    else:
        executor = []

    prios = f.get("优先级") or []
    if isinstance(prios, list) and prios:
        p0 = prios[0]
        priority = p0.get("text", p0) if isinstance(p0, dict) else str(p0)
    else:
        priority = ""

    done = f.get("完成状态")
    if isinstance(done, list):
        done = bool(done[0]) if done else False
    else:
        done = bool(done)

    return {
        "rid": rid,
        "name": f.get("任务名称", ""),
        "executor": executor,
        "detail": f.get("任务详情描述", ""),
        "deadline": f.get("截止日期", ""),
        "done": done,
        "priority": priority,
        "progress": f.get("任务进度", ""),
    }


def get_todo_records_tenant(
    base_token: str,
    table_id: str,
    view_id: Optional[str] = None,
    app_id: str = "",
    app_secret: str = "",
) -> List[dict]:
    """
    用 tenant_access_token 读取多维表格记录，返回归一化行列表。
    若应用未被授权访问该 base 或无 bitable 权限，会抛异常（调用方应回退 lark-cli）。
    """
    if not app_id or not app_secret:
        raise Exception("缺少 app_id / app_secret，无法获取 tenant_access_token")
    tok = _get_tenant_token(app_id, app_secret)

    rows: List[dict] = []
    page_token = None
    while True:
        params = {"page_size": 100}
        if view_id:
            params["view_id"] = view_id
        if page_token:
            params["page_token"] = page_token
        url = f"{BASE_URL}/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/records"
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {tok}"},
            params=params,
            timeout=20,
        )
        d = r.json()
        if d.get("code") != 0:
            raise Exception(f"bitable读取失败: {d.get('msg')} (code={d.get('code')})")
        data = d.get("data", {})
        for rec in data.get("items", []):
            rows.append(_map_record(rec))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
    return rows
