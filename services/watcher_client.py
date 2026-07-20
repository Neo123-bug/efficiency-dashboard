# -*- coding: utf-8 -*-
"""
Watcher (Davinci) 数据接入客户端
从 abdavinci.aihuishou.com 拉取所有 widget 的聚合数据
"""

import requests
import json
import os
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class WatcherClient:
    """Watcher Davinci 数据客户端"""

    BASE_URL = "https://abdavinci.aihuishou.com/api/v3/statistics/share/data"
    LOGIN_URL = "https://abdavinci.aihuishou.com/"
    CAS_URL = "https://sso.aihuishou.com/cas"

    USERNAME = os.environ.get('AIHUISHOU_CAS_USERNAME', '')
    PASSWORD = os.environ.get('AIHUISHOU_CAS_PASSWORD', '')

    # 成都筛选值
    CHENGDU_FILTER = "优检成都检测中心"

    def __init__(self, session_id=None, config_path=None):
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json;charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Expect": "",
        })
        self.session_id = session_id
        if session_id:
            self.session.cookies.set("SESSION", session_id, domain=".aihuishou.com")

        # 加载widget配置
        self.widgets = []
        if config_path and os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8-sig") as f:
                config = json.load(f)
            self.widgets = config.get("widgets", [])

        self._cache = {}
        self._cache_time = {}
        self.cache_ttl = 180  # 3分钟缓存

    def login(self):
        """CAS登录获取SESSION - 通过CAS标准登录流程"""
        try:
            import re
            from urllib.parse import quote
            
            print("[Watcher] 尝试CAS登录...")
            
            # 清除旧cookie
            self.session.cookies.clear()
            
            # 目标服务URL（abdavinci首页）
            service_url = "https://abdavinci.aihuishou.com/"
            
            # Step 1: 直接访问CAS登录页，带service参数
            login_url = f"{self.CAS_URL}/login?service={quote(service_url, safe='')}"
            resp = self.session.get(login_url, timeout=15, allow_redirects=True)
            print(f"[Watcher] CAS登录页状态: {resp.status_code}, URL: {resp.url[:80]}")
            
            if resp.status_code != 200:
                print(f"[Watcher] 无法访问CAS登录页")
                return False
            
            html = resp.text
            
            # 提取表单参数
            lt_match = re.search(r'name="lt"\s+value="([^"]+)"', html)
            exec_match = re.search(r'name="execution"\s+value="([^"]+)"', html)
            event_match = re.search(r'name="_eventId"\s+value="([^"]+)"', html)
            
            lt = lt_match.group(1) if lt_match else ""
            execution = exec_match.group(1) if exec_match else "e1s1"
            event_id = event_match.group(1) if event_match else "submit"
            
            if not execution:
                print("[Watcher] 未找到CAS表单参数，登录失败")
                return False
            
            # Step 2: 提交登录表单
            print(f"[Watcher] 提交登录表单 (execution: {execution[:30]}...)")
            form_data = {
                "username": self.USERNAME,
                "password": self.PASSWORD,
                "lt": lt,
                "execution": execution,
                "_eventId": event_id,
            }
            
            # 保存当前headers，登录过程中使用干净的headers（避免X-Requested-With等干扰ticket回调）
            saved_headers = self.session.headers.copy()
            self.session.headers.clear()
            self.session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            
            resp2 = self.session.post(
                login_url,
                data=form_data,
                timeout=15,
                allow_redirects=True,
                headers={
                    "Referer": login_url,
                    "Content-Type": "application/x-www-form-urlencoded",
                }
            )
            print(f"[Watcher] 登录后状态: {resp2.status_code}, URL: {resp2.url[:100]}")
            
            # 如果ticket回调返回了417，手动再访问一次首页确认session是否生效
            if resp2.status_code == 417 and "ticket=" in resp2.url:
                print("[Watcher] Ticket回调返回417，再次访问首页验证...")
                resp3 = self.session.get(service_url, timeout=15, allow_redirects=True)
                print(f"[Watcher] 再次访问首页状态: {resp3.status_code}, URL: {resp3.url[:80]}")
            
            # 恢复API请求的headers
            self.session.headers.clear()
            self.session.headers.update(saved_headers)
            
            # 检查是否拿到了abdavinci的SESSION cookie
            session_found = False
            for cookie in self.session.cookies:
                if cookie.name == "SESSION" and "aihuishou.com" in cookie.domain:
                    self.session_id = cookie.value
                    session_found = True
                    print(f"[Watcher] 登录成功，SESSION: {self.session_id[:20]}...")
                    break
            
            if not session_found:
                print("[Watcher] 未找到SESSION cookie，登录可能失败")
                # 打印所有cookie调试
                for cookie in self.session.cookies:
                    print(f"[Watcher] Cookie: {cookie.name} = {cookie.value[:20]}... domain={cookie.domain}")
                return False
            
            # Step 3: 验证session有效性（调用一次简单API）
            # 用第一个widget做测试
            if self.widgets:
                test_widget = self.widgets[0]
                test_token = test_widget.get("token", "")
                if test_token:
                    test_url = f"{self.BASE_URL}/{test_token}"
                    test_payload = {
                        "cache": False,
                        "dashboardId": test_widget.get("dashboardId", ""),
                        "dateTime": [],
                        "expired": 300,
                        "filterData": [],
                        "filters": ['"运营中心" in (\'优检成都检测中心\')'],
                        "groups": ["统计日期"],
                        "headerFilters": [],
                        "mode": "chart",
                        "needSum": False,
                        "orders": [{"column": "统计日期", "direction": "asc"}],
                        "pageNo": 1,
                        "pageSize": 10,
                        "params": [
                            {"value": "'2026-07-10'", "name": "date_begin"},
                            {"value": "'2026-07-10'", "name": "date_end"}
                        ],
                        "roleIds": test_widget.get("roleIds", "366BE0BD5EA97D3B82AD54E7159C296C"),
                        "selectedChart": 1,
                        "uniqueIdentifier": "login_test",
                        "widgetId": test_widget.get("widgetId", 0),
                    }
                    try:
                        test_resp = self.session.post(test_url, json=test_payload, timeout=15)
                        if test_resp.status_code == 200:
                            print(f"[Watcher] Session验证通过，API正常返回")
                            return True
                        else:
                            print(f"[Watcher] Session验证失败，API返回 {test_resp.status_code}")
                            return False
                    except Exception as e:
                        print(f"[Watcher] Session验证异常: {e}")
                        return False
            
            return session_found
            
        except Exception as e:
            print(f"[Watcher] 登录失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def fetch_widget_data(self, widget, date_str=None):
        """拉取单个widget的数据"""
        try:
            return self._fetch_widget_data_safe(widget, date_str)
        except Exception as e:
            widget_id = str(widget.get("widgetId", "unknown")) if isinstance(widget, dict) else "unknown"
            print(f"[Watcher] w{widget_id} 拉取异常: {e}")
            return {"error": str(e), "widgetId": widget_id}

    def _fetch_widget_data_safe(self, widget, date_str=None):
        """安全拉取单个widget（内部方法，外层有try-except包裹）"""
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        if not isinstance(widget, dict):
            return {"error": "widget配置格式错误", "widgetId": ""}

        widget_id = str(widget.get("widgetId", ""))
        token = widget.get("token", "")
        filter_field = widget.get("filterField", "运营中心")
        filter_value = widget.get("filterValue", self.CHENGDU_FILTER)
        group_field = widget.get("groupField", "统计日期")
        dashboard_id = widget.get("dashboardId", "")

        # 缓存检查
        cache_key = f"{widget_id}_{date_str}"
        if cache_key in self._cache:
            if time.time() - self._cache_time.get(cache_key, 0) < self.cache_ttl:
                return self._cache[cache_key]

        url = f"{self.BASE_URL}/{token}"

        # 构造filters
        filters = [f'"{filter_field}" in (' + f"'{filter_value}'" + ')']

        # 额外过滤条件
        extra_filters = widget.get("extraFilters")
        if isinstance(extra_filters, list):
            for ef in extra_filters:
                if isinstance(ef, dict):
                    ef_field = ef.get("field", "")
                    ef_value = ef.get("value", "")
                    extra_filter = f'"{ef_field}" in (' + f"'{ef_value}'" + ')'
                    filters.append(extra_filter)

        # 日期参数
        date_param_name = "date_begin"
        date_param = widget.get("dateParam")
        if isinstance(date_param, list) and len(date_param) > 0:
            first_item = date_param[0]
            if isinstance(first_item, dict):
                date_param_name = first_item.get("name", "date_begin")
            elif isinstance(first_item, str):
                date_param_name = first_item

        params = [
            {"name": date_param_name, "value": f"'{date_str}'"},
            {"name": "date_end", "value": f"'{date_str}'"}
        ]

        # roleIds
        role_ids = widget.get("roleIds", "366BE0BD5EA97D3B82AD54E7159C296C")

        import uuid
        payload = {
            "cache": False,
            "check": False,
            "dashboardId": dashboard_id,
            "dateTime": [],
            "expired": 300,
            "filterData": [],
            "filters": filters,
            "groups": [group_field] if isinstance(group_field, str) else (group_field if isinstance(group_field, list) else ["统计日期"]),
            "headerFilters": [],
            "mode": "chart",
            "needSum": False,
            "orders": [{"column": group_field if isinstance(group_field, str) else "统计日期", "direction": "asc"}],
            "pageNo": 1,
            "pageSize": 1000,
            "params": params,
            "roleIds": role_ids,
            "selectedChart": 1,
            "uniqueIdentifier": str(uuid.uuid4()),
            "widgetId": int(widget_id) if widget_id.isdigit() else widget_id,
        }

        try:
            resp = self.session.post(url, json=payload, timeout=30)

            # 如果未登录/SESSION过期，重新登录（417=Expectation Failed 也是登录过期的表现）
            need_relogin = resp.status_code in (401, 403, 302, 417) or "login" in resp.url or "cas" in resp.url.lower()
            if need_relogin:
                print(f"[Watcher] w{widget_id} 需要重新登录 (HTTP {resp.status_code})")
                if self.login():
                    resp = self.session.post(url, json=payload, timeout=30)
                else:
                    return {"error": "登录失败", "widgetId": widget_id, "status": resp.status_code}

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    # 确保返回的是dict
                    if not isinstance(data, dict):
                        data = {"raw": data, "result": {"data": []}}
                except ValueError:
                    data = {"error": "返回数据不是JSON格式", "raw": resp.text[:500]}
                self._cache[cache_key] = data
                self._cache_time[cache_key] = time.time()
                return data
            else:
                return {"error": f"HTTP {resp.status_code}", "widgetId": widget_id, "status": resp.status_code}

        except Exception as e:
            return {"error": str(e), "widgetId": widget_id}

    def fetch_all_widgets(self, date_str=None):
        """拉取所有widget的数据"""
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        results = {}
        for w in self.widgets:
            name = w.get("name", f"widget_{w.get('widgetId','unknown')}")
            widget_id = str(w.get("widgetId", ""))
            print(f"[Watcher] 正在拉取: {name} (w{widget_id})")
            data = self.fetch_widget_data(w, date_str)
            results[name] = {
                "widgetId": widget_id,
                "dashboardId": w.get("dashboardId", ""),
                "data": data,
                "fetch_time": datetime.now().isoformat(),
            }
            time.sleep(0.3)  # 避免请求过快

        return results

    def get_summary(self, date_str=None):
        """获取关键指标的汇总数据"""
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        all_data = self.fetch_all_widgets(date_str)
        summary = {}

        for name, info in all_data.items():
            if not isinstance(info, dict):
                summary[name] = {"error": "返回格式异常"}
                continue
            data = info.get("data", {})
            if not isinstance(data, dict):
                summary[name] = {"error": "数据格式异常", "raw_type": str(type(data))}
                continue
            if data.get("result"):
                result = data["result"]
                if isinstance(result, dict):
                    rows = result.get("data", [])
                    if rows and len(rows) > 0:
                        summary[name] = rows[0]
                    else:
                        summary[name] = None
                else:
                    summary[name] = None
            elif data.get("error"):
                summary[name] = {"error": data["error"]}
            else:
                summary[name] = {"error": "无数据或格式异常"}

        return summary

    def get_trend_data(self, days=7):
        """获取近N天的趋势数据"""
        all_dates = []
        for i in range(days - 1, -1, -1):
            d = datetime.now() - timedelta(days=i)
            all_dates.append(d.strftime("%Y-%m-%d"))

        trend = {}
        # 只拉取关键widget的趋势（避免请求太多）
        key_widgets = [w for w in self.widgets if any(
            kw in w.get("name", "") for kw in ["覆盖率", "趋势", "汇总", "核心指标"]
        )]

        for date_str in all_dates:
            for w in key_widgets:
                name = w.get("name", "")
                data = self.fetch_widget_data(w, date_str)
                if isinstance(data, dict) and data.get("result", {}).get("data"):
                    rows = data["result"]["data"]
                    if rows:
                        if name not in trend:
                            trend[name] = []
                        # 取第一行的数值列
                        row = rows[0]
                        val = None
                        for k, v in row.items():
                            if k not in [w.get("groupField", "统计日期")] and isinstance(v, (int, float)):
                                val = v
                                break
                        trend[name].append({"date": date_str, "value": val})
                time.sleep(0.2)

        return trend


if __name__ == "__main__":
    # 测试
    config = r"C:\watcher_probe\widgets_config.json"
    client = WatcherClient(
        session_id="ZTQ0YTk1YWItZTQyNC00MzkzLTgxZDYtMGE4MjNmNGFhMDhj",
        config_path=config
    )

    print("测试watcher连接...")
    result = client.fetch_widget_data(client.widgets[0])
    print(json.dumps(result, ensure_ascii=False, indent=2)[:500])
