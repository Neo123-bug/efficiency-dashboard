"""
排班数据解析服务
从两个飞书文档读取8月排班数据，解析为统一结构。

文档1 (wiki): App/魔镜/哥斯拉/充电/X光/分流 6个组
文档2 (sheets): 拍照大件/拍照手机/隐私清除 3个组
"""
import subprocess
import json
import os
import re
from datetime import datetime
from typing import List, Dict, Optional

# lark-cli 路径
_LARK_CLI_DIR = r"C:\Users\Administrator\.workbuddy\binaries\node\cli-connector-packages"
_NODE_DIR = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2"

# 文档配置
DOC1_URL = "https://atrenew.feishu.cn/wiki/XI8Aw6mr6isYkDkDtRqcl7HLnng?sheet=Tpf5nN"
DOC1_SHEET_ID = "Tpf5nN"

DOC2_URL = "https://atrenew.feishu.cn/sheets/VpkbsUo56hipQItjin4cuju2nid"
DOC2_SHEET_ID = "9HFkHx"  # 2026年8月

# 排班值 → 类型映射（用于前端着色）
SCHEDULE_TYPE_MAP = {
    # 工作类
    "苹果": "work-apple", "1": "work", "出勤": "work",
    "魔镜": "work-mirror", "充电": "work-charge",
    "X-Ray": "work-xray", "X光": "work-xray",
    "分流": "work-divert",
    # 休息
    "休": "rest", "休息": "rest",
    # 加班
    "加": "overtime", "加班": "overtime",
    # 请假
    "请假": "leave",
    # 支援
    "支援": "support",
    # 异常
    "异常": "abnormal",
}

# 类型 → 中文标签
TYPE_LABELS = {
    "work": "出勤", "work-apple": "苹果", "work-mirror": "魔镜",
    "work-charge": "充电", "work-xray": "X光", "work-divert": "分流",
    "rest": "休息", "overtime": "加班", "leave": "请假",
    "support": "支援", "abnormal": "异常",
}

# 类型 → 中等饱和度颜色（浅底深字，久看不累；与 schedule.html 保持一致）
TYPE_COLORS = {
    "work": "#86efac", "work-apple": "#93c5fd", "work-mirror": "#d8b4fe",
    "work-charge": "#67e8f9", "work-xray": "#fcd34d", "work-divert": "#a5b4fc",
    "rest": "#d1d5db", "overtime": "#fdba74", "leave": "#fca5a5",
    "support": "#93c5fd", "abnormal": "#f9a8d4",
}


def _build_env():
    """构建包含 node 路径的子进程环境"""
    env = os.environ.copy()
    env["PATH"] = _NODE_DIR + os.pathsep + _LARK_CLI_DIR + os.pathsep + env.get("PATH", "")
    return env


def _run_lark_cli(url: str, sheet_id: str) -> Optional[str]:
    """调用 lark-cli 读取飞书表格 CSV 数据"""
    lark_cli = os.path.join(_LARK_CLI_DIR, "lark-cli.cmd")
    cmd = [lark_cli, "sheets", "+csv-get", "--url", url, "--sheet-id", sheet_id]
    env = _build_env()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90,
                           shell=True, env=env)
        if r.returncode != 0:
            print(f"[schedule] lark-cli failed (rc={r.returncode}): {r.stderr[:200]}")
            return None
        data = json.loads(r.stdout)
        if data.get("ok"):
            return data.get("data", {}).get("annotated_csv", "")
        else:
            print(f"[schedule] lark-cli returned not ok: {data.get('error', '')}")
            return None
    except Exception as e:
        print(f"[schedule] lark-cli exception: {e}")
        return None


def _parse_csv_rows(csv_str: str) -> List[List[str]]:
    """
    解析带 [row=N] 前缀的 annotated CSV 为行数组。
    使用 [row=N] 标记切分行，避免引号内换行导致行错位。
    """
    if not csv_str:
        return []
    # 按 [row=N] 标记切分（每个标记代表一行的开始）
    # [row=1] xxx\n[row=2] yyy\n...
    row_pattern = re.compile(r'\[row=(\d+)\]\s*')
    # 找到所有 [row=N] 的位置
    matches = list(row_pattern.finditer(csv_str))
    rows = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(csv_str)
        line = csv_str[start:end].rstrip("\n").rstrip("\r")
        row = _parse_csv_line(line)
        rows.append(row)
    return rows


def _parse_csv_line(line: str) -> List[str]:
    """解析单行 CSV，处理引号内的逗号和换行"""
    result = []
    current = ""
    in_quotes = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            if in_quotes and i + 1 < len(line) and line[i + 1] == '"':
                current += '"'
                i += 2
                continue
            in_quotes = not in_quotes
        elif ch == ',' and not in_quotes:
            result.append(current.strip())
            current = ""
        else:
            current += ch
        i += 1
    result.append(current.strip())
    return result


