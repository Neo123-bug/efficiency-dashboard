# -*- coding: utf-8 -*-
"""
飞书多维表格客户端 - 用于同步人员花名册数据
直接调用飞书开放平台API，不依赖lark-cli
"""
import json
import time
import logging
import requests
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class FeishuBitableClient:
    """飞书多维表格客户端"""

    def __init__(self, app_id: str, app_secret: str, base_url: str = "https://open.feishu.cn"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip('/')
        self._tenant_access_token = None
        self._token_expire_time = 0

    def _get_tenant_access_token(self) -> str:
        """获取租户访问令牌（带缓存）"""
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
                logger.info(f"[飞书] 获取tenant_access_token成功，有效期{data.get('expire', 7200)}s")
                return self._tenant_access_token
            else:
                logger.error(f"[飞书] 获取token失败: {data}")
                raise Exception(f"获取飞书token失败: {data.get('msg', '未知错误')}")
        except Exception as e:
            logger.error(f"[飞书] 获取token异常: {e}")
            raise

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_tenant_access_token()}",
            "Content-Type": "application/json; charset=utf-8"
        }

    def list_records(self, app_token: str, table_id: str,
                     page_size: int = 100, view_id: str = None) -> List[Dict]:
        """
        分页获取多维表格所有记录
        """
        all_records = []
        page_token = None
        url = f"{self.base_url}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"

        while True:
            params = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            if view_id:
                params["view_id"] = view_id

            try:
                resp = requests.get(url, headers=self._headers(), params=params, timeout=15)
                data = resp.json()

                if data.get('code') != 0:
                    logger.error(f"[飞书] 获取记录失败: {data}")
                    raise Exception(f"获取飞书记录失败: {data.get('msg', '未知错误')}")

                items = data.get('data', {}).get('items', [])
                all_records.extend(items)

                has_more = data.get('data', {}).get('has_more', False)
                page_token = data.get('data', {}).get('page_token')

                if not has_more or not page_token:
                    break

                logger.info(f"[飞书] 已获取{len(all_records)}条记录，继续翻页...")

            except Exception as e:
                logger.error(f"[飞书] 获取记录异常: {e}")
                raise

        logger.info(f"[飞书] 共获取{len(all_records)}条记录")
        return all_records

    def list_fields(self, app_token: str, table_id: str) -> List[Dict]:
        """获取表格字段列表"""
        url = f"{self.base_url}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        try:
            resp = requests.get(url, headers=self._headers(), params={"page_size": 100}, timeout=10)
            data = resp.json()
            if data.get('code') == 0:
                return data.get('data', {}).get('items', [])
            else:
                logger.error(f"[飞书] 获取字段失败: {data}")
                return []
        except Exception as e:
            logger.error(f"[飞书] 获取字段异常: {e}")
            return []


def sync_staff_from_feishu(config: dict, output_path: str) -> dict:
    """
    从飞书多维表格同步人员花名册
    config: {
        'app_id': '飞书应用ID',
        'app_secret': '飞书应用密钥',
        'base_token': '多维表格token',
        'table_id': '数据表ID',
        'view_id': '视图ID(可选)',
    }
    output_path: 输出json文件路径
    返回: {total, groups, source, last_update}
    """
    client = FeishuBitableClient(
        app_id=config['app_id'],
        app_secret=config['app_secret']
    )

    records = client.list_records(
        app_token=config['base_token'],
        table_id=config['table_id'],
        view_id=config.get('view_id')
    )

    # 岗位分组映射
    position_groups = {
        "APP": ["APP"],
        "哥斯拉": ["哥斯拉"],
        "魔镜": ["魔镜"],
        "Xray": ["xray", "Xray", "XRAY"],
        "充电移交": ["充电移交"],
        "B端拍照": ["B端拍照"],
        "C端拍照": ["C端拍照", "瑕疵图"],
        "隐私/信息维修": ["隐私", "信息维修"],
    }

    def get_dept_group(position_tag):
        if not position_tag:
            return "未分组"
        for group_name, tags in position_groups.items():
            for tag in tags:
                if tag.lower() == str(position_tag).lower():
                    return group_name
        return "未分组"

    # 转换记录为标准格式
    staff_list = []
    for rec in records:
        fields = rec.get('fields', {})

        # 提取字段值（兼容不同类型的字段）
        def get_field(name, default=''):
            val = fields.get(name, default)
            if isinstance(val, list):
                # 人员、多选等字段可能是list
                if val and isinstance(val[0], dict):
                    return val[0].get('text', val[0].get('name', str(val[0])))
                return str(val[0]) if val else default
            if val is None:
                return default
            return str(val)

        emp_no = get_field('工号', '')
        name = get_field('姓名', '')

        if not emp_no and not name:
            continue  # 跳过空行

        # 入职日期处理（日期字段是毫秒时间戳）
        hire_date_val = fields.get('入职日期', '')
        if isinstance(hire_date_val, (int, float)):
            from datetime import datetime
            hire_date = datetime.fromtimestamp(hire_date_val / 1000).strftime('%Y-%m-%d')
        elif isinstance(hire_date_val, list) and hire_date_val:
            ts = hire_date_val[0]
            if isinstance(ts, (int, float)):
                from datetime import datetime
                hire_date = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d')
            else:
                hire_date = str(ts)
        else:
            hire_date = str(hire_date_val) if hire_date_val else ''

        position_tag = get_field('岗位标签', '')
        dept_group = get_dept_group(position_tag)

        staff = {
            'employee_no': emp_no,
            'name': name,
            'employee_name': name,
            'hire_date': hire_date,
            'employee_type': get_field('员工类型', ''),
            'c2': get_field('C2', ''),
            'c3': get_field('C3', ''),
            'c4': get_field('C4', ''),
            'c5': get_field('C5', ''),
            'position_tag': position_tag,
            'dept_group': dept_group,
            'record_id': rec.get('record_id', ''),
        }
        staff_list.append(staff)

    # 按工号排序
    staff_list.sort(key=lambda x: x.get('employee_no', ''))

    # 按组统计
    groups = {}
    for s in staff_list:
        g = s['dept_group']
        if g not in groups:
            groups[g] = {'name': g, 'count': 0}
        groups[g]['count'] += 1

    order = ["APP", "哥斯拉", "魔镜", "Xray", "充电移交", "B端拍照", "C端拍照", "隐私/信息维修"]
    group_list = sorted(groups.values(), key=lambda x: order.index(x['name']) if x['name'] in order else 99)

    # 写入文件
    output = {
        "staff": staff_list,
        "groups": group_list,
        "total": len(staff_list),
        "last_update": time.strftime('%Y-%m-%d %H:%M:%S'),
        "source": f"飞书多维表格同步 ({config.get('base_token', '')[:12]}...)"
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info(f"[飞书同步] 完成：{len(staff_list)}人，{len(group_list)}个组 -> {output_path}")
    return output
