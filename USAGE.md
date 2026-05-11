# WeChat Digest 使用说明

这份文档只讲日常怎么用。底层 CLI 还保留 `digest-now`、`digest-backfill`、`run` 等高级入口，但日常建议只记住两种执行方式：

1. 日常自动模式：启动后台服务，然后用微信里的 `wd` 命令配置群聊和定时日报。
2. 一次性总结模式：手动填群名和日期，临时倒查并生成一份 AI 总结。

退出和诊断作为辅助入口放在最后。

## 前置条件

- PC 微信已登录，并且主窗口能被桌面自动化操作。
- `.venv` 已安装好项目依赖。
- `.env` 里配置了可用的大模型接口：

```dotenv
OPENAI_BASE_URL=...
OPENAI_API_KEY=...
OPENAI_MODEL=...
```

- `config.yaml` 里至少有一个 `wechat.groups`，并且 `openclaw.enabled: true`。
- 如果要让结果以“机器人/对方发给我”的形式出现在左侧，OpenClaw Gateway 必须已启动，并且你要先给机器人发过一次 `wd 状态` 或其他 `wd` 命令，让系统记住回发会话。

## 执行方式一：日常自动模式

适合每天固定时间自动生成日报。

### 1. 启动

双击：

```text
start_wechat_digest.cmd
```

它会启动两件事：

- OpenClaw Gateway：负责接收/回发微信里的 `wd` 命令。
- 本地 digest scheduler：负责每天到点倒查群聊、调用大模型、再通过 OpenClaw 回发给你。

等待状态下不会主动操作微信窗口；只有到点采集时才会短暂切换微信窗口。

### 2. 在微信里配置当前群

给机器人私聊发送：

```text
wd 群 你的微信群名
```

例如：

```text
wd 群 相亲相爱一家
```

后续日报、搜索、记忆和定时任务都会优先使用这个当前群，而不是 `config.yaml` 里的默认群。

### 3. 设置每天定时

```text
wd 定时 22:00
```

关闭定时：

```text
wd 定时 关
```

查看状态：

```text
wd 状态
```

### 4. 日报命令规则

现在日报参数只保留两种，不混用：

```text
wd 日报 今天
wd 日报 昨天
wd 日报 2026-05-11
wd 日报 80
```

- `日报 日期`：按日期倒查。
- `日报 数字`：使用这个数字作为滚动轮数，日期仍按默认逻辑处理。
- 不支持 `wd 日报 今天 80` 这种混合写法。

## 执行方式二：一次性总结模式

适合临时查某个群今天、昨天或指定日期的消息，不改定时设置。

双击：

```text
fetch_once.cmd
```

它会让你输入：

- `Group chat name`：微信群名。
- `Date`：`today`、`yesterday` 或 `YYYY-MM-DD`。
- `Max scroll rounds`：向上倒查轮数，默认 `200`。
- `Delivery`：默认 `openclaw`。

推荐保持：

```text
Delivery: openclaw
```

这样结果会通过 OpenClaw 发回你最近一次 `wd` 私聊会话，显示效果更像“别人发给你”，不会变成你在文件传输助手里自己发给自己。

如果 OpenClaw 还没有回发目标，先给机器人发：

```text
wd 状态
```

再重新双击 `fetch_once.cmd`。

### 其他发送方式

如果只想在黑窗里看结果，不发微信：

```text
Delivery: print
```

如果 OpenClaw 不可用，想走桌面微信直接发送：

```text
Delivery: wechat
```

这时脚本会继续问 `Send to`，默认是 `文件传输助手`。这个模式可读性较差，只作为兜底。

### CLI 高级用法

```powershell
.\.venv\Scripts\wechat-digest.exe fetch-once --group "群名" --date today --max-scrolls 200 --delivery openclaw
```

只打印不发送：

```powershell
.\.venv\Scripts\wechat-digest.exe fetch-once --group "群名" --date yesterday --delivery print
```

## 退出

停止整个系统，包括 OpenClaw Gateway 和本地调度器：

```text
stop_wechat_digest.cmd
```

## 诊断

检查桌面微信是否能被自动化连接：

```powershell
.\.venv\Scripts\wechat-digest.exe --config config.yaml check-wechat
```

看后台调度器日志：

```text
logs/digest-scheduler.out.log
logs/digest-scheduler.err.log
```

看旧的一次性 Windows 计划任务日志：

```text
logs/scheduled.log
```

## 数据保存规则

本地 SQLite 数据库在：

```text
data/wechat_digest.sqlite3
```

其中：

- `messages`：保存采集到的群消息。
- `digests`：保存生成过的日报/一次性总结。
- `digests.scope`：区分不同群和不同模式，例如 `相亲相爱一家`、`once:相亲相爱一家`。
- `state`：保存当前群、定时时间、OpenClaw 回发会话等运行状态。

这个设计避免“同一天多个群/多个模式”互相覆盖。

## 建议的日常习惯

- 每天只需要保持 `start_wechat_digest.cmd` 启动过一次。
- 换群时发 `wd 群 群名`。
- 临时查当天所有消息时用 `fetch_once.cmd`，不要改定时配置。
- 如果结果没发回来，先发 `wd 状态`，再跑一次一次性总结。
- 如果微信窗口被自动化占用，双击 `stop_wechat_digest.cmd`。