def _get_val(row: List[str], idx: int) -> str:
    """安全获取行中指定列的值"""
    if idx < len(row):
        return row[idx].strip()
    return ""


def _parse_doc1(csv_str: str) -> List[Dict]:
    """
    解析文档1 (wiki): App/魔镜/哥斯拉/充电/X光/分流
    结构: 岗位(A) | 工号(B) | 姓名(C) | Day1-31(D-AG) | 产能(AH) | 苹果(AI) | 出勤(AJ) | 休息(AK) | 加班(AL)
    """
    rows = _parse_csv_rows(csv_str)
    groups = []

    # 定义各组的行范围 (基于数据结构)
    # App: rows 4-27 (index 3-26), summary rows 28-29
    # 魔镜: rows 30-32 (index 29-31), no separate summary
    # 哥斯拉: rows 33-53 (index 32-52), summary rows 54-55
    # 充电: rows 56-59 (index 55-58)
    # X光: rows 60-62 (index 59-61)
    # 分流: rows 63-64 (index 62-63)
    # 合计: row 66 (index 65)

    group_defs = [
        {"name": "App", "start_row": 4, "end_row": 27, "headcount_row": 28, "capacity_row": 29},
        {"name": "魔镜", "start_row": 30, "end_row": 32, "headcount_row": None, "capacity_row": None},
        {"name": "哥斯拉", "start_row": 33, "end_row": 53, "headcount_row": 54, "capacity_row": 55},
        {"name": "充电", "start_row": 56, "end_row": 59, "headcount_row": None, "capacity_row": None},
        {"name": "X光", "start_row": 60, "end_row": 62, "headcount_row": None, "capacity_row": None},
        {"name": "分流", "start_row": 63, "end_row": 64, "headcount_row": None, "capacity_row": None},
    ]

    for gd in group_defs:
        members = []
        for row_idx in range(gd["start_row"] - 1, gd["end_row"]):
            if row_idx >= len(rows):
                break
            row = rows[row_idx]
            emp_id = _get_val(row, 1)  # B列 工号
            name = _get_val(row, 2)    # C列 姓名
            # Day1-31 在 D-AG 列 (index 3-33)，先提取供后续判断与保存
            days = []
            for d in range(31):
                days.append(_get_val(row, 3 + d))

            if not name or name in ("出勤人数", "每日产能", "合计出勤人数", "姓名"):
                continue
            # 跳过表头行（days 是 1-31 的数字序列）
            if name and not emp_id and days and all(d == str(i + 1) for i, d in enumerate(days[:31])):
                continue
            # 跳过空行和新人占位
            if not emp_id and name in ("新人1", "新人2"):
                # 新人也保留
                pass
            elif not emp_id and not name:
                continue

            # 统计列: 产能(34), 苹果(35), 出勤(36), 休息(37), 加班(38)
            stats = {
                "产能": _get_val(row, 34),
                "出勤天数": _get_val(row, 36),
                "休息天数": _get_val(row, 37),
                "加班天数": _get_val(row, 38),
            }

            members.append({
                "position": _get_val(row, 0) or gd["name"],
                "id": emp_id,
                "name": name,
                "days": days,
                "stats": stats,
            })

        # 每日出勤人数
        daily_headcount = []
        if gd["headcount_row"]:
            row_idx = gd["headcount_row"] - 1
            if row_idx < len(rows):
                row = rows[row_idx]
                for d in range(31):
                    val = _get_val(row, 3 + d)
                    daily_headcount.append(val)

        # 每日产能
        daily_capacity = []
        if gd["capacity_row"]:
            row_idx = gd["capacity_row"] - 1
            if row_idx < len(rows):
                row = rows[row_idx]
                for d in range(31):
                    val = _get_val(row, 3 + d)
                    daily_capacity.append(val)

        groups.append({
            "name": gd["name"],
            "source": "doc1",
            "members": members,
            "daily_headcount": daily_headcount,
            "daily_capacity": daily_capacity,
        })

    # 合计出勤人数 (row 66)
    total_row_idx = 65  # 0-based
    if total_row_idx < len(rows):
        row = rows[total_row_idx]
        total_headcount = []
        for d in range(31):
            total_headcount.append(_get_val(row, 3 + d))
        # 挂到第一个组上或单独返回
        if groups:
            groups[0]["_total_headcount"] = total_headcount

    return groups


