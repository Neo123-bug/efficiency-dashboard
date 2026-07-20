# -*- coding: utf-8 -*-
"""
飞书邮件客户端 - 应用身份版
使用tenant_access_token读取指定用户邮箱
"""
import os
import json
import time
import re
import logging
import requests
from typing import Dict, List, Optional
from datetime import datetime
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

def _get_mail_subject(msg):
    if isinstance(msg, dict):
        return msg.get('subject', '') or msg.get('title', '') or ''
    return ''

def _get_mail_body(msg):
    if not isinstance(msg, dict):
        return ''
    body = msg.get('body', {})
    if isinstance(body, dict):
        content = body.get('content', '') or ''
    else:
        content = str(body or '')
    return content

def _get_mail_date(msg):
    if not isinstance(msg, dict):
        return ''
    # 飞书邮件API返回的时间戳（秒或毫秒）
    ts = msg.get('received_time') or msg.get('sent_time') or msg.get('created_time') or 0
    try:
        ts = int(ts)
        if ts > 1e12:  # 毫秒
            ts = ts / 1000
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
    except:
        return str(ts or '')


# ============================================================
# 飞书邮箱客户端（应用身份）
# ============================================================
class FeishuMailTenantClient:
    """
    使用应用身份(tenant_access_token)访问飞书邮箱
    需要指定mailbox（邮箱地址）
    """
    def __init__(self, app_id: str, app_secret: str, mailbox: str,
                 base_url: str = "https://open.feishu.cn"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.mailbox = mailbox
        self.base_url = base_url.rstrip('/')
        self._tenant_token = None
        self._token_expire_time = 0

    def _get_tenant_token(self) -> str:
        """获取tenant_access_token，自动刷新"""
        if self._tenant_token and time.time() < self._token_expire_time - 120:
            return self._tenant_token

        url = f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }, timeout=10)
        data = resp.json()
        if data.get('code') == 0:
            self._tenant_token = data['tenant_access_token']
            self._token_expire_time = time.time() + data.get('expire', 7200)
            return self._tenant_token
        raise Exception(f"获取tenant_token失败: {data.get('msg', 'unknown')}")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_tenant_token()}",
            "Content-Type": "application/json; charset=utf-8"
        }

    def _request(self, method, url, **kwargs):
        """带重试的请求封装"""
        max_retries = 3
        timeout = kwargs.pop('timeout', 30)
        for i in range(max_retries):
            try:
                resp = requests.request(method, url, timeout=timeout, **kwargs)
                # token过期自动刷新
                if resp.status_code == 200:
                    try:
                        d = resp.json()
                        if d.get('code') in (99991663, 99991664, 99991668):  # token相关错误
                            self._tenant_token = None
                            continue
                    except:
                        pass
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
        """
        url = f"{self.base_url}/open-apis/mail/v1/user_mailboxes/{self.mailbox}/messages"
        all_ids = []
        page_token = None
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

                has_more = data.get('data', {}).get('has_more', False)
                page_token = data.get('data', {}).get('page_token')
                if not has_more or not page_token:
                    break

            # 第二步：批量读取邮件详情并按主题筛选
            if all_ids:
                batch_url = f"{self.base_url}/open-apis/mail/v1/user_mailboxes/{self.mailbox}/messages/batch_get"
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
        except Exception as e:
            logger.error(f"[飞书邮件] search_mails异常: {e}")

        return matched


# ============================================================
# 质量指标解析（从邮件正文提取数据）
# ============================================================
def _extract_app_metrics(text: str) -> Dict:
    """从APP执行/失误邮件正文中提取指标"""
    result = {}
    # 成都相关的行
    # 简单提取：找"成都"后面的数字
    lines = text.split('\n')
    for line in lines:
        if '成都' in line:
            # 提取数字
            nums = re.findall(r'[\d.]+%?', line)
            if nums:
                # 按位置尝试匹配
                # 典型格式：城市 执行率 覆盖率 失误率 ...
                pass
            break
    
    # 用正则提取关键指标
    patterns = [
        (r'执行率[^\d]*([\d.]+)%?', 'app_execution'),
        (r'覆盖率[^\d]*([\d.]+)%?', 'app_coverage'),
        (r'失误率[^\d]*([\d.]+)%?', 'app_error'),
    ]
    for pat, key in patterns:
        m = re.search(pat, text)
        if m:
            try:
                result[key] = float(m.group(1))
            except:
                pass
    
    return result

def _extract_reshoot_metrics(text: str) -> Dict:
    """从重拍率邮件正文中提取指标"""
    result = {}
    
    patterns = [
        (r'重拍率[^\d]*([\d.]+)%?', 'reshoot_rate'),
        (r'重拍量[^\d]*([\d.]+)', 'reshoot_count'),
        (r'近7天重拍率[^\d]*([\d.]+)%?', 'reshoot_7d_rate'),
        (r'哥斯拉重拍率[^\d]*([\d.]+)%?', 'godzilla_reshoot_rate'),
        (r'哥斯拉重拍量[^\d]*([\d.]+)', 'godzilla_reshoot_count'),
    ]
    for pat, key in patterns:
        m = re.search(pat, text)
        if m:
            try:
                result[key] = float(m.group(1))
            except:
                pass
    
    return result


def get_quality_metrics(config: Dict) -> Dict:
    """
    获取运营质量指标（从飞书邮件提取）
    返回格式: {'items': [...], 'latest_date': '...'}
    """
    app_id = config['app_id']
    app_secret = config['app_secret']
    mailbox = config['mailbox']
    app_keyword = config.get('app_mail_keyword', 'APP执行')
    reshoot_keyword = config.get('reshoot_mail_keyword', '重拍率')
    
    client = FeishuMailTenantClient(app_id, app_secret, mailbox)
    
    items = []
    latest_date = ''
    
    # 1. 搜索APP执行/失误邮件
    try:
        app_mails = client.search_mails(app_keyword, limit=3)
        for mail in app_mails:
            date = _get_mail_date(mail)
            body_html = _get_mail_body(mail)
            body_text = strip_html(body_html)
            metrics = _extract_app_metrics(body_text)
            for key, val in metrics.items():
                metric_config = config.get('metrics', {}).get(key, {})
                items.append({
                    'key': key,
                    'name': metric_config.get('name', key),
                    'value': val,
                    'date': date,
                    'category': 'app',
                })
            if date > latest_date:
                latest_date = date
            break  # 只取最新一封
    except Exception as e:
        logger.error(f"[质量] APP邮件解析失败: {e}")
    
    # 2. 搜索重拍率邮件
    try:
        reshoot_mails = client.search_mails(reshoot_keyword, limit=3)
        for mail in reshoot_mails:
            date = _get_mail_date(mail)
            body_html = _get_mail_body(mail)
            body_text = strip_html(body_html)
            metrics = _extract_reshoot_metrics(body_text)
            for key, val in metrics.items():
                metric_config = config.get('metrics', {}).get(key, {})
                items.append({
                    'key': key,
                    'name': metric_config.get('name', key),
                    'value': val,
                    'date': date,
                    'category': 'reshoot',
                })
            if date > latest_date:
                latest_date = date
            break  # 只取最新一封
    except Exception as e:
        logger.error(f"[质量] 重拍邮件解析失败: {e}")
    
    return {
        'items': items,
        'latest_date': latest_date,
        'total': len(items),
    }


def save_quality_cache(data: Dict, cache_path: str):
    """保存质量数据缓存"""
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    data['_updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_quality_cache(cache_path: str) -> Dict:
    """读取质量数据缓存"""
    try:
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"读取质量缓存失败: {e}")
    return {'items': [], 'latest_date': '', 'total': 0}
