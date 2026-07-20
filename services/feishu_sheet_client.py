# -*- coding: utf-8 -*-
"""
飞书云文档（Sheet）客户端
从「产出」和「出勤」两个sheet读取数据，计算效率达成率
"""
import os
import json
import time
import logging
import requests
from typing import Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class FeishuSheetClient:
    """飞书云文档Sheet客户端"""

    def __init__(self, app_id: str, app_secret: str, base_url: str = "https://open.feishu.cn"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip('/')
        self._tenant_access_token = None
        self._token_expire_time = 0

    def _get_tenant_access_token(self) -> str:
        if self._tenant_access_token and time.time() < self._token_expire_time - 60:
            return self._tenant_access_token

        url = f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal"
        try:
            resp = requests.post(url, json={
                "app_id": self.app_id,
                "app_secret": self.app_secret
            }, timeout=10)
            data = resp.json()
            if data.get('code') == 0:
                self._tenant_access_token = data['tenant_access_token']
                self._token_expire_time = time.time() + data.get('expire', 7200)
                return self._tenant_access_token
            else:
                raise Exception(f"获取飞书token失败: {data.get('msg', '未知错误')}")
        except Exception as e:
            logger.error(f"[飞书Sheet] 获取token异常: {e}")
            raise

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_tenant_access_token()}",
            "Content-Type": "application/json; charset=utf-8"
        }

    def read_range(self, spreadsheet_token: str, sheet_id: str,
                   start_row: int, end_row: int,
                   start_col: str = "A", end_col: str = "AZ") -> List[List]:
        """读取指定范围（带值渲染）"""
        range_str = f"{sheet_id}!{start_col}{start_row}:{end_col}{end_row}"
        url = f"{self.base_url}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{range_str}"
        try:
            resp = requests.get(url, headers=self._headers(),
                                params={"valueRenderOption": "ToString"},
                                timeout=15)
            data = resp.json()
            if data.get('code') == 0:
                return data.get('data', {}).get('valueRange', {}).get('values', [])
            else:
                logger.error(f"[飞书Sheet] 读取失败: {data}")
                return []
        except Exception as e:
            logger.error(f"[飞书Sheet] 读取异常: {e}")
            return []

    def read_row(self, spreadsheet_token: str, sheet_id: str,
                 row: int, max_col: str = "AZ") -> List:
        values = self.read_range(spreadsheet_token, sheet_id, row, row, "A", max_col)
        return values[0] if values else []


def _parse_date(date_val):
    """解析日期，支持Excel序列号、YYYY-MM-DD、M月D日等格式"""
    if not date_val:
        return None
    date_str = str(date_val).strip()
    if not date_str:
        return None

    # YYYY-MM-DD
    if '-' in date_str and len(date_str) >= 8:
        try:
            return datetime.strptime(date_str[:10], '%Y-%m-%d')
        except:
            pass

    # M月D日
    if '月' in date_str and '日' in date_str:
        try:
            d = datetime.strptime(
                date_str.replace('年', '').replace('月', '-').replace('日', ''), '%m-%d'
            )
            return d.replace(year=datetime.now().year)
        except:
            pass

    # Excel序列号
    try:
        num = float(date_val)
        if 40000 < num < 60000:
            base = datetime(1899, 12, 30)
            return base + timedelta(days=int(num))
        if num > 1e12:
            return datetime.fromtimestamp(num / 1000)
        if num > 1e9:
            return datetime.fromtimestamp(num)
    except:
        pass

    return None


def _parse_num(v):
    """解析数值"""
    if v is None or v == '' or v == '-':
        return None
    try:
        return float(str(v).strip().replace('%', '').replace(',', ''))
    except:
        return None


def get_efficiency_trend(config: dict) -> Dict:
    """
    获取近7天大仓辅助效率达成趋势（折线图用）
    从"产出"和"出勤"sheet读取，效率 = 产出/出勤
    """
    client = FeishuSheetClient(
        app_id=config['app_id'],
        app_secret=config['app_secret']
    )

    token = config.get('spreadsheet_token', '')
    output_sheet = config.get('output_sheet_id', 'Ha88TR')   # 产出sheet
    attend_sheet = config.get('attend_sheet_id', 'L5vzjP')  # 出勤sheet
    date_row = config.get('date_row', 1)
    baseline_row = config.get('baseline_row', 152)  # 基准行
    current_row = config.get('current_row', 153)    # 当前行

    # 读日期行（从E列开始，跳过前几列表头）
    date_values = client.read_row(token, output_sheet, date_row, "DD")
    # 读产出的基准和当前行
    output_base = client.read_row(token, output_sheet, baseline_row, "DD")
    output_curr = client.read_row(token, output_sheet, current_row, "DD")
    # 读出勤的基准和当前行
    attend_base = client.read_row(token, attend_sheet, baseline_row, "DD")
    attend_curr = client.read_row(token, attend_sheet, current_row, "DD")

    # 解析并计算
    daily_data = []  # [(date_obj, display_date, baseline_eff, current_eff)]
    max_len = max(len(date_values), len(output_base), len(output_curr), len(attend_base), len(attend_curr))

    for i in range(max_len):
        date_val = date_values[i] if i < len(date_values) else ''
        d = _parse_date(date_val)
        if not d:
            continue

        ob = _parse_num(output_base[i]) if i < len(output_base) else None
        oc = _parse_num(output_curr[i]) if i < len(output_curr) else None
        ab = _parse_num(attend_base[i]) if i < len(attend_base) else None
        ac = _parse_num(attend_curr[i]) if i < len(attend_curr) else None

        # 出勤为0或空则跳过
        if not ab or not ac or ab <= 0 or ac <= 0:
            continue

        base_eff = ob / ab if ob is not None else None
        curr_eff = oc / ac if oc is not None else None

        if base_eff is not None and curr_eff is not None:
            display = f"{d.month}月{d.day}日"
            daily_data.append((d, display, base_eff, curr_eff))

    # 按日期排序（从早到晚），取最近7天
    daily_data.sort(key=lambda x: x[0])
    if len(daily_data) > 7:
        daily_data = daily_data[-7:]

    dates = [d[1] for d in daily_data]
    baseline = [round(d[2], 4) for d in daily_data]
    current = [round(d[3], 4) for d in daily_data]

    logger.info(f"[效率趋势] 有效数据点: {len(dates)}个")
    return {'dates': dates, 'baseline': baseline, 'current': current}


