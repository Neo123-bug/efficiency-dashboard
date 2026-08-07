# -*- coding: utf-8 -*-
"""
飞书邮件数据提取（通过 lark-cli 调用已授权用户身份）
从 APP执行/失误 和 重拍率 邮件中提取成都运营中心指标
"""
import json
import os
import re
import subprocess
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# lark-cli 实际路径（Windows 下使用 .cmd 入口）
LARK_CLI = os.environ.get(
    "LARK_CLI",
    r"C:\Users\Administrator\.workbuddy\binaries\node\cli-connector-packages\lark-cli.cmd"
)


def _run_lark_cli(args: List[str], timeout: int = 60) -> dict:
    """调用 lark-cli 并返回 JSON 结果"""
    cmd = [LARK_CLI] + args
    env = os.environ.copy()
    # 确保 PATH 包含 node 等依赖
    env["PATH"] = os.path.dirname(LARK_CLI) + os.pathsep + env.get("PATH", "")
    use_shell = LARK_CLI.endswith('.cmd')
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
            shell=use_shell,
        )
        # 关键修复：用字节读取后按 utf-8 容错解码，避免输出含非法字节时
        # 子进程解码线程抛 UnicodeDecodeError 导致整次抓取失败
        out = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
        err = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
        # lark-cli 可能把结果输出到 stdout 或 stderr
        text = out if out.strip().startswith("{") else err
        start = text.find("{")
        if start == -1:
            logger.warning(f"[lark-cli] 无 JSON 输出: {cmd}")
            return {}
        data = json.loads(text[start:])
        if not data.get("ok", True):
            # 不再静默吞错：明确记录授权/调用失败原因
            err_obj = data.get("error", {})
            logger.warning(
                f"[lark-cli] 调用失败(type={err_obj.get('type')}, "
                f"subtype={err_obj.get('subtype')}): {err_obj.get('message')}"
            )
        return data
    except Exception as e:
        logger.error(f"[lark-cli] 调用异常: {e}")
        return {}


def _search_mails(query: str, max_results: int = 7) -> List[dict]:
    """搜索邮件列表"""
    data = _run_lark_cli([
        "mail", "+triage",
        "--query", query,
        "--max", str(max_results),
        "--as", "user",
        "--format", "json",
    ])
    # +triage 直接返回顶层对象，messages 在根级
    return data.get("messages", []) or []


def _read_mail(message_id: str) -> Optional[dict]:
    """读取单封邮件详情"""
    data = _run_lark_cli([
        "mail", "+message",
        "--message-id", message_id,
        "--as", "user",
        "--format", "json",
    ])
    # +message 返回 {ok, identity, data: {...}}
    return data.get("data")


def _parse_percent(text: str) -> Optional[float]:
    """解析百分比，返回小数（0.9481）"""
    if not text:
        return None
    text = text.strip().replace(",", "")
    m = re.search(r"(\d+\.?\d*)\s*%", text)
    if m:
        try:
            return float(m.group(1)) / 100
        except:
            return None
    return None


def _parse_number(text: str) -> Optional[float]:
    """解析数值"""
    if not text:
        return None
    text = text.strip().replace(",", "")
    m = re.search(r"(\d+\.?\d*)", text)
    if m:
        try:
            return float(m.group(1))
        except:
            return None
    return None


def _extract_chengdu_row(text: str, city: str = "成都") -> List[str]:
    """
    从邮件正文中提取包含目标城市的整行数据。
    邮件是空格分隔的表格文本，按行拆分后找到城市所在行。
    """
    # 把多个空格合并为单个，方便 split
    lines = re.split(r"[\r\n]+", text)
    for line in lines:
        if city in line:
            return line.split()
    return []


