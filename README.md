# 自媒体数据看板

这个项目会生成一个暗色创作者运营数据看板。当前页面支持 B 站、抖音、小红书三平台统一展示；默认使用本地 fixture / 缓存数据渲染，不需要账号、不需要 Cookie、不需要网络，适合第一次安全运行。开启实时模式后，它只尝试读取你自己的授权数据，并在失败时回退到缓存、手动导入或示例数据。抖音和小红书的数据源优先级是官方 API / OpenAPI、本人账号授权 Cookie、手动导入、不可用占位；未取得的字段显示 `--`，不会臆造接口、估算数据或绕过平台风控。

当前仓库名仍保留 `bilibili-dashboard`，但项目定位已经升级为单个创作者 IP 的三平台自媒体数据看板。结构说明见 [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md)，评论洞察需求计划见 [`docs/COMMENT_INSIGHTS_PLAN.md`](docs/COMMENT_INSIGHTS_PLAN.md)。

页面会把 NAS 更新节奏、平台数据质量和内容参考图分开展示。部署成功不等于每个平台数据都完整；例如 B 站详情接口失败时，页面仍可发布，但对应平台会显示“部分可用”。

## 本地快速开始

macOS / Linux：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py --fixture
open dashboard/output/index.html
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py --fixture
start dashboard/output/index.html
```

`--fixture` 模式不会读取任何真实凭据，也不会发起网络请求。它会用 `data/fixtures/sample_history.json` 生成 `data/history.json` 和 `dashboard/output/index.html`。

## 三平台统一看板

页面顶部会显示三张平台卡片：

- B 站：沿用现有创作中心 / 缓存数据，可展示粉丝、播放、点赞、收藏、评论、分享、投币、弹幕等字段。
- 抖音：优先读取官方 API / OpenAPI，其次读取你本人账号授权后台 Cookie 数据源，最后才使用手动导入。
- 小红书：优先读取官方开放平台、蒲公英、创作者后台授权接口，其次读取你本人账号授权后台 Cookie 数据源，最后才使用手动导入。

粉丝涨幅口径：

- `相比昨日的涨粉`：当前成功快照减昨日结束前最近一次可用粉丝快照；历史不足显示 `--`。
- `7日涨粉`：当前成功快照减 7 日前可用快照；历史不足显示 `--`。
- `30日涨粉`：当前成功快照减 30 日前可用快照；历史不足显示 `--`。

内容指标表按 `今日 / 昨日 / Δ / Δ%` 展示。平台只提供累计值时，项目会用 UTC+8 自然日边界和历史快照差值计算；历史不足、字段不可得或昨日值为 0 时显示 `--`，不会用估算值冒充真实值。

## 评论雷达

评论雷达已接入 B 站公开视频评论的小样本抓取。它会读取最新评论和高赞评论，按点赞、回复、发布时间和关键词筛出“需要回复 / 争议上升 / 选题机会 / 纠错提醒”等运营信号。

完整评论只写入本机或 NAS 的 `data/private/comments.json`，这个目录已被 Git 忽略。公开页面只展示脱敏后的短摘要、点赞数、回复数和评分，不展示用户名、原始评论 ID 或完整私有缓存。

本地手动试抓：

```bash
export ENABLE_COMMENT_INSIGHTS=1
python scripts/fetch_bilibili_comments.py
python main.py --cache --no-feishu --no-bark
```

评论模块不会改变 NAS 当前更新频率，也不会做全量高频抓取；接口异常、风控、验证码或评论区关闭时，评论模块会记录日志并让主看板继续生成。

## 抖音 / 小红书数据源

抖音和小红书支持四级数据来源，按顺序自动降级：

1. 官方 API / OpenAPI：配置平台授权令牌和官方数据地址后优先使用。
2. 授权后台 Cookie：你登录自己的后台后，把可返回 JSON 的汇总请求地址配置到 `DOUYIN_DATA_URL` / `XIAOHONGSHU_DATA_URL`；如果小红书汇总接口不含作品列表，把作品列表请求地址单独配置到 `XIAOHONGSHU_CONTENT_DATA_URL`。
3. 手动导入：如果官方 API 和后台 Cookie 都不可用，读取 `data/manual_platform_metrics.json`。
4. 不可用占位：没有可靠数据源时，页面显示 `-- / 暂不可用`。

项目只请求你显式配置的数据源，不自动发现接口、不自动登录、不绕过验证码、不破解签名、不抓取其他账号。如果遇到动态签名、验证码、权限不足或风控响应，会记录脱敏日志并降级。

本地官方 API 示例：

```bash
export DOUYIN_ACCESS_TOKEN='只放在本机环境变量里'
export DOUYIN_OPEN_ID='如官方接口需要则填写'
export DOUYIN_OFFICIAL_DATA_URL='你已获授权的官方 API 数据地址'
export XIAOHONGSHU_ACCESS_TOKEN='只放在本机环境变量里'
export XIAOHONGSHU_OPEN_ID='如官方接口需要则填写'
export XIAOHONGSHU_OFFICIAL_DATA_URL='你已获授权的官方 / 蒲公英 / 创作者接口数据地址'
python main.py --live --no-feishu
```

本地授权 Cookie 数据源示例：

```bash
export DOUYIN_COOKIE='只放在本机环境变量里'
export DOUYIN_DATA_URL='你自己后台里可返回 JSON 的数据接口地址'
export XIAOHONGSHU_COOKIE='只放在本机环境变量里'
export XIAOHONGSHU_DATA_URL='你自己后台里可返回 JSON 的汇总接口地址'
export XIAOHONGSHU_CONTENT_DATA_URL='可选：你自己后台里可返回 JSON 的作品列表接口地址'
python main.py --live --no-feishu
```

不要把官方令牌、Cookie、数据 URL 发到公开页面、提交记录、Issue、截图或日志里。

手动导入方式：

`data/manual_platform_metrics.json` 的每个平台至少应包含：

- `source`: 固定为 `manual_import`
- `accountId`: 平台账号标识
- `capturedAt`: 这批数据在后台看到或导出的时间
- `importedAt`: 导入本项目的时间
- `fans`: 当前粉丝数
- `metrics`: 累计或当前共有指标
- `customMetrics`: 平台定制累计或当前指标
- `sourceStatus`: 手动数据状态说明

页面会显示“数据源：手动导入”和最近手动更新时间，避免把手动数据伪装成实时数据。

常用字段：

- `fans`：当前粉丝数
- `growth.cycle`：手动导入时用于覆盖“相比昨日的涨粉”
- `growth.7d`：7 日涨粉
- `growth.30d`：30 日涨粉
- `today`：今日播放/阅读、点赞、收藏、评论、分享
- `yesterday`：昨日播放/阅读、点赞、收藏、评论、分享
- `customToday` / `customYesterday`：平台定制指标

填完后运行：

```bash
python main.py --no-feishu
```

程序会把汇总快照写进 `data/history.json`，把最近作品明细保存到 `latest_content`，刷新 `dashboard/output/index.html`。不要在这个文件里填写 Cookie、token、手机号、密码或 Bark key。

## 实时 Bilibili 模式

实时模式只用于你自己的 Bilibili 创作中心数据，不支持多账号抓取，也不支持抓取其他创作者的私有数据。项目不会绕过验证码、WBI 签名、风控、CSRF、访问频率限制或任何平台保护机制。

本地运行：

```bash
export ENABLE_BILIBILI_FETCH=1
export BILIBILI_COOKIE='只放在本机环境变量里'
python main.py --live
```

请不要把 `BILIBILI_COOKIE` 写入 README、Issue、提交记录、截图或日志。Cookie 可能过期或触发风控；遇到相关响应时程序会停止实时获取，并回退到缓存或 fixture 数据。不要高频运行实时模式。

## Feishu 多维表格同步

飞书同步是可选功能，缺少配置时会自动跳过，不会影响看板生成。

基本步骤：

1. 在飞书开放平台创建应用。
2. 给应用授予多维表格读写所需权限。
3. 在目标 Bitable 表中手动创建字段。
4. 设置环境变量或 GitHub Actions Secrets。

必需字段：

- `日期`
- `总粉丝数`
- `7日涨粉`
- `总播放量`
- `总点赞数`
- `视频数据JSON`

环境变量：

```bash
export FEISHU_APP_ID='...'
export FEISHU_APP_SECRET='...'
export FEISHU_BASE_APP_TOKEN='...'
export FEISHU_TABLE_ID='...'
export FEISHU_DATE_FORMAT='iso'
```

`FEISHU_DATE_FORMAT` 可选，支持 `iso` 或 `ms`，默认 `iso`。如果飞书字段是日期毫秒值，可设为 `ms`。

## GitHub Actions Secrets

在仓库的 Settings → Secrets and variables → Actions 中添加：

- `BILIBILI_COOKIE`
- `DOUYIN_COOKIE`，可选，抖音本人账号授权后台 Cookie
- `XIAOHONGSHU_COOKIE`，可选，小红书本人账号授权后台 Cookie
- `DOUYIN_ACCESS_TOKEN`，可选，抖音官方 API 授权令牌
- `DOUYIN_OPEN_ID`，可选，抖音官方 API 账号标识
- `DOUYIN_OFFICIAL_DATA_URL`，可选，抖音官方 API 数据地址
- `XIAOHONGSHU_ACCESS_TOKEN`，可选，小红书官方 / 蒲公英授权令牌
- `XIAOHONGSHU_OPEN_ID`，可选，小红书官方接口账号标识
- `XIAOHONGSHU_OFFICIAL_DATA_URL`，可选，小红书官方 / 蒲公英 / 创作者接口数据地址
- `DOUYIN_DATA_URL`，可选，抖音后台授权数据源地址
- `XIAOHONGSHU_DATA_URL`，可选，小红书后台授权数据源地址
- `XIAOHONGSHU_CONTENT_DATA_URL`，可选，小红书作品列表授权数据源地址；如果 `XIAOHONGSHU_DATA_URL` 只是账号汇总接口，需单独配置这个字段才能刷新最新作品明细
- `BARK_DEVICE_KEY`，可选，用于 iPhone Bark 推送
- `FEISHU_APP_ID`，可选
- `FEISHU_APP_SECRET`，可选
- `FEISHU_BASE_APP_TOKEN`，可选
- `FEISHU_TABLE_ID`，可选

如果需要设置非敏感配置，可在 Variables 中添加：

- `FEISHU_DATE_FORMAT`
- `BILIBILI_ACCOUNT_ID`
- `DOUYIN_ACCOUNT_ID`
- `XIAOHONGSHU_ACCOUNT_ID`
- `BARK_GROUP`
- `BARK_SOUND`
- `LOG_RETENTION_DAYS`
- `PLATFORM_CONTENT_LIMIT`

## GitHub Pages

到 Settings → Pages → Source 选择 `GitHub Actions`。不要选择 `main / dashboard/output`，因为工作流会使用 Pages Actions 部署 `dashboard/output`。

注意：GitHub Pages 输出可能公开可访问。如果看板包含不想公开的真实创作者数据，请保持仓库私有，或不要启用 Pages 发布。

## 自动更新工作流

`.github/workflows/daily_fetch.yml` 是云端兜底流程。当前主路径仍然是由 NAS 定时抓取和渲染，再推送到 GitHub；GitHub Pages 只负责部署静态页面。为了避免 NAS 停止推送后页面长期静默过期，GitHub Actions 会每 30 分钟醒一次，先检查公开数据时间；只有超过约 60 分钟没有更新时才抓取和发布。

需要刷新时，云端兜底或手动备用流程会：

- 安装依赖。
- 运行 `python main.py --live --snapshot-date yesterday`，把当天抓到的可用数据写入前一天日期，并刷新三平台统一看板。
- 运行测试。
- 提交更新后的 `data/history.json` 和 `dashboard/output/index.html`。
- 上传并部署 GitHub Pages。

`.github/workflows/pages_deploy.yml` 会在 NAS 推送 `dashboard/output/**` 后自动部署 GitHub Pages。

NAS 侧负责抓取和提交，GitHub 侧只负责静态部署。按仓库名推断，线上看板地址通常是：

```text
https://lazydog08.github.io/bilibili-dashboard/
```

`daily_fetch.yml` 当前的备用计划是 `12,42 * * * *`，GitHub Actions 的 `schedule` 使用 UTC。它不是替代 NAS 的主路径，而是一个看门狗：NAS 正常推送时会跳过平台抓取；NAS 计划任务、SSH 推送或本地锁异常导致页面超过 `DASHBOARD_CLOUD_STALE_MINUTES=60` 分钟未更新时，才由 GitHub Actions 接管刷新。

本地手动更新：

```bash
python main.py --live --no-feishu
```

如果只是使用缓存刷新页面：

```bash
python main.py --cache --no-feishu
```

缓存渲染不会把旧数据伪装成刚更新。页面会保留已有数据时间，并在浏览器端按当前时间判断是否已经超过更新阈值。

## NAS 每 30 分钟自动更新

项目已提供适合 NAS / Linux 定时任务调用的脚本。推荐由 NAS 每 30 分钟完成抓取、渲染和云端发布，GitHub Pages 只负责展示静态页面，不负责抓取平台数据。

```bash
scripts/nas_update_dashboard.sh
```

推荐做法：

1. 在 NAS 上用 Git 克隆 GitHub 仓库到固定目录，例如绿联 NAS 当前使用的 `/home/小黑/bilibili-dashboard`，或 `/volume1/docker/bilibili-dashboard`、`/root/bilibili-dashboard` 等 NAS 实际共享目录。
2. 复制 `data/secrets/dashboard.env.example` 到 `~/.config/bilibili-dashboard/dashboard.env`。
3. 只在这个仓库外部的 `dashboard.env` 里填写 Cookie、Bark device key、抖音 / 小红书授权接口等敏感配置。
4. 设置 `DASHBOARD_PUBLISH_DIR` 为 NAS Web 目录，例如 `/volume1/web/bilibili-dashboard`。
5. 设置 `DASHBOARD_CLOUD_REMOTE_URL=git@github.com:lazydog08/bilibili-dashboard.git`，并给 NAS 配置 GitHub SSH deploy key。脚本会在发现已有 `origin` 与该配置不一致时自动纠正为这个 SSH remote。
6. 保持 `DASHBOARD_GIT_PUSH=0`，统一通过 `nas_update_and_push_cloud.sh` 推送云端。
7. 在 NAS 的计划任务里每 30 分钟执行一次云端更新：

```bash
cd /path/to/bilibili-dashboard && DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH=1 ./scripts/nas_update_and_push_cloud.sh
```

Linux crontab 示例见 `scripts/nas_cron.example`：

```cron
*/30 * * * * cd '/path/to/bilibili-dashboard' && DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH=1 ./scripts/nas_update_and_push_cloud.sh >/dev/null 2>&1
```

绿联 NAS 如果普通用户写 crontab 失败，例如报 `/var/spool/cron/: mkstemp: Permission denied`，推荐把定时任务安装到 root crontab，并用 `su - 小黑 -c ...` 切回 NAS 用户执行项目：

```cron
*/30 * * * * /bin/su - '小黑' -c 'cd '\''/home/小黑/bilibili-dashboard'\'' && DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH=1 ./scripts/nas_update_and_push_cloud.sh >/dev/null 2>&1'
```

也可以在 root shell 中用脚本生成同样的 root-su crontab 块：

```bash
DASHBOARD_NAS_CRON_MODE=root-su DASHBOARD_NAS_RUN_AS_USER='小黑' DASHBOARD_REPO_DIR='/home/小黑/bilibili-dashboard' scripts/install_nas_hourly_cron.sh
```

如果 NAS 支持普通用户 `crontab`，可以在 NAS 仓库目录执行默认安装：

```bash
scripts/install_nas_hourly_cron.sh
```

`nas_update_dashboard.sh` 会：

- 按 `DASHBOARD_MODE` 抓取数据并重新生成页面。
- 默认在 NAS 流程中启用 `ENABLE_COMMENT_INSIGHTS=1`，抓取 B 站评论到 `data/private/comments.json`，再用缓存渲染模式把脱敏摘要写进静态页面。
- 把页面写到 `dashboard/output/index.html`。
- 如果配置了 `DASHBOARD_PUBLISH_DIR`，同步复制到 NAS Web 目录。
- 写入公开心跳 `data/nas_status.json`，并同步到 `dashboard/output/nas_status.json`，用于从 GitHub 仓库或 GitHub Pages 判断 NAS 最近一次是否真的跑过。
- 写入 `data/logs/nas-update.log`，不输出 Cookie、token 或 Bark key。

`nas_update_and_push_cloud.sh` 会：

- 校验并维护 Git remote；如果配置了 `DASHBOARD_CLOUD_REMOTE_URL`，已有 remote 被同步成 HTTPS 或其他地址时会自动 `set-url` 回配置值。
- 拉取远端最新状态，避免覆盖 GitHub 上的数据。
- 给整个云端推送流程加锁；如果上一次还没跑完，本次会跳过。锁会记录 PID，并在超过 `DASHBOARD_LOCK_MAX_AGE_SECONDS` 且 PID 已不存在时自动清理，避免 NAS 重启、断电或任务被杀后永久卡死。
- 默认按 `DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH=1` 先调用 `nas_update_dashboard.sh` 抓取并生成最新页面。
- 提交并推送 `data/history.json`、`data/nas_status.json`、`dashboard/output/index.html` 和 `dashboard/output/nas_status.json` 到 GitHub 仓库的 `main` 分支；如果推送时远端刚好有新提交，会同步后重试一次。
- 触发 `.github/workflows/pages_deploy.yml`，由 GitHub Pages 更新在线看板。

公开心跳可以直接检查：

```text
https://lazydog08.github.io/bilibili-dashboard/nas_status.json
```

它只包含运行时间、模式、渲染/评论状态、Git 分支和短 SHA，不包含 Cookie、token、评论私有缓存或原始平台响应。只要 NAS 定时任务正常推送，即使业务数据没有变化，这个文件也会刷新并形成一条 Git 提交记录。

云端推送主路径依赖 NAS 宿主机提供 `git` 和 SSH。部署前先检查：

```bash
command -v git
command -v ssh
```

如果 NAS 没有 Git，需要先通过 NAS 系统包、应用中心或对应固件的可用方式安装 Git/SSH 组件，再期待 `nas_update_and_push_cloud.sh` 推送 GitHub。不要把 Cookie、token 或 deploy key 写入仓库。

如果 NAS 环境是 iStoreOS / OpenWrt，系统里通常没有 Python，但有 Docker。此时可以先用 Docker 包装脚本本地生成页面：

```cron
*/30 * * * * /root/bilibili-dashboard/scripts/nas_docker_update.sh >/dev/null 2>&1
```

`nas_docker_update.sh` 会用 `python:3.11-slim` 容器运行项目，项目目录默认是 `/root/bilibili-dashboard`。它只负责本地生成页面，不负责推送 GitHub Pages。首次运行会拉取 Docker 镜像，后续每 30 分钟更新只复用本地镜像。Docker 镜像默认不包含 Git/SSH；当前云端发布推荐让宿主 NAS 执行 `nas_update_and_push_cloud.sh`。

脚本行为：

- 推荐 `DASHBOARD_MODE=bilibili-only`，会只更新 B 站真实数据，抖音 / 小红书沿用缓存或手动导入数据。
- 如果确实要拉取三平台实时数据，可改为 `DASHBOARD_MODE=live`。
- 如果缺少某个平台凭据，会自动降级到缓存、手动数据或 `--`，不会中断整个看板。
- 默认不跑测试，避免定时任务消耗 NAS 资源；需要时设置 `RUN_DASHBOARD_TESTS=1`。
- 默认启用评论雷达；如果想临时关闭评论抓取，设置 `ENABLE_COMMENT_FETCH=0`。如果想页面也不展示评论模块，设置 `ENABLE_COMMENT_INSIGHTS=0`。
- 默认不启用飞书；需要时设置 `ENABLE_FEISHU_SYNC=1` 并配置 `FEISHU_*`。
- Bark 未配置会跳过；配置 `BARK_DEVICE_KEY` 后每次更新会推送三平台摘要。
- `DASHBOARD_UPDATE_INTERVAL_MINUTES=30` 会让页面右上角“下次更新”按每 30 分钟显示。这个值只控制页面展示；真正执行频率由 NAS cron / 计划任务决定。
- `DASHBOARD_PAGE_REFRESH_SECONDS=1800` 会让已经打开的静态页面每 30 分钟自动刷新一次，从而看到 NAS 刚生成的新 HTML。
- `DASHBOARD_NAS_STATUS_ENABLED=1` 会写入公开心跳文件；默认开启，用来验证 NAS cron 是否真实运行。
- 静态页面会把数据时间写入 HTML，并在浏览器打开时重新计算新鲜度。超过约 3 个更新间隔后，顶部更新时间和“NAS 更新节奏”会自动降级为停更提示，即使旧 HTML 被反复刷新也不会继续显示绿色正常。

可选项：

- `DASHBOARD_PUBLISH_DIR=/volume1/web/bilibili-dashboard`：每次更新后把 `index.html` 复制到 NAS Web 目录，方便手机浏览。
- `DASHBOARD_CLOUD_REMOTE_URL=git@github.com:lazydog08/bilibili-dashboard.git`：NAS 目录不是 Git 仓库时，用这个远端初始化推送。
- `DASHBOARD_CLOUD_BRANCH=main`：推送到 GitHub 的分支。
- `DASHBOARD_GIT_PULL_BEFORE_PUSH=1`：推送前先拉取远端，避免覆盖云端数据。
- `DASHBOARD_LOCK_MAX_AGE_SECONDS=7200`：NAS 更新锁的最大存活时间。锁超时且 PID 不存在时会被自动清理。
- `DASHBOARD_CLOUD_UPDATE_BEFORE_PUSH=1`：云端发布任务每 30 分钟先抓取最新数据，再推送 GitHub Pages。
- `DASHBOARD_NAS_CRON_MODE=root-su`：安装 root crontab 时用 `su - $DASHBOARD_NAS_RUN_AS_USER -c ...` 切回 NAS 用户执行。
- `DASHBOARD_NAS_RUN_AS_USER=小黑`：root-su cron 模式下实际运行项目的 NAS 用户。
- `DASHBOARD_ENV_FILE=/path/to/dashboard.env`：指定仓库外部的真实配置文件；默认是 `~/.config/bilibili-dashboard/dashboard.env`。
- `DASHBOARD_UPDATE_INTERVAL_MINUTES=30`：页面显示下一次每 30 分钟刷新时间。
- `DASHBOARD_PAGE_REFRESH_SECONDS=1800`：页面自动刷新间隔；设置为 `0` 可关闭自动刷新。
- `DASHBOARD_NAS_STATUS_PATH=data/nas_status.json`：公开心跳在仓库内的源文件路径；保持默认即可。
- `DASHBOARD_NAS_RUNNER_ID=nas`：公开心跳里的运行器标识，可改为 `ugreen-nas` 这类不含隐私的名字。
- `DASHBOARD_MODE=bilibili-only`：只抓取 B 站，适合 NAS 本地看板低风险运行。
- `DASHBOARD_MODE=cache`：只用本地缓存刷新页面，不请求平台网络。
- `DASHBOARD_MODE=fixture`：只用示例数据测试脚本。
- `ENABLE_COMMENT_INSIGHTS=1`：渲染评论雷达；NAS 更新脚本默认开启。
- `ENABLE_COMMENT_FETCH=1`：NAS 更新脚本在主看板生成后抓取评论；设置为 `0` 可只展示已有评论缓存。
- `COMMENT_VIDEO_LIMIT=10`：每轮最多检查多少条 B 站视频评论。
- `COMMENT_RANKED_VIDEO_LIMIT=3`：每轮额外抓取高赞评论的视频数量。

如果不想公开真实数据，删除定时的 `nas_update_and_push_cloud.sh` 计划任务，改用 `nas_update_dashboard.sh` 本地输出即可。

## Bark 推送

Bark 是可选功能。未配置时更新流程仍会正常完成，并输出“Bark 未配置，跳过推送”。

环境变量：

```bash
export BARK_DEVICE_KEY='只放在本机或 GitHub Secret'
export BARK_GROUP='数据看板'
export BARK_SOUND='minuet'
```

推送正文会汇总 B 站、抖音、小红书三平台粉丝和涨粉情况。不要把 `BARK_DEVICE_KEY` 写入 README、Issue、提交记录、截图或日志。

## 常用命令

```bash
python main.py --fixture
python main.py --live
python main.py --no-feishu
python main.py --no-feishu --no-bark
python -m pytest
```

## 安全说明

- 不要公开 Cookie、飞书密钥或访问令牌。
- 不要公开 Bark device key。
- 真实配置文件放在 `~/.config/bilibili-dashboard/dashboard.env`，不要放在项目目录里。
- 不要提交原始 Bilibili API 响应。
- 不要使用本项目抓取其他创作者的私有数据。
- 不要使用本项目绕过抖音、小红书、B 站的验证码、登录、反爬或风控。
- 如果真实数据不适合公开，不要把生成页面发布到公开 GitHub Pages。
- 实时获取失败时，页面会继续使用缓存或 fixture，并显示警告。

## 故障排查

Cookie 过期或触发风控：重新获取 `BILIBILI_COOKIE`，或者改为手动、低频运行。程序看到相关响应会输出固定提示并停止实时获取。

API 结构变化：看板会尽量用兼容字段回退；如果关键字段缺失，会显示警告并以 0 作为安全默认值。

飞书权限不足：检查应用权限、表格授权、字段名是否完全一致，以及 `FEISHU_*` 配置是否存在。

GitHub Pages 部署失败：确认 Pages Source 是 `GitHub Actions`，并检查工作流是否拥有 `pages: write` 和 `id-token: write` 权限。

页面显示 fixture 而不是实时数据：检查 `ENABLE_BILIBILI_FETCH=1`、`BILIBILI_COOKIE` 是否存在，以及工作流 Secrets 是否配置到正确仓库。

GitHub Enterprise 不支持时区计划：使用上面的 UTC cron 方案。

抖音 / 小红书一直显示 `--`：检查官方 API 授权、后台 Cookie 数据源或 `data/manual_platform_metrics.json`。如果接口需要动态签名、验证码或权限未开放，项目会降级显示 `--`，不会用估算值填充。

Bark 未推送：检查 `BARK_DEVICE_KEY` 是否配置到环境变量或 GitHub Actions Secret；不要把 device key 写入仓库。
