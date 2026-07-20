# -*- coding: utf-8 -*-
"""
飞书邮箱客户端
从飞书邮箱中读取报表邮件，提取APP执行率、覆盖率、失误率、重拍率等指标
"""
import os
import json
import time
import re
import logging
import requests
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from html.parser import HTMLParser

logger = logging.getLogger(__name__)


class MLStripper(HTMLParser):
    """HTML转纯文本"""
    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text = []

    def handle_data(self, d):
        self.text.append(d)

    def get_data(self):
        return ''.join(self.text)


def strip_html(html):
    s = MLStripper()
    try:
        s.feed(html or '')
        return s.get_data()
    except:
        return html or ''


class FeishuMailClient:
    """飞书邮箱客户端"""

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
            logger.error(f"[飞书邮件] 获取token异常: {e}")
            raise

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_tenant_access_token()}",
            "Content-Type": "application/json; charset=utf-8"
        }

    def search_mails(self, mailbox_id: str, keyword: str, limit: int = 20) -> List[Dict]:
        """
        搜索邮件：列出最近N封邮件，按主题关键词本地筛选
        mailbox_id: 用户邮箱地址
        keyword: 主题关键词（包含匹配）
        """
        url = f"{self.base_url}/open-apis/mail/v1/user_mailboxes/{mailbox_id}/messages"
        matched = []
        page_token = None
        total_fetched = 0
        max_fetch = 50  # 最多拉取50封，避免循环太多

        try:
            while len(matched) < limit and total_fetched < max_fetch:
                params = {
                    "page_size": min(20, limit * 2),
                    "folder_id": "INBOX",
                }
                if page_token:
                    params["page_token"] = page_token

                resp = requests.get(url, headers=self._headers(), params=params, timeout=15)
                data = resp.json()

                if data.get('code') != 0:
                    logger.warning(f"[飞书邮件] 拉取列表失败: code={data.get('code')}, msg={data.get('msg')}")
                    break

                items = data.get('data', {}).get('items', []) or []
                if not items:
                    break

                total_fetched += len(items)

                # 逐个取邮件详情，按主题筛选
                for msg_id in items:
                    if len(matched) >= limit:
                        break
                    detail = self.get_mail_content(mailbox_id, str(msg_id))
                    if detail:
                        subject = detail.get('subject', '') or ''
                        if keyword in subject:
                            matched.append(detail)

                has_more = data.get('data', {}).get('has_more', False)
                page_token = data.get('data', {}).get('page_token')
                if not has_more or not page_token:
                    break

            logger.info(f"[飞书邮件] 关键词'{keyword}': 拉取{total_fetched}封，命中{len(matched)}封")
            return matched

        except Exception as e:
            logger.error(f"[飞书邮件] 搜索异常: {e}")
            return matched

    def get_mail_content(self, mailbox_id: str, message_id: str) -> Optional[Dict]:
        """获取邮件详情（含正文），返回 message 对象"""
        url = f"{self.base_url}/open-apis/mail/v1/user_mailboxes/{mailbox_id}/messages/{message_id}"
        try:
            resp = requests.get(url, headers=self._headers(), params={"format": "full"}, timeout=15)
            data = resp.json()
            if data.get('code') == 0:
                return data.get('data', {}).get('message', {}) or data.get('data', {})
            else:
                logger.warning(f"[飞书邮件] 获取详情失败: code={data.get('code')}, msg={data.get('msg')}")
                return None
        except Exception as e:
            logger.error(f"[飞书邮件] 获取详情异常: {e}")
            return None


def _parse_percent(text: str) -> Optional[float]:
    """从文本中解析百分比数值（返回小数，如85.3% -> 0.853）"""
    if not text:
        return None
    # 匹配数字+%
    m = re.search(r'(\d+\.?\d*)\s*%', text)
    if m:
        try:
            return float(m.group(1)) / 100
        except:
            return None
    # 纯数字（假设是百分比）
    m = re.search(r'(\d+\.?\d*)', text)
    if m:
        try:
            v = float(m.group(1))
            if v <= 1:  # 已经是小数
                return v
            return v / 100
        except:
            return None
    return None


