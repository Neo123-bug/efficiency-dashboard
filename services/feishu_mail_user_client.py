# -*- coding: utf-8 -*-
"""
飞书邮件客户端 - 用户身份版
使用设备流授权，自动管理refresh_token
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

# ============================================================
# HTML 转纯文本
# ============================================================
class MLStripper(HTMLParser):
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


# ============================================================
# 飞书邮箱客户端（用户身份）
# ============================================================
class FeishuMailUserClient:
    """
    使用用户身份(user_access_token)访问飞书邮箱
    token_path: token保存文件路径（JSON格式）
    """
    def __init__(self, app_id: str, app_secret: str, token_path: str,
                 base_url: str = "https://open.feishu.cn"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip('/')
        self.token_path = token_path
        self._user_access_token = None
        self._token_expire_time = 0
        self._refresh_token = None
        self._load_token()

    def _load_token(self):
        """从本地文件加载token"""
        try:
            if os.path.exists(self.token_path):
                with open(self.token_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._user_access_token = data.get('access_token', '')
                self._refresh_token = data.get('refresh_token', '')
                self._token_expire_time = data.get('expires_at', 0)
        except Exception as e:
            logger.warning(f"[飞书邮件] 加载token失败: {e}")

    def _save_token(self, access_token: str, refresh_token: str, expires_in: int):
        """保存token到本地文件"""
        try:
            data = {
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_at': time.time() + expires_in,
                'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
            with open(self.token_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[飞书邮件] 保存token失败: {e}")

    def _get_user_access_token(self) -> str:
        """获取用户access_token，自动刷新"""
        if self._user_access_token and time.time() < self._token_expire_time - 120:
            return self._user_access_token

        # 用refresh_token刷新（v2 oauth方式）
        if self._refresh_token:
            try:
                url = f"{self.base_url}/open-apis/authen/v2/oauth/token"
                resp = requests.post(url, json={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self.app_id,
                    "client_secret": self.app_secret,
                }, timeout=10)
                data = resp.json()
                if 'access_token' in data:
                    self._user_access_token = data.get('access_token', '')
                    self._refresh_token = data.get('refresh_token', self._refresh_token)
                    self._token_expire_time = time.time() + data.get('expires_in', 7200)
                    self._save_token(self._user_access_token, self._refresh_token,
                                    data.get('expires_in', 7200))
                    return self._user_access_token
                else:
                    logger.warning(f"[飞书邮件] 刷新token失败: {data.get('error_description', data.get('msg', 'unknown'))}")
            except Exception as e:
                logger.error(f"[飞书邮件] 刷新token异常: {e}")

        raise Exception("用户未授权或token已过期，请重新授权")

    def _get_tenant_token(self) -> str:
        """获取应用tenant_access_token（用于刷新用户token）"""
        url = f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }, timeout=10)
        data = resp.json()
        if data.get('code') == 0:
            return data['tenant_access_token']
        raise Exception(f"获取tenant_token失败: {data.get('msg')}")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_user_access_token()}",
            "Content-Type": "application/json; charset=utf-8"
        }

    def is_authorized(self) -> bool:
        """检查是否已授权"""
        return bool(self._refresh_token)

    def get_device_auth_url(self) -> Dict:
        """
        生成设备流授权链接（首次授权用）
        返回: {'verification_url': '...', 'user_code': '...', 'device_code': '...'}
        """
        import base64
        scope = "mail:user_mailbox.message:readonly mail:user_mailbox.message.subject:read mail:user_mailbox.message.body:read mail:user_mailbox.message.address:read mail:user_mailbox.folder:read offline_access"
        # 尝试标准设备授权端点（form-encoded + Basic Auth）
        urls_to_try = [
            f"{self.base_url}/open-apis/authen/v1/device_authorization",
            f"{self.base_url}/open-apis/authen/v1/device_token",
        ]
        auth_str = f"{self.app_id}:{self.app_secret}"
        auth_b64 = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')
        
        last_error = None
        for url in urls_to_try:
            try:
                # 方式1: form-encoded + Basic Auth (标准OAuth方式)
                resp = requests.post(url, data={
                    "client_id": self.app_id,
                    "scope": scope,
                }, headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {auth_b64}",
                }, timeout=15)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        if data.get('device_code') or (data.get('data') and data['data'].get('device_code')):
                            result = data.get('data', data)
                            # 统一字段名
                            return {
                                'device_code': result.get('device_code', ''),
                                'user_code': result.get('user_code', ''),
                                'verification_url': result.get('verification_uri', result.get('verification_url', '')),
                                'verification_uri_complete': result.get('verification_uri_complete', ''),
                                'expires_in': result.get('expires_in', 600),
                                'interval': result.get('interval', 5),
                            }
                    except:
                        pass
                # 方式2: JSON body
                resp2 = requests.post(url, json={
                    "app_id": self.app_id,
                    "app_secret": self.app_secret,
                    "grant_type": "device_code",
                    "scope": scope,
                }, headers={"Content-Type": "application/json"}, timeout=15)
                if resp2.status_code == 200:
                    try:
                        data = resp2.json()
                        if data.get('code') == 0 and data.get('data'):
                            d = data['data']
                            return {
                                'device_code': d.get('device_code', ''),
                                'user_code': d.get('user_code', ''),
                                'verification_url': d.get('verification_uri', d.get('verification_url', '')),
                                'verification_uri_complete': d.get('verification_uri_complete', ''),
                                'expires_in': d.get('expires_in', 600),
                                'interval': d.get('interval', 5),
                            }
                    except:
                        pass
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as e:
                last_error = str(e)
        
        raise Exception(f"生成授权链接失败，所有端点均不可用: {last_error}")

    def poll_device_auth(self, device_code: str, max_wait: int = 600) -> bool:
        """轮询设备流授权结果"""
        # 尝试v2 oauth token端点（与refresh_token保持一致）
        urls_to_try = [
            f"{self.base_url}/open-apis/authen/v2/oauth/token",
            f"{self.base_url}/open-apis/authen/v1/access_token",
            f"{self.base_url}/open-apis/authen/v1/oauth2/token",
        ]
        start = time.time()
        interval = 5
        while time.time() - start < max_wait:
            for url in urls_to_try:
                try:
                    resp = requests.post(url, json={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": self.app_id,
                        "client_secret": self.app_secret,
                    }, timeout=10)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    # 标准响应: access_token, refresh_token, expires_in
                    if 'access_token' in data:
                        self._user_access_token = data.get('access_token', '')
                        self._refresh_token = data.get('refresh_token', '')
                        self._token_expire_time = time.time() + data.get('expires_in', 7200)
                        self._save_token(self._user_access_token, self._refresh_token,
                                        data.get('expires_in', 7200))
                        return True
                    # 飞书格式: code + data
                    if data.get('code') == 0 and data.get('data'):
                        tok = data['data']
                        self._user_access_token = tok.get('access_token', '')
                        self._refresh_token = tok.get('refresh_token', '')
                        self._token_expire_time = time.time() + tok.get('expires_in', 7200)
                        self._save_token(self._user_access_token, self._refresh_token,
                                        tok.get('expires_in', 7200))
                        return True
                    # 授权中: authorization_pending
                    err = data.get('error', '') or data.get('msg', '')
                    if 'pending' in err.lower() or 'authorization_pending' in err.lower():
                        break  # 跳出url循环，sleep后重试
                except Exception as e:
                    logger.debug(f"[飞书邮件] 轮询{url}异常: {e}")
            time.sleep(interval)
        return False

    def _request(self, method, url, **kwargs):
        """带重试的请求封装"""
        max_retries = 3
        timeout = kwargs.pop('timeout', 30)
        for i in range(max_retries):
            try:
                resp = requests.request(method, url, timeout=timeout, **kwargs)
                return resp
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                if i < max_retries - 1:
                    time.sleep(2 * (i + 1))
                    continue
                raise
        return None

    def search_mails(self, keyword: str, limit: int = 20) -> List[Dict]:
        """
        搜索邮件：拉ID列表 → 批量读详情 → 按主题筛选
        mailbox默认是 me（当前授权用户）
        """
        url = f"{self.base_url}/open-apis/mail/v1/user_mailboxes/me/messages"
        all_ids = []
        page_token = None
        total_fetched = 0
        max_fetch = 100
        matched = []

        try:
            # 第一步：拉邮件ID列表
            while len(all_ids) < max_fetch:
                params = {"page_size": 20, "folder_id": "INBOX"}
                if page_token:
                    params["page_token"] = page_token

                resp = self._request("GET", url, headers=self._headers(), params=params)
                data = resp.json()

                if data.get('code') != 0:
                    logger.warning(f"[飞书邮件] 拉取列表失败: code={data.get('code')}, msg={data.get('msg')}")
                    break

                items = data.get('data', {}).get('items', []) or []
                if not items:
                    break

                for item in items:
                    if isinstance(item, dict):
                        mid = item.get('message_id', '') or item.get('id', '')
                    else:
                        mid = str(item)
                    if mid:
                        all_ids.append(mid)

                total_fetched += len(items)

                has_more = data.get('data', {}).get('has_more', False)
                page_token = data.get('data', {}).get('page_token')
                if not has_more or not page_token:
                    break

            # 第二步：批量读取邮件详情并按主题筛选
            matched = []
            if all_ids:
                batch_url = f"{self.base_url}/open-apis/mail/v1/user_mailboxes/me/messages/batch_get"
                # 分批，每批最多20个（飞书API限制）
                for i in range(0, len(all_ids), 20):
                    if len(matched) >= limit:
                        break
                    batch = all_ids[i:i+20]
                    try:
                        resp = self._request("POST", batch_url, headers=self._headers(),
                                           json={"message_ids": batch})
                        data = resp.json()
                        if data.get('code') == 0:
                            messages = data.get('data', {}).get('messages', []) or []
                            for msg in messages:
                                if len(matched) >= limit:
                                    break
                                subject = _get_mail_subject(msg)
                                if keyword in subject:
                                    matched.append(msg)
                        else:
                            logger.warning(f"[飞书邮件] batch_get失败: code={data.get('code')}, msg={data.get('msg')}")
                    except Exception as e:
                        logger.error(f"[飞书邮件] batch_get异常: {e}")
                        # 降级：逐封读取
                        for mid in batch:
                            if len(matched) >= limit:
                                break
                            detail = self.get_mail_content(mid)
                            if detail and keyword in _get_mail_subject(detail):
                                matched.append(detail)

            logger.info(f"[飞书邮件] 关键词'{keyword}': 拉取{total_fetched}封，命中{len(matched)}封")
            return matched

        except Exception as e:
            logger.error(f"[飞书邮件] 搜索异常: {e}")
            return matched

    def get_mail_content(self, message_id: str) -> Optional[Dict]:
        """获取邮件详情（含正文）"""
        url = f"{self.base_url}/open-apis/mail/v1/user_mailboxes/me/messages/{message_id}"
        try:
            resp = self._request("GET", url, headers=self._headers(), params={"format": "full"})
            data = resp.json()
            if data.get('code') == 0:
                return data.get('data', {}).get('message', {}) or data.get('data', {})
            else:
                logger.warning(f"[飞书邮件] 获取详情失败: code={data.get('code')}, msg={data.get('msg')}")
                return None
        except Exception as e:
            logger.error(f"[飞书邮件] 获取详情异常: {e}")
            return None


# ============================================================
# 邮件解析函数（复用原有逻辑）
# ============================================================
def _parse_percent(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r'(\d+\.?\d*)\s*%', text)
    if m:
        try:
            return float(m.group(1)) / 100
        except:
            return None
    m = re.search(r'(\d+\.?\d*)', text)
    if m:
        try:
            v = float(m.group(1))
            if v <= 1:
                return v
            return v / 100
        except:
            return None
    return None

def _parse_number(text: str) -> Optional[float]:
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

def _extract_table_rows(html_content: str) -> List[List[str]]:
    if not html_content:
        return []
    rows = []
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

def _find_row_by_keyword(table_rows, keyword, city=None):
    if not table_rows:
        return None
    if city:
        for row in table_rows:
            row_text = ' '.join(str(c) for c in row)
            if keyword in row_text and city in row_text:
                return row
    for row in table_rows:
        row_text = ' '.join(str(c) for c in row)
        if keyword in row_text:
            return row
    return None


def parse_app_mail(html_content: str, city: str = "成都") -> Dict:
    """解析APP执行、失误邮件"""
    result = {'app_execution': None, 'app_coverage': None, 'app_error': None}
    table_rows = _extract_table_rows(html_content)

    if not table_rows:
        text = strip_html(html_content)
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

    # 找成都行
    city_row = None
    for row in table_rows:
        if city in ' '.join(row):
            city_row = row
            break

    if city_row:
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

    # 兜底
    for kw, key in [('执行率', 'app_execution'), ('覆盖率', 'app_coverage'), ('失误率', 'app_error')]:
        if result[key] is None:
            row = _find_row_by_keyword(table_rows, kw, city)
            if row:
                for cell in row:
                    v = _parse_percent(cell)
                    if v is not None:
                        result[key] = v
                        break

    return result


def parse_reshoot_mail(html_content: str, city: str = "成都") -> Dict:
    """解析重拍率邮件"""
    result = {
        'godzilla_reshoot_rate': None,
        'godzilla_reshoot_count': None,
        'reshoot_7d_rate': None,
    }
    table_rows = _extract_table_rows(html_content)
    text = strip_html(html_content)

    if not table_rows:
        m = re.search(r'哥斯拉.*?重拍率[：:]*\s*(\d+\.?\d*)\s*%', text)
        if m:
            result['godzilla_reshoot_rate'] = float(m.group(1)) / 100
        else:
            m = re.search(r'重拍率[：:]*\s*(\d+\.?\d*)\s*%', text)
            if m:
                result['godzilla_reshoot_rate'] = float(m.group(1)) / 100
        m = re.search(r'重拍(?:量|数|台数)[：:]*\s*(\d+[\d,]*)', text)
        if m:
            result['godzilla_reshoot_count'] = float(m.group(1).replace(',', ''))
        m = re.search(r'(?:近7天|7天|近七日|七日).*?重拍率[：:]*\s*(\d+\.?\d*)\s*%', text)
        if m:
            result['reshoot_7d_rate'] = float(m.group(1)) / 100
        return result

    city_row = None
    for row in table_rows:
        if city in ' '.join(row):
            city_row = row
            break

    if city_row:
        header_row = None
        for row in table_rows:
            row_text = ' '.join(row)
            if ('重拍率' in row_text or '重拍量' in row_text) and city not in row_text:
                header_row = row
                break
        if header_row:
            for i, header in enumerate(header_row):
                if i >= len(city_row):
                    continue
                if '重拍率' in header and '7' not in header:
                    v = _parse_percent(city_row[i])
                    if v is not None:
                        result['godzilla_reshoot_rate'] = v
                elif '重拍量' in header or '重拍数' in header or '重拍台数' in header:
                    v = _parse_number(city_row[i])
                    if v is not None:
                        result['godzilla_reshoot_count'] = v
                elif ('7天' in header or '近7' in header or '七日' in header) and '重拍率' in header:
                    v = _parse_percent(city_row[i])
                    if v is not None:
                        result['reshoot_7d_rate'] = v

    # 兜底
    if result['godzilla_reshoot_rate'] is None:
        m = re.search(r'哥斯拉.*?重拍率[：:]*\s*(\d+\.?\d*)\s*%', text)
        if m:
            result['godzilla_reshoot_rate'] = float(m.group(1)) / 100
    if result['godzilla_reshoot_count'] is None:
        m = re.search(r'重拍(?:量|数|台数)[：:]*\s*(\d+[\d,]*)', text)
        if m:
            result['godzilla_reshoot_count'] = float(m.group(1).replace(',', ''))
    if result['reshoot_7d_rate'] is None:
        m = re.search(r'(?:近7天|7天|近七日).*?重拍率[：:]*\s*(\d+\.?\d*)\s*%', text)
        if m:
            result['reshoot_7d_rate'] = float(m.group(1)) / 100

    return result


def _get_mail_body(mail: dict) -> str:
    if not mail:
        return ''
    body = mail.get('body', {})
    if isinstance(body, dict):
        return body.get('content', '') or ''
    return str(body) if body else ''

def _get_mail_subject(mail: dict) -> str:
    return mail.get('subject', '') or ''

def _get_mail_date(mail: dict) -> str:
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


# ============================================================
# 对外主函数
# ============================================================
def get_quality_metrics(config: dict) -> Dict:
    """
    获取运营质量指标（从飞书邮件中读取，用户身份）
    """
    app_id = config.get('app_id', '')
    app_secret = config.get('app_secret', '')
    token_path = config.get('mail_token_path', '')
    city = config.get('city_filter', '成都')
    app_kw = config.get('app_mail_keyword', 'APP执行')
    reshoot_kw = config.get('reshoot_mail_keyword', '重拍率')

    result = {'latest_date': '', 'items': {}, 'sources': {}}

    try:
        client = FeishuMailUserClient(app_id, app_secret, token_path)
        if not client.is_authorized():
            logger.warning("[运营质量] 邮件未授权，请先进行用户授权")
            result['error'] = 'unauthorized'
            return result
    except Exception as e:
        logger.error(f"[运营质量] 初始化邮件客户端失败: {e}")
        result['error'] = str(e)
        return result

    # 1. 搜索APP执行失误邮件
    try:
        app_mails = client.search_mails(app_kw, limit=10)
        if app_mails:
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

    # 2. 搜索重拍率邮件
    try:
        reshoot_mails = client.search_mails(reshoot_kw, limit=10)
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
