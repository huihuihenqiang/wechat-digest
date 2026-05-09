# 微信群 AI 日报助手

把一天的微信群消息，压缩成一份可以行动的中文日报。

这个项目是一个运行在本机 Windows 环境里的微信群情报整理助手。它通过桌面微信自动化读取指定群聊的最新消息，将聊天记录沉淀到本地 SQLite 数据库，再调用 OpenAI 兼容模型生成结构化 Markdown 日报，最后把日报私发给指定接收人。它适合用来跟踪课程群、项目群、社群运营群、招聘机会群和各类高频信息流，帮你从刷屏里捞出真正重要的通知、资源、风险和待办。

> 本项目只使用桌面微信 UI 自动化，不做 hook、抓包、注入，也不会在群里自动回复。请在符合微信规则、组织规范和群成员隐私预期的前提下使用。

## 项目亮点

- **本地优先**：聊天消息默认只存放在本机 SQLite 数据库中，仓库不会包含你的真实配置、日志或消息数据。
- **高信噪比日报**：提示词强调“可行动、可追踪、可验证”，不是简单复述聊天记录。
- **结构化输出**：日报包含今日摘要、关键信息、机会与资源、决策与结论、待办、风险和重要原话。
- **可托管运行**：支持持续采集、定时日报、手动生成、dry-run 预览，适合接入 Windows 任务计划程序。
- **OpenAI 兼容接口**：可使用 OpenAI 或其他兼容 `/chat/completions` 的模型服务。

## 工作流

```text
桌面微信 -> wxauto4 读取群聊 -> 本地 SQLite 存储 -> LLM 生成日报 -> 私发到指定接收人
```

核心模块：

- `wechat_digest.wechat`：封装桌面微信自动化读写能力。
- `wechat_digest.storage`：负责消息、日报和发送状态的本地持久化。
- `wechat_digest.digest`：构造日报提示词并调用 OpenAI 兼容接口。
- `wechat_digest.service`：串联采集、清理、生成和发送流程。
- `wechat_digest.cli`：提供命令行入口。

## 环境要求

- Windows 10/11
- 64 位 Python 3.9 到 3.12
- PC 微信已登录，并保持主窗口可被自动化操作
- 一个 OpenAI 兼容模型接口

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
```

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

只生成并打印日报，不发送：

```powershell
wechat-digest digest-now --date 2026-05-08 --dry-run
```

持续采集并在 `digest.time` 到点后自动发送日报：

```powershell
wechat-digest run
```

项目也提供了 PowerShell 脚本：

```powershell
.\scripts\run_once.ps1
.\scripts\run_once.ps1 -DryRun
.\scripts\run_scheduled.ps1
```

## 日报结构

模型会尽量输出下面这些部分：

- **今日摘要**：3 到 6 条最重要的变化、机会、风险和待办。
- **关键信息**：按“群名 / 发送者：事项”记录可追踪信息。
- **机会与资源**：招聘、活动、报名、资料、链接、求助等。
- **决策与结论**：只记录群里明确形成的决定。
- **待办**：事项、负责人和截止时间。
- **风险与需要跟进**：未解决问题、潜在冲突和需要确认的信息。
- **重要原话**：保留少量关键短句，便于回查上下文。
- **被忽略内容**：说明哪些低价值内容被过滤。

## 隐私与安全

以下文件默认不会进入 Git 仓库：

- `.env`：模型接口地址、API Key 和模型名。
- `config.yaml`：你的真实群名、接收人和本地路径。
- `data/`：本地 SQLite 数据库，可能包含原始聊天记录。
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