def _parse_number(text: str) -> Optional[float]:
    """从文本中解析数值"""
    if not text:
        return None
    text = str(text).replace(',', '').replace('，', '').strip()
    m = re.search(r'(\d+\.?\d*)', text)
    if m:
        try:
            return float(m.group(1))
        except:
            return None
    return None


def _find_row_by_keyword(table_rows: List[List[str]], keyword: str, city: str = None) -> Optional[List[str]]:
    """
    在表格行中查找包含关键词的行
    如果指定city，优先找同时包含城市的行
    """
    if not table_rows:
        return None

    # 先找同时含有关键词和城市的行
    if city:
        for row in table_rows:
            row_text = ' '.join(str(c) for c in row)
            if keyword in row_text and city in row_text:
                return row

    # 再找只含关键词的行
    for row in table_rows:
        row_text = ' '.join(str(c) for c in row)
        if keyword in row_text:
            return row

    return None


def _extract_table_rows(html_content: str) -> List[List[str]]:
    """从HTML中提取所有表格的行数据"""
    if not html_content:
        return []

    rows = []
    # 简单的表格解析：找<tr>...</tr>
    tr_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
    td_pattern = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.DOTALL | re.IGNORECASE)

    for tr_match in tr_pattern.finditer(html_content):
        tr_html = tr_match.group(1)
        cells = []
        for td_match in td_pattern.finditer(tr_html):
            cell_text = strip_html(td_match.group(1)).strip()
            cells.append(cell_text)
        if cells and any(c for c in cells):
            rows.append(cells)

    return rows


def parse_app_mail(html_content: str, city: str = "成都") -> Dict:
    """
    解析"APP执行、失误"邮件
    提取：APP执行率、APP覆盖率、APP失误率
    """
    result = {
        'app_execution': None,
        'app_coverage': None,
        'app_error': None,
    }

    table_rows = _extract_table_rows(html_content)

    if not table_rows:
        # 没有表格，尝试从纯文本提取
        text = strip_html(html_content)

        # 尝试匹配各种格式
        patterns = [
            (r'执行率[：:]*\s*(\d+\.?\d*)\s*%', 'app_execution'),
            (r'覆盖率[：:]*\s*(\d+\.?\d*)\s*%', 'app_coverage'),
            (r'失误率[：:]*\s*(\d+\.?\d*)\s*%', 'app_error'),
        ]
        for pattern, key in patterns:
            m = re.search(pattern, text)
            if m:
                result[key] = float(m.group(1)) / 100

        return result

    # 有表格，在表格中查找
    # 尝试找"成都"行 + "执行率/覆盖率/失误率"列，或者反过来
    city_row = None
    for row in table_rows:
        row_text = ' '.join(row)
        if city in row_text:
            city_row = row
            break

    if city_row:
        # 先找表头，确定各列含义
        header_row = None
        for row in table_rows:
            row_text = ' '.join(row)
            if ('执行率' in row_text or '覆盖率' in row_text or '失误率' in row_text) and city not in row_text:
                header_row = row
                break

        if header_row:
            for i, header in enumerate(header_row):
                if i >= len(city_row):
                    continue
                val = _parse_percent(city_row[i])
                if val is None:
                    val = _parse_number(city_row[i])
                    if val and val > 1:
                        val = val / 100
                if val is None:
                    continue
                if '执行率' in header:
                    result['app_execution'] = val
                elif '覆盖率' in header:
                    result['app_coverage'] = val
                elif '失误率' in header:
                    result['app_error'] = val

    # 如果上面没找到，试试直接按关键词找行
    if result['app_execution'] is None:
        row = _find_row_by_keyword(table_rows, '执行率', city)
        if row:
            for cell in row:
                v = _parse_percent(cell)
                if v is not None:
                    result['app_execution'] = v
                    break

    if result['app_coverage'] is None:
        row = _find_row_by_keyword(table_rows, '覆盖率', city)
        if row:
            for cell in row:
                v = _parse_percent(cell)
                if v is not None:
                    result['app_coverage'] = v
                    break

    if result['app_error'] is None:
        row = _find_row_by_keyword(table_rows, '失误率', city)
        if row:
            for cell in row:
                v = _parse_percent(cell)
                if v is not None:
                    result['app_error'] = v
                    break

    return result