def _extract_app_metrics(text: str, city: str = "成都") -> Dict[str, Dict]:
    """
    从 APP执行/失误 邮件正文中提取成都指标。
    返回：{metric_key: {'latest': val, 'avg_7d': val}}
    """
    result = {
        "app_execution": {"latest": None, "avg_7d": None},
        "app_coverage": {"latest": None, "avg_7d": None},
        "app_error": {"latest": None, "avg_7d": None},
    }

    def _parse_table_in_section(section_start: str, city: str):
        """
        在指定表格段中查找城市行，并返回数值列表。
        表格列之间用空格分隔，表头以 '战区 运营中心 ...' 开始。
        """
        pos = text.find(section_start)
        if pos == -1:
            return None
        # 截取该表格段（到下一个已知表格标题或末尾）
        next_sections = [
            "手机APP覆盖率", "笔记本APP执行率", "笔记本APP覆盖率",
            "耳机APP执行率", "耳机APP覆盖率", "固态硬盘APP覆盖率",
            "APP失误率-手机", "APP失误率-笔记本"
        ]
        end = len(text)
        for ns in next_sections:
            np = text.find(ns, pos + len(section_start))
            if np != -1 and np < end:
                end = np
        segment = text[pos:end]

        # 找到城市第一次出现的位置
        cp = segment.find(city)
        if cp == -1:
            return None
        # 向前找一段，向后找一段，然后按空格 split
        # 城市行前面是 战区，后面是多个数字/百分比
        start = max(0, cp - 30)
        # 向后取约 200 字符，足够包含一整行
        line_text = segment[start:cp + 200]
        # 把城市前面的内容按空格 split，找到城市所在 token 位置
        tokens = line_text.split()
        try:
            idx = tokens.index(city)
        except ValueError:
            return None
        # 城市行格式：战区 运营中心 昨日A 昨日B 昨日率 近七天A 近七天B 近七天率 ...
        # token 结构：[..., 战区, 成都, 数字, 数字, 百分比, 数字, 数字, 百分比, ...]
        # idx-1 = 战区， idx = 成都
        if idx + 7 >= len(tokens):
            return None
        return {
            "region": tokens[idx - 1],
            "latest_rate": _parse_percent(tokens[idx + 3]),
            "avg_7d_rate": _parse_percent(tokens[idx + 6]),
        }

    # 手机APP执行率
    row = _parse_table_in_section("手机APP执行率", city)
    if row:
        result["app_execution"]["latest"] = row["latest_rate"]
        result["app_execution"]["avg_7d"] = row["avg_7d_rate"]

    # 手机APP覆盖率
    row = _parse_table_in_section("手机APP覆盖率", city)
    if row:
        result["app_coverage"]["latest"] = row["latest_rate"]
        result["app_coverage"]["avg_7d"] = row["avg_7d_rate"]

    # APP失误率-手机
    row = _parse_table_in_section("APP失误率-手机", city)
    if row:
        result["app_error"]["latest"] = row["latest_rate"]
        result["app_error"]["avg_7d"] = row["avg_7d_rate"]

    return result


def _extract_reshoot_metrics(text: str, city: str = "成都") -> Dict[str, Dict]:
    """
    从 运中门店重拍率数据 邮件正文中提取成都指标。
    返回：{metric_key: {'latest': val, 'avg_7d': val}}
    """
    result = {
        "godzilla_reshoot_rate": {"latest": None, "avg_7d": None},
        "godzilla_reshoot_count": {"latest": None, "avg_7d": None},
        "reshoot_7d_rate": {"latest": None, "avg_7d": None},
    }

    # 重拍率数据表格列顺序：
    # 任务来源 战区 运营中心 前一日重拍量 前一日看图量 前一日重拍率 近7天重拍量 近7天看图量 近7天重拍率 ...
    # 成都行示例：运中 北区 成都 32 2386 1.34% 262 16273 1.61% ...
    pos = text.find("重拍率数据")
    if pos == -1:
        return result
    segment = text[pos:]
    cp = segment.find(city)
    if cp == -1:
        return result

    line_text = segment[max(0, cp - 30):cp + 200]
    tokens = line_text.split()
    try:
        idx = tokens.index(city)
    except ValueError:
        return result

    # token 结构：[..., 运中, 北区, 成都, 前一日重拍量, 前一日看图量, 前一日重拍率, 近7天重拍量, 近7天看图量, 近7天重拍率, ...]
    if idx + 7 >= len(tokens):
        return result

    latest_count = _parse_number(tokens[idx + 1])
    latest_rate = _parse_percent(tokens[idx + 3])
    d7_count = _parse_number(tokens[idx + 4])
    d7_rate = _parse_percent(tokens[idx + 6])

    if latest_count is not None:
        result["godzilla_reshoot_count"]["latest"] = latest_count
    if d7_count is not None:
        # 邮件「近7天重拍量」列为 7 天累计总量，直接取值（不做除以7的均值变换）
        result["godzilla_reshoot_count"]["avg_7d"] = d7_count
    if latest_rate is not None:
        result["godzilla_reshoot_rate"]["latest"] = latest_rate
    if d7_rate is not None:
        result["godzilla_reshoot_rate"]["avg_7d"] = d7_rate
        result["reshoot_7d_rate"]["latest"] = d7_rate
        result["reshoot_7d_rate"]["avg_7d"] = d7_rate

    return result