def get_latest_metrics(config: dict) -> Dict:
    """
    获取最新一日的10个岗位效率指标
    从"产出"和"出勤"sheet读取，效率 = 产出/出勤
    """
    client = FeishuSheetClient(
        app_id=config['app_id'],
        app_secret=config['app_secret']
    )

    token = config.get('spreadsheet_token', '')
    output_sheet = config.get('output_sheet_id', 'Ha88TR')
    attend_sheet = config.get('attend_sheet_id', 'L5vzjP')
    date_row = config.get('date_row', 1)
    metric_rows = config.get('metric_rows', {})
    metric_order = config.get('metric_order', list(metric_rows.keys()))

    # 读日期行
    date_values = client.read_row(token, output_sheet, date_row, "DD")

    # 找最新日期列（从左到右找，Excel序列号越大越新，取最大的）
    latest_col_idx = -1
    latest_date_obj = None
    latest_date_str = ''
    for i, val in enumerate(date_values):
        d = _parse_date(val)
        if d and (latest_date_obj is None or d > latest_date_obj):
            latest_date_obj = d
            latest_col_idx = i
            latest_date_str = f"{d.month}月{d.day}日"

    if latest_col_idx < 0:
        return {'latest_date': '', 'metrics': {}, 'error': '未找到日期列'}

    logger.info(f"[效率指标] 最新日期: {latest_date_str} (列索引={latest_col_idx})")

    # 计算列字母
    col_idx = latest_col_idx
    if col_idx < 26:
        col_letter = chr(ord('A') + col_idx)
    else:
        col_letter = chr(ord('A') + col_idx // 26 - 1) + chr(ord('A') + col_idx % 26)

    # 收集需要读的行
    all_rows = set()
    for key, m in metric_rows.items():
        if m.get('baseline_row'):
            all_rows.add(m['baseline_row'])
        if m.get('current_row'):
            all_rows.add(m['current_row'])

    if not all_rows:
        return {'latest_date': latest_date_str, 'metrics': {}, 'error': '未配置行号'}

    min_row = min(all_rows)
    max_row = max(all_rows)
    # 读产出和出勤的对应列
    output_data = client.read_range(token, output_sheet, min_row, max_row, col_letter, col_letter)
    attend_data = client.read_range(token, attend_sheet, min_row, max_row, col_letter, col_letter)

    # 构建行号->数值的映射
    output_map = {}
    attend_map = {}
    for i in range(max_row - min_row + 1):
        row_num = min_row + i
        if i < len(output_data) and output_data[i]:
            output_map[row_num] = _parse_num(output_data[i][0])
        if i < len(attend_data) and attend_data[i]:
            attend_map[row_num] = _parse_num(attend_data[i][0])

    # 计算每个指标
    metrics = {}
    for key in metric_order:
        m = metric_rows.get(key, {})
        name = m.get('name', key)
        base_row = m.get('baseline_row')
        curr_row = m.get('current_row')

        base_eff = None
        curr_eff = None

        ob = output_map.get(base_row)
        ab = attend_map.get(base_row)
        if ob is not None and ab and ab > 0:
            base_eff = ob / ab

        oc = output_map.get(curr_row)
        ac = attend_map.get(curr_row)
        if oc is not None and ac and ac > 0:
            curr_eff = oc / ac

        metrics[key] = {
            'name': name,
            'baseline': round(base_eff, 4) if base_eff is not None else None,
            'current': round(curr_eff, 4) if curr_eff is not None else None,
        }

    return {
        'latest_date': latest_date_str,
        'metrics': metrics,
    }


def save_trend_cache(data: dict, cache_path: str):
    """缓存趋势数据"""
    try:
        cache_data = {**data, 'last_update': time.strftime('%Y-%m-%d %H:%M:%S')}
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[效率趋势] 缓存写入失败: {e}")


def load_trend_cache(cache_path: str) -> Optional[dict]:
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[效率趋势] 缓存读取失败: {e}")
        return None


def load_metric_cache(cache_path: str) -> Optional[dict]:
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[效率指标] 缓存读取失败: {e}")
        return None


def save_metric_cache(data: dict, cache_path: str):
    """缓存指标数据"""
    try:
        cache_data = {**data, 'last_update': time.strftime('%Y-%m-%d %H:%M:%S')}
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[效率指标] 缓存写入失败: {e}")