def _parse_doc2(csv_str: str) -> List[Dict]:
    """
    解析文档2 (sheets): 拍照大件/拍照手机/隐私清除
    结构: 团队(A) | 出勤率(B) | 工号(C) | 姓名(D) | Day1-31(E-AI) | 出勤天数(AJ)
    """
    rows = _parse_csv_rows(csv_str)
    groups = []

    # 数据从 row 9 开始 (index 8)
    # Row 9 (index 8): 拍照-出勤人数 (summary)
    # Row 10+ (index 9+): 人员数据
    # 团队标签在 A 列: 拍照-大件, 拍照-手机, 拍照-兼职, 隐私清除

    current_group = None
    current_members = []
    daily_headcount_photo = []

    # 提取拍照出勤人数汇总 (row 9, index 8)
    if len(rows) > 8:
        row = rows[8]
        if "拍照-出勤人数" in _get_val(row, 0):
            for d in range(31):
                daily_headcount_photo.append(_get_val(row, 4 + d))

    # 遍历人员行 (row 10 到 row 31, index 9-30)
    team_map = {}  # team_name -> members
    person_team = None

    for row_idx in range(9, min(35, len(rows))):
        row = rows[row_idx]
        team_label = _get_val(row, 0)
        emp_id = _get_val(row, 2)  # C列 工号
        name = _get_val(row, 3)    # D列 姓名

        # 检测团队标签
        if team_label and ("拍照" in team_label or "隐私" in team_label):
            if "大件" in team_label:
                person_team = "拍照-大件"
            elif "手机" in team_label:
                person_team = "拍照-手机"
            elif "兼职" in team_label:
                person_team = "拍照-兼职"
            elif "隐私" in team_label:
                person_team = "隐私清除"
            else:
                person_team = team_label
        elif team_label and "合计" in team_label:
            # 汇总行，跳过
            continue
        elif team_label and "部门" in team_label:
            continue

        # 跳过空行
        if not emp_id and not name:
            # 检查是否是汇总行
            if team_label and "合计" in team_label:
                continue
            continue

        # 跳过汇总行
        if team_label and ("合计" in team_label or "出勤人数" in team_label):
            continue
        if name in ("拍照-大件", "拍照-手机", "拍照-兼职", "隐私-合计出勤",
                     "拍照&隐私-合计出勤", "拍照-全职", "拍照-兼职"):
            continue

        if not name:
            continue

        # Day1-31 在 E-AI 列 (index 4-34)
        days = []
        for d in range(31):
            days.append(_get_val(row, 4 + d))

        # 出勤天数在 AJ 列 (index 35)
        attend_days = _get_val(row, 35)

        # 出勤率
        rate = _get_val(row, 1)

        member = {
            "position": person_team or "未分组",
            "id": emp_id,
            "name": name,
            "days": days,
            "stats": {
                "出勤天数": attend_days,
                "出勤率": rate,
            },
        }

        if person_team not in team_map:
            team_map[person_team] = []
        team_map[person_team].append(member)

    # 构建组列表
    # 合并拍照-大件和拍照-兼职（如果分开的话）
    photo_groups = ["拍照-大件", "拍照-手机", "拍照-兼职"]
    for team_name in photo_groups:
        if team_name in team_map:
            groups.append({
                "name": team_name,
                "source": "doc2",
                "members": team_map[team_name],
                "daily_headcount": daily_headcount_photo if team_name == "拍照-大件" else [],
                "daily_capacity": [],
            })

    if "隐私清除" in team_map:
        # 提取隐私清除的出勤汇总
        privacy_headcount = []
        # 隐私-合计出勤 在 row 32 (index 31)
        if len(rows) > 31:
            row = rows[31]
            if "隐私" in _get_val(row, 0) or "合计" in _get_val(row, 0):
                for d in range(31):
                    privacy_headcount.append(_get_val(row, 4 + d))

        groups.append({
            "name": "隐私清除",
            "source": "doc2",
            "members": team_map["隐私清除"],
            "daily_headcount": privacy_headcount,
            "daily_capacity": [],
        })

    return groups


# 岗位标签 → 颜色（与人员管理花名册一致）
POSITION_COLORS = {
    'APP':       '#f59e0b',  # 金黄
    '哥斯拉':     '#3b82f6',  # 蓝
    '魔镜':       '#22c55e',  # 绿
    'xray':      '#a855f7',  # 紫
    'Xray':      '#a855f7',
    '充电移交':   '#ef4444',  # 红
    'C端拍照':   '#fb923c',  # 浅橙
    'B端拍照':   '#ea580c',  # 深橙
    '瑕疵图':     '#f97316',  # 亮橙
    '隐私':       '#06b6d4',  # 青
    '信息维修':   '#14b8a6',  # 蓝绿
}

