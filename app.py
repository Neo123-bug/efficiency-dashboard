# -*- coding: utf-8 -*-
"""
效率看板 — 完全自主设计的 Flask 应用
复用原始数据缓存文件，前端全新打造
端口 8080，不动原始 5000
"""
import os, sys, json, time, threading, subprocess
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, request, jsonify, make_response
from config import (EFFICIENCY_TREND_CONFIG, TREND_CACHE_PATH, METRIC_CACHE_PATH,
    QUALITY_MAIL_CONFIG, QUALITY_CACHE_PATH,
    KDOCS_CONFIG, APP_ANDROID_STANDARD, APP_IOS_STANDARD,
    XRAY_CHENGDU_MACHINES, XRAY_STANDARD_UPH,
    PHOTO_SERVICE_STANDARDS)
from services.feishu_sheet_client import get_efficiency_trend, load_trend_cache, save_trend_cache, get_latest_metrics, save_metric_cache, load_metric_cache
from services.feishu_mail_client import save_quality_cache, load_quality_cache
from services.lark_cli_mail_client import get_quality_metrics_from_mail

app = Flask(__name__)
app.secret_key = 'my-dashboard-v1'
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

# 读取原版缓存目录（与原始应用共享数据源）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 缓存目录：默认用本应用自身目录（云端独立部署时不再依赖上级 efficiency-dashboard）；
# 可用环境变量 DASHBOARD_CACHE_DIR 覆盖（如指向挂载卷实现持久化）。
CACHE_SOURCE = os.environ.get('DASHBOARD_CACHE_DIR', BASE_DIR)
STAFF_ROSTER = os.path.join(BASE_DIR, 'staff_roster.json')

