# 项目长期记忆

## 运维约定
- 8080 看板常驻：watchdog 每5分钟探测，端口未监听则自动拉起。
- **后台启动 app.py 必须 cd 到 my-dashboard**：`cd /c/Users/Administrator/WorkBuddy/2026-07-16-10-31-12/my-dashboard && C:/Program Files/Python311/python.exe app.py`。`run_in_background` 不继承 shell 的 cd，否则 cwd 落到 workspace 根 → 找不到 app.py。
- 重启前先 `taskkill /PID <旧PID> /F`；若看门狗恰好补拉会双监听，需清理成单实例（只保留一个 LISTENING）。

## 代码坑点
- 三数据源（拍照/自动化/APP）的 `staff_list` 元素**必须含 `rate` 字段（达成率%）**，`index()` 首页汇总与 Top10 排序都依赖 `s['rate']`。拍照 `_adapt_photo` 主口径是 `hourly_comprehensive_rate`，需同步映射到 `rate`（2026-07-19 第三十次修复即因此崩）。
- 拍照魔方（watcher 3535）widget 原始无首末台时间字段，需在 config 加 `min/max(步骤一完成时间)` 聚合；工时=首末跨度(>13:30 剔1h)，目标固定 245。
- 首页三大区块（效率达成/近7天/质量）来自飞书数据源，与本地业务数据（拍照/自动化/各组）是两套独立缓存，刷新端点不同（`/api/refresh` 刷飞书，`/api/photo/refresh` `/api/automation/refresh` 刷本地）。
