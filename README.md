# 微信群 AI 日报助手

日常使用请优先看 [USAGE.md](USAGE.md)。那里把入口收敛成“日常自动模式”和“一次性总结模式”，并列出了启动、退出、诊断和 OpenClaw 回发方式。

把一天的微信群消息，压缩成一份可以行动的中文日报。

飞书和钉钉都有类似的功能，自动总结群聊消息，但是微信由于各种限制没有这个功能呢，于是我就做这样一个项目。这个项目是一个运行在本机 Windows 环境里的微信群情报整理助手。它通过桌面微信自动化读取指定群聊的最新消息，将聊天记录沉淀到本地 SQLite 数据库，再调用 OpenAI 兼容模型生成结构化 Markdown 日报，最后把日报私发给指定接收人。它适合用来跟踪课程群、项目群、社群运营群、招聘机会群和各类高频信息流，帮你从刷屏里捞出真正重要的通知、资源、风险和待办。

> 本项目只使用桌面微信 UI 自动化，不做 hook、抓包、注入，也不会在群里自动回复。请在符合微信规则、组织规范和群成员隐私预期的前提下使用。

## 项目亮点

- **本地优先**：聊天消息默认只存放在本机 SQLite 数据库中，仓库不会包含你的真实配置、日志或消息数据。
- **高信噪比日报**：提示词强调“可行动、可追踪、可验证”，不是简单复述聊天记录。
- **结构化输出**：日报包含今日摘要、关键信息、机会与资源、决策与结论、待办、风险和重要原话。
- **可托管运行**：支持持续采集、定时日报、手动生成、dry-run 预览，适合接入 Windows 任务计划程序。
- **OpenAI 兼容接口**：可使用 OpenAI 或其他兼容 `/chat/completions` 的模型服务。

## 工作流

```text
按需倒查：桌面微信 -> wxauto4 倒查指定日期群聊 -> 本地 SQLite -> LLM 日报 -> 私发/返回命令会话
手机命令：手机微信私聊 -> OpenClaw Gateway/Weixin 插件 -> wechat-digest OpenClaw 插件 -> 本项目 CLI 执行一次 -> 原会话回复
```

核心模块：

- `wechat_digest.wechat`：封装桌面微信自动化读写能力。
- `wechat_digest.backfill`：负责一次性倒查指定日期窗口的群消息。
- `wechat_digest.commands`：解析 `/日报`、`/查`、`/原文`、`/状态` 等命令。
- `openclaw-plugin/wechat-digest`：OpenClaw 原生插件，拦截 `wd` 前缀并把本项目作为固定工具调用。
- `wechat_digest.openclaw`：旧的 assistant fallback 通道，仍可直连 OpenClaw Weixin 登录态。
- `wechat_digest.storage`：负责消息、日报和发送状态的本地持久化。
- `wechat_digest.digest`：构造日报提示词并调用 OpenAI 兼容接口。
- `wechat_digest.service`：串联采集、清理、生成和发送流程。
- `wechat_digest.cli`：提供命令行入口。

## 环境要求

- Windows 10/11
- 64 位 Python 3.9 到 3.12
- PC 微信已登录，并保持主窗口可被自动化操作
- 一个 OpenAI 兼容模型接口
- 可选：OpenClaw Gateway 与 Tencent `openclaw-weixin` 插件，用于手机端私聊命令入口

`wxauto4` 目前不支持 32 位 Python，也不支持 Python 3.13。如果安装时报 `No matching distribution found for wxauto4`，通常是 Python 版本或位数不匹配。

## 安装

推荐使用 `uv` 创建 64 位 Python 3.12 虚拟环境：

```powershell
uv venv --python 3.12 .venv
uv pip install --python .\.venv\Scripts\python.exe -e .
```

也可以使用标准 `venv`：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e .
```

开发测试依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 配置

复制示例文件：

```powershell
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
```

编辑 `.env`：

```dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-openai-compatible-api-key
OPENAI_MODEL=gpt-4o-mini
```

编辑 `config.yaml`：

```yaml
wechat:
  groups:
    - "需要总结的微信群名"
  recipient: "文件传输助手"
  poll_interval_seconds: 60
  message_fetch_limit: 80
  message_chunk_size: 1800

