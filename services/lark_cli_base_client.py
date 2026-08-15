# -*- coding: utf-8 -*-
"""
人员管理名单同步客户端 —— 从飞书云文档(多维表格)实时读取人员花名册。

使用 lark-cli 的 user 身份访问（绕过 bot 应用权限限制），流程：
  1. 用 wiki node token 解析出多维表格的 base(app) token
  2. 读取字段列表，建立 field_id -> 字段名 映射
  3. 分页读取记录（值数组形式），按 field_id_list 还原成 {字段名: 值}
  4. 转换为标准 staff 结构并写盘

数据来源（用户提供的云文档）：
  wiki 节点: Xs3owjpB1iXcdEkOV2Zc7AsEni7
  表格 ID : tbljuasjgQRZDN0I
"""
import subprocess
import json
import os
import time
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

LARK_CLI = r"C:\Users\Administrator\.workbuddy\binaries\node\cli-connector-packages\lark-cli.cmd"

# lark-cli.cmd 内部调 node.exe，但 node 不一定在 app.py 进程的 PATH 里
# （看门狗/系统启动时 PATH 可能缺少 managed node 路径）。
# 这里把 managed node 目录注入到子进程 PATH，确保 lark-cli.cmd 能找到 node。
_MANAGED_NODE_DIR = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2"
_NODE_EXE = os.path.join(_MANAGED_NODE_DIR, "node.exe")
_cached_env = None


def _get_env_with_node():
    """返回带 managed node 目录的 env 副本（缓存）"""
    global _cached_env
    if _cached_env is not None:
        return _cached_env
    env = os.environ.copy()
    if os.path.isfile(_NODE_EXE):
        sep = ';' if os.name == 'nt' else ':'
        env['PATH'] = _MANAGED_NODE_DIR + sep + env.get('PATH', '')
    _cached_env = env
    return env
WIKI_NODE_TOKEN = "Xs3owjpB1iXcdEkOV2Zc7AsEni7"   # 人员管理名单 wiki 节点
TABLE_ID = "tbljuasjgQRZDN0I"

# 岗位标签 -> 系统归类(岗位组别)
# C端拍照 / B端拍照 / 瑕疵图 均归为「拍照」
# 隐私 / 信息维修 合并为一组
PHOTO_TAGS = {'B端拍照', 'C端拍照', '瑕疵图'}
PRIVACY_TAGS = {'隐私', '信息维修'}

# 与前端 CSS 类名保持一致
ROLE_CLASS_MAP = {
    'APP': 'rt-app', '哥斯拉': 'rt-godzilla', '魔镜': 'rt-mirror',
    'xray': 'rt-xray', 'Xray': 'rt-xray',
    '充电移交': 'rt-charge',
    'C端拍照': 'rt-c-photo', 'B端拍照': 'rt-b-photo', '瑕疵图': 'rt-defect',
    '隐私': 'rt-privacy', '信息维修': 'rt-repair',
}
AVATAR_COLOR_MAP = {
    'APP': '#1565c0', '哥斯拉': '#f57f17', '魔镜': '#2e7d32',
    'xray': '#6a1b9a', 'Xray': '#6a1b9a',
    '充电移交': '#ad1457',
    'C端拍照': '#c2185b', 'B端拍照': '#880e4f', '瑕疵图': '#e65100',
    '隐私': '#b71c1c', '信息维修': '#ad1457',
}


# ── 底层调用 ──
def _run(args: List[str], timeout: int = 90) -> dict:
    r = subprocess.run([LARK_CLI] + args, capture_output=True, text=True,
                       timeout=timeout, shell=True, env=_get_env_with_node())
    if r.returncode != 0:
        raise RuntimeError(f"lark-cli 执行失败 (rc={r.returncode}): {r.stderr[:300]}")
    try:
        return json.loads(r.stdout)
    except Exception:
        raise RuntimeError(f"lark-cli 输出非 JSON: {r.stdout[:300]}")


def _resolve_base_token() -> str:
    """wiki node token -> 多维表格 app_token (obj_token)"""
    out = _run(["api", "GET", "/open-apis/wiki/v2/spaces/get_node",
                "--params", json.dumps({"type": "bitable", "token": WIKI_NODE_TOKEN})])
    node = out.get("data", {}).get("node", {})
    token = node.get("obj_token")
    if not token:
        raise RuntimeError("无法从 wiki 节点解析出多维表格 token")
    return token


