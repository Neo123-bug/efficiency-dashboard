@echo off
REM 成都辅助运营数据看板 一键打开
REM 1) 确保 8080 服务在线（看门狗会拉起未运行的进程）
"C:\Program Files\Python311\python.exe" "C:\Users\Administrator\WorkBuddy\2026-07-16-10-31-12\my-dashboard\watchdog.py"
REM 2) 等待服务绑定端口
timeout /t 6 /nobreak >nul
REM 3) 打开看板首页
start "" "http://127.0.0.1:8080/"
exit
