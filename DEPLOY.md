# 成都辅助运营数据看板 — 云端部署说明

看板已从「依赖本机 + 手动刷新」改造为 **完全独立、云端自洽** 的形态：
- ✅ 所有数据源均为公网域名（wirelessgate.aihuishou.com / abdavinci.aihuishou.com / kdocs.cn / 飞书 SaaS），脱离本机可访问
- ✅ 缓存目录默认使用应用自身目录（`DASHBOARD_CACHE_DIR` 可覆盖），不再依赖本地 `efficiency-dashboard`
- ✅ 内置后台调度线程：**启动即刷新一次，之后每 5 分钟自动全量刷新**（`/api/refresh/all` 的逻辑）
- ✅ 纯 Flask + requests，依赖极简（仅 `Flask` + `requests`）

---

## 免费部署平台推荐（按省事程度）

### 1. Render.com（最推荐，免费）
- 免费 Web Service 层，支持 Python；每天有免费额度
- 15 分钟无访问会休眠（可用下方「保活」办法解决自动刷新）
- 步骤：
  1. 把 `my-dashboard/` 目录推到 GitHub 私有/公开仓库
  2. render.com → New → Web Service → 关联仓库
  3. Build Command: `pip install -r requirements.txt`
  4. Start Command: `python app.py`
  5. 端口填 `8080`
  6. 部署完成后获得 `https://xxx.onrender.com` 公网地址
  - **省事做法（推荐）**：浏览器直接打开 👉 `https://render.com/deploy?repo=https://github.com/Neo123-bug/efficiency-dashboard`
    会自动带好仓库，并套用仓库里 `render.yaml` 预填的构建/启动命令、区域（Singapore）、计划（Free），跳过控制台导航。
  - 注册 / GitHub 授权若遇白屏或 404：手动开 `https://dashboard.render.com`，或用**无痕窗口**重走 GitHub 授权（多半是登录态串了）。

### 2. Railway.app（免费 $5 额度，常驻不休眠）
- 关联 GitHub 仓库 → Deploy → 自动识别 Procfile
- 比 Render 更适合「常驻 + 高频定时刷新」

### 3. Fly.io（免费额度，容器化）
- `fly launch` 读取 Dockerfile → `fly deploy`
- 免费额度内可常驻

### 4. PythonAnywhere（免费，有限 CPU）
- 手动上传 + 配 WSGI，免费层每天有 CPU 时间限制

---

## 自动刷新的保活（针对 Render 休眠）

Render 免费层 15 分钟休眠后，后台线程会随进程暂停；下次访问时进程唤醒并立即刷新。
若希望「即使无人访问也每 5 分钟刷新」，在 Render 的 Cron 或外部服务（如 cron-job.org，
免费）定时 GET `https://你的域名/api/refresh/all` 即可触发刷新（POST 亦可，接口已支持）。

> 注意：自动刷新依赖各系统 SESSION / Cookie 有效。爱回收系 SESSION 有时效，
> 失效后刷新会失败（页面仍展示上次成功缓存）。代码已内置 CAS relogin，
> 若 SESSION 彻底过期需更新 config.py 中的 session 值。

---

## 环境变量（云端部署必填 —— 凭证已外置，不再硬编码）

⚠️ 代码已不再包含任何明文密钥。在 Render / Railway 等平台的 **Environment Variables** 中必须填写以下变量，否则飞书/爱回收数据无法抓取：

| 变量 | 说明 | 来源 |
|------|------|------|
| `GODZILLA_SESSION` | 哥斯拉 SESSION | 原 config.py |
| `MIRROR_SESSION` | 魔镜 SESSION | 原 config.py |
| `WATCHER_SESSION` | 拍照 Watcher SESSION（含 `SESSION=` 前缀） | 原 config.py |
| `FEISHU_APP_SECRET` | 飞书多维表格（人员管理）密钥 | 原 config.py |
| `FEISHU_TREND_APP_SECRET` | 飞书云文档（效率趋势）/ 邮件密钥 | 原 config.py |
| `AIHUISHOU_CAS_USERNAME` | 爱回收 CAS 登录账号 | 原 data_sources.py |
| `AIHUISHOU_CAS_PASSWORD` | 爱回收 CAS 登录密码 | 原 data_sources.py |
| `XRAY_SESSION` | X 光系统 SESSION（可选，失效需更新） | 原 config.py |
| `GUANGHE_SESSION` | 光合 SESSION（可选，逗号分隔多个） | 原 config.py |
| `DASHBOARD_CACHE_DIR` | 缓存目录（持久化挂载点） | 应用自身目录 |
| `DASHBOARD_NO_AUTO_REFRESH` | 设为 `1` 关闭内置自动刷新 | 不设置=开启 |

> 本地开发用同名 `.env` 文件（已 gitignore，不入库）提供这些变量，逻辑完全一致。

---

## 一键本地验证

```bash
pip install -r requirements.txt
python app.py
# 访问 http://127.0.0.1:8080
# 启动日志应出现：[自动刷新] 后台调度线程已启动，每5分钟全量刷新一次

---

## 飞书应用权限（云端自动刷新「飞书数据」必需）

代码已改为 `tenant_access_token`（应用身份）读取飞书，不再依赖本机 lark-cli 授权。
但应用需在飞书开放平台开通以下权限，云端才能真正刷到飞书数据。
（趋势/效率/业务数据无需额外权限，部署即刷。）

**Yami 应用**（`cli_aab8ee79953a5beb`，对应环境变量 `FEISHU_TREND_APP_SECRET`）需开通：

1. **邮件读取**（质量指标 / 其他数据指标）：
   - 权限：`mail:user_mailbox.message:readonly`（应用身份读邮件）
   - 开通入口：https://open.feishu.cn/app/cli_aab8ee79953a5beb/auth
   - 备注：应用身份读用户邮件通常需企业管理员审批；开通后质量指标云端即可自动刷新。
2. **待办多维表格**（飞书待办同步）：
   - 把待办多维表格（base token `ZKagbd0Fsa0sP2sKPZychRrMnMg`）在「协作者 / 添加应用」中授权给 Yami 应用，并开通 bitable 读权限。
   - 否则云端读待办会返回 `91403 Forbidden`，自动刷新不到（页面显示空待办）。

> 若暂不打算开这两处权限，云端部署后：趋势/效率/业务数据正常刷新，飞书质量指标与待办保持空（本机仍可用 lark-cli 回退）。

---

## 部署后安全收尾（建议做）

部署成功后，去 GitHub → 头像 → **Settings** → **Developer settings** → **Personal access tokens**，把用于推送代码的令牌 **Revoke** 掉。代码已推完，令牌留着有泄露风险；以后改代码重新生成一把即可。

---

## 常见问题

**Q：部署后页面打不开 / 数据空白？**
A：先看 Render 日志有没有报错。多半是 7 个环境变量漏填或填错（尤其 `WATCHER_SESSION` 的值**开头就带 `SESSION=`** 这 7 个字母，别漏）。

**Q：改了代码，云端多久更新？**
A：重新 push 到 GitHub，Render 自动重新部署（约 2~5 分钟）。

**Q：SESSION 会不会过期？**
A：源系统登录凭证可能定期失效，失效后对应数据会变空 / 报错。届时重新生成凭证、在 Render 环境变量里更新即可（告诉我，我帮你查新值）。

**Q：可以让 Agent 代劳部署吗？**
A：可以。你去 render.com 用邮箱注册（这步只能你做），进 **Account Settings → API Keys** 生成一把 `rnd_xxx` 发我，剩下的创建服务、填密钥、触发部署我用 Render API 帮你跑完。

