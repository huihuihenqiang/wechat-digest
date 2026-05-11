# WeChat Digest OpenClaw Plugin

This local OpenClaw plugin exposes the current project as fixed, one-shot tools.

Install it from the project root with a link install:

```powershell
openclaw plugins install -l .\openclaw-plugin\wechat-digest
openclaw gateway restart
openclaw plugins inspect wechat-digest --runtime --json
```

OpenClaw may block installation because this plugin uses Node `child_process.spawn`
to run the fixed local CLI. If you accept that local-only risk:

```powershell
openclaw plugins install -l .\openclaw-plugin\wechat-digest --dangerously-force-unsafe-install
```

The plugin claims private messages that start with `wd` and calls:

```powershell
.\.venv\Scripts\wechat-digest.exe handle <command>
```

Examples:

```text
wd 状态
wd 日报 昨天
wd 查 报名
wd 原文 m123
```

It does not open a shell and only runs fixed `wechat-digest` CLI arguments.
