#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在桌面创建「成都辅助运营数据日报.lnk」，使用橙色自定义图标。"""
import os
from win32com.client import Dispatch

base = r"C:\Users\Administrator\WorkBuddy\2026-07-16-10-31-12\my-dashboard"
desktop = os.path.join(os.path.expanduser("~"), "Desktop")
lnk_path = os.path.join(desktop, "成都辅助运营数据日报.lnk")
bat = os.path.join(base, "launch_dashboard.bat")
ico = os.path.join(base, "dashboard_icon.ico")

if os.path.exists(lnk_path):
    os.remove(lnk_path)

shell = Dispatch("WScript.Shell")
sc = shell.CreateShortcut(lnk_path)
sc.TargetPath = bat
sc.WorkingDirectory = base
sc.Description = "打开成都辅助运营数据看板 (127.0.0.1:8080)"
sc.WindowStyle = 7  # 最小化运行，不弹黑窗口
sc.IconLocation = ico + ",0"
sc.Save()
print("已创建快捷方式:", lnk_path)
print("目标:", sc.TargetPath)
print("图标:", sc.IconLocation)
