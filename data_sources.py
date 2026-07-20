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

            page += 1

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

    def fetch_widget_data(self, widget_name: str, date_begin: str,
                          date_end: str) -> Optional[List[Dict]]:
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

        url = f'{self.base_url}/{token}'

        filter_field = widget.get('filter_field', self.filter_field)
        filter_value = widget.get('filter_value', self.filter_value)
        date_param_name = widget.get('date_param_name', 'date_begin')
        groups = widget.get('groups', [])
        aggregators = widget.get('aggregators', [])
        page_size = widget.get('page_size', 1000)
        dashboard_id = widget.get('dashboard_id', '')
        role_ids = widget.get('role_ids', '')

        # 构造请求体
        payload = {
            'cache': False,
            'check': False,
            'dashboardId': dashboard_id,
            'dateTime': [],
            'expired': 300,
            'filterData': [],
            'filters': [f'"{filter_field}" in (\'{filter_value}\')'],
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

        try:
            resp = requests.post(
                url, json=payload, headers=self._get_headers(),
                verify=False, timeout=self.timeout
            )
            if resp.status_code != 200:
                logger.error(f'Watcher widget {widget_name} HTTP {resp.status_code}')
                return None

            data = resp.json()

            # 检查返回码
            header = data.get('header', {})
            if header.get('code') != 200:
                logger.error(
                    f'Watcher widget {widget_name} error: '
                    f'code={header.get("code")}, msg={header.get("msg")}'
                )
                if header.get('code') in [401, 403] or '未登录' in str(header.get('msg', '')):
                    self.session_expired = True
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

        except requests.RequestException as e:
            logger.error(f'Watcher widget {widget_name} request error: {e}')
            return None
        except json.JSONDecodeError as e:
            logger.error(f'Watcher widget {widget_name} JSON parse error: {e}')
            return None

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