def parse_reshoot_mail(html_content: str, city: str = "成都") -> Dict:
    """
    解析"运中门店重拍率数据"邮件
    提取：哥斯拉重拍率、哥斯拉重拍量、近7天重拍率
    """
    result = {
        'godzilla_reshoot_rate': None,
        'godzilla_reshoot_count': None,
        'reshoot_7d_rate': None,
    }

    table_rows = _extract_table_rows(html_content)
    text = strip_html(html_content)

    if not table_rows:
        # 纯文本提取
        # 哥斯拉重拍率
        m = re.search(r'哥斯拉.*?重拍率[：:]*\s*(\d+\.?\d*)\s*%', text)
        if m:
            result['godzilla_reshoot_rate'] = float(m.group(1)) / 100
        else:
            m = re.search(r'重拍率[：:]*\s*(\d+\.?\d*)\s*%', text)
            if m:
                result['godzilla_reshoot_rate'] = float(m.group(1)) / 100

        # 重拍量/重拍数
        m = re.search(r'重拍(?:量|数|台数)[：:]*\s*(\d+[\d,]*)', text)
        if m:
            result['godzilla_reshoot_count'] = float(m.group(1).replace(',', ''))

        # 近7天重拍率
        m = re.search(r'(?:近7天|7天|近七日|七日).*?重拍率[：:]*\s*(\d+\.?\d*)\s*%', text)
        if m:
            result['reshoot_7d_rate'] = float(m.group(1)) / 100

        return result

    # 有表格，尝试从表格中提取
    # 找成都行
    city_row = None
    for row in table_rows:
        row_text = ' '.join(row)
        if city in row_text:
            city_row = row
            break

    if city_row:
        # 找表头
        header_row = None
        for row in table_rows:
            row_text = ' '.join(row)
            if ('重拍率' in row_text or '重拍量' in row_text or '重拍数' in row_text) and city not in row_text:
                header_row = row
                break

        if header_row:
            for i, header in enumerate(header_row):
                if i >= len(city_row):
                    continue
                cell_val = city_row[i]
                if '重拍率' in header and '7' not in header:
                    v = _parse_percent(cell_val)
                    if v is not None:
                        result['godzilla_reshoot_rate'] = v
                elif '重拍量' in header or '重拍数' in header or '重拍台数' in header:
                    v = _parse_number(cell_val)
                    if v is not None:
                        result['godzilla_reshoot_count'] = v
                elif ('7天' in header or '近7' in header or '七日' in header) and '重拍率' in header:
                    v = _parse_percent(cell_val)
                    if v is not None:
                        result['reshoot_7d_rate'] = v

    # 兜底：从全量文本中找
    if result['godzilla_reshoot_rate'] is None:
        m = re.search(r'哥斯拉.*?重拍率[：:]*\s*(\d+\.?\d*)\s*%', text)
        if m:
            result['godzilla_reshoot_rate'] = float(m.group(1)) / 100

    if result['godzilla_reshoot_count'] is None:
        m = re.search(r'(?:哥斯拉.*?)?重拍(?:量|数|台数)[：:]*\s*(\d+[\d,]*)', text)
        if m:
            result['godzilla_reshoot_count'] = float(m.group(1).replace(',', ''))

    if result['reshoot_7d_rate'] is None:
        m = re.search(r'(?:近7天|7天|近七日).*?重拍率[：:]*\s*(\d+\.?\d*)\s*%', text)
        if m:
            result['reshoot_7d_rate'] = float(m.group(1)) / 100

    return result


def _get_mail_body(mail: dict) -> str:
    """从邮件详情中提取正文HTML"""
    if not mail:
        return ''
    body = mail.get('body', {})
    if isinstance(body, dict):
        return body.get('content', '') or ''
    return str(body) if body else ''


def _get_mail_subject(mail: dict) -> str:
    return mail.get('subject', '') or ''


