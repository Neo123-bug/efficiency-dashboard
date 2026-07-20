# -*- coding: utf-8 -*-
"""
数据源服务 - 三个真实API数据源的封装
- GodzillaClient: 哥斯拉拍照效率数据
- MirrorClient: 魔镜质检效率数据
- WatcherClient: abdavinci报表数据（watcher系统）
"""
import time
import json
import os
import logging
import time
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple

import urllib3
import requests

# 禁用内网自签证书警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import (
    GODZILLA_CONFIG, MIRROR_CONFIG, WATCHER_CONFIG, EFFICIENCY_CONFIG
)

logger = logging.getLogger(__name__)


def _parse_dt(value):
    """将 watcher 返回的时间字符串（如 '2026-07-19 09:46:18'）解析为 datetime，失败返回 None。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    # 尝试毫秒时间戳
    try:
        if s.isdigit():
            return datetime.fromtimestamp(int(s) / 1000.0)
    except (ValueError, TypeError, OSError):
        pass
    return None


class BaseClient:
    """基础API客户端，提供通用的请求、重试、错误处理"""

    def __init__(self, base_url: str, session: str, timeout: int = 30, max_retries: int = 3):
        self.base_url = base_url.rstrip('/')
        self.session = session
        self.timeout = timeout
        self.max_retries = max_retries
        self.session_expired = False
        # 用requests.Session维持完整cookie链，登录后SESSION才能真正生效
        self._s = requests.Session()
        self._s.verify = False
        self._s.headers.update({
            'x-requested-with': 'XMLHttpRequest',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Connection': 'keep-alive',
        })
        # 如果已有session值，先塞进去
        if session:
            self._s.cookies.set('SESSION', session, domain='.aihuishou.com')

    def _get_headers(self) -> Dict[str, str]:
        """获取当前session的headers（只读引用）"""
        return dict(self._s.headers)

    def _request(self, method: str, path: str, params: Optional[Dict] = None,
                 json_body: Optional[Dict] = None) -> Optional[Dict]:
        """
        带重试的HTTP请求（使用self._s维持完整cookie链）
        返回解析后的JSON字典，失败返回None
        """
        url = f'{self.base_url}{path}'
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                if method.upper() == 'GET':
                    resp = self._s.get(
                        url, params=params,
                        timeout=self.timeout,
                        allow_redirects=False
                    )
                elif method.upper() == 'POST':
                    resp = self._s.post(
                        url, params=params, json=json_body,
                        timeout=self.timeout,
                        allow_redirects=False
                    )
                else:
                    raise ValueError(f'Unsupported method: {method}')

                # 检查是否登录过期 - 自动CAS重登（HTTP 302重定向）
                if resp.status_code == 302 or 'login' in resp.url.lower() or 'cas' in resp.url.lower():
                    logger.warning(f'Session expired for {self.__class__.__name__}, auto relogin...')
                    if self.auto_relogin():
                        logger.info(f'{self.__class__.__name__} relogin success, retrying request')
                        continue
                    self.session_expired = True
                    logger.error(f'{self.__class__.__name__} auto relogin failed')
                    return None

                if resp.status_code != 200:
                    logger.warning(
                        f'{self.__class__.__name__} HTTP {resp.status_code} '
                        f'attempt {attempt}/{self.max_retries}: {url}'
                    )
                    last_error = f'HTTP {resp.status_code}'
                    time.sleep(2 ** attempt)
                    continue

                try:
                    data = resp.json()
                except json.JSONDecodeError:
                    logger.warning(f'{self.__class__.__name__} JSON parse failed: {url}')
                    last_error = 'JSON parse error'
                    time.sleep(2 ** attempt)
                    continue

                # 检查业务码302（登录过期），触发自动重登
                if isinstance(data, dict):
                    biz_code = data.get('code')
                    biz_msg = data.get('msg', '') or ''
                    if biz_code == 302 or ('to auth' in str(biz_msg).lower() and 'login' not in url.lower()):
                        logger.warning(
                            f'Session expired (biz code={biz_code}, msg={biz_msg}) for {self.__class__.__name__}, auto relogin...'
                        )
                        if self.auto_relogin():
                            logger.info(f'{self.__class__.__name__} relogin success, retrying request')
                            continue
                        self.session_expired = True
                        logger.error(f'{self.__class__.__name__} auto relogin failed')
                        return data

                return data

            except requests.RequestException as e:
                last_error = str(e)
                logger.warning(
                    f'{self.__class__.__name__} request error attempt {attempt}/{self.max_retries}: {e}'
                )
                time.sleep(2 ** attempt)
                continue

        logger.error(f'{self.__class__.__name__} request failed after {self.max_retries} retries: {last_error}')
        return None

    def auto_relogin(self) -> bool:
        """
        SESSION过期时自动通过CAS重新登录
        成功返回True并更新self.session，失败返回False
        """
        try:
            import re
            from urllib.parse import quote

            USERNAME = os.environ.get('AIHUISHOU_CAS_USERNAME', '')
            PASSWORD = os.environ.get('AIHUISHOU_CAS_PASSWORD', '')
            CAS_URL = 'https://sso.aihuishou.com/cas/login'

            # 用一个干净的session做CAS登录流程
            s = requests.Session()
            s.verify = False
            s.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            })

            # Step 1: 访问目标API触发302跳转到CAS
            test_url = self._get_test_api_url()
            if not test_url:
                logger.error(f'{self.__class__.__name__}: no test API for relogin')
                return False

            r0 = s.get(test_url, allow_redirects=True, timeout=20)
            logger.info(f'{self.__class__.__name__} relogin step1: {r0.status_code} -> {r0.url[:80]}')

            if 'cas' not in r0.url.lower() and 'login' not in r0.url.lower():
                # 已经登录了，直接拿session
                for c in s.cookies:
                    if c.name == 'SESSION' and 'wirelessgate' in c.domain:
                        self.session = c.value
                        self.session_expired = False
                        return True
                return False

            # Step 2: 提取CAS表单参数并提交登录
            html = r0.text
            exec_match = re.search(r'name="execution"\s+value="([^"]+)"', html)
            csrf_match = re.search(r'name="_csrf"\s+value="([^"]+)"', html)
            execution = exec_match.group(1) if exec_match else 'e1s1'
            _csrf = csrf_match.group(1) if csrf_match else ''

            form_data = {
                'username': USERNAME,
                'password': PASSWORD,
                'execution': execution,
                '_eventId': 'submit',
            }
            if _csrf:
                form_data['_csrf'] = _csrf

            r1 = s.post(r0.url, data=form_data, allow_redirects=True, timeout=20)
            logger.info(f'{self.__class__.__name__} relogin step2: {r1.status_code} -> {r1.url[:80]}')

            # Step 3: 提取SESSION cookie
            new_session = ''
            for c in s.cookies:
                if c.name == 'SESSION' and ('wirelessgate' in c.domain or 'aihuishou' in c.domain):
                    new_session = c.value
                    break

            if not new_session:
                logger.error(f'{self.__class__.__name__} relogin failed: no SESSION cookie')
                return False

            # Step 4: 用同一个Session对象验证（走完整重定向，确保cookie生效）
            r_verify = s.get(test_url, allow_redirects=True, timeout=20)
            verify_ok = True
            # 检查是否还在登录页
            if 'cas' in r_verify.url.lower() or 'login' in r_verify.url.lower():
                verify_ok = False
            # 检查返回内容里的业务码
            try:
                vdata = r_verify.json()
                if isinstance(vdata, dict):
                    if vdata.get('code') == 302 or 'to auth' in str(vdata.get('msg', '')).lower():
                        verify_ok = False
            except:
                pass
            if not verify_ok:
                logger.error(f'{self.__class__.__name__} relogin verification failed')
                return False

            self.session = new_session
            self.session_expired = False

            # 关键：把登录成功session的所有cookies同步到self._s
            # 不只是SESSION，CAS登录可能产生多个会话cookie（route、CASTGC等）
            # 不同步的话self._s发请求还是旧cookie，会302死循环
            for c in s.cookies:
                self._s.cookies.set_cookie(c)
            # 同步User-Agent等关键headers，避免服务端校验不一致
            if s.headers.get('User-Agent'):
                self._s.headers['User-Agent'] = s.headers['User-Agent']
            if s.headers.get('Accept'):
                self._s.headers['Accept'] = s.headers['Accept']
            if s.headers.get('Accept-Language'):
                self._s.headers['Accept-Language'] = s.headers['Accept-Language']

            logger.info(f'{self.__class__.__name__} auto relogin success: {new_session[:20]}...')

            # 同步更新config.py（持久化，下次启动用新session）
            self._update_config_file(new_session)

            return True
        except Exception as e:
            logger.error(f'{self.__class__.__name__} auto relogin exception: {e}')
            return False

    def _get_test_api_url(self) -> str:
        """子类返回用于验证session的测试API完整URL"""
        return ''

    def _update_config_file(self, new_session: str):
        """将新SESSION持久化到config.py"""
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.py')
            if not os.path.exists(config_path):
                return

            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()

            import re
            cls_name = self.__class__.__name__
            if cls_name == 'GodzillaClient':
                # 匹配哥斯拉session行
                pattern = r"('session':\s*')[A-Za-z0-9_\-]+('\s*,\s*\n\s*'operation_center_id)"
                def gz_repl(m):
                    return m.group(1) + new_session + m.group(2)
                new_content = re.sub(pattern, gz_repl, content)
            elif cls_name == 'MirrorClient':
                pattern = r"(MIRROR_CONFIG\s*=\s*\{[^{]*?'session':\s*')[^']*(',\s*\n\s*'api_path')"
                def mr_repl(m):
                    return m.group(1) + new_session + m.group(2)
                new_content = re.sub(pattern, mr_repl, content, flags=re.DOTALL)
            else:
                return

            if new_content != content:
                with open(config_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                logger.info(f'{cls_name}: SESSION saved to config.py')
        except Exception as e:
            logger.warning(f'{self.__class__.__name__} update config file failed: {e}')

    def check_session_valid(self) -> bool:
        """检查SESSION是否有效"""
        raise NotImplementedError


class GodzillaClient(BaseClient):
    """
    哥斯拉（创意怪兽）拍照效率数据源
    接口: GET /fe/image-gather-report/search
    """

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or GODZILLA_CONFIG
        super().__init__(cfg['base_url'], cfg['session'])
        self.operation_center_id = cfg['operation_center_id']
        self.machine_type = cfg['machine_type']
        self.page_size = cfg['page_size']
        self.total_duration_key = cfg['total_duration_key']

    def check_session_valid(self) -> bool:
        """检查session是否有效 - 请求一页数据测试"""
        data = self._fetch_page(1, date.today().strftime('%Y-%m-%d'))
        return data is not None and 'data' in data

    def _get_test_api_url(self) -> str:
        """用于CAS登录跳转测试的完整API URL"""
        return f'{self.base_url}/fe/image-gather-report/search?current=1&pageSize=1&operationCenterId={self.operation_center_id}'

    def _fetch_page(self, page: int, month_date: str) -> Optional[Dict]:
        """获取单页数据"""
        params = {
            'current': page,
            'pageSize': self.page_size,
            'machineType': self.machine_type,
            'month': f'{month_date} 00:00:00',
            'operationCenterId': self.operation_center_id,
        }
        return self._request('GET', '/fe/image-gather-report/search', params=params)

    def fetch_by_date(self, target_date: str) -> List[Dict]:
        """
        按日期拉取当天所有拍照明细记录
        :param target_date: 日期字符串 YYYY-MM-DD
        :return: 标准化后的记录列表
        """
        records = []
        page = 1
        consecutive_empty = 0
        max_consecutive_empty = 5  # 连续多少页没有当天数据就停止
        max_pages = 200  # 最大页数限制，防止死循环

        while True:
            page_data = self._fetch_page(page, target_date)
            if page_data is None:
                logger.error(f'Godzilla fetch page {page} failed')
                break

            # 检查返回结构，兼容多种格式
            data_list = None
            total = 0

            if isinstance(page_data, dict):
                # 情况1: { code: 0, data: [...], total: N }
                if isinstance(page_data.get('data'), list):
                    data_list = page_data['data']
                    total = page_data.get('total', 0) or 0
                # 情况2: { code: 0, data: { list: [...], total: N } }
                elif isinstance(page_data.get('data'), dict):
                    data_obj = page_data['data']
                    for list_key in ['list', 'records', 'items', 'rows', 'content']:
                        if isinstance(data_obj.get(list_key), list):
                            data_list = data_obj[list_key]
                            break
                    for total_key in ['total', 'totalElements', 'totalCount', 'count']:
                        if total_key in data_obj:
                            total = data_obj[total_key] or 0
                            break
                # 情况3: 没有data字段，直接就是列表
                if data_list is None:
                    for key in page_data:
                        if isinstance(page_data[key], list) and len(page_data[key]) > 0:
                            first_item = page_data[key][0]
                            if isinstance(first_item, dict) and ('startDt' in first_item or 'employeeNo' in first_item):
                                data_list = page_data[key]
                                break

                # 检查是否登录过期
                if page_data.get('code') and page_data['code'] not in [0, 200]:
                    msg = page_data.get('msg', '') or page_data.get('message', '')
                    logger.warning(f'Godzilla API error: code={page_data["code"]}, msg={msg}')
                    if '未登录' in str(msg) or '登录' in str(msg) or page_data['code'] in [401, 403]:
                        self.session_expired = True
                    break

            if data_list is None:
                logger.warning(f'Godzilla page {page}: could not find data list in response')
                logger.debug(f'Response keys: {list(page_data.keys()) if isinstance(page_data, dict) else type(page_data)}')
                break

            if len(data_list) == 0:
                logger.info(f'Godzilla page {page} empty, stopping')
                break

            # 确保元素是字典
            dict_records = [r for r in data_list if isinstance(r, dict)]
            if len(dict_records) != len(data_list):
                logger.warning(f'Godzilla page {page}: {len(data_list) - len(dict_records)} non-dict records skipped')

            if not dict_records:
                break

            # 筛选当天的记录（API按月返回，按startDt判断）
            today_in_page = [
                rec for rec in dict_records
                if isinstance(rec.get('startDt'), str) and rec['startDt'].startswith(target_date)
            ]
            records.extend(today_in_page)

            if not today_in_page:
                consecutive_empty += 1
                if consecutive_empty >= max_consecutive_empty:
                    logger.info(
                        f'Godzilla: no today records for {max_consecutive_empty} '
                        f'consecutive pages, stopping at page {page}'
                    )
                    break
            else:
                consecutive_empty = 0

            # 判断是否最后一页
            total_pages = (total + self.page_size - 1) // self.page_size if total else 0
            if page >= total_pages or len(dict_records) < self.page_size:
                break

            # 安全限制：最多max_pages页
            if page >= max_pages:
                logger.warning(f'Godzilla: reached max_pages ({max_pages}), stopping')
                break

            # 每2页sleep一下，避免持续网络请求触发EDR
            if page % 2 == 0:
                time.sleep(2)

            page += 1
            # EDR sleep patch - avoid killing by EDR (每2页歇2秒)
            if page % 2 == 0:
                time.sleep(2)

        logger.info(f'Godzilla fetched {len(records)} records for {target_date}')
        return self._normalize_records(records)

    def fetch_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        """
        按日期范围拉取数据
        """
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        all_records = []
        current = start
        while current <= end:
            date_str = current.strftime('%Y-%m-%d')
            all_records.extend(self.fetch_by_date(date_str))
            current += timedelta(days=1)
        return all_records

    def _normalize_records(self, records: List[Dict]) -> List[Dict]:
        """
        将原始API记录标准化为统一格式
        字段: employee_no, employee_name, start_time, photo_count,
              duration_sec, total_duration_sec, delay_sec, product_no,
              machine_id, service_standard, data_source
        """
        normalized = []
        for rec in records:
            # 计算拍照数量 - gatherTypeImageInfoMap各类型图片数量之和
            image_map = rec.get('gatherTypeImageInfoMap') or {}
            photo_count = 0
            if isinstance(image_map, dict):
                for key, images in image_map.items():
                    if isinstance(images, list):
                        photo_count += len(images)
                    elif isinstance(images, (int, float)):
                        photo_count += int(images)

            # 计算总时长 - gatherElapsedTimeMap中"总时长"字段（毫秒）
            time_map = rec.get('gatherElapsedTimeMap') or {}
            total_ms = 0
            if isinstance(time_map, dict):
                total_ms = int(time_map.get(self.total_duration_key, 0) or 0)
            total_sec = total_ms // 1000

            normalized.append({
                'employee_no': str(rec.get('employeeNo', '')),
                'employee_name': '',  # 哥斯拉API不返回姓名，需要从花名册映射
                'start_time': rec.get('startDt', ''),
                'photo_count': photo_count,
                'duration_sec': total_sec,  # 设备运行时长
                'total_duration_sec': total_sec,  # 总耗时长（暂时等于设备时长）
                'delay_sec': 0,  # 延迟时长
                'product_no': str(rec.get('productNo', '')),
                'machine_id': str(rec.get('machineId', '')),
                'service_standard': str(rec.get('serviceStandardId', '')),
                'data_source': 'godzilla',
                'position_tag': '拍照',
            })

        return normalized


class MirrorClient(BaseClient):
    """
    魔镜（创意魔镜）质检效率数据源
    接口: GET /fe/v1/screenDefectReport/page
    """

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or MIRROR_CONFIG
        super().__init__(cfg['base_url'], cfg['session'])
        self.api_path = cfg['api_path']
        self.page_size = cfg['page_size']
        self.place_id = cfg.get('place_id')  # 按场所ID过滤（客户端过滤）
        self.max_pages = cfg.get('max_pages', 100)

    def check_session_valid(self) -> bool:
        """检查session是否有效"""
        data = self._fetch_page(0, date.today())
        return data is not None

    def _get_test_api_url(self) -> str:
        """用于CAS登录跳转测试的完整API URL"""
        today = date.today().strftime('%Y-%m-%d')
        return f'{self.base_url}{self.api_path}?page=0&size=1&beginTime={today}%2000:00:00&endTime={today}%2023:59:59'

    def _fetch_page(self, page: int, target_date: date) -> Optional[Dict]:
        """获取单页数据"""
        params = {
            'page': page,
            'size': self.page_size,
            'beginTime': f'{target_date.strftime("%Y-%m-%d")} 00:00:00',
            'endTime': f'{target_date.strftime("%Y-%m-%d")} 23:59:59',
        }
        return self._request('GET', self.api_path, params=params)

    def fetch_by_date(self, target_date: str) -> List[Dict]:
        """
        按日期拉取当天所有质检明细记录
        支持按 place_id 客户端过滤（成都运营中心）
        """
        records = []
        page = 0
        d = datetime.strptime(target_date, '%Y-%m-%d').date()

        while True:
            page_data = self._fetch_page(page, d)
            if page_data is None:
                logger.error(f'Mirror fetch page {page} failed')
                break

            # 检查返回结构，兼容多种格式
            data_list = None
            total = 0

            if isinstance(page_data, dict):
                # 检查是否登录过期
                if page_data.get('code') and page_data['code'] not in [0, 200]:
                    msg = page_data.get('msg', '') or page_data.get('message', '')
                    logger.warning(f'Mirror API error: code={page_data["code"]}, msg={msg}')
                    if '未登录' in str(msg) or '登录' in str(msg) or page_data['code'] in [401, 403, 302]:
                        self.session_expired = True
                    break

                # 情况1: { data: { list/records/items: [...] } }
                if isinstance(page_data.get('data'), dict):
                    data_obj = page_data['data']
                    for list_key in ['list', 'records', 'items', 'rows', 'content']:
                        if isinstance(data_obj.get(list_key), list):
                            data_list = data_obj[list_key]
                            break
                    for total_key in ['total', 'totalElements', 'totalCount', 'count']:
                        if total_key in data_obj:
                            total = data_obj[total_key] or 0
                            break
                # 情况2: { data: [...] }
                elif isinstance(page_data.get('data'), list):
                    data_list = page_data['data']
                    total = page_data.get('total', len(data_list)) or 0
                # 情况3: { list: [...] } 直接返回
                elif isinstance(page_data.get('list'), list):
                    data_list = page_data['list']
                    total = page_data.get('total', len(data_list)) or 0

            if data_list is None:
                logger.warning(f'Mirror page {page}: could not find data list in response')
                break

            if len(data_list) == 0:
                logger.info(f'Mirror page {page} empty, stopping')
                break

            # 确保元素是字典
            dict_records = [r for r in data_list if isinstance(r, dict)]
            if not dict_records:
                break

            # 客户端过滤：只保留指定场所的数据（成都）
            if self.place_id is not None:
                before = len(dict_records)
                dict_records = [
                    r for r in dict_records
                    if r.get('placeId') == self.place_id
                ]
                logger.debug(
                    f'Mirror page {page}: place filter {before} -> {len(dict_records)} '
                    f'(place_id={self.place_id})'
                )

            records.extend(dict_records)
            logger.info(
                f'Mirror page {page}: got {len(dict_records)} records '
                f'(place_id={self.place_id}), total so far: {len(records)}'
            )

            # 判断是否最后一页
            if total and ((page + 1) * self.page_size) >= total:
                logger.info(f'Mirror: reached last page (total={total}, page={page})')
                break
            # 如果返回的原始记录数少于预期，说明没有更多了
            if len(data_list) < self.page_size:
                logger.info(f'Mirror page {page}: only {len(data_list)} records (< {self.page_size}), stopping')
                break

            # 安全限制
            if page + 1 >= self.max_pages:
                logger.warning(f'Mirror: reached max_pages ({self.max_pages}), stopping')
                break

            page += 1

        logger.info(f'Mirror fetched {len(records)} records for {target_date}')
        return self._normalize_records(records)

    def fetch_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        """按日期范围拉取数据"""
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        all_records = []
        current = start
        while current <= end:
            date_str = current.strftime('%Y-%m-%d')
            all_records.extend(self.fetch_by_date(date_str))
            current += timedelta(days=1)
        return all_records

    def _normalize_records(self, records: List[Dict]) -> List[Dict]:
        """
        标准化魔镜数据
        实际API字段: operatorNO(工号, createdDate(创建时间), captureCost(耗时ms),
                    deviceCode(设备编号), modelName(机型), defectResult(缺陷结果逗号分隔),
                    serialNo(流水号), thirdPartyBizNo(业务单号)
        """
        normalized = []
        for rec in records:
            # 员工号 -  - 注意大小写: operatorNO
            emp_no = (
                rec.get('operatorNO') or rec.get('operatorNo')
                or rec.get('employeeNo') or rec.get('userNo')
                or rec.get('staffNo') or ''
            )
            # 员工姓名 - 魔镜API可能不返回，留空由花名册映射
            emp_name = (
                rec.get('operatorName') or rec.get('employeeName')
                or rec.get('userName') or rec.get('staffName') or ''
            )
            # 开始时间 - 优先 createdDate
            start_time = (
                rec.get('createdDate') or rec.get('createTime')
                or rec.get('startTime') or rec.get('testTime')
                or rec.get('finishTime') or ''
            )
            # 质检耗时 - captureCost 毫秒
            duration_ms = (
                rec.get('captureCost') or rec.get('costTime')
                or rec.get('duration') or rec.get('elapsed') or 0
            )
            duration_sec = 0
            if isinstance(duration_ms, (int, float)) and duration_ms > 0:
                if duration_ms > 1000:
                    duration_sec = int(duration_ms // 1000)
                else:
                    duration_sec = int(duration_ms)

            # 缺陷数量 - defectResult 是逗号分隔的缺陷类型字符串
            defect_count = 0
            defect_result = rec.get('defectResult') or ''
            if isinstance(defect_result, str) and defect_result:
                defect_count = len([d for d in defect_result.split(',') if d.strip()])
            elif isinstance(rec.get('defectCount'), (int, float)):
                    defect_count = int(rec['defectCount'])
            elif isinstance(rec.get('errorCount'), (int, float)):
                defect_count = int(rec['errorCount'])

            # 设备编号 - deviceCode / machineId
            machine_id = (
                rec.get('deviceCode') or rec.get('machineId')
                or rec.get('deviceNo') or ''
            )

            # 产品/业务号 - serialNo / thirdPartyBizNo
            product_no = (
                rec.get('serialNo') or rec.get('thirdPartyBizNo')
                or rec.get('productNo') or rec.get('itemNo') or ''
            )

            normalized.append({
                'employee_no': str(emp_no),
                'employee_name': str(emp_name),
                'start_time': str(start_time),
                'check_count': 1,  # 每条记录代表一次质检
                'duration_sec': duration_sec,
                'total_duration_sec': duration_sec,
                'product_no': str(product_no),
                'machine_id': str(machine_id),
                'defect_count': defect_count,
                'data_source': 'mirror',
                'position_tag': '质检',
            })

        return normalized




class GuangheClient:
    """光合系统客户端 - 拍拍拍照（PPPZ）数据
    认证策略：
      1) 优先用 SESSION cookie（与 X光 同源 wirelessgate.aihuishou.com，SESSION 通用），
         依次尝试 GUANGHE_SESSIONS 列表中的多个 token，命中即用；
      2) 全部 SESSION 失效时回退 CAS 登录（复用共用账号）。
    """
    
    def __init__(self, base_url=None, operator_center_id=None, sessions=None):
        import config as _cfg
        self.base_url = (base_url or getattr(_cfg, 'GUANGHE_BASE_URL', None)
                         or "https://wirelessgate.aihuishou.com/creative-lightcore").rstrip('/')
        self.operator_center_id = operator_center_id or 3
        self.username = os.environ.get('AIHUISHOU_CAS_USERNAME', '')
        self.password = os.environ.get('AIHUISHOU_CAS_PASSWORD', '')
        # SESSION token 列表（优先使用，依次回退）
        if sessions:
            self.sessions = sessions if isinstance(sessions, list) else [sessions]
        else:
            cfg_sessions = getattr(_cfg, 'GUANGHE_SESSIONS', None)
            self.sessions = cfg_sessions if isinstance(cfg_sessions, list) else (
                [cfg_sessions] if cfg_sessions else [])
        self.session = None          # requests.Session 对象
        self.active_session = None   # 当前生效的 SESSION token（None=CAS 模式）
        self._ensure_session()
    
    def _build_session_with_cookie(self, token):
        """用 SESSION cookie 构造请求会话（不登录）"""
        import requests
        s = requests.Session()
        s.verify = False
        s.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'X-Requested-With': 'XMLHttpRequest',
        })
        s.cookies.set('SESSION', token, domain='wirelessgate.aihuishou.com')
        return s
    
    def _session_valid(self, s):
        """轻量校验该 SESSION 是否仍有效（调用 user/get）"""
        try:
            r = s.get(self.base_url + '/fe/user/get', timeout=10)
            if r.status_code == 200:
                try:
                    return r.json().get('code') == 0
                except Exception:
                    return False
            return False
        except Exception:
            return False
    
    def _login(self):
        """CAS登录获取session（回退方案）"""
        import re, requests
        s = requests.Session()
        s.verify = False
        s.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        })
        service_url = self.base_url.rstrip('/') + '/login/cas'
        cas_url = 'https://sso.aihuishou.com/cas/login?service=' + requests.utils.quote(service_url)
        r = s.get(cas_url, allow_redirects=True, timeout=30)
        exec_match = re.search(r'name="execution"\s+value="([^"]+)"', r.text)
        csrf_match = re.search(r'name="_csrf"\s+value="([^"]+)"', r.text)
        execution = exec_match.group(1) if exec_match else 'e1s1'
        _csrf = csrf_match.group(1) if csrf_match else ''
        form_data = {
            'username': self.username,
            'password': self.password,
            'execution': execution,
            '_eventId': 'submit',
        }
        if _csrf:
            form_data['_csrf'] = _csrf
        s.post(r.url, data=form_data, allow_redirects=True, timeout=30)
        s.headers.update({
            'Accept': 'application/json, text/plain, */*',
            'X-Requested-With': 'XMLHttpRequest',
        })
        self.session = s
        self.active_session = None
    
    def _ensure_session(self):
        """确保 session 有效：先试 SESSION token 列表，回退 CAS"""
        # 1) 尝试 SESSION cookie 列表
        for token in self.sessions:
            if not token:
                continue
            s = self._build_session_with_cookie(token)
            if self._session_valid(s):
                self.session = s
                self.active_session = token
                return
        # 2) 回退 CAS 登录
        self._login()
    
    def fetch_photo_tasks(self, date_str=None, operator_center_id=None, page_size=100):
        """
        拉取指定日期的光合拍照任务数据
        date_str: YYYY-MM-DD，默认今天
        operator_center_id: 运营中心ID，默认3=成都优检
        返回: list of dict
        """
        from datetime import datetime
        if operator_center_id is None:
            operator_center_id = self.operator_center_id
        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')
        
        self._ensure_session()
        url = self.base_url + '/fe/take_picture_task/search'
        all_data = []
        page = 1
        
        while True:
            params = {
                'current': page,
                'pageSize': page_size,
                'ahsOperatorCenterId': operator_center_id,
                'startTimeFrom': date_str + ' 00:00:00.000',
                'startTimeTo': date_str + ' 23:59:59.000',
            }
            try:
                r = self.session.get(url, params=params, timeout=30)
                d = r.json()
                data = d.get('data', [])
                if not data:
                    break
                all_data.extend(data)
                total = d.get('total', 0)
                if len(all_data) >= total or page > 50:
                    break
                page += 1
            except Exception as e:
                print(f"[GuangheClient] 拉取失败 page={page}: {e}")
                break
        
        return all_data




class XRayClient(BaseClient):
    """X光系统客户端 - X光检测设备数据
    认证: 与哥斯拉/魔镜完全一致——优先使用环境变量 XRAY_SESSION，
          失效时由 BaseClient.auto_relogin 自动通过 CAS(yashi.zhang1114) 重新登录。
          （实测：X光 search 接口未带SESSION时返回302跳CAS，CAS登录可得有效SESSION）
    真实接口: {XRAY_BASE_URL}/fe/inspection-report-es/search  (POST)
    筛选参数: rayMachineId（注意不是 xRayMachineId，后者无效）
    说明: 接口不支持按日期筛选，需在本地按 startDt 前缀截断到当天。
    """

    def __init__(self, base_url=None, session=None):
        import config as _cfg
        base = (base_url or _cfg.XRAY_BASE_URL).rstrip('/')
        sess = session or getattr(_cfg, 'XRAY_SESSION', '') or ''
        super().__init__(base, sess)

    def _get_test_api_url(self) -> str:
        """用于CAS登录跳转测试的完整API URL（GET，未登录时跳CAS）"""
        return f'{self.base_url}/fe/inspection-report-es/search?current=1&pageSize=1&rayMachineId=XRayBig0005'

    def fetch_machine_records(self, machine_id, date_str=None, max_pages=200, page_size=1000):
        """
        拉取指定设备指定日期的X光检测记录
        machine_id: 设备ID，如 XRayBig0005 / DESKTOP-L63QQ6A
        date_str: YYYY-MM-DD，默认今天
        返回: list of dict（当天的记录）
        """
        from datetime import datetime
        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')

        url = '/fe/inspection-report-es/search'
        all_records = []
        for page in range(1, max_pages + 1):
            try:
                d = self._request(
                    'POST', url,
                    json_body={'current': page, 'pageSize': page_size, 'rayMachineId': machine_id},
                )
                if d is None:
                    # 认证彻底失败（auto_relogin 也失败）
                    print(f'[XRayClient] 拉取中断 machine={machine_id} page={page}: 请求失败')
                    break
                rows = d.get('data') or []
                if not rows:
                    break
                # 接口不支持按日期筛选，本地按 startDt 截断到当天
                day_rows = [x for x in rows if (x.get('startDt') or '').startswith(date_str)]
                all_records.extend(day_rows)
                # 本页最晚记录已早于当天 → 后续页都是更早的历史，提前结束
                last = rows[-1].get('startDt', '')
                if last and not last.startswith(date_str):
                    break
                if len(rows) < page_size:
                    break
            except Exception as e:
                print(f'[XRayClient] 拉取失败 machine={machine_id} page={page}: {e}')
                break

        return all_records


class WatcherClient(BaseClient):
    """
    Watcher (abdavinci) 报表数据源
    通过POST请求调用 share/data API获取widget数据
    """

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or WATCHER_CONFIG
        # watcher的session已经包含SESSION=前缀
        session_value = cfg['session']
        if session_value.startswith('SESSION='):
            session_value = session_value[len('SESSION='):]
        super().__init__(cfg['base_url'], session_value)
        self.filter_field = cfg['filter_field']
        self.filter_value = cfg['filter_value']
        self.widgets = {
            'godzilla_detail': cfg.get('godzilla_detail', {}),
            'mirror_detail': cfg.get('mirror_detail', {}),
            'attendance_detail': cfg.get('attendance_detail', {}),
            'photo_detail': cfg.get('photo_detail', {}),
            'flaw_photo': cfg.get('flaw_photo', {}),
            'flaw_photo_detail': cfg.get('flaw_photo_detail', {}),
        }

    def _get_headers(self) -> Dict[str, str]:
        """Watcher需要Content-Type"""
        headers = super()._get_headers()
        headers['Content-Type'] = 'application/json;charset=UTF-8'
        return headers

    def check_session_valid(self) -> bool:
        """检查session是否有效"""
        # 用一个简单的widget测试
        widget = self.widgets.get('godzilla_detail')
        if not widget:
            return False
        today = date.today().strftime('%Y-%m-%d')
        result = self.fetch_widget_data('godzilla_detail', today, today)
        return result is not None and len(result) > 0

    def auto_relogin(self) -> bool:
        """Watcher(abdavinci)专用：abdavinci失效返回417而非302，无法走BaseClient的302触发流程，这里强制CAS登录"""
        try:
            import re, requests
            from urllib.parse import quote
            USERNAME = os.environ.get('AIHUISHOU_CAS_USERNAME', '')
            PASSWORD = os.environ.get('AIHUISHOU_CAS_PASSWORD', '')
            CAS_URL = 'https://sso.aihuishou.com/cas/login'
            s = requests.Session(); s.verify = False
            s.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            })
            service = self.base_url
            r0 = s.get(CAS_URL, params={'service': service}, allow_redirects=True, timeout=20)
            exec_match = re.search(r'name="execution"\s+value="([^"]+)"', r0.text)
            csrf_match = re.search(r'name="_csrf"\s+value="([^"]+)"', r0.text)
            execution = exec_match.group(1) if exec_match else 'e1s1'
            _csrf = csrf_match.group(1) if csrf_match else ''
            form = {'username': USERNAME, 'password': PASSWORD, 'execution': execution, '_eventId': 'submit'}
            if _csrf: form['_csrf'] = _csrf
            s.post(r0.url, data=form, allow_redirects=True, timeout=20)
            new_session = ''
            for c in s.cookies:
                if c.name == 'SESSION' and 'aihuishou' in c.domain:
                    new_session = c.value; break
            if not new_session:
                logger.error('Watcher relogin failed: no SESSION cookie')
                return False
            # 同步到请求session
            for c in s.cookies:
                self._s.cookies.set_cookie(c)
            self.session = new_session
            self.session_expired = False
            logger.info(f'Watcher auto relogin success: {new_session[:20]}...')
            return True
        except Exception as e:
            logger.error(f'Watcher relogin exception: {e}')
            return False

    def fetch_widget_data(self, widget_name: str, date_begin: str,
                          date_end: str, _retry: bool = False) -> Optional[List[Dict]]:
        """
        获取指定widget的数据
        :param widget_name: widget名称（godzilla_detail, mirror_detail, attendance_detail）
        :param date_begin: 开始日期 YYYY-MM-DD
        :param date_end: 结束日期 YYYY-MM-DD
        :return: 数据行列表
        """
        widget = self.widgets.get(widget_name)
        if not widget:
            logger.error(f'Watcher: widget {widget_name} not found')
            return None

        token = widget.get('share_token', '')
        if not token:
            logger.error(f'Watcher: widget {widget_name} has no share_token')
            return None

        # widget级别的base_url覆盖（如photo_detail用abdavinci域名）
        widget_base_url = widget.get('base_url', self.base_url)
        # widget级别的session覆盖
        widget_session = widget.get('session', self.session)
        url = f'{widget_base_url}/{token}'

        filter_field = widget.get('filter_field', self.filter_field)
        filter_value = widget.get('filter_value', self.filter_value)
        date_param_name = widget.get('date_param_name', 'date_begin')
        groups = widget.get('groups', [])
        aggregators = widget.get('aggregators', [])
        page_size = widget.get('page_size', 1000)
        dashboard_id = widget.get('dashboard_id', '')
        role_ids = widget.get('role_ids', '')

        # 多 filter 支持：优先用 widget 配置的 filters 列表，否则用单 filter_field/value
        widget_filters = widget.get('filters')
        if widget_filters:
            filters = list(widget_filters)
        else:
            filters = [f'"{filter_field}" in (\'{filter_value}\')']

        # 构造请求体
        payload = {
            'cache': False,
            'check': False,
            'dashboardId': dashboard_id,
            'dateTime': [],
            'expired': 300,
            'filterData': [],
            'filters': filters,
            'groups': groups,
            'headerFilters': [],
            'mode': 'chart',
            'needSum': False,
            'orders': [{'column': groups[0], 'direction': 'asc'}] if groups else [],
            'pageNo': 1,
            'pageSize': page_size,
            'params': [
                {'name': date_param_name, 'value': f"'{date_begin}'"},
                {'name': 'date_end', 'value': f"'{date_end}'"},
            ],
            'aggregators': aggregators,
            'roleIds': role_ids,
            'selectedChart': 1,
            'uniqueIdentifier': '',
            'widgetId': int(widget.get('widget_id', 0)) if widget.get('widget_id') else 0,
        }

        # 始终带上当前SESSION（self.session 可能已被CAS重登刷新）。
        # 注意：必须用 self._s.post 或显式 Cookie 头，否则 session cookie 不会随请求发送。
        # 拍照魔方等 widget 还需阿里云 WAF 的 acw_tc 挑战 cookie，否则返回 417。
        # 重试机制：每次尝试重新计算会话（写死的 widget 级 session 过期后，
        # 本次重登得到的 self.session 才能在重试中生效）；并对 417/WAF挑战、
        # CAS登录页、以及后端偶发 500（数据源类型=>null，StarRocks 抖动）
        # 做有限次重试，避免单点失败直接归零。
        max_attempts = 3
        data = None
        # WAF 的 acw_tc 挑战 cookie（短时效）。每次尝试用最新值；
        # 417 响应会下发明新的 acw_tc，捕获后用于后续重试。
        acw_tc_override = widget.get('acw_tc')
        for attempt in range(1, max_attempts + 1):
            widget_session = widget.get('session')
            if widget_session and widget_session.startswith('SESSION='):
                widget_session = widget_session[len('SESSION='):]
            if not widget_session:
                widget_session = self.session
            headers = self._get_headers()
            acw_tc = acw_tc_override
            if acw_tc:
                headers['Cookie'] = f'SESSION={widget_session}; acw_tc={acw_tc}'
            else:
                headers['Cookie'] = f'SESSION={widget_session}'
            try:
                resp = self._s.post(
                    url, json=payload, headers=headers,
                    timeout=self.timeout
                )
            except Exception as e:
                logger.warning(f'Watcher widget {widget_name} attempt {attempt} request exception: {e}')
                if attempt < max_attempts:
                    time.sleep(1)
                    continue
                return None

            # 每次响应都刷新 acw_tc（WAF 可能在 417 挑战时下发新 cookie）
            _fresh_tc = resp.cookies.get('acw_tc')
            if _fresh_tc:
                acw_tc_override = _fresh_tc

            if resp.status_code != 200:
                # 417 通常是会话过期/WAF挑战，尝试CAS重登后重试
                if resp.status_code == 417:
                    logger.warning(f'Watcher widget {widget_name} HTTP 417 (attempt {attempt}), auto relogin...')
                    self.auto_relogin()
                    if attempt < max_attempts:
                        time.sleep(1)
                        continue
                logger.error(f'Watcher widget {widget_name} HTTP {resp.status_code}')
                return None

            # 阿里云 WAF/CAS 在 SESSION 失效时可能返回 HTTP 200 + CAS 登录页 HTML
            # （而非 417），此时 resp.json() 会失败。提前检测，触发重登。
            _ct = resp.headers.get('Content-Type', '')
            _body_head = resp.text[:2000]
            if ('AHS CAS Login' in _body_head
                    or '<title>AHS CAS' in _body_head
                    or ('text/html' in _ct and 'login' in _body_head.lower())):
                logger.warning(f'Watcher widget {widget_name} returned CAS login page (attempt {attempt}), auto relogin...')
                self.auto_relogin()
                if attempt < max_attempts:
                    time.sleep(1)
                    continue
                logger.error(f'Watcher widget {widget_name} session expired (CAS login page)')
                return None

            try:
                data = resp.json()
            except Exception:
                logger.error(f'Watcher widget {widget_name} response is not JSON (status {resp.status_code})')
                return None

            # 检查返回码：非 200（如 500 数据源类型=>null）多为后端偶发抖动，重试
            header = data.get('header', {})
            if header.get('code') != 200:
                logger.warning(
                    f'Watcher widget {widget_name} backend code={header.get("code")} '
                    f'(attempt {attempt}), retrying...'
                )
                if attempt < max_attempts:
                    time.sleep(1)
                    continue
                logger.error(
                    f'Watcher widget {widget_name} error: '
                    f'code={header.get("code")}, msg={header.get("msg")}'
                )
                if header.get('code') in [401, 403] or '未登录' in str(header.get('msg', '')):
                    self.session_expired = True
                return None

            break  # 成功取到数据

        if data is None:
            logger.error(f'Watcher widget {widget_name} failed after {max_attempts} attempts')
            return None

        # 提取数据行
        rows = None
        payload_data = data.get('payload')
        if isinstance(payload_data, list):
            rows = payload_data
        elif isinstance(data.get('data'), dict):
            d = data['data']
            for key in ['rows', 'data', 'list', 'records']:
                if key in d and isinstance(d[key], list):
                    rows = d[key]
                    break
        elif isinstance(data.get('data'), list):
            rows = data['data']

        if rows is None:
            logger.warning(f'Watcher widget {widget_name}: no data rows found')
            return []

        logger.info(f'Watcher widget {widget_name}: fetched {len(rows)} rows')
        return rows

    def fetch_godzilla_detail(self, date_begin: str, date_end: str) -> List[Dict]:
        """获取哥斯拉明细（从watcher历史数据）"""
        rows = self.fetch_widget_data('godzilla_detail', date_begin, date_end)
        if not rows:
            return []
        return self._normalize_godzilla_rows(rows)

    def _normalize_godzilla_rows(self, rows: List[Dict]) -> List[Dict]:
        """标准化watcher返回的哥斯拉数据行"""
        fields = self.widgets.get('godzilla_detail', {}).get('fields', {})
        result = []
        for row in rows:
            # row可能是列表格式，需要根据列名映射
            # 也可能是字典格式
            if isinstance(row, dict):
                result.append({
                    'employee_no': str(row.get(fields.get('employee_no', ''), '')),
                    'employee_name': str(row.get(fields.get('employee_name', ''), '')),
                    'date': str(row.get(fields.get('date', ''), '')),
                    'duration_sec': int(float(row.get(fields.get('duration', ''), 0) or 0)),
                    'total_duration_sec': int(float(row.get(fields.get('total_duration', ''), 0) or 0)),
                    'delay_sec': int(float(row.get(fields.get('delay', ''), 0) or 0)),
                    'position_tag': str(row.get(fields.get('position_tag', ''), '')),
                    'service_standard': str(row.get(fields.get('service_standard', ''), '')),
                    'data_source': 'watcher_godzilla',
                })
        return result

    def fetch_photo_detail(self, date_begin: str, date_end: str) -> List[Dict]:
        """获取拍照明细（拍拍拍照补充数据源）"""
        rows = self.fetch_widget_data('photo_detail', date_begin, date_end)
        if not rows:
            return []
        return self._normalize_photo_detail_rows(rows)

    def fetch_flaw_photo(self, date_begin: str, date_end: str) -> int:
        """获取瑕疵图拍照完成量（widget 7757，体积指标）。

        返回当日瑕疵图拍照完成总量（台）。widget 仅按 创建日期/运营中心名称 聚合，
        无人员维度，因此只返回合计数字。
        """
        rows = self.fetch_widget_data('flaw_photo', date_begin, date_end)
        if not rows:
            return 0
        total = 0
        for row in rows:
            if isinstance(row, dict):
                # 聚合列名可能是 瑕疵图拍照完成量 / count / 瑕疵图拍照完成量_agg 等
                for key in ('瑕疵图拍照完成量', 'count', 'total', 'cnt', 'num'):
                    if key in row:
                        try:
                            total += int(float(row[key] or 0))
                        except (ValueError, TypeError):
                            pass
                        break
            elif isinstance(row, (int, float)):
                total += int(row)
        return total

    def _normalize_photo_detail_rows(self, rows: List[Dict]) -> List[Dict]:
        """标准化watcher返回的拍照明细数据行（适配拍照魔方 widget 3535）

        3535 返回行字段：拍照移交完成日期, 步骤一拍照方式, 服务标准, 步骤一处理人, 物品量, 拍照时长H, 步骤一时长
        - 步骤一处理人 → employee_name
        - 物品量 → count（台数）
        - 拍照时长H → duration_sec（小时，转秒）—— 注意：这是「部门间移交等待时长」，非操作耗时
        - 步骤一时长 → op_duration_sec（秒，真实拍照步骤操作耗时，单位已是秒）
        - 服务标准 → service_standard（拍照魔方对应 PJTPZ）
        - photo_method 固定为「拍照魔方」（数据源已按此过滤）
        """
        fields = self.widgets.get('photo_detail', {}).get('fields', {})
        result = []
        for row in rows:
            if isinstance(row, dict):
                # 护栏：Watcher 拍照数据只取「拍照魔方」；其余拍照方式(哥斯拉/光合/室检App等)
                # 由 GodzillaClient / GuangheClient 各自负责，严禁在此计入，避免与它们重复统计。
                raw_method = str(row.get(fields.get('photo_method_raw', '步骤一拍照方式'), '')).strip()
                if raw_method and raw_method != '拍照魔方':
                    continue
                emp_name = str(row.get(fields.get('employee_name', '步骤一处理人'), '')).strip()
                count_str = str(row.get(fields.get('count', '物品量'), 0) or 0)
                duration_str = str(row.get(fields.get('duration', '拍照时长H'), 0) or 0)
                op_dur_str = str(row.get(fields.get('op_duration', '步骤一时长'), 0) or 0)
                try:
                    count = int(float(count_str))
                except (ValueError, TypeError):
                    count = 0
                try:
                    # 拍照时长H 单位为小时，转换为秒（移交等待时长，非操作耗时）
                    duration_h = float(duration_str)
                    duration_sec = int(duration_h * 3600)
                except (ValueError, TypeError):
                    duration_sec = 0
                try:
                    # 步骤一时长 单位已是秒，直接使用（真实拍照步骤操作耗时）
                    op_duration_sec = int(float(op_dur_str))
                except (ValueError, TypeError):
                    op_duration_sec = 0
                # 首末台完成时间（用于纯拍照魔方人员工时跨度计算）
                ft_raw = row.get(fields.get('first_time', '步骤一完成时间_first'))
                lt_raw = row.get(fields.get('last_time', '步骤一完成时间_last'))
                first_time = _parse_dt(ft_raw)
                last_time = _parse_dt(lt_raw)
                result.append({
                    'employee_name': emp_name,
                    'date': str(row.get(fields.get('date', '拍照移交完成日期'), '')),
                    'count': count,
                    'duration_sec': duration_sec,
                    'op_duration_sec': op_duration_sec,
                    'service_standard': str(row.get(fields.get('service_standard', '服务标准'), '')),
                    'category': str(row.get(fields.get('category', ''), '')),
                    'photo_method': '拍照魔方',
                    'photo_count': count,
                    'first_time': first_time,
                    'last_time': last_time,
                    'data_source': 'watcher_photo_detail',
                })
        return result


# 注：原文件此处有一个重复的 XRayClient（CAS 登录实现，已删除）。
# 统一使用上方（820 行附近）唯一的 SESSION 认证实现。
