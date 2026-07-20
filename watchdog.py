#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
看门狗：检测 8080 端口是否存活，未监听则拉起 my-dashboard。
由 Windows 任务计划程序开机 + 每5分钟调用。
"""
import subprocess
import socket
import sys
import os

PORT = 8080
PYTHON = r"C:/Program Files/Python311/python.exe"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(APP_DIR, "app.py")
LOG = os.path.join(APP_DIR, "watchdog.log")


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
        creationflags=0x00000008,  # DETACHED_PROCESS
    )


def main():
    try:
        if not port_listening(PORT):
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"[watchdog] 8080 not listening, launching app...\n")
            launch()
        else:
            # 已存活，无需动作
            pass
    except Exception as e:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[watchdog] ERROR: {e}\n")


if __name__ == "__main__":
    main()