def _get_mail_date(mail: dict) -> str:
    """从邮件中提取日期字符串 YYYY-MM-DD"""
    sent_time = mail.get('sent_time')
    if sent_time:
        try:
            ts = int(sent_time)
            if ts > 1e12:
                ts = ts / 1000
            return datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
        except:
            pass
    return ''


def get_quality_metrics(config: dict) -> Dict:
    """
    获取运营质量指标（从飞书邮件中读取）
    返回：{
        'latest_date': '2026-07-11',
        'items': { 'app_execution': {'value': 0.95, 'avg_7d': 0.93}, ... },
        'sources': { 'app_mail': '邮件主题', 'reshoot_mail': '邮件主题' }
    }
    """
    client = FeishuMailClient(
        app_id=config.get('app_id', ''),
        app_secret=config.get('app_secret', '')
    )

    mailbox = config.get('mailbox', 'me')
    city = config.get('city_filter', '成都')
    app_kw = config.get('app_mail_keyword', 'APP执行')
    reshoot_kw = config.get('reshoot_mail_keyword', '重拍率')

    result = {
        'latest_date': '',
        'items': {},
        'sources': {},
    }

    # 1. 搜索APP执行失误邮件（搜最近10封）
    try:
        app_mails = client.search_mails(mailbox, app_kw, limit=10)
        if app_mails:
            # 最新一封
            latest = app_mails[0]
            body = _get_mail_body(latest)
            parsed = parse_app_mail(body, city)
            for k, v in parsed.items():
                if v is not None:
                    result['items'][k] = {'value': v, 'avg_7d': None}
            result['sources']['app_mail'] = _get_mail_subject(latest)
            result['latest_date'] = _get_mail_date(latest)

            # 近7天均值
            if len(app_mails) >= 1:
                app_sum = {'app_execution': [], 'app_coverage': [], 'app_error': []}
                for m in app_mails[:7]:
                    body = _get_mail_body(m)
                    parsed = parse_app_mail(body, city)
                    for k, v in parsed.items():
                        if v is not None and k in app_sum:
                            app_sum[k].append(v)
                for k, vals in app_sum.items():
                    if vals and k in result['items']:
                        result['items'][k]['avg_7d'] = round(sum(vals) / len(vals), 4)
    except Exception as e:
        logger.error(f"[运营质量] APP邮件读取失败: {e}", exc_info=True)

    # 2. 搜索重拍率邮件（搜最近10封）
    try:
        reshoot_mails = client.search_mails(mailbox, reshoot_kw, limit=10)
        if reshoot_mails:
            latest = reshoot_mails[0]
            body = _get_mail_body(latest)
            parsed = parse_reshoot_mail(body, city)
            for k, v in parsed.items():
                if v is not None:
                    result['items'][k] = {'value': v, 'avg_7d': None}
            result['sources']['reshoot_mail'] = _get_mail_subject(latest)
            if not result['latest_date']:
                result['latest_date'] = _get_mail_date(latest)

            # 近7天均值
            if len(reshoot_mails) >= 1:
                reshoot_sum = {'godzilla_reshoot_rate': [], 'godzilla_reshoot_count': [], 'reshoot_7d_rate': []}
                for m in reshoot_mails[:7]:
                    body = _get_mail_body(m)
                    parsed = parse_reshoot_mail(body, city)
                    for k, v in parsed.items():
                        if v is not None and k in reshoot_sum:
                            reshoot_sum[k].append(v)
                for k, vals in reshoot_sum.items():
                    if vals and k in result['items']:
                        result['items'][k]['avg_7d'] = round(sum(vals) / len(vals), 4)
    except Exception as e:
        logger.error(f"[运营质量] 重拍率邮件读取失败: {e}", exc_info=True)

    result['last_update'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    logger.info(f"[运营质量] 提取到 {len(result['items'])} 个指标")
    return result


def save_quality_cache(data: dict, cache_path: str):
    """缓存运营质量数据"""
    try:
        cache_data = {**data, 'last_update': time.strftime('%Y-%m-%d %H:%M:%S')}
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[运营质量] 缓存写入失败: {e}")


def load_quality_cache(cache_path: str) -> Optional[dict]:
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[运营质量] 缓存读取失败: {e}")
        return None