def _read_json(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {}

def _read_cache(name):
    return _read_json(os.path.join(CACHE_SOURCE, name))

# ── Jinja helpers ──
def _fmt(n):
    try: return '{:,}'.format(int(n))
    except: return str(n) if n else '0'

def _pct(v):
    try: return '{:.1f}%'.format(float(v))
    except: return '-'

app.jinja_env.globals.update(fmt=_fmt, pct=_pct, round=round, len=len, str=str, int=int)

# ── 指标格式化辅助 ──
def _pct_or_dash(v):
    try:
        if v is None: return '--'
        return '{:.2f}%'.format(float(v) * 100)
    except:
        return '--'

def _fmt_or_dash(v):
    try:
        if v is None: return '--'
        n = float(v)
        if n == int(n):
            return '{:,}'.format(int(n))
        return '{:,.2f}'.format(n)
    except:
        return '--'

def _change_cls(change):
    if change is None: return 'zero'
    if change > 0.001: return 'up'
    if change < -0.001: return 'down'
    return 'zero'

# 岗位标签 → 胶囊颜色CSS类
_ROLE_CLASS_MAP = {
    'APP': 'rt-app', '哥斯拉': 'rt-godzilla', '魔镜': 'rt-mirror',
    'xray': 'rt-xray', 'Xray': 'rt-xray',
    '充电移交': 'rt-charge',
    'C端拍照': 'rt-c-photo', 'B端拍照': 'rt-b-photo', '瑕疵图': 'rt-defect',
    '隐私': 'rt-privacy', '信息维修': 'rt-repair',
}
def _role_class(tag):
    return _ROLE_CLASS_MAP.get(tag, '')

# 头像底色：单标签组 = 该组卡片主色；多标签组 = 同色系内不同色区分岗位
_AVATAR_COLOR_MAP = {
    # 单岗位组 → 对应顶部卡片颜色
    'APP': '#f59e0b',        # 黄(金)  = APP卡片
    '哥斯拉': '#3b82f6',     # 蓝      = 哥斯拉卡片
    '魔镜': '#22c55e',       # 绿      = 魔镜卡片
    'xray': '#a855f7',       # 紫
    'Xray': '#a855f7',
    '充电移交': '#ef4444',   # 红/粉   = 充电移交卡片
    # 拍照组(橙系) → 三个不同色区分岗位
    'C端拍照': '#fb923c',    # 浅橙
    'B端拍照': '#ea580c',    # 深橙
    '瑕疵图': '#f97316',     # 亮橙
    # 隐私/信息维修组(青系) → 两个不同色区分岗位
    '隐私': '#06b6d4',       # 青
    '信息维修': '#14b8a6',   # 蓝绿(teal)
}
def _get_avatar_color(tag):
    return _AVATAR_COLOR_MAP.get(tag, '#888')

app.jinja_env.globals.update(
    pct_or_dash=_pct_or_dash, fmt_or_dash=_fmt_or_dash,
    change_cls=_change_cls, role_class=_role_class,
    _get_avatar_color=_get_avatar_color
)

# ── 数据处理（适配缓存格式到模板） ──
def _build_hourly_data(people):
    """按人员 hourly_counts 构造时段表数据（行=人员，列=小时段）。
    people: [{name, cat, hourly_counts, target}]"""
    hourly_data = []
    for p in people:
        hc = p.get('hourly_counts') or {}
        name = p.get('name', '')
        cat = p.get('cat', '全职')
        target = p.get('target', 0) or 1
        for hstr, cnt in sorted(hc.items()):
            try:
                hi = int(hstr)
            except (ValueError, TypeError):
                continue
            r_hr = round(cnt / target * 100, 2) if target > 0 else 0
            hourly_data.append({
                'name': name,
                'cat': cat,
                'hour_label': '%02d:00-%02d:00' % (hi, hi + 1),
                'count': cnt,
                'target': target,
                'rate': r_hr,
            })
    return hourly_data


def _adapt_photo(data, keep=None):
    """photo_cache.json → 模板数据（后端预处理：排序/排名/时段综合达成）

    返回结构：
    - staff_list: 有出勤的人员（products>0），按处理量降序，含连续 rank + hourly_comprehensive_rate
    - hourly_data: 时段明细（仅出勤人员）
    - hourly_rates: {name → 时段综合达成率 = 各时段(cnt/标准×100)的算术均值}
    - hourly_totals: {hour_label → 合计}
    - hourly_summary: 预计算的汇总行（避免 Jinja2 for 作用域 bug）
    - 原始 data 原样透传（供 method_groups / service_standard_groups 等卡片用）
    """
    ranking = data.get('staff_ranking', []) or []
    if keep is not None:
        ranking = [s for s in ranking if keep(s)]

    # ---- 构建行：只保留今日有出勤数据的人员 ----
    def norm_type(et):
        t = (et or '').replace('外包', '').strip()
        return '兼职' if '兼职' in t else '全职'

    rows = []
    for s in ranking:
        total = s.get('total_count', 0) or 0
        name = s.get('name', s.get('employee_name', '-'))
        hc = s.get('hourly_counts', {}) or {}
        # 效率达成目标 = 哥斯拉各服务标准(全职/兼职) + 光合各品类标准 + 拍照魔方(PJTPZ)
        # 按处理量加权混合（拍拍=哥斯拉拍拍130/120 + 光合各品类标准）。
        # 复用模块级函数 calc_photo_target，与缓存生成(_calc_photo_target_full)口径一致。
        from services.efficiency_service import calc_photo_target
        std = calc_photo_target(s)

        # 时段综合达成率 = 各时段(cnt/标准×100) 的算术平均值
        # 若无时段数据（如纯 watcher 来源无逐小时明细），回退到 UPPH 达成率
        rates_for_person = []
        for hstr, cnt in hc.items():
            try:
                hi = int(hstr)
            except (ValueError, TypeError):
                continue
            r_hr = round(cnt / std * 100, 2) if std > 0 else 0
            rates_for_person.append(r_hr)
        if rates_for_person:
            hourly_comprehensive_rate = round(sum(rates_for_person) / len(rates_for_person), 2)
        else:
            # 无时段数据时回退：UPPH 达成率 = efficiency / standard × 100
            uph_val = (s.get('efficiency', 0) or 0)
            hourly_comprehensive_rate = round(uph_val / std * 100, 2) if std > 0 else 0

        rows.append({
            'raw': s,                    # 原始缓存记录（模板仍需读取 machine_id / flaw_count 等）
            'name': name,
            'employee_no': s.get('employee_no', ''),
            'emp_type': norm_type(s.get('employee_type', '')),
            'position_tag': s.get('position_tag', ''),
            'products': total,
            'hours': s.get('work_hours', 0) or 0,
            'upph': s.get('efficiency', 0) or 0,
            'standard': std,
            'achievement_rate': s.get('achievement_rate', 0) or 0,   # 缓存原始达成(备用)
            'hourly_comprehensive_rate': hourly_comprehensive_rate,     # 时段综合达成（主口径）
            'rate': hourly_comprehensive_rate,                          # 首页/Top10 统一达成率口径
            'hourly_counts': hc,
            'data_sources': s.get('data_sources', []),
            'photo_cube_count': s.get('photo_cube_count', 0),
            'flaw_count': s.get('flaw_count', 0),
            # 超短工时标记：工作时长过短(默认<1h)会导致 UPPH/达成率失真，仅标注提醒，不剔除数据
            'short_hours': (s.get('work_hours', 0) or 0) < 1.0,
        })

    # 只保留出勤人员（与时段表一致）
    active_rows = [r for r in rows if r['products'] > 0]
    active_rows.sort(key=lambda x: x['products'], reverse=True)

    # 连续排名
    for _i, _r in enumerate(active_rows, 1):
        _r['rank'] = _i

    # 每行达标标记（与表格渲染口径统一：时段综合达成率 >= 100%）
    for _r in active_rows:
        _r['qualified'] = _r.get('rate', 0) >= 100

    # ---- 汇总指标 ----
    staff_count = len(active_rows)
    total_products = sum(r['products'] for r in active_rows)
    upph_list = [r['upph'] for r in active_rows if r['upph'] > 0]
    avg_upph = round(sum(upph_list) / len(upph_list), 2) if upph_list else 0
    total_hours = sum(r['hours'] for r in active_rows)

    # 达标人数 / 达标率（与每行 qualified 口径统一）
    qualified_count = sum(1 for r in active_rows if r.get('qualified'))
    unqualified_count = staff_count - qualified_count
    pass_rate = round(qualified_count / max(staff_count, 1) * 100, 1)

    # ---- 时段表数据（仅出勤人员） ----
    hourly_data = []
    hourly_totals = {}
    hourly_rates = {}   # name → 时段综合达成率
    hourly_summary_by_hour = {}

    for r in active_rows:
        hc = r['hourly_counts']
        std = r['standard']
        name = r['name']
        for hstr, cnt in sorted(hc.items()):
            try:
                hi = int(hstr)
            except (ValueError, TypeError):
                continue
            label = '%02d:00-%02d:00' % (hi, hi + 1)
            r_hr = round(cnt / std * 100, 2) if std > 0 else 0
            hourly_data.append({
                'name': name,
                'employee_no': r['employee_no'],
                'cat': r['emp_type'],
                'hour_label': label,
                'count': cnt,
                'target': std,
                'rate': r_hr,
            })
            hourly_totals[label] = hourly_totals.get(label, 0) + cnt
            hourly_summary_by_hour[label] = hourly_summary_by_hour.get(label, 0) + cnt

        hourly_rates[name] = r['hourly_comprehensive_rate']

    return {
        # 原始 data 透传（模板头部 KPI 卡片 / method_groups 等需要）
        '_raw': data,
        # 出勤人员列表（已排序+排名+时段综合达成率+qualified标记）
        'staff_list': active_rows,
        # ---- KPI 指标（与表格 qualified 口径统一：基于时段综合达成率） ----
        'staff_count': staff_count,
        'total_products': total_products,
        'total_hours': round(total_hours, 1),
        'avg_upph': avg_upph,
        'achievement_rate': pass_rate,          # 达标率(%)
        'achieved_count': qualified_count,      # 达标人数
        'unqualified_count': unqualified_count,  # 未达标人数
        # 时段数据
        'hourly_data': hourly_data,
        'hourly_totals': hourly_totals,
        'hourly_rates': hourly_rates,
        'hourly_summary': {
            'by_hour': hourly_summary_by_hour,
            'total': sum(hourly_summary_by_hour.values()),
        },
        'last_update': data.get('last_update', ''),
    }

def _adapt_auto(data, keep=None):
    """automation_cache.json → 模板数据（可传入 keep 谓词只保留特定组别人员）"""
    ranking = data.get('staff_ranking', []) or []
    if keep is not None:
        ranking = [s for s in ranking if keep(s)]
    return {
        'total_products': data.get('total_count', 0),
        'avg_upph': data.get('avg_efficiency', 0),
        'staff_count': len(ranking),
        'qualified_rate': data.get('achievement_rate', 0),
        'staff_list': [{
            'name': s.get('employee_name', s.get('name', '-')),
            'products': s.get('total_count', 0),
            'hours': s.get('work_hours', 0),
            'upph': s.get('efficiency', 0),
            'standard': s.get('standard_uph', 0),
            'rate': s.get('achievement_rate', 0),
        } for s in ranking],
        'last_update': data.get('last_update', ''),
    }


# ── 人员花名册分组过滤（各效率页只展示对应组别的人员）──
def _load_roster_groups():
    """返回 {dept_group: {'nos':set, 'names':set}}，用于按组过滤效率页人员。"""
    roster = _read_json(STAFF_ROSTER)
    if not roster:
        return {}
    staff = roster.get('staff', []) if isinstance(roster, dict) else roster
    groups = {}
    for s in staff:
        g = s.get('dept_group', '')
        if not g:
            continue
        gset = groups.setdefault(g, {'nos': set(), 'names': set(), 'names_norm': set()})
        no = str(s.get('employee_no', '') or '').strip()
        nm = (s.get('name') or s.get('employee_name', '') or '').strip()
        if no:
            gset['nos'].add(no)
        if nm:
            gset['names'].add(nm)
            gset['names_norm'].add(nm.replace(' ', ''))   # 去空格，防"李 娟"≠"李娟"
    return groups


def _emp_in_groups(item, group_sets):
    """判断效率数据项是否属于给定分组集合中的任一成员（按工号或姓名匹配）。"""
    if not group_sets:
        return False
    no = str(item.get('employee_no') or item.get('emp_no') or item.get('staff_no') or '').strip()
    nm = (item.get('employee_name') or item.get('name') or item.get('staff_name') or '').strip()
    nm_norm = nm.replace(' ', '')
    for gset in group_sets:
        if no and no in gset['nos']:
            return True
        if nm and nm in gset['names']:
            return True
        if nm_norm and nm_norm in gset['names_norm']:
            return True
    return False


def _keep_for_groups(group_names):
    """根据分组名构造过滤谓词；若花名册缺失或分组不存在则返回 None（不过滤）。"""
    if not group_names:
        return None
    rg = _load_roster_groups()
    sets = [rg[g] for g in group_names if g in rg]
    if not sets:
        return None
    return lambda it: _emp_in_groups(it, sets)


def _adapt_auto_grouped(raw, roster_groups, xray_raw=None):
    """自动化效率 → 哥斯拉/魔镜 按人员维度；X-RAY 按设备维度（设备编号即身份）。

    返回结构：
    - kpi: 6个指标(总人数/总处理量/总工时/平均UPPH/达标率/达标人数) —— 仅人员维度(哥斯拉+魔镜)
    - channels: {全部/哥斯拉/魔镜/xray} 各自的人员/设备列表+汇总
    - hourly_data: 人员时段数据(哥斯拉+魔镜)
    - hourly_rates / hourly_totals
    - xray_list: 设备维度行(machine_id 即身份)
    - xray_hourly_data / xray_hourly_rates / xray_hourly_totals: 设备时段
    """
    ranking = raw.get('staff_ranking', []) or []
    gz = roster_groups.get('哥斯拉')
    mj = roster_groups.get('魔镜')
    combined = [g for g in (gz, mj) if g]

    def keep(it):
        return _emp_in_groups(it, combined) if combined else True

    def norm_type(et):
        t = (et or '').replace('外包', '').strip()
        return '兼职' if '兼职' in t else '全职'

    def count_devices(s):
        """统计一人使用的设备台数（device_codes / machine_id 逗号分隔）"""
        raw = s.get('device_codes') or s.get('machine_id') or ''
        if isinstance(raw, list):
            return len([x for x in raw if x])
        if raw:
            return len([c.strip() for c in str(raw).replace('，', ',').split(',') if c.strip()])
        return 0

    def row(s):
        """统一行结构（哥斯拉/魔镜 人员维度）"""
        products_val = s.get('total_count', 0) or 0
        hours_val = s.get('work_hours', 0) or 0
        ds = s.get('data_source', '哥斯拉')
        ndev = s.get('machine_count') or count_devices(s)

        # 出勤时长午休已由缓存(_aggregate_by_staff)按"末台作业时间>=13:00剔除1h"统一扣减，
        # 此处直接用缓存 work_hours，避免重复扣减导致分母偏小、达成虚高。
        hc = s.get('hourly_counts', {}) or {}
        adj_hours = hours_val

        # UPPH = 处理量 / 调整后出勤时长（分母用真实出勤时长，午休已剔除）
        if adj_hours > 0 and products_val:
            upph_val = round(products_val / adj_hours, 1)
        else:
            upph_val = s.get('efficiency', 0) or 0

        # 标准：单人用源标准(默认52)，双台/多台(>=2)特殊
        std = s.get('standard_uph', s.get('target_efficiency', 0)) or 52
        if ndev >= 2:
            if ds == 'mirror':
                std = 123
            else:  # 哥斯拉双台：综合标准(逐小时>=70→135, 否则58 的平均)
                if hc:
                    total_target_cap = sum((135 if v >= 70 else 58) for v in hc.values())
                    std = round(total_target_cap / len(hc), 1)
                else:
                    std = 135

        # 效率达成 = UPPH / 标准
        rate_val = round(upph_val / std * 100, 1) if (std and upph_val) else 0

        return {
            'name': s.get('name', s.get('employee_name', '-')),
            'employee_no': s.get('employee_no', ''),
            'employee_type': norm_type(s.get('employee_type', '')),
            'position': s.get('position_tag', '') or s.get('group_name', '') or ('哥斯拉' if ds == 'godzilla' else '魔镜'),  # 中文岗位
            'products': products_val,
            'hours': round(adj_hours, 1) if adj_hours else 0,
            'upph': upph_val,
            'standard': std,
            'rate': rate_val,
            'qualified': rate_val >= 100,
            'device_codes': s.get('device_codes', s.get('machine_id', '')),  # 设备编号
            'hourly_counts': hc,
            'hourly_detail': s.get('hourly_detail', []),  # 每小时: {hour,volume,target,achievement}
            'num_devices': ndev,  # 设备台数(供时段表判断双台)
            'source': s.get('data_source', '哥斯拉'),
            'lunch_deducted': 0,  # 午休已由缓存 work_hours 统一处理，此处不再单独扣
        }

    # 按数据源分两组（哥斯拉/魔镜 —— 人员维度）
    godzilla_list, mirror_list = [], []
    for s in ranking:
        if not keep(s):
            continue
        ds = s.get('data_source', '')
        r = row(s)
        if ds == 'mirror':
            mirror_list.append(r)
        else:
            godzilla_list.append(r)

    godzilla_list.sort(key=lambda x: x['products'], reverse=True)
    mirror_list.sort(key=lambda x: x['products'], reverse=True)

    # 全局连续排名（按效率达成率降序，1~N）
    all_staff = godzilla_list + mirror_list
    all_staff.sort(key=lambda x: x.get('rate', 0), reverse=True)
    for _i, _s in enumerate(all_staff, 1):
        _s['rank'] = _i

    # ── X光：设备维度（设备编号即身份，非人员维度）──
    xray_list = []
    xray_devices = (xray_raw or {}).get('staff_ranking', []) or []
    if not xray_devices:
        # 未拉取/未配置时，按配置的设备列表生成占位设备行，保证通道始终可见
        xray_devices = [{'machine_id': m, 'name': m, 'total_count': 0,
                         'work_hours': 0, 'upph': 0, 'achievement_rate': 0,
                         'hourly_counts': {}} for m in XRAY_CHENGDU_MACHINES]
    for d in xray_devices:
        mid = (d.get('machine_id') or d.get('name') or '-')
        products = d.get('total_count', 0) or 0
        hours = d.get('work_hours', 0) or 0
        upph = d.get('upph', 0) or 0
        # 效率达成：与时段表统一口径（各时段达成率的算术平均），不再用缓存原始 achievement_rate
        hc_raw = d.get('hourly_counts', {}) or {}
        _xr_rates = []
        for _hstr, _cnt in hc_raw.items():
            try: int(_hstr)
            except (ValueError, TypeError): continue
            if XRAY_STANDARD_UPH > 0:
                _xr_rates.append(round(_cnt / XRAY_STANDARD_UPH * 100, 2))
        rate = round(sum(_xr_rates) / len(_xr_rates), 2) if _xr_rates else 0
        xray_list.append({
            'name': mid,                 # 设备编号（作为行身份）
            'machine_id': mid,
            'employee_type': '设备',
            'position': 'X-Ray',
            'products': products,
            'hours': round(hours, 1) if hours else 0,
            'upph': upph,
            'standard': XRAY_STANDARD_UPH,
            'rate': rate,
            'qualified': rate >= 100,
            'device_codes': '',
            'hourly_counts': d.get('hourly_counts', {}),
            'source': 'xray',
        })

    # all_staff 已在上方排名时定义并排序（按总处理量降序）

    # ---- 6个KPI ----
    active_staff = [r for r in all_staff if r['products'] > 0]
    staff_count = len(active_staff)
    total_products = sum(r['products'] for r in active_staff)
    total_hours = sum(r['hours'] for r in active_staff)
    avg_upph_val = round(sum(r['upph'] for r in active_staff if r['upph'] > 0) / max(staff_count, 1), 1)
    qualified_count = sum(1 for r in active_staff if r['qualified'])
    unqualified_count = staff_count - qualified_count
    pass_rate = round(qualified_count / max(staff_count, 1) * 100, 1)

    kpi = {
        'staff_count': staff_count,
        'total_products': total_products,
        'total_hours': round(total_hours, 1),
        'avg_upph': avg_upph_val,
        'pass_rate': pass_rate,
        'qualified_count': qualified_count,
        'unqualified_count': unqualified_count,
    }

    # ---- 通道分组汇总 ----
    def channel_summary(lst, label, count_all=False):
        active = [r for r in lst if r['products'] > 0]
        # 设备维度(如X光)即使无数据时也要展示配置的设备，故 count 取全量
        cnt = len(lst) if count_all else len(active)
        prod = sum(r['products'] for r in active)
        aupph = round(sum(r['upph'] for r in active if r['upph'] > 0) / max(cnt, 1), 1) if cnt else 0
        th = round(sum(r['hours'] for r in active), 1)
        qcnt = sum(1 for r in active if r['qualified'])
        uqcnt = cnt - qcnt
        prate = round(qcnt / max(cnt, 1) * 100, 1) if cnt else 0
        return {
            'label': label,
            'staff': lst,           # 原始列表（含未出勤，模板过滤）
            'active_staff': active, # 只出勤的
            'count': cnt,
            'total_products': prod,
            'avg_upph': aupph,
            # 完整KPI（供顶部6卡片按通道切换）
            'total_hours': th,
            'pass_rate': prate,
            'qualified_count': qcnt,
            'unqualified_count': uqcnt,
        }

    channels = {
        'all': channel_summary(all_staff, '全部通道'),
        'godzilla': channel_summary(godzilla_list, '🦖 哥斯拉'),
        'mirror': channel_summary(mirror_list, '🪞 魔镜'),
        'xray': channel_summary(xray_list, '⚡ X-RAY', count_all=True),
    }

    # ---- 时段处理量（人员维度：哥斯拉+魔镜） ----
    hourly_data = []
    hourly_totals = {}           # 全通道合计
    hourly_totals_by_channel = {'all': {}, 'godzilla': {}, 'mirror': {}}
    hourly_rates = {}

    # 按通道拆分 active_staff
    gz_active = [r for r in godzilla_list if r['products'] > 0]
    mr_active = [r for r in mirror_list if r['products'] > 0]

    for s in active_staff:
        hc = s.get('hourly_counts') or {}
        name = s.get('name', '')
        tgt = s.get('standard', 52)
        is_gz_2dev = (s.get('source') == 'godzilla' and s.get('num_devices', 0) == 2)
        src = s.get('source', '哥斯拉')

        for hstr, cnt in sorted(hc.items()):
            try:
                hi = int(hstr)
            except (ValueError, TypeError):
                continue
            # 哥斯拉双台设备：该小时处理量>=70 目标135，否则58；其余用各自标准
            if is_gz_2dev:
                tgt_h = 135 if cnt >= 70 else 58
            else:
                tgt_h = tgt
            r_hr = round(cnt / tgt_h * 100, 2) if tgt_h > 0 else 0
            label = '%02d:00-%02d:00' % (hi, hi+1)
            hourly_data.append({
                'name': name,
                'employee_no': s.get('employee_no', ''),
                'cat': s.get('employee_type', '全职'),
                'channel': src,
                'hour_label': label,
                'count': cnt,
                'target': tgt_h,
                'rate': r_hr,
            })
            # 全通道合计
            hourly_totals[label] = hourly_totals.get(label, 0) + cnt
            # 分通道合计
            ch_key = src if src in ('godzilla', 'mirror') else 'godzilla'
            hourly_totals_by_channel[ch_key][label] = hourly_totals_by_channel[ch_key].get(label, 0) + cnt

        # 时段表「达成率」列与人明细「效率达成」保持同一计算逻辑：直接复用该行综合算出的 rate
        rate_key = f"{name}|{s.get('source', '哥斯拉')}"
        hourly_rates[rate_key] = round(s.get('rate', 0), 2)

    # ---- 时段处理量（设备维度：X光，按设备编号聚合） ----
    xray_hourly_data = []
    xray_hourly_totals = {}
    xray_hourly_rates = {}
    for d in xray_list:
        hc = d.get('hourly_counts') or {}
        name = d['name']
        tgt = XRAY_STANDARD_UPH
        rates_for_device = []
        for hstr, cnt in sorted(hc.items()):
            try:
                hi = int(hstr)
            except (ValueError, TypeError):
                continue
            r_hr = round(cnt / tgt * 100, 2) if tgt > 0 else 0
            label = '%02d:00-%02d:00' % (hi, hi + 1)
            xray_hourly_data.append({
                'name': name,
                'employee_no': '',
                'cat': '设备',
                'channel': 'xray',
                'hour_label': label,
                'count': cnt,
                'target': tgt,
                'rate': r_hr,
            })
            xray_hourly_totals[label] = xray_hourly_totals.get(label, 0) + cnt
            rates_for_device.append(r_hr)
        if rates_for_device:
            xray_hourly_rates[f"{name}|xray"] = round(sum(rates_for_device) / len(rates_for_device), 2)

    # ---- 时段处理量：全局连续排名（按总处理量降序，与人员效率明细一致） ----
    from collections import defaultdict
    person_totals = defaultdict(lambda: {'total': 0, 'channel': ''})
    for h in hourly_data:
        key = (h['name'], h['channel'])
        person_totals[key]['total'] += h['count']
        person_totals[key]['channel'] = h['channel']
    # 全部汇总到一起，按总销量全局降序，分配连续 rank 1~N
    all_entries = []
    for (name, ch), info in person_totals.items():
        if info['total'] > 0:
            all_entries.append({'name': name, 'channel': ch, 'total': info['total']})
    all_entries.sort(key=lambda x: x['total'], reverse=True)
    for i, entry in enumerate(all_entries, 1):
        entry['rank'] = i
    # 展平为 {name|channel: rank}
    hourly_ranks = {}
    for e in all_entries:
        hourly_ranks[f"{e['name']}|{e['channel']}"] = e['rank']

    return {
        'kpi': kpi,
        'channels': channels,
        'godzilla': godzilla_list,
        'mirror': mirror_list,
        'xray_list': xray_list,
        'hourly_data': hourly_data,
        'hourly_rates': hourly_rates,
        'hourly_totals': hourly_totals,
        'hourly_totals_by_channel': hourly_totals_by_channel,
        'hourly_ranks': hourly_ranks,          # 时段表组内排名
        'xray_hourly_data': xray_hourly_data,
        'xray_hourly_rates': xray_hourly_rates,
        'xray_hourly_totals': xray_hourly_totals,
    }


# 需要过滤的非人名关键字
_NON_PERSON_KEYS = {'汇总', '合计', '支援', '辅助', '模板', '示例'}

def _is_person_name(name):
    """判断名称是否为有效人员姓名"""
    if not name or not name.strip():
        return False
    cleaned = name.strip()
    for k in _NON_PERSON_KEYS:
        if k in cleaned:
            return False
    return True


def _load_roster_info():
    """从花名册加载 name→{employee_type, c5, dept_group} 映射（键同时存原样与去空格，防空格名不匹配）"""
    info = {}
    try:
        roster = _read_json(STAFF_ROSTER)
        if roster and isinstance(roster, dict):
            for s in roster.get('staff', []):
                nm = (s.get('name') or '').strip()
                if nm:
                    rec = {
                        'employee_type': s.get('employee_type', ''),
                        'c5': s.get('c5', ''),
                        'dept_group': s.get('dept_group', ''),
                        'employee_no': s.get('employee_no', ''),
                    }
                    info[nm] = rec
                    info[nm.replace(' ', '')] = rec   # 去空格别名
    except Exception:
        pass
    return info


def _adapt_app(data, keep=None):
    """金山文档APP效率数据 -> 模板数据（8 KPI + 全职双表/兼职单表 + 时段）

    核心逻辑：
    - 按花名册 employee_type 分为 全职 / 兼职
    - 全职拆为两张表：APP登记(安卓装跑) + 苹果二步登记(苹果测跑)
    - 兼职只有一张表：APP登记(安卓)
    - 工时 = 首台扫码时间 ~ 末台扫码时间，末台>13:00扣1小时
    - 达标标准：安卓26.3 / 苹果40.5
    """
    from datetime import datetime, time as _time

    ranking = data.get('staff_ranking', []) or []

    # 过滤非人员记录
    ranking = [s for s in ranking if _is_person_name(s.get('name', ''))]

    # 规范化姓名：去除内部空格（源数据偶发"李 娟"带空格，导致花名册工号按姓名匹配失败）
    for s in ranking:
        s['name'] = (s.get('name') or '').replace(' ', '').strip()

    # 花名册组过滤（APP组）
    if keep is not None:
        ranking = [s for s in ranking if keep(s)]

    # 加载花名册补充信息
    roster_info = _load_roster_info()

    # ---- 辅助函数 ----
    def parse_dt(s):
        if not s:
            return None
        if isinstance(s, datetime):
            return s
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M:%S'):
            try:
                return datetime.strptime(str(s).strip()[:19], fmt)
            except Exception:
                continue
        return None

    def calc_work_hours(first_str, last_str):
        first = parse_dt(first_str)
        last = parse_dt(last_str)
        if not first or not last:
            return 0
        hours = (last - first).total_seconds() / 3600
        # 末台扫码时间 >= 13:00 则扣1小时午休
        if last.time() >= _time(13, 0):
            hours -= 1
        return round(max(hours, 0), 2)

    def calc_today_hours_from_hourly(hourly_counts):
        """从『当日』逐小时产量推算今日效率出勤时长：首末活跃小时之差；
        末台活跃小时 >= 13:00 扣 1 小时午休。源数据 overall_first/last 为月跨度，
        故当日工时必须依据 hourly_counts 计算，避免把月累计工时当今日。"""
        if not hourly_counts:
            return 0
        try:
            active = [int(h) for h in hourly_counts if (hourly_counts.get(h) or 0) > 0]
        except (ValueError, TypeError):
            return 0
        if not active:
            return 0
        first_h, last_h = min(active), max(active)
        span = last_h - first_h
        if last_h >= 13:
            span -= 1
        return round(max(span, 0), 2)

    def norm_type(et):
        """类型列只显示 全职/兼职：去掉「外包」前缀"""
        t = (et or '').replace('外包', '').strip()
        return '兼职' if '兼职' in t else '全职'

    def make_row(s):
        name = (s.get('name') or '').strip()
        ri = roster_info.get(name, {})
        emp_type = norm_type(ri.get('employee_type', ''))

        # 装跑类型：按『今日』实际有记录的表判断（不再用整月 android_count/ios_count）
        # APP登记表(安卓)有today记录 → 安卓；苹果二步登记表(苹果)有today记录 → 苹果；都有 → 安卓+苹果
        ta = s.get('today_android_first')
        ti = s.get('today_ios_first')
        if ta and ti:
            wt = '安卓+苹果'
        elif ta:
            wt = '安卓'
        elif ti:
            wt = '苹果'
        else:
            # 今日两个表都无记录 → 显示"无"（今日未参与任何装跑）
            wt = '无'
        a = s.get('android_count', 0) or 0
        i = s.get('ios_count', 0) or 0

        # 总处理量 = 『今日』真实处理量（按日期筛选的当日扫码条数）。
        # 注意：today / (android_count+ios_count) 均为整月累计，绝不能回退，
        # 否则今天未扫码的人员会显示成整月数字。今天无扫码则今日=0。
        total = s.get('today_real') or 0

        # 效率时长：使用『今日』扫码首末时间（带日期，已按 target_date 筛选）；
        # 末台扫码 >= 13:00 扣 1 小时午休。源缓存的 work_hours / overall_* 是月累计，均不使用。
        # 今日无任何扫码记录（总处理量=0）则效率时长记为 0，避免回退到月累计时长。
        if total <= 0:
            wh = 0
            hours_estimated = False
        else:
            tf = s.get('today_overall_first')
            tl = s.get('today_overall_last')
            wh = calc_work_hours(tf, tl)
            if not wh:
                # 今日无带时间扫码记录时，用当日逐小时产量推算首末活跃小时（末台>=13:00 扣1h）
                wh = calc_today_hours_from_hourly(s.get('hourly_counts'))
            wh = round(wh, 2) if wh else 0
            # 兜底：源数据时间戳损坏（首末均为午夜、小时被截成0）导致工时=0，但确有扫码量 →
            # 按标准班次(7h，午休已扣)估算，避免"上班却显示0达成"的误导。真实工时需源头修正时间戳。
            hours_estimated = False
            if wh == 0:
                wh = 7.0
                hours_estimated = True

        # UPPH：总处理量 / 效率时长
        upph = round(total / wh, 2) if wh > 0 and total > 0 else 0

        # 达标标准：安卓 26.3 / 苹果 40.5 / 安卓+苹果 按数量加权
        if wt == '苹果':
            std = APP_IOS_STANDARD
        elif wt == '安卓+苹果':
            if a and i:
                std = (APP_ANDROID_STANDARD * a + APP_IOS_STANDARD * i) / (a + i)
            elif a:
                std = APP_ANDROID_STANDARD
            else:
                std = APP_IOS_STANDARD
        else:
            std = APP_ANDROID_STANDARD

        rate = round(upph / std * 100, 2) if std > 0 and upph > 0 else 0

        return {
            'name': name,
            'emp_no': ri.get('employee_no', ''),     # 工号（来自花名册，姓名已归一化匹配）
            'emp_type': emp_type,
            'position': 'APP',                         # 岗位列只显示 APP
            'products': total,
            'device': wt,                             # 装跑类型
            'hours': wh,                             # 效率时长
            'hours_estimated': hours_estimated,      # 工时是否为损坏时间戳兜底估算（⚠）
            'upph': upph,
            'standard': round(std, 2),
            'rate': rate,
            'qualified': rate >= 100,
        }

    # 统一构建 APP 人员明细（单表，类型仅 全职/兼职）
    app_rows = [make_row(s) for s in ranking]
    app_rows.sort(key=lambda x: x['products'], reverse=True)

    # ── 过滤：只保留今日有出勤数据的人员（处理量 > 0） ──
    # 今日未扫码的人不展示在明细中，KPI也只按实际出勤人数统计
    active_rows = [r for r in app_rows if r.get('products', 0) > 0]
    active_names = set(r['name'] for r in active_rows)
    # 时段也只取出勤人员的
    active_ranking = [s for s in ranking if s.get('name', '') in active_names]

    # ---- 8个KPI计算（基于实际出勤人数） ----
    staff_count = len(active_rows)
    total_products = sum(r.get('products', 0) for r in active_rows)

    upph_vals = [r.get('upph', 0) for r in active_rows if r.get('upph', 0) > 0]
    avg_upph_val = round(sum(upph_vals) / len(upph_vals), 2) if upph_vals else 0

    total_wh = sum(r.get('hours', 0) for r in active_rows)
    avg_hours = round(total_wh / staff_count, 1) if staff_count > 0 else 0

    # 单小时运能：各时段团队总量的最大值（峰值小时产能），与时段表合计行一致
    _hourly_totals_for_ops = {}
    for s in active_ranking:
        for hstr, cnt in (s.get('hourly_counts') or {}).items():
            try:
                _hourly_totals_for_ops[hstr] = _hourly_totals_for_ops.get(hstr, 0) + int(cnt)
            except (ValueError, TypeError):
                continue
    ops_per_hour = max(_hourly_totals_for_ops.values()) if _hourly_totals_for_ops else 0

    qualified_count = sum(1 for r in active_rows if r['qualified'])
    unqualified_count = staff_count - qualified_count
    ft_count = sum(1 for r in active_rows if r['emp_type'] == '全职')
    pt_count = sum(1 for r in active_rows if r['emp_type'] == '兼职')

    # ---- 时段展开（仅出勤人员，目标按装跑类型区分） ----
    hourly_data = []
    hourly_totals = {}  # hour_label → 总量
    hourly_rates = {}   # name → 各时段达成率（cnt/目标×100）的算术平均值

    # 构建姓名→装跑类型的映射（从已计算的 app_rows 取 device）
    device_map = {r['name']: r.get('device', '安卓') for r in active_rows}

    for s in active_ranking:
        hc = s.get('hourly_counts') or {}
        name = s.get('name', '')
        ri2 = roster_info.get(name, {})
        et = ri2.get('employee_type', '')
        cat = '兼职' if '兼职' in et else '全职'

        # 按装跑类型确定目标
        wt = device_map.get(name, '安卓')
        if wt == '苹果':
            tgt = APP_IOS_STANDARD
        elif wt == '安卓+苹果':
            tgt = (APP_ANDROID_STANDARD + APP_IOS_STANDARD) / 2  # 综合平均
        else:
            tgt = APP_ANDROID_STANDARD

        rates_for_person = []  # 该人各时段达成率
        for hstr, cnt in sorted(hc.items()):
            try:
                hi = int(hstr)
            except (ValueError, TypeError):
                continue
            r_hr = round(cnt / tgt * 100, 2) if tgt > 0 else 0
            label = '%02d:00-%02d:00' % (hi, hi+1)
            hourly_data.append({
                'name': name,
                'emp_no': ri2.get('employee_no', ''),
                'cat': cat,
                'hour_label': label,
                'count': cnt,
                'target': tgt,
                'rate': r_hr,
            })
            # 累加时段合计
            hourly_totals[label] = hourly_totals.get(label, 0) + cnt
            rates_for_person.append(r_hr)

        # 时段表「达成率」列 = 该人各时段达成率（cnt/目标×100）的算术平均值
        if rates_for_person:
            hourly_rates[name] = round(sum(rates_for_person) / len(rates_for_person), 2)
        else:
            hourly_rates[name] = 0

    return {
        'staff_count': staff_count,
        'total_products': total_products,
        'avg_upph': avg_upph_val,
        'ops_per_hour': ops_per_hour,
        'total_hours': round(total_wh, 1),
        'avg_hours': avg_hours,
        'qualified_count': qualified_count,
        'unqualified_count': unqualified_count,
        'app_rows': active_rows,       # ← 只返回出勤人员
        'staff_list': active_rows,
        'ft_count': ft_count,
        'pt_count': pt_count,
        'hourly_data': hourly_data,
        'hourly_totals': hourly_totals,
        'hourly_rates': hourly_rates,  # name → 平均时段达成率
        'last_update': data.get('last_update', '') or data.get('date', ''),
    }


def _get_app_standard(staff_item):
    """根据工作类型返回APP UPPH标准"""
    wt = staff_item.get('work_type', '')
    if wt == '苹果' or wt == 'ios':
        return APP_IOS_STANDARD
    return APP_ANDROID_STANDARD


def _get_source_tag(work_type):
    """根据工作类型返回强赔宗源标签"""
    if work_type == '苹果' or work_type == 'ios' or work_type == '安卓+苹果':
        return 'iOS'
    return '安卓'

# ── 路由 ──
@app.route('/')
def index():
    photo = _adapt_photo(_read_cache('photo_cache.json'))
    auto = _adapt_auto(_read_cache('automation_cache.json'))
    app_d = _adapt_app(_read_cache('app_cache.json'))

    total_count = photo['total_products'] + auto['total_products'] + app_d['total_products']
    total_staff = photo['staff_count'] + auto['staff_count'] + app_d['staff_count']
    avg_eff = round(total_count / max(total_staff, 1), 1)

    all_rates = [s.get('rate', 0) for s in photo['staff_list'] + auto['staff_list'] + app_d['staff_list'] if s.get('rate', 0) > 0]
    avg_rate = round(sum(all_rates) / len(all_rates), 1) if all_rates else 0

    # Top10
    all_staff = []
    for s in photo['staff_list']:
        s2 = dict(s); s2['tag'] = '拍照'; all_staff.append(s2)
    for s in auto['staff_list']:
        s2 = dict(s); s2['tag'] = '质检'; all_staff.append(s2)
    for s in app_d['staff_list']:
        s2 = dict(s); s2['tag'] = 'APP录入'; all_staff.append(s2)
    all_staff.sort(key=lambda x: x.get('rate', 0), reverse=True)
    top10 = all_staff[:10]

    return render_template('index.html', active='home',
        photo_data=photo, mirror_data=auto, app_data=app_d,
        total_count=total_count, total_staff=total_staff,
        avg_efficiency=avg_eff, achievement_rate=avg_rate,
        top10=top10, cache_time=datetime.now().strftime('%H:%M:%S'))

@app.route('/photo')
def photo():
    # 拍照效率：后端预处理（排序/排名/时段综合达成），同时保留原始 data 供卡片使用
    raw = _read_cache('photo_cache.json') or {}
    d = _adapt_photo(raw)
    return render_template('photo.html', active='photo',
        data=raw,                    # 原始缓存（method_groups / service_standard_groups 等卡片需要）
        pd=d,                        # 结构化数据（staff_list/hourly_data/hourly_rates）
        cache_time=datetime.now().strftime('%H:%M:%S'))

@app.route('/automation')
def automation():
    rg = _load_roster_groups()
    xray_raw = _load_xray_data()
    auto = _adapt_auto_grouped(_read_cache('automation_cache.json'), rg, xray_raw)
    return render_template('automation.html', active='automation',
        godzilla_list=auto['godzilla'],
        mirror_list=auto['mirror'],
        xray_list=auto['xray_list'],
        hourly_data=auto.get('hourly_data', []),
        hourly_rates=auto.get('hourly_rates', {}),
        hourly_totals=auto.get('hourly_totals', {}),
        hourly_totals_by_channel=auto.get('hourly_totals_by_channel', {}),
        hourly_ranks=auto.get('hourly_ranks', {}),
        xray_hourly_data=auto.get('xray_hourly_data', []),
        xray_hourly_rates=auto.get('xray_hourly_rates', {}),
        xray_hourly_totals=auto.get('xray_hourly_totals', {}),
        kpi=auto['kpi'],
        k=auto['kpi'],
        channels=auto['channels'],
        cache_time=datetime.now().strftime('%H:%M:%S'))


def _load_xray_data():
    """读取X光设备数据（独立缓存 xray_cache.json，与原始扣子应用一致）。"""
    for p in (os.path.join(BASE_DIR, 'xray_cache.json'),
              os.path.join(CACHE_SOURCE, 'xray_cache.json')):
        d = _read_json(p)
        if d and d.get('staff_ranking'):
            return d
    return {}

@app.route('/app-efficiency')
def app_efficiency():
    """APP效率监控 - 优先读缓存，支持从金山文档实时获取"""
    # 尝试从缓存读取
    cache_data = _read_cache('app_cache.json')
    if not cache_data or not cache_data.get('staff_ranking'):
        # 缓存为空时尝试从金山文档获取
        cache_data = _fetch_app_from_kodos()
    # 只展示花名册 APP 组人员
    keep = _keep_for_groups(['APP'])
    return render_template('app.html', active='app',
        data=_adapt_app(cache_data, keep),
        cache_time=datetime.now().strftime('%H:%M:%S'))


@app.route('/api/app-efficiency/refresh', methods=['POST'])
def api_app_efficiency_refresh():
    """从金山文档实时刷新APP效率数据"""
    try:
        data = _fetch_app_from_kodos(force=True)
        return jsonify({
            'ok': True,
            'staff_count': len(data.get('staff_ranking', [])),
            'total_products': data.get('total_count', 0),
            'last_update': data.get('last_update', ''),
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/photo/refresh', methods=['POST'])
def api_photo_refresh():
    """从数据源实时刷新拍照效率（按当日日期拉取，确保为今日数据而非月累计）"""
    try:
        from services.efficiency_service import EfficiencyService
        from datetime import date
        svc = EfficiencyService()
        today = date.today().strftime('%Y-%m-%d')
        data = svc.get_photo_efficiency(today)
        _save_json(os.path.join(CACHE_SOURCE, 'photo_cache.json'), data)
        _save_json(os.path.join(BASE_DIR, 'photo_cache.json'), data)
        sl = data.get('staff_ranking', []) or data.get('staff_list', [])
        return jsonify({
            'ok': True,
            'staff_count': len(sl),
            'total_count': data.get('total_count', 0),
            'last_update': data.get('date', today),
            'session_expired': data.get('session_expired', False),
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/automation/refresh', methods=['POST'])
def api_automation_refresh():
    """从数据源实时刷新自动化效率（按当日日期拉取，确保为今日数据而非月累计）"""
    try:
        from services.efficiency_service import EfficiencyService
        from datetime import date
        svc = EfficiencyService()
        today = date.today().strftime('%Y-%m-%d')
        data = svc.get_automation_efficiency(today)
        _save_json(os.path.join(CACHE_SOURCE, 'automation_cache.json'), data)
        _save_json(os.path.join(BASE_DIR, 'automation_cache.json'), data)
        sl = data.get('staff_ranking', []) or data.get('staff_list', [])
        resp = {
            'ok': True,
            'staff_count': len(sl),
            'total_count': data.get('total_count', 0),
            'last_update': data.get('date', today),
            'session_expired': data.get('session_expired', False),
            'xray': None,
        }
        # X光设备数据（独立缓存，设备维度）
        try:
            xray_data = svc.get_xray_efficiency(today)
            _save_json(os.path.join(CACHE_SOURCE, 'xray_cache.json'), xray_data)
            _save_json(os.path.join(BASE_DIR, 'xray_cache.json'), xray_data)
            resp['xray'] = {
                'machines': len(xray_data.get('staff_ranking', []) or []),
                'total_count': xray_data.get('total_count', 0),
            }
        except Exception as e:
            app.logger.error(f'[X光] 刷新失败: {e}')
            resp['xray'] = {'error': str(e)}
        return jsonify(resp)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/watcher/refresh', methods=['POST'])
def api_watcher_refresh():
    """从Watcher(abdavinci综合报表)实时刷新综合数据，写入 watcher_cache.json"""
    try:
        from services.efficiency_service import EfficiencyService
        from datetime import date
        svc = EfficiencyService()
        today = date.today().strftime('%Y-%m-%d')
        wc = svc.watcher_client
        details = {}
        for name in ['godzilla_detail', 'mirror_detail', 'attendance_detail']:
            try:
                rows = wc.fetch_widget_data(name, today, today)
                details[name] = {
                    'rows': len(rows) if rows else 0,
                    'sample': rows[0] if rows else None,
                }
            except Exception as e:
                details[name] = {'error': str(e)}
        gz = details.get('godzilla_detail', {}).get('rows', 0)
        mr = details.get('mirror_detail', {}).get('rows', 0)
        att = details.get('attendance_detail', {}).get('rows', 0)
        data = {
            'date': today,
            'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'widgets': {
                '哥斯拉拍照人数': gz,
                '魔镜质检人数': mr,
                '出勤人数': att,
                '数据状态': '实时' if (gz or mr or att) else '空',
            },
            'details': details,
        }
        _save_json(os.path.join(CACHE_SOURCE, 'watcher_cache.json'), data)
        _save_json(os.path.join(BASE_DIR, 'watcher_cache.json'), data)
        return jsonify({'ok': True, 'godzilla': gz, 'mirror': mr, 'attendance': att})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# 后台刷新锁：防止 5 分钟定时触发时上一次刷新尚未完成导致重叠
_refresh_all_lock = threading.Lock()


def _perform_refresh_all():
    """实际执行全量刷新（在后台线程中运行）"""
    result = {'time': datetime.now().strftime('%H:%M:%S'), 'details': {}}
    errors = []

    # === 飞书数据源优先刷新（放在最前，避免被慢外部源拖垮导致冻结）===
    # A. 飞书三大区块（趋势 / 效率指标 / 质量指标）
    try:
        feishu_ok = True
        try:
            td = get_efficiency_trend(EFFICIENCY_TREND_CONFIG)
            if td and td.get('dates'):
                td['baseline'] = [round(v * 100, 2) if v is not None else None for v in td['baseline']]
                td['current'] = [round(v * 100, 2) if v is not None else None for v in td['current']]
                save_trend_cache(td, TREND_CACHE_PATH)
        except Exception as e:
            feishu_ok = False; app.logger.error(f'[自动刷新] 趋势失败: {e}')
        try:
            md = get_latest_metrics(EFFICIENCY_TREND_CONFIG)
            if md and md.get('metrics'):
                save_metric_cache(md, METRIC_CACHE_PATH)
        except Exception as e:
            feishu_ok = False; app.logger.error(f'[自动刷新] 效率指标失败: {e}')
        try:
            qd = get_quality_metrics_from_mail()
            if qd and qd.get('items'):
                save_quality_cache(qd, QUALITY_CACHE_PATH)
        except Exception as e:
            feishu_ok = False; app.logger.error(f'[自动刷新] 质量指标失败: {e}')
        result['details']['feishu'] = {'ok': feishu_ok}
    except Exception as e:
        errors.append('feishu'); result['details']['feishu'] = {'error': str(e)}
    # B. 飞书待办（多维表格）—— 纳入自动刷新，避免待办页数据源冻结
    try:
        items, err = _sync_feishu_todos(force=True)
        result['details']['feishu_todo'] = {
            'ok': err is None, 'count': len(items or []), 'error': err
        }
    except Exception as e:
        errors.append('feishu_todo'); result['details']['feishu_todo'] = {'error': str(e)}

    # 1. 自动化(哥斯拉/魔镜/X光)
    try:
        r = api_automation_refresh()
        j = r.get_json()
        result['details']['automation'] = j
    except Exception as e:
        errors.append('automation'); result['details']['automation'] = {'error': str(e)}

    # 2. 拍照(光合)
    try:
        r = api_photo_refresh()
        j = r.get_json()
        result['details']['photo'] = j
    except Exception as e:
        errors.append('photo'); result['details']['photo'] = {'error': str(e)}

    # 3. Watcher(综合报表)
    try:
        r = api_watcher_refresh()
        j = r.get_json()
        result['details']['watcher'] = j
    except Exception as e:
        errors.append('watcher'); result['details']['watcher'] = {'error': str(e)}

    # 4. APP(金山文档)
    try:
        r = api_app_efficiency_refresh()
        j = r.get_json()
        result['details']['app'] = j
    except Exception as e:
        errors.append('app'); result['details']['app'] = {'error': str(e)}

    if errors:
        result['status'] = 'partial'
        result['message'] = '部分刷新失败: ' + '、'.join(errors)
    else:
        result['status'] = 'ok'
        result['message'] = '全部刷新成功'
    return result


@app.route('/api/refresh/all', methods=['POST'])
def api_refresh_all():
    """统一刷新全部数据源：自动化(哥斯拉/魔镜/X光) + 拍照(光合) + Watcher + APP。
    后台异步执行（外部源偶发较慢，最长可达数分钟），立即返回，避免客户端超时。"""
    if not _refresh_all_lock.acquire(blocking=False):
        return jsonify({'status': 'skipped', 'message': '刷新正在进行中，跳过本次触发',
                        'time': datetime.now().strftime('%H:%M:%S')})

    def _worker():
        try:
            _perform_refresh_all()
        except Exception:
            pass
        finally:
            _refresh_all_lock.release()

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({'status': 'started', 'message': '已在后台开始刷新',
                    'time': datetime.now().strftime('%H:%M:%S')})


def _fetch_app_from_kodos(force=False):
    """从金山文档获取APP效率数据（通过efficiency_service）"""
    cache_path = os.path.join(CACHE_SOURCE, 'app_cache.json')

    if not force:
        # 检查缓存是否有效（5分钟内）
        if os.path.exists(cache_path):
            try:
                mtime = os.path.getmtime(cache_path)
                if (time.time() - mtime) < 300:  # 5分钟
                    return _read_json(cache_path)
            except:
                pass

    # 从金山文档实时拉取
    try:
        from services.efficiency_service import EfficiencyService
        svc = EfficiencyService()
        data = svc.get_app_efficiency()

        # 保存到原版应用缓存目录
        try:
            _save_json(cache_path, data)
        except:
            pass

        # 同时保存到my-dashboard目录
        local_cache = os.path.join(BASE_DIR, 'app_cache.json')
        try:
            _save_json(local_cache, data)
        except:
            pass

        return data
    except Exception as e:
        app.logger.error(f'[APP效率] 金山文档获取失败: {e}')
        # 兜底返回已有缓存
        if os.path.exists(cache_path):
            return _read_json(cache_path)
        return {'staff_ranking': [], 'total_count': 0, 'staff_count': 0}


def _save_json(path, data):
    def _default(o):
        # datetime 等不可序列化对象转字符串，避免缓存写入失败
        if isinstance(o, (datetime,)):
            return o.strftime('%Y-%m-%d %H:%M:%S')
        return str(o)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=_default)

@app.route('/groups')
def groups():
    """各组人员效率 — 4通道汇总 + 效率对比 + 拍照人员明细"""
    # 禁止浏览器缓存，确保每次刷新都是最新数据
    from flask import make_response
    resp = make_response(_groups_body())
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


def _groups_body():
    # ═══ 直接复用各效率监控页的适配器，保证数据完全一致 ═══

    # ── APP 通道：复用 APP效率监控 适配器 ──
    app_cache = _read_cache('app_cache.json') or {}
    app_adapt = _adapt_app(app_cache, _keep_for_groups(['APP']))
    app_rows = [r for r in app_adapt.get('app_rows', []) if r.get('products', 0) > 0]
    app_total = sum(r['products'] for r in app_rows)
    app_rates = [r['rate'] for r in app_rows if r.get('rate', 0) > 0]
    app_achieve = round(sum(app_rates) / len(app_rates), 1) if app_rates else 0
    app_staff_list = [{
        'name': r['name'],
        'employee_no': '',
        'type': r.get('emp_type', '全职'),
        'position': 'APP',
        'achievement_rate': round(r['rate'], 1),
        'total_count': r['products'],
        'work_hours': r.get('hours', 0) or 0,
        'upph': round(r['upph'], 1),
    } for r in app_rows]

    # ── 哥斯拉/魔镜：复用 自动化效率监控 适配器 ──
    rg = _load_roster_groups()
    auto = _adapt_auto_grouped(_read_cache('automation_cache.json'), rg, _load_xray_data())

    def _build_channel(lst):
        """从 _adapt_auto_grouped 的行构造人员明细 + 通道汇总"""
        active = [r for r in lst if r['products'] > 0]
        total = sum(r['products'] for r in active)
        rates = [r['rate'] for r in active if r.get('rate', 0) > 0]
        achieve = round(sum(rates) / len(rates), 1) if rates else 0
        staff = [{
            'name': r['name'],
            'employee_no': r.get('employee_no', ''),
            'type': r.get('employee_type', '全职'),
            'position': r.get('position', ''),
            'achievement_rate': round(r['rate'], 1),
            'total_count': r['products'],
            'work_hours': r.get('hours', 0) or 0,
            'upph': round(r['upph'], 1),
        } for r in active]
        return active, total, achieve, staff

    gz_active, gz_total, gz_achieve, gz_staff_list = _build_channel(auto.get('godzilla', []))
    mj_active, mj_total, mj_achieve, mj_staff_list = _build_channel(auto.get('mirror', []))

    # ── 拍照：复用 拍照效率监控 缓存（与 photo.html 同源）──
    photo_cache = _read_cache('photo_cache.json') or {}
    photo_ranking = photo_cache.get('staff_ranking', []) or []
    ph_total = sum((s.get('total_count', 0) or 0) for s in photo_ranking)
    ph_rates = [s.get('achievement_rate', 0) or 0 for s in photo_ranking if (s.get('achievement_rate', 0) or 0) > 0]
    ph_achieve = round(sum(ph_rates) / len(ph_rates), 1) if ph_rates else 0
    ph_staff_list = [{
        'name': s.get('name', '-'),
        'employee_no': s.get('employee_no', ''),
        'type': (s.get('employee_type', '') or '').replace('外包', '').strip() or '全职',
        'position': (s.get('position_tag', '拍照组') or '').replace('瑕疵图', 'C端拍照') or '拍照组',
        'achievement_rate': round(s.get('achievement_rate', 0) or 0, 1),
        'total_count': s.get('total_count', 0) or 0,
        'work_hours': s.get('work_hours', 0) or 0,
        'upph': round(s.get('efficiency', 0) or 0, 1),
        'machine_id': s.get('machine_id', ''),
        'target_efficiency': s.get('target_efficiency', 0) or 0,
        'data_sources': s.get('data_sources', []),
    } for s in photo_ranking]

    channels = [
        {'key': 'app', 'label': 'APP人员效率', 'icon': '📱', 'color': 'blue',
         'count': len(app_rows), 'total': app_total, 'achieve': app_achieve,
         'staff_list': app_staff_list},
        {'key': 'godzilla', 'label': '哥斯拉人员效率', 'icon': '🦎', 'color': 'orange',
         'count': len(gz_active), 'total': gz_total, 'achieve': gz_achieve,
         'staff_list': gz_staff_list},
        {'key': 'mirror', 'label': '魔镜人员效率', 'icon': '🔮', 'color': 'purple',
         'count': len(mj_active), 'total': mj_total, 'achieve': mj_achieve,
         'staff_list': mj_staff_list},
        {'key': 'photo', 'label': '拍照人员效率', 'icon': '📷', 'color': 'green',
         'count': len(photo_ranking), 'total': ph_total, 'achieve': ph_achieve,
         'staff_list': ph_staff_list},
    ]

    return render_template('groups.html', active='groups',
        channels=channels,
        app_detail=app_staff_list, godzilla_detail=gz_staff_list,
        mirror_detail=mj_staff_list, photo_detail=ph_staff_list,
        cache_time=datetime.now().strftime('%H:%M:%S'))


def _norm_emp_type(s):
    """从员工记录提取规范化类型（全职/兼职）"""
    t = (s.get('employee_type', '') or ''
         or s.get('type', '') or '').replace('外包', '').strip()
    return '兼职' if '兼职' in t else ('全职' if t else '全职')

@app.route('/staff')
def staff():
    roster = _read_json(STAFF_ROSTER)
    staff_list = roster if isinstance(roster, list) else roster.get('staff', roster.get('employees', []))

    # 按岗位组别统计（7个分类卡片）
    category_stats = _compute_category_stats(staff_list)
    last_update = roster.get('last_update', '') if isinstance(roster, dict) else ''

    return render_template('staff.html', active='staff',
        staff_list=staff_list, staff_count=len(staff_list),
        category_stats=category_stats,
        last_update=last_update,
        role_class_map=_ROLE_CLASS_MAP,
        avatar_color_map=_AVATAR_COLOR_MAP,
        cache_time=datetime.now().strftime('%H:%M:%S'))


def _compute_category_stats(staff_list):
    """计算7个岗位组别的员工数量"""
    # 原始分组映射（dept_group → 显示名）
    PHOTO_TAGS = {'B端拍照', 'C端拍照', '瑕疵图'}
    PRIVACY_TAGS = {'隐私', '信息维修'}

    # 7个固定分类及顺序
    categories = [
        ('APP', 'APP'),
        ('哥斯拉', '哥斯拉'),
        ('魔镜', '魔镜'),
        ('Xray', 'Xray'),
        ('充电移交', '充电移交'),
        ('拍照', '拍照'),       # B端+C端+瑕疵图 合并
        ('隐私/信息维修', '隐私/信息维修'),  # 隐私+信息维修 合并
    ]

    counts = {key: 0 for key, _ in categories}
    for s in staff_list:
        dg = s.get('dept_group', '')
        pt = s.get('position_tag', '')

        # 拍照类合并
        if pt in PHOTO_TAGS or dg in PHOTO_TAGS:
            counts['拍照'] += 1
        # 隐私/信息维修合并
        elif pt in PRIVACY_TAGS or dg == '隐私/信息维修':
            counts['隐私/信息维修'] += 1
        # 其他精确匹配 dept_group 或 position_tag
        elif dg in counts:
            counts[dg] += 1
        elif pt.lower() == 'xray' or pt == 'Xray':
            counts['Xray'] += 1

    return [(label, counts[key]) for key, label in categories]

@app.route('/api/staff/refresh', methods=['POST'])
def api_staff_refresh():
    """从飞书云文档(人员管理名单)实时刷新人员数据"""
    try:
        from services.lark_cli_base_client import sync_staff_from_lark_base
        sync_staff_from_lark_base(STAFF_ROSTER)
        roster = _read_json(STAFF_ROSTER)
        staff_list = roster if isinstance(roster, list) else roster.get('staff', [])
        cat_stats = _compute_category_stats(staff_list)
        return jsonify({
            'ok': True,
            'total': len(staff_list),
            'last_update': roster.get('last_update', '') if isinstance(roster, dict) else '',
            'source': roster.get('source', '') if isinstance(roster, dict) else '',
            'category_stats': cat_stats,
            'staff': staff_list,
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/settings')
def settings():
    return render_template('settings.html', active='settings',
        config={
            'godzilla_fulltime': 58, 'godzilla_parttime': 52,
            'mirror': 183.7, 'app_android': 26.3, 'app_ios': 40.5,
            'photo_fulltime': 58, 'photo_parttime': 52,
        }, cache_time=datetime.now().strftime('%H:%M:%S'))

@app.route('/watcher')
def watcher():
    return render_template('watcher.html', active='watcher',
        data=_read_cache('watcher_cache.json'),
        cache_time=datetime.now().strftime('%H:%M:%S'))

@app.route('/schedule')
def schedule():
    return render_template('schedule.html', active='schedule', cache_time=datetime.now().strftime('%H:%M:%S'))

# ── 待办事项（飞书云文档同步 + 本地手动添加，合并展示）──
TODO_PATH = os.path.join(BASE_DIR, 'todo_data.json')
FEISHU_TODO_CACHE = os.path.join(BASE_DIR, 'feishu_todo_cache.json')

def _lark_cli():
    """解析 lark-cli 可执行文件路径（优先 PATH，回退到连接器安装目录）。"""
    import shutil
    p = shutil.which('lark-cli') or shutil.which('lark-cli.cmd')
    if p:
        return p
    cand = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))),
        '.workbuddy', 'binaries', 'node', 'cli-connector-packages', 'lark-cli.cmd')
    return cand if os.path.exists(cand) else 'lark-cli'
# 飞书多维表格（待办云文档）配置
FEISHU_TODO_BASE = {
    'base_token': 'ZKagbd0Fsa0sP2sKPZychRrMnMg',
    'table_id': 'tblRKF5q8mWx9I6n',
    'view_id': 'vew9eoF1ig',
    'url': 'https://atrenew.feishu.cn/base/ZKagbd0Fsa0sP2sKPZychRrMnMg',
}
# 飞书 select 优先级 -> 前端色
FEISHU_PRIO_COLOR = {
    '重要紧急': '#e53935',
    '紧急不重要': '#fb8c00',
    '重要不紧急': '#1e88e5',
    '不重要不紧急': '#9e9e9e',
}

def _load_todos():
    d = _read_json(TODO_PATH)
    return d.get('items', []) if isinstance(d, dict) else []

def _save_todos(items):
    _save_json(TODO_PATH, {
        'items': items,
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

# ── 飞书云文档待办同步 ──
def _run_lark_record_list():
    """调用 lark-cli 拉取飞书多维表格记录（markdown），返回 (text, error)。"""
    cfg = FEISHU_TODO_BASE
    cmd = [
        _lark_cli(), 'base', '+record-list',
        '--base-token', cfg['base_token'],
        '--table-id', cfg['table_id'],
        '--view-id', cfg['view_id'],
        '--as', 'user',
    ]
    try:
        r = subprocess.run(cmd, shell=False, capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            return None, (r.stderr or r.stdout).strip() or 'lark-cli 返回非零'
        return r.stdout, None
    except FileNotFoundError:
        return None, 'lark-cli 未安装，请先配置飞书连接器'
    except subprocess.TimeoutExpired:
        return None, 'lark-cli 拉取超时'

def _parse_record_markdown(text):
    """解析 lark-cli record-list 的 markdown 表格，提取 _record_id + 各字段。"""
    lines = text.splitlines()
    header = None
    hidx = -1
    for i, l in enumerate(lines):
        if l.strip().startswith('|') and '_record_id' in l:
            header = [c.strip() for c in l.strip().strip('|').split('|')]
            hidx = i
            break
    if not header:
        return []
    def idx(name):
        return header.index(name) if name in header else -1
    i_name = idx('任务名称'); i_exec = idx('任务执行人')
    i_detail = idx('任务详情描述'); i_deadline = idx('截止日期')
    i_done = idx('完成状态'); i_prio = idx('优先级'); i_prog = idx('任务进度')
    n = len(header)
    out = []
    for l in lines[hidx + 1:]:
        s = l.strip()
        if not s.startswith('|'):
            continue
        if set(s) <= set('|- '):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        # 防御：单元格内含 '|' 导致列数溢出，多余部分并入「任务详情描述」
        if len(cells) > n and i_detail >= 0:
            extra = len(cells) - n
            merged = '|'.join(cells[i_detail:i_detail + 1 + extra])
            cells = cells[:i_detail] + [merged] + cells[i_detail + 1 + extra:]
        if len(cells) < n:
            continue
        rid = cells[0]
        exec_cell = cells[i_exec] if i_exec >= 0 else '[]'
        prio_cell = cells[i_prio] if i_prio >= 0 else '[]'
        try:
            execs = json.loads(exec_cell) if exec_cell else []
        except Exception:
            execs = []
        try:
            prios = json.loads(prio_cell) if prio_cell else []
        except Exception:
            prios = []
        prio = prios[0] if isinstance(prios, list) and prios else ''
        out.append({
            'rid': rid,
            'name': cells[i_name] if i_name >= 0 else '',
            'executor': [u.get('name', '') for u in execs if isinstance(u, dict)],
            'detail': cells[i_detail] if i_detail >= 0 else '',
            'deadline': cells[i_deadline] if i_deadline >= 0 else '',
            'done': (cells[i_done].lower() == 'true') if i_done >= 0 else False,
            'priority': prio,
            'progress': cells[i_prog] if i_prog >= 0 else '',
        })
    return out

def _sync_feishu_todos(force=False):
    """同步飞书待办到本地缓存。返回 (items, error)。items 为归一化字典列表。
    优先 tenant_access_token（云端可用），失败回退本机 lark-cli。"""
    cache = _read_json(FEISHU_TODO_CACHE)
    if not force and cache.get('items') is not None:
        ts = cache.get('synced_at_ts', 0)
        if time.time() - ts < 300:  # 5 分钟内复用缓存
            return cache['items'], None

    # —— tenant_access_token 路径（云端自洽）——
    try:
        import config
        from services.feishu_todo_client import get_todo_records_tenant
        cfg = FEISHU_TODO_BASE
        app_id = config.EFFICIENCY_TREND_CONFIG['app_id']
        app_secret = config.EFFICIENCY_TREND_CONFIG['app_secret']
        rows = get_todo_records_tenant(
            cfg['base_token'], cfg['table_id'], cfg.get('view_id'), app_id, app_secret)
        items = [{
            'id': 'feishu:' + r['rid'],
            'rid': r['rid'],
            'source': 'feishu',
            'name': r['name'],
            'executor': r['executor'],
            'detail': r['detail'],
            'deadline': (r['deadline'] or '')[:10],
            'done': r['done'],
            'priority': r['priority'],
            'progress': r['progress'] or '',
        } for r in rows]
        _save_json(FEISHU_TODO_CACHE, {
            'items': items,
            'synced_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'synced_at_ts': int(time.time()),
        })
        logger.info(f"[待办] tenant 路径同步 {len(items)} 条")
        return items, None
    except Exception as e:
        app.logger.warning(f"[待办] tenant 路径失败，回退 lark-cli: {e}")

    # —— 回退：本机 lark-cli（用户授权）——
    text, err = _run_lark_record_list()
    if err:
        if cache.get('items') is not None:
            return cache['items'], err
        return [], err
    rows = _parse_record_markdown(text)
    items = [{
        'id': 'feishu:' + r['rid'],
        'rid': r['rid'],
        'source': 'feishu',
        'name': r['name'],
        'executor': r['executor'],
        'detail': r['detail'],
        'deadline': r['deadline'][:10] if r['deadline'] else '',
        'done': r['done'],
        'priority': r['priority'],
        'progress': r['progress'] or '',
    } for r in rows]
    _save_json(FEISHU_TODO_CACHE, {
        'items': items,
        'synced_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'synced_at_ts': int(time.time()),
    })
    return items, None

def _load_feishu_todos():
    d = _read_json(FEISHU_TODO_CACHE)
    return d.get('items', []) if isinstance(d, dict) else []

def _write_feishu_done(rid, done):
    """回写完成状态到飞书云文档（checkbox 字段 完成状态）。"""
    cfg = FEISHU_TODO_BASE
    cmd = [
        _lark_cli(), 'base', '+record-upsert',
        '--base-token', cfg['base_token'],
        '--table-id', cfg['table_id'],
        '--record-id', rid,
        '--as', 'user',
        '--json', json.dumps({'完成状态': bool(done)}, ensure_ascii=False),
    ]
    try:
        r = subprocess.run(cmd, shell=False, capture_output=True, text=True, timeout=60)
        return r.returncode == 0, (r.stderr or r.stdout).strip()
    except Exception as e:
        return False, str(e)

def _merge_todos():
    """合并飞书云文档 + 本地手动待办。"""
    feishu, _ = _sync_feishu_todos(force=False)
    local = _load_todos()
    return feishu + local

@app.route('/todo')
def todo():
    items = _merge_todos()
    return render_template('todo.html', active='todo',
        items=items,
        total=len(items),
        done=sum(1 for i in items if i.get('done')),
        pending=sum(1 for i in items if not i.get('done')),
        feishu_url=FEISHU_TODO_BASE['url'],
        cache_time=datetime.now().strftime('%H:%M:%S'))

@app.route('/api/todo/list')
def api_todo_list():
    items = _merge_todos()
    # 排序：未完成在前，完成在后；同状态按来源(云文档优先)稳定排列
    items.sort(key=lambda x: (1 if x.get('done') else 0, 0 if x.get('source') == 'feishu' else 1))
    return jsonify({'items': items, 'total': len(items)})

@app.route('/api/todo/sync', methods=['POST'])
def api_todo_sync():
    items, err = _sync_feishu_todos(force=True)
    items = items + _load_todos()
    items.sort(key=lambda x: (1 if x.get('done') else 0, 0 if x.get('source') == 'feishu' else 1))
    return jsonify({'ok': err is None, 'items': items, 'total': len(items),
                   'error': err, 'synced_at': datetime.now().strftime('%H:%M:%S')})

@app.route('/api/todo/add', methods=['POST'])
def api_todo_add():
    data = request.get_json(force=True, silent=True) or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'ok': False, 'error': '事项内容不能为空'}), 400
    items = _load_todos()
    item = {
        'id': int(time.time() * 1000),
        'source': 'local',
        'content': content,
        'owner': (data.get('owner') or '').strip(),
        'due': (data.get('due') or '').strip(),
        'priority': data.get('priority') or '中',
        'done': False,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }
    items.append(item)
    _save_todos(items)
    return jsonify({'ok': True, 'item': item})

@app.route('/api/todo/toggle', methods=['POST'])
def api_todo_toggle():
    data = request.get_json(force=True, silent=True) or {}
    tid = data.get('id')
    if str(tid).startswith('feishu:'):
        rid = str(tid).split(':', 1)[1]
        # 先读当前状态
        cur = next((i for i in _load_feishu_todos() if i.get('rid') == rid), None)
        new_done = not (cur.get('done') if cur else False)
        ok, msg = _write_feishu_done(rid, new_done)
        if ok:
            _sync_feishu_todos(force=True)
        return jsonify({'ok': ok, 'error': None if ok else msg})
    items = _load_todos()
    for it in items:
        if str(it.get('id')) == str(tid):
            it['done'] = not it.get('done', False)
            break
    _save_todos(items)
    return jsonify({'ok': True})

@app.route('/api/todo/delete', methods=['POST'])
def api_todo_delete():
    data = request.get_json(force=True, silent=True) or {}
    tid = data.get('id')
    items = [it for it in _load_todos() if str(it.get('id')) != str(tid)]
    _save_todos(items)
    return jsonify({'ok': True})

def _collect_alerts(threshold=80.0):
    """汇总各通道（APP/哥斯拉/魔镜/拍照）当日效率 < threshold 的人员。
    复用与首页/各组页完全一致的适配器，保证数字与监控页对齐。"""
    alerts = []

    def add(name, channel, rate, standard, upph, products, etype=''):
        # 只统计今日有产出、且有效率值的人员
        if products is None or products <= 0:
            return
        if rate is None or rate <= 0:
            return
        if rate >= threshold:
            return
        gap = round(threshold - rate, 1)
        sev = 'crit' if rate < 60 else 'warn'
        alerts.append({
            'name': name, 'channel': channel, 'rate': round(rate, 1),
            'standard': standard, 'upph': round(upph, 1) if upph else 0,
            'products': products, 'gap': gap, 'severity': sev, 'type': etype,
        })

    # ── APP 通道 ──
    app_adapt = _adapt_app(_read_cache('app_cache.json'), _keep_for_groups(['APP']))
    for r in app_adapt.get('app_rows', []):
        add(r['name'], 'APP', r.get('rate', 0), r.get('standard', 0),
            r.get('upph', 0), r.get('products', 0), r.get('emp_type', ''))

    # ── 哥斯拉 / 魔镜 ──
    rg = _load_roster_groups()
    auto = _adapt_auto_grouped(_read_cache('automation_cache.json'), rg, _load_xray_data())
    for ch_label, lst in (('哥斯拉', auto.get('godzilla', [])), ('魔镜', auto.get('mirror', []))):
        for r in lst:
            add(r['name'], ch_label, r.get('rate', 0), r.get('standard', 0),
                r.get('upph', 0), r.get('products', 0), r.get('employee_type', ''))

    # ── 拍照 通道 ──
    photo = _adapt_photo(_read_cache('photo_cache.json'))
    for s in photo.get('staff_list', []):
        add(s.get('name', '-'), '拍照', s.get('rate', 0), s.get('standard', 0),
            s.get('upph', 0), s.get('products', 0), s.get('type', ''))

    alerts.sort(key=lambda x: x['rate'])
    ch_counts = {}
    for a in alerts:
        ch_counts[a['channel']] = ch_counts.get(a['channel'], 0) + 1
    return alerts, ch_counts

@app.route('/alert')
def alert():
    alerts, ch_counts = _collect_alerts(80.0)
    resp = make_response(render_template('alert.html', active='alert',
        alerts=alerts, ch_counts=ch_counts, total=len(alerts),
        threshold=80,
        cache_time=datetime.now().strftime('%H:%M:%S')))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@app.route('/api/trend/efficiency')
def api_trend():
    # 优先读缓存
    cache = load_trend_cache(TREND_CACHE_PATH)
    if cache:
        # 缓存30分钟内有效
        try:
            last = datetime.strptime(cache.get('last_update', ''), '%Y-%m-%d %H:%M:%S')
            if (datetime.now() - last).total_seconds() < 1800:
                return jsonify({
                    'dates': cache['dates'],
                    'baseline': cache['baseline'],
                    'current': cache['current']
                })
        except: pass

    # 实时从飞书读取
    try:
        data = get_efficiency_trend(EFFICIENCY_TREND_CONFIG)
        if data and data.get('dates'):
            # 转换为百分比
            data['baseline'] = [round(v * 100, 2) if v is not None else None for v in data['baseline']]
            data['current'] = [round(v * 100, 2) if v is not None else None for v in data['current']]
            save_trend_cache(data, TREND_CACHE_PATH)
            return jsonify(data)
    except Exception as e:
        app.logger.error(f'[趋势] 飞书读取失败: {e}')

    # 兜底：返回缓存或占位数据
    if cache:
        # 兜底也做百分比转换（兼容旧缓存的小数格式）
        baseline = [round(v * 100, 2) if v is not None and v < 1 else v for v in cache.get('baseline', [])]
        current = [round(v * 100, 2) if v is not None and v < 1 else v for v in cache.get('current', [])]
        return jsonify({
            'dates': cache.get('dates', []),
            'baseline': baseline,
            'current': current
        })

    # 无任何数据时返回最近7天占位
    dates, baseline, current = [], [], []
    today = datetime.now()
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        dates.append(f"{d.month}月{d.day}日")
        baseline.append(85.0); current.append(88.0)
    return jsonify({'dates': dates, 'baseline': baseline, 'current': current})

@app.route('/api/refresh', methods=['GET', 'POST'])
def api_refresh():
    # 触发飞书数据刷新（趋势 + 效率指标 + 质量指标）
    result = {'status': 'ok', 'time': datetime.now().strftime('%H:%M:%S'), 'details': {}}
    errors = []

    # 1. 趋势数据
    try:
        data = get_efficiency_trend(EFFICIENCY_TREND_CONFIG)
        if data and data.get('dates'):
            data['baseline'] = [round(v * 100, 2) if v is not None else None for v in data['baseline']]
            data['current'] = [round(v * 100, 2) if v is not None else None for v in data['current']]
            save_trend_cache(data, TREND_CACHE_PATH)
            result['details']['trend'] = {'ok': True, 'points': len(data['dates'])}
    except Exception as e:
        app.logger.error(f'[刷新] 趋势数据失败: {e}')
        errors.append('趋势')
        result['details']['trend'] = {'ok': False, 'error': str(e)}

    # 2. 效率指标
    try:
        data = get_latest_metrics(EFFICIENCY_TREND_CONFIG)
        if data and data.get('metrics'):
            save_metric_cache(data, METRIC_CACHE_PATH)
            result['details']['efficiency'] = {'ok': True, 'metrics': len(data['metrics'])}
    except Exception as e:
        app.logger.error(f'[刷新] 效率指标失败: {e}')
        errors.append('效率指标')
        result['details']['efficiency'] = {'ok': False, 'error': str(e)}

    # 3. 质量指标（邮件）
    try:
        data = get_quality_metrics_from_mail()
        if data and data.get('items'):
            save_quality_cache(data, QUALITY_CACHE_PATH)
            result['details']['quality'] = {'ok': True, 'metrics': len(data['items'])}
    except Exception as e:
        app.logger.error(f'[刷新] 质量指标失败: {e}')
        errors.append('质量指标')
        result['details']['quality'] = {'ok': False, 'error': str(e)}

    if errors:
        result['status'] = 'partial'
        result['message'] = f'部分刷新失败: {"、".join(errors)}'
    else:
        result['message'] = '刷新成功'
    return jsonify(result)

@app.route('/api/metrics/efficiency')
def api_metrics_efficiency():
    # 优先读缓存
    cache = load_metric_cache(METRIC_CACHE_PATH)
    if cache:
        try:
            last = datetime.strptime(cache.get('last_update', ''), '%Y-%m-%d %H:%M:%S')
            if (datetime.now() - last).total_seconds() < 1800:
                return _format_metrics_response(cache)
        except: pass

    # 实时从飞书读取
    try:
        data = get_latest_metrics(EFFICIENCY_TREND_CONFIG)
        if data and data.get('metrics'):
            save_metric_cache(data, METRIC_CACHE_PATH)
            return _format_metrics_response(data)
    except Exception as e:
        app.logger.error(f'[效率指标] 飞书读取失败: {e}')

    # 兜底：返回缓存或空数据
    if cache:
        return _format_metrics_response(cache)
    return jsonify({'latest_date': '', 'metrics': []})


def _format_metrics_response(data):
    """将效率指标转为前端可用的百分比格式"""
    metrics = []
    order = EFFICIENCY_TREND_CONFIG.get('metric_order', list(data.get('metrics', {}).keys()))
    for key in order:
        m = data.get('metrics', {}).get(key, {})
        baseline = m.get('baseline')
        current = m.get('current')
        change = None
        if baseline is not None and current is not None and baseline > 0:
            change = round((current - baseline) / baseline, 4)
        metrics.append({
            'key': key,
            'name': m.get('name', key),
            'baseline': round(baseline * 100, 2) if baseline is not None else None,
            'current': round(current * 100, 2) if current is not None else None,
            'change': round(change * 100, 2) if change is not None else None,
        })
    return jsonify({
        'latest_date': data.get('latest_date', ''),
        'last_update': data.get('last_update', ''),
        'metrics': metrics
    })


@app.route('/api/metrics/quality')
def api_metrics_quality():
    # 优先读缓存
    cache = load_quality_cache(QUALITY_CACHE_PATH)
    if cache:
        try:
            last = datetime.strptime(cache.get('last_update', ''), '%Y-%m-%d %H:%M:%S')
            if (datetime.now() - last).total_seconds() < 1800:
                return _format_quality_response(cache)
        except: pass

    # 实时从飞书邮件读取
    try:
        data = get_quality_metrics_from_mail()
        if data and data.get('items'):
            save_quality_cache(data, QUALITY_CACHE_PATH)
            return _format_quality_response(data)
    except Exception as e:
        app.logger.error(f'[质量指标] 飞书邮件读取失败: {e}')

    # 兜底
    if cache:
        return _format_quality_response(cache)
    return jsonify({'latest_date': '', 'items': [], 'sources': {}})


def _format_quality_response(data):
    """将质量指标转为前端可用格式"""
    items = []
    order = ['app_execution', 'app_coverage', 'app_error', 'godzilla_reshoot_rate', 'godzilla_reshoot_count', 'reshoot_7d_rate']
    for key in order:
        item = data.get('items', {}).get(key)
        if not item:
            continue
        value = item.get('latest', item.get('value'))
        avg_7d = item.get('avg_7d')
        # 重拍量用数值，其余用百分比
        is_count = key in ('godzilla_reshoot_count',)
        if is_count:
            v = round(value, 0) if value is not None else None
            a = round(avg_7d, 0) if avg_7d is not None else None
        else:
            v = round(value * 100, 2) if value is not None else None
            a = round(avg_7d * 100, 2) if avg_7d is not None else None
        items.append({
            'key': key,
            'name': QUALITY_MAIL_CONFIG.get('metrics', {}).get(key, {}).get('name', key),
            'value': v,
            'avg_7d': a,
            'unit': 'count' if is_count else 'percent',
        })
    return jsonify({
        'latest_date': data.get('latest_date', ''),
        'last_update': data.get('last_update', ''),
        'items': items,
        'sources': data.get('sources', {})
    })

@app.route('/api/status')
def api_status():
    info = {}
    for f in ['photo_cache.json','automation_cache.json','app_cache.json','xray_cache.json']:
        p = os.path.join(CACHE_SOURCE, f)
        if os.path.exists(p):
            ts = os.path.getmtime(p)
            info[f] = {'age_s': int(time.time() - ts), 'time': datetime.fromtimestamp(ts).strftime('%H:%M:%S')}
        else:
            info[f] = {'age_s': -1, 'time': 'N/A'}
    return jsonify({'status': 'running', 'caches': info})

# ── 自动刷新调度（脱离本机也能自动跑） ──
def _auto_refresh_loop():
    """后台守护线程：启动时先刷新一次，之后每5分钟全量刷新一次。"""
    import time as _time
    print('[自动刷新] 后台调度线程已启动，每5分钟全量刷新一次')
    # 启动后立即先刷一次，保证访问时已有数据
    try:
        _perform_refresh_all()
        print('[自动刷新] 启动首次全量刷新完成')
    except Exception as e:
        print(f'[自动刷新] 首次刷新异常: {e}')
    while True:
        _time.sleep(300)  # 5分钟
        try:
            _perform_refresh_all()
            print('[自动刷新] 周期全量刷新完成')
        except Exception as e:
            print(f'[自动刷新] 周期刷新异常: {e}')

def start_scheduler():
    """启动自动刷新后台线程（设置环境变量 DASHBOARD_NO_AUTO_REFRESH=1 可关闭）。"""
    if os.environ.get('DASHBOARD_NO_AUTO_REFRESH'):
        print('[自动刷新] 已通过环境变量禁用')
        return
    t = threading.Thread(target=_auto_refresh_loop, daemon=True)
    t.start()

if __name__ == '__main__':
    print('=' * 55)
    print('  效率看板 — 自主设计版')
    print('  访问地址: http://127.0.0.1:8080')
    print('  原始扣子: http://127.0.0.1:5000 (未改动)')
    print('=' * 55)
    # 端口优先读环境变量 PORT（Render/Fly/Railway 等 PaaS 注入），本地默认 8080
    _port = int(os.environ.get('PORT', 8080))
    start_scheduler()
    app.run(host='0.0.0.0', port=_port, debug=False, use_reloader=False, threaded=True)
