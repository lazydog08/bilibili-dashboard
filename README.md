# bilibili-dashboard

这个项目会生成一个暗色 B 站创作者数据看板。它默认使用本地 fixture 数据渲染，不需要账号、不需要 Cookie、不需要网络，适合第一次安全运行。开启实时模式后，它只尝试读取你自己的 Bilibili 创作中心数据，并在失败时回退到缓存或示例数据。

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
- `FEISHU_APP_ID`，可选
- `FEISHU_APP_SECRET`，可选
- `FEISHU_BASE_APP_TOKEN`，可选
- `FEISHU_TABLE_ID`，可选

如果需要设置飞书日期格式，可在 Variables 中添加：

- `FEISHU_DATE_FORMAT`

## GitHub Pages

到 Settings → Pages → Source 选择 `GitHub Actions`。不要选择 `main / dashboard/output`，因为工作流会使用 Pages Actions 部署 `dashboard/output`。

注意：GitHub Pages 输出可能公开可访问。如果看板包含不想公开的真实创作者数据，请保持仓库私有，或不要启用 Pages 发布。

## 自动更新工作流

`.github/workflows/daily_fetch.yml` 会：

- 每天北京时间 12:30 运行，也可手动触发。
- 安装依赖。
- 运行 `python main.py --live --snapshot-date yesterday`，把当天抓到的可用数据写入前一天日期。
- 运行测试。
- 提交更新后的 `data/history.json` 和 `dashboard/output/index.html`。
- 上传并部署 GitHub Pages。

如果你的 GitHub Enterprise 环境不支持 schedule 的 `timezone` 字段，把计划任务替换成 UTC cron：

```yaml
on:
  schedule:
    - cron: '30 4 * * *'
```

因为北京时间中午 12:30 是 UTC 04:30。

## 常用命令

```bash
python main.py --fixture
python main.py --live
python main.py --no-feishu
python -m pytest
```

## 安全说明

- 不要公开 Cookie、飞书密钥或访问令牌。
- 不要提交原始 Bilibili API 响应。
- 不要使用本项目抓取其他创作者的私有数据。
- 如果真实数据不适合公开，不要把生成页面发布到公开 GitHub Pages。
- 实时获取失败时，页面会继续使用缓存或 fixture，并显示警告。

## 故障排查

Cookie 过期或触发风控：重新获取 `BILIBILI_COOKIE`，或者改为手动、低频运行。程序看到相关响应会输出固定提示并停止实时获取。

API 结构变化：看板会尽量用兼容字段回退；如果关键字段缺失，会显示警告并以 0 作为安全默认值。

飞书权限不足：检查应用权限、表格授权、字段名是否完全一致，以及 `FEISHU_*` 配置是否存在。

GitHub Pages 部署失败：确认 Pages Source 是 `GitHub Actions`，并检查工作流是否拥有 `pages: write` 和 `id-token: write` 权限。

页面显示 fixture 而不是实时数据：检查 `ENABLE_BILIBILI_FETCH=1`、`BILIBILI_COOKIE` 是否存在，以及工作流 Secrets 是否配置到正确仓库。

GitHub Enterprise 不支持时区计划：使用上面的 UTC cron 方案。
