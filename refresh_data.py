#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据刷新器：确保 8080 服务在线后，调用 /api/refresh/all 拉取当日全量业务数据。
由 Windows 任务计划程序在每日多个时间点调用（如 09:30/12:30/15:30/18:30/21:30）。
"""
import subprocess
import socket
import os
import sys
import time
import urllib.request
import urllib.error

PORT = 8080
PYTHON = r"C:/Program Files/Python311/python.exe"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(APP_DIR, "app.py")
LOG = os.path.join(APP_DIR, "refresh_data.log")
REFRESH_URL = f"http://127.0.0.1:{PORT}/api/refresh/all"


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="")


def port_listening(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def launch():
    # DETACHED_PROCESS：子进程脱离父进程，任务计划程序退出后仍存活
    subprocess.Popen(
        [PYTHON, APP],
        cwd=APP_DIR,
        stdout=open(os.path.join(APP_DIR, "flask_stdout.log"), "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=0x00000008,
    )


def ensure_service(max_wait: int = 40):
    """确保 8080 在线；不在则拉起，最多等待 max_wait 秒。"""
    if port_listening(PORT):
        return True
    log("8080 未监听，尝试拉起服务...")
    try:
        launch()
    except Exception as e:
        log(f"拉起失败: {e}")
        return False
    for _ in range(max_wait):
        time.sleep(1)
        if port_listening(PORT):
            log("服务已就绪")
            return True
    log("等待服务超时")
    return False


def do_refresh():
    req = urllib.request.Request(REFRESH_URL, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        body = r.read().decode("utf-8", errors="replace")
    return body


def log_refresh_result(body: str):
    try:
        d = json.loads(body)
        status = d.get("status")
        if status == "started":
            log("已触发后台刷新（异步执行中，数据稍后更新）")
        elif status == "skipped":
            log("刷新进行中，本次触发跳过")
        else:
            log(f"刷新返回: {body[:300]}")
    except Exception:
        log(f"刷新返回(非JSON): {body[:200]}")


def main():
    if not ensure_service():
        log("服务不可用，跳过本次刷新")
        return
    try:
        body = do_refresh()
        log_refresh_result(body)
    except urllib.error.HTTPError as e:
        log(f"刷新HTTP错误 {e.code}: {e.read().decode('utf-8','replace')[:300]}")
    except Exception as e:
        log(f"刷新异常: {e}")


if __name__ == "__main__":
    main()