def get_quality_metrics_from_mail() -> Dict:
    """
    主入口：从飞书邮件中提取运营质量指标。
    返回与 feishu_mail_client.get_quality_metrics 兼容的结构。
    """
    result = {
        "latest_date": "",
        "items": {},
        "sources": {},
        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 1. APP 执行/失误邮件
    try:
        app_mails = _search_mails("APP执行、失误", max_results=7)
        if app_mails:
            latest = app_mails[0]
            mail = _read_mail(latest["message_id"])
            if mail:
                body = mail.get("body_plain_text", "")
                parsed = _extract_app_metrics(body)
                for k, v in parsed.items():
                    if v["latest"] is not None:
                        result["items"][k] = v
                result["sources"]["app_mail"] = latest.get("subject", "")
                # 尝试从 sent_time 解析日期
                sent = latest.get("date", "")
                if sent:
                    try:
                        result["latest_date"] = datetime.fromisoformat(sent.replace("Z", "+00:00")).strftime("%Y-%m-%d")
                    except:
                        pass

            # 7 天均值：仅对缺少 7 日均值的指标，取最近 7 封邮件的 latest 值平均
            # （APP 执行率/覆盖率/失误率、重拍率 邮件已自带 7 天列，应保留原值）
            if len(app_mails) > 1:
                sums = {"app_execution": [], "app_coverage": [], "app_error": []}
                for m in app_mails[:7]:
                    mail = _read_mail(m["message_id"])
                    if not mail:
                        continue
                    body = mail.get("body_plain_text", "")
                    p = _extract_app_metrics(body)
                    for k in sums:
                        if p[k]["latest"] is not None:
                            sums[k].append(p[k]["latest"])
                for k, vals in sums.items():
                    if vals and k in result["items"] and result["items"][k].get("avg_7d") is None:
                        result["items"][k]["avg_7d"] = round(sum(vals) / len(vals), 4)
    except Exception as e:
        logger.error(f"[邮件质量] APP 邮件提取失败: {e}", exc_info=True)

    # 2. 重拍率邮件
    try:
        reshoot_mails = _search_mails("运中门店重拍率数据", max_results=7)
        if reshoot_mails:
            latest = reshoot_mails[0]
            mail = _read_mail(latest["message_id"])
            if mail:
                body = mail.get("body_plain_text", "")
                parsed = _extract_reshoot_metrics(body)
                for k, v in parsed.items():
                    if v["latest"] is not None:
                        result["items"][k] = v
                result["sources"]["reshoot_mail"] = latest.get("subject", "")
                if not result["latest_date"]:
                    sent = latest.get("date", "")
                    if sent:
                        try:
                            result["latest_date"] = datetime.fromisoformat(sent.replace("Z", "+00:00")).strftime("%Y-%m-%d")
                        except:
                            pass

            # 7 天均值：仅对缺少 7 日均值的指标补充
            if len(reshoot_mails) > 1:
                sums = {
                    "godzilla_reshoot_rate": [],
                    "godzilla_reshoot_count": [],
                    "reshoot_7d_rate": [],
                }
                for m in reshoot_mails[:7]:
                    mail = _read_mail(m["message_id"])
                    if not mail:
                        continue
                    body = mail.get("body_plain_text", "")
                    p = _extract_reshoot_metrics(body)
                    for k in sums:
                        if p[k]["latest"] is not None:
                            sums[k].append(p[k]["latest"])
                for k, vals in sums.items():
                    if vals and k in result["items"] and result["items"][k].get("avg_7d") is None:
                        result["items"][k]["avg_7d"] = round(sum(vals) / len(vals), 4)
    except Exception as e:
        logger.error(f"[邮件质量] 重拍率邮件提取失败: {e}", exc_info=True)

    logger.info(f"[邮件质量] 提取到 {len(result['items'])} 个指标")
    return result


if __name__ == "__main__":
    # 本地测试
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(get_quality_metrics_from_mail(), ensure_ascii=False, indent=2))