def _get_field_id_map(base_token: str) -> Dict[str, str]:
    out = _run(["base", "+field-list", "--base-token", base_token,
                "--table-id", TABLE_ID, "--format", "json", "--limit", "100"])
    fields = out.get("data", {}).get("fields", [])
    return {f["id"]: f["name"] for f in fields}


def _list_records_named(base_token: str, id2name: Dict[str, str]) -> List[dict]:
    rows_out = []
    offset = 0
    while True:
        out = _run(["base", "+record-list", "--base-token", base_token,
                    "--table-id", TABLE_ID, "--format", "json",
                    "--limit", "200", "--offset", str(offset)])
        d = out.get("data", {})
        fidlist = d.get("field_id_list", [])
        rows = d.get("data", []) or []
        for rec in rows:
            named = {id2name.get(fid, fid): val for fid, val in zip(fidlist, rec)}
            rows_out.append(named)
        if not d.get("has_more") or not rows:
            break
        offset += len(rows)
    return rows_out


# ── 字段值处理 ──
def _val(v):
    if v is None:
        return ''
    if isinstance(v, list):
        if not v:
            return ''
        if isinstance(v[0], dict):
            return v[0].get('text') or v[0].get('name') or str(v[0])
        return str(v[0])
    return str(v)


def _dept_group(tag: str) -> str:
    if tag in PHOTO_TAGS:
        return '拍照'
    if tag in PRIVACY_TAGS:
        return '隐私/信息维修'
    if tag == 'APP':
        return 'APP'
    if tag == '哥斯拉':
        return '哥斯拉'
    if tag == '魔镜':
        return '魔镜'
    if tag and tag.lower() == 'xray':
        return 'Xray'
    if tag == '充电移交':
        return '充电移交'
    return '未分组'


# ── 主入口 ──
def get_staff_from_lark_base() -> List[dict]:
    """返回标准 staff 列表（不写盘），每条含 role_class / avatar_color 便于前端渲染"""
    base_token = _resolve_base_token()
    id2name = _get_field_id_map(base_token)
    rows = _list_records_named(base_token, id2name)

    staff = []
    for n in rows:
        emp_no = _val(n.get('工号'))
        name = _val(n.get('姓名'))
        if not emp_no and not name:
            continue  # 跳过空行
        tag = _val(n.get('岗位标签'))
        dept = _dept_group(tag)
        hire = _val(n.get('入职日期'))
        if ' ' in hire:
            hire = hire.split(' ')[0]
        staff.append({
            'employee_no': emp_no,
            'name': name,
            'employee_name': name,
            'position_tag': tag,
            'dept_group': dept,
            'employee_type': _val(n.get('员工类型')),
            'hire_date': hire,
            'c2': _val(n.get('C2')),
            'c3': _val(n.get('C3')),
            'c4': _val(n.get('C4')),
            'c5': _val(n.get('C5')),
            'role_class': ROLE_CLASS_MAP.get(tag, ''),
            'avatar_color': AVATAR_COLOR_MAP.get(tag, '#888'),
        })
    staff.sort(key=lambda x: x.get('employee_no', ''))
    return staff


def sync_staff_from_lark_base(output_path: str) -> dict:
    """同步并写盘，返回统计信息"""
    staff = get_staff_from_lark_base()
    groups = {}
    for s in staff:
        g = s['dept_group']
        groups[g] = groups.get(g, 0) + 1

    out = {
        "staff": staff,
        "groups": [{"name": k, "count": v} for k, v in groups.items()],
        "total": len(staff),
        "last_update": time.strftime('%Y-%m-%d %H:%M:%S'),
        "source": "飞书云文档·人员管理名单 (实时同步)",
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    logger.info(f"[云文档同步] 完成：{len(staff)}人 -> {output_path}")
    return out


if __name__ == '__main__':
    import sys
    res = get_staff_from_lark_base()
    print(f"读取到 {len(res)} 人")
    for s in res[:3]:
        print(json.dumps(s, ensure_ascii=False))