collection:
  mode: "backfill"
  backfill_until: "previous_day_start"
  backfill_fetch_limit: 500
  backfill_max_scrolls: 20
  scroll_pause_seconds: 0.2

digest:
  time: "22:00"
  timezone: "Asia/Hong_Kong"
  retention_days: 30

storage:
  path: "data/wechat_digest.sqlite3"

llm:
  base_url: "${OPENAI_BASE_URL}"
  api_key: "${OPENAI_API_KEY}"
  model: "${OPENAI_MODEL}"
  timeout_seconds: 60
  max_input_messages: 500
  temperature: 0.2

openclaw:
  enabled: true
  channel: "openclaw-weixin"
  transport: "weixin-direct"
  account_id: ""
  base_dir: ""
  sync_path: "data/openclaw-weixin-direct-sync.json"
  command: "openclaw"
  args:
    - "mcp"
    - "serve"
  poll_timeout_seconds: 30
  request_timeout_seconds: 90
```

## OpenClaw 插件模式（推荐）

推荐长期使用这个模式：**OpenClaw 常驻，本项目不常驻**。OpenClaw 负责收微信私聊消息；本项目提供一个本地 OpenClaw 插件，看到 `wd` 前缀就调用固定 CLI 命令，执行完立即退出。

先安装并登录 OpenClaw 微信插件：

```powershell
npx -y @tencent-weixin/openclaw-weixin-cli install
openclaw channels login --channel openclaw-weixin
openclaw pairing list openclaw-weixin
```

把本项目插件以 link 方式安装进 OpenClaw。link 模式会保留到当前项目目录的引用，插件才能找到 `.venv` 和 `config.yaml`：

```powershell
openclaw plugins install -l .\openclaw-plugin\wechat-digest
openclaw plugins inspect wechat-digest --runtime --json
openclaw plugins list --enabled
```

这个插件会用 Node `child_process.spawn` 启动固定的 `wechat-digest` CLI，因此 OpenClaw 的危险代码扫描可能会拦截安装并提示 `Shell command execution detected`。这是它能把本项目当作一次性工具调用的原因；插件没有使用 shell，也不会拼接任意命令。如果你确认接受这个本地固定工具风险，用下面的 break-glass 参数安装：

```powershell
openclaw plugins install -l .\openclaw-plugin\wechat-digest --dangerously-force-unsafe-install
```

让 OpenClaw Gateway 后台常驻：

```powershell
openclaw gateway install
openclaw gateway start
openclaw gateway status --require-rpc
```

如果改过插件代码或配置，重启 Gateway：

```powershell
openclaw gateway restart
```

现在手机微信私聊里使用 `wd` 前缀：

```text
wd 状态
wd 日报 昨天
wd 倒查 昨天
wd 查 报名
wd 查 群A 报名
wd 原文 m123
```

`wd` 命令由插件的 `inbound_claim` hook 直接处理，通常不会进入 OpenClaw 大模型，所以不会因为这个命令触发 `Missing API key for provider "openai"`。但如果你还想让 OpenClaw 处理普通自然语言消息，仍然需要在 OpenClaw 里配置可用的 provider/API key。

OpenClaw 插件暴露的固定工具：

```text
wechat_digest_command
wechat_digest_status
wechat_digest_backfill
wechat_digest_search
wechat_digest_context
```

这些工具内部只会调用本项目固定 CLI，不开放任意 shell。倒查群聊时仍然需要 PC 微信在线，并且会短暂切换微信窗口；搜索、状态和原文查询只读本地 SQLite，不需要操作微信窗口。

### 备用 assistant 模式

如果你不想让 OpenClaw Gateway 接管消息，也可以继续用旧模式：

```powershell
wechat-digest assistant
```

旧模式会让本项目自己常驻监听 OpenClaw Weixin 登录态。当前推荐方案是 OpenClaw 插件模式，因为边界更清楚：OpenClaw 常驻收消息，本项目只作为一次性工具执行。

## 常用命令

检查桌面微信连接：

```powershell
wechat-digest check-wechat
```

给配置的接收人发送测试消息：

```powershell
wechat-digest send-test
```

采集一次最新消息，然后为今天生成日报并发送：

```powershell
wechat-digest digest-now --collect-once
```

倒查昨天群聊消息，生成日报并发送：

```powershell
wechat-digest digest-backfill
```

倒查指定日期并只预览，不发送：

```powershell
wechat-digest digest-backfill --date 2026-05-08 --dry-run
```

倒查指定日期、生成日报并打印给调用方，不通过桌面微信发送：

```powershell
wechat-digest digest-backfill --date 2026-05-08 --print
```

本地工具命令，适合 OpenClaw 插件或手动排错：

```powershell
wechat-digest status
wechat-digest search 报名
wechat-digest context m123
wechat-digest handle wd 查 报名
```

只生成并打印日报，不发送：

```powershell
wechat-digest digest-now --date 2026-05-08 --dry-run
```

持续采集并在 `digest.time` 到点后自动发送日报：

```powershell
wechat-digest run
```

`run` 是旧的持续轮询模式。更推荐长期使用 OpenClaw 插件模式接收手机命令，按需运行 `digest-backfill --print` 或让 Windows 任务计划程序每天运行一次 `digest-backfill`。

项目也提供了 PowerShell 脚本：

```powershell
.\scripts\run_once.ps1
.\scripts\run_once.ps1 -DryRun
.\scripts\run_scheduled.ps1
```

## 日报结构

模型会尽量输出下面这些部分：

- **今日摘要**：3 到 6 条最重要的变化、机会、风险和待办。
- **关键信息**：按“群名 / 发送者：事项 [m数字]”记录可追踪信息。
- **机会与资源**：招聘、活动、报名、资料、链接、求助等。
- **决策与结论**：只记录群里明确形成的决定。
- **待办**：事项、负责人和截止时间。
- **风险与需要跟进**：未解决问题、潜在冲突和需要确认的信息。
- **重要原话**：保留少量关键短句，便于回查上下文。
- **被忽略内容**：说明哪些低价值内容被过滤。

日报里的 `[m123]` 是本地消息引用。你可以发 `wd 原文 m123` 查看上下文，或用 `wd 查 关键词` 在本地消息库里搜索。图片、视频、文件等无法解析的媒体会以 `[图片：未解析]`、`[视频：未解析]`、`[文件：未解析]` 之类的占位进入库，不会让日报生成报错。

## 隐私与安全

以下文件默认不会进入 Git 仓库：

- `.env`：模型接口地址、API Key 和模型名。
- `config.yaml`：你的真实群名、接收人和本地路径。
- `data/`：本地 SQLite 数据库，可能包含原始聊天记录。
- `logs/`：本地运行日志，可能包含命令文本和排错信息。
- `wxauto_logs/`：桌面微信自动化日志，可能包含窗口名和已发送内容。
- `.venv/`、`.venv-*/`、`*.egg-info/`：本地环境和构建产物。

发布前建议再运行一次：

```powershell
git status --ignored -s
rg -n -i "api[_-]?key|secret|token|password|bearer|sk-" .
```

## 开发

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

只做语法检查：

```powershell
.\.venv\Scripts\python.exe -m py_compile src\wechat_digest\*.py
```

## 适用场景

- 每天需要追踪多个微信群，但不想反复翻聊天记录。
- 希望把通知、资源、报名入口、任务和风险统一沉淀。
- 需要一份能快速回看、能转发、能归档的中文日报。
- 想要保留“本地采集、本地存储、主动发送”的可控工作流。

## 免责声明

本项目用于个人信息整理和自动化辅助。使用者需要自行确认相关群聊内容的采集、存储、模型处理和转发方式符合适用规则与隐私要求。不要将未经授权的聊天记录、个人信息或敏感业务信息上传到公共仓库或第三方服务。