# 排班组名 → 花名册 position_tag 映射
GROUP_TO_POSITION_TAG = {
    'App':       'APP',
    '魔镜':       '魔镜',
    '哥斯拉':     '哥斯拉',
    '充电':       '充电移交',
    'X光':       'xray',
    '分流':       'APP',      # 分流人员通常属于 APP 组
    '拍照-大件':  'C端拍照',
    '拍照-手机':  'C端拍照',
    '拍照-兼职':  'C端拍照',
    '隐私清除':   '隐私',
}


def _load_roster_lookup():
    """从 staff_roster.json 构建工号→人员信息、姓名→人员信息 的查找表"""
    roster_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'staff_roster.json')
    try:
        with open(roster_path, 'r', encoding='utf-8') as f:
            roster = json.load(f)
    except Exception:
        return {}, {}

    staff = roster.get('staff', roster) if isinstance(roster, dict) else roster
    by_id = {}
    by_name = {}
    for s in staff:
        no = str(s.get('employee_no') or '').strip()
        nm = str(s.get('name') or s.get('employee_name') or '').strip().replace(' ', '')
        info = {
            'position_tag': s.get('position_tag', ''),
            'dept_group': s.get('dept_group', ''),
            'c5': s.get('c5', ''),
            'employee_type': s.get('employee_type', ''),
            'avatar_color': s.get('avatar_color', ''),
            'role_class': s.get('role_class', ''),
        }
        if no:
            by_id[no] = info
        if nm:
            by_name[nm] = info
    return by_id, by_name


def _enrich_with_roster(groups):
    """用花名册数据给每个排班人员补全岗位信息"""
    by_id, by_name = _load_roster_lookup()
    for g in groups:
        default_tag = GROUP_TO_POSITION_TAG.get(g['name'], '')
        default_color = POSITION_COLORS.get(default_tag, '#888')
        for m in g['members']:
            emp_id = str(m.get('id', '')).strip()
            name = str(m.get('name', '')).strip().replace(' ', '')
            # 先按工号匹配，再按姓名匹配
            info = by_id.get(emp_id) or by_name.get(name, {})
            tag = info.get('position_tag', '') or default_tag
            color = POSITION_COLORS.get(tag, default_color)
            m['position_tag'] = tag
            m['position_color'] = color
            m['dept_group'] = info.get('dept_group', '')
            m['employee_type'] = info.get('employee_type', '')
            m['c5'] = info.get('c5', '')
    return groups


def fetch_schedule_data() -> dict:
    """获取并解析两个飞书文档的排班数据"""
    # 获取文档1
    csv1 = _run_lark_cli(DOC1_URL, DOC1_SHEET_ID)
    groups1 = _parse_doc1(csv1) if csv1 else []

    # 获取文档2
    csv2 = _run_lark_cli(DOC2_URL, DOC2_SHEET_ID)
    groups2 = _parse_doc2(csv2) if csv2 else []

    # 合并
    all_groups = groups1 + groups2

    # 用花名册补全岗位信息
    all_groups = _enrich_with_roster(all_groups)

    # 统计总人数
    total_people = sum(len(g["members"]) for g in all_groups)

    # 今天日期
    today = datetime.now()
    today_day = today.day
    today_weekday = ["一", "二", "三", "四", "五", "六", "日"][today.weekday()]

    # 今天的出勤人数（从第一个组的 _total_headcount 取）
    today_headcount = ""
    if groups1 and "_total_headcount" in groups1[0]:
        hc = groups1[0]["_total_headcount"]
        if today_day <= len(hc):
            today_headcount = hc[today_day - 1]
        # 移除临时字段
        del groups1[0]["_total_headcount"]

    # 统计今天各类状态人数
    today_rest_count = 0
    today_leave_count = 0
    for g in all_groups:
        for m in g.get("members", []):
            days = m.get("days", [])
            if today_day <= len(days):
                val = str(days[today_day - 1]).strip()
                if val in ("休", "休息"):
                    today_rest_count += 1
                elif val == "请假":
                    today_leave_count += 1

    return {
        "month": "2026年8月",
        "month_code": "2026-08",
        "last_update": today.strftime("%Y-%m-%d %H:%M:%S"),
        "total_people": total_people,
        "total_groups": len(all_groups),
        "today_day": today_day,
        "today_weekday": f"周{today_weekday}",
        "today_headcount": today_headcount,
        "today_rest_count": today_rest_count,
        "today_leave_count": today_leave_count,
        "today_rest_leave_count": today_rest_count + today_leave_count,
        "groups": all_groups,
        "type_colors": TYPE_COLORS,
        "type_labels": TYPE_LABELS,
    }


if __name__ == "__main__":
    data = fetch_schedule_data()
    print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
    print(f"\n--- 总人数: {data['total_people']}, 组数: {data['total_groups']} ---")
