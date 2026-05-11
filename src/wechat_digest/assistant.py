from __future__ import annotations

import re
from dataclasses import dataclass

from .backfill import BackfillCollector
from .commands import AssistantCommand, parse_assistant_command
from .config import AppConfig
from .digest import split_message
from .models import StoredMessage
from .service import DigestService
from .storage import DigestStore
from .timeutils import app_timezone, parse_flexible_date
from .wechat import WeChatClient


@dataclass(frozen=True)
class AssistantResponse:
    content: str
    chunks: list[str]


class AssistantRuntime:
    def __init__(
        self,
        config: AppConfig,
        store: DigestStore,
        wechat_client: WeChatClient,
        service: DigestService,
    ) -> None:
        self.config = config
        self.store = store
        self.wechat_client = wechat_client
        self.service = service
        self.backfill = BackfillCollector(config, store, wechat_client)
        self.active_group: str | None = None

    def handle_text(self, text: str) -> AssistantResponse:
        command = parse_assistant_command(text, self.config.wechat.groups)
        content = self.handle_command(command)
        return AssistantResponse(content, split_message(content, self.config.wechat.message_chunk_size))

    def handle_command(self, command: AssistantCommand) -> str:
        if command.kind == "digest":
            return self._handle_digest(command)
        if command.kind == "search":
            return self._handle_search(command)
        if command.kind == "context":
            return self._handle_context(command)
        if command.kind == "status":
            return self._handle_status()
        if command.kind == "help":
            return _help_text()
        if command.kind == "group":
            return self._handle_group(command)
        if command.kind == "memory":
            return self._handle_memory(command)
        if command.kind == "schedule":
            return self._handle_schedule(command)
        return f"没有识别这个命令：{command.raw_text}\n\n{_help_text()}"

    def _handle_digest(self, command: AssistantCommand) -> str:
        if command.query:
            return (
                "日报参数不要混用。请只选一种：\n"
                "- wd 日报 昨天\n"
                "- wd 日报 今天\n"
                "- wd 日报 2026-05-10\n"
                "- wd 日报 50"
            )
        target_date = parse_flexible_date(command.date_text, self.config.digest.timezone, default_to_yesterday=False)
        max_scrolls = command.scroll_count or self.config.collection.backfill_max_scrolls
        groups = self._resolve_groups(command.group_name)
        backfill_result = self.backfill.collect_date(target_date, max_scrolls=max_scrolls, groups=groups)
        result = self.service.generate_daily_digest(target_date, dry_run=False, allow_fallback=True, send=False, groups=groups)

        parts = [f"已倒查 {target_date.isoformat()}"]
        parts.append(f"扫描 {backfill_result.scroll_count} 轮")
        parts.append(f"读取 {backfill_result.fetched} 条")
        parts.append(f"新增 {backfill_result.inserted} 条")
        if backfill_result.earliest_message_time:
            earliest_local = backfill_result.earliest_message_time.astimezone(
                app_timezone(self.config.digest.timezone)
            )
            parts.append(f"最早读到 {earliest_local.strftime('%H:%M')}")
        prefix = "，".join(parts) + "。\n\n"
        return prefix + result.content

    def _handle_search(self, command: AssistantCommand) -> str:
        query = (command.query or "").strip()
        if not query:
            return "请给我要搜索的关键词，例如：wd 查 报名 或 wd 查 群A deadline"
        groups = self._resolve_groups(command.group_name)
        messages = self.store.search_messages(query, groups=groups, limit=10)
        if not messages:
            scope = command.group_name or (self.active_group or "配置的群")
            return f"没有在{scope}里找到：{query}"
        lines = [f"# 搜索结果：{query}", ""]
        for message in messages:
            lines.append(_format_message_result_highlighted(message, self.config.digest.timezone, query))
            ctx = self.store.get_context(message.id, before=2, after=2)
            if ctx:
                for ctx_msg in ctx:
                    if ctx_msg.id == message.id:
                        continue
                    ctx_line = _format_context_line(ctx_msg, self.config.digest.timezone)
                    lines.append(f"    {'<<' if ctx_msg.id < message.id else '>>'} {ctx_line}")
                lines.append("")
        if messages:
            lines.append("---")
            lines.append("发送 wd 原文 m数字 查看上下文详情")
        return "\n".join(lines)

    def _handle_context(self, command: AssistantCommand) -> str:
        if command.message_id is None:
            return "请提供消息引用，例如：wd 原文 m123"
        messages = self.store.get_context(command.message_id)
        if not messages:
            return f"没有找到消息引用：m{command.message_id}"
        lines = [f"# 原文上下文 m{command.message_id}", ""]
        for message in messages:
            lines.append(_format_message_result(message, self.config.digest.timezone))
        return "\n".join(lines)

    def _handle_status(self) -> str:
        schedule = self.store.get_schedule()
        schedule_line = f"每天 {schedule['time']}" if schedule["enabled"] else "已关闭"
        current = self.active_group or self.store.get_state("active_group")
        if current:
            group_line = current
        else:
            group_line = "未设置"
        return "\n".join([
            "# 微信群情报助手",
            "",
            f"**当前群聊**：{group_line}",
            f"**定时日报**：{schedule_line}",
            f"**入库消息**：{self.store.count_messages()} 条",
            "",
            "---",
            "",
            "## 快速入门",
            "",
            "1. 发 `wd 群 你的群名` 设置要监控的群",
            "2. 发 `wd 定时 22:00` 开启每天自动日报",
            "3. 发 `wd 日报 昨天` 立即生成一份日报试试",
            "",
            "---",
            "",
            "## 所有命令",
            "",
            "### wd 群 `<群名>`",
            "设置或切换当前群聊。后续日报、搜索、记忆都针对此群。",
            "示例：`wd 群 相亲相爱一家`",
            "",
            "### wd 日报 `<日期>`",
            "按日期倒查消息并生成 AI 日报。",
            "日期格式：`今天` / `昨天` / `YYYY-MM-DD`",
            "示例：`wd 日报 昨天`、`wd 日报 2026-05-10`",
            "",
            "### wd 日报 `<数字>`",
            "用指定滚动轮数生成日报（轮数越多查得越远）。",
            "示例：`wd 日报 80` 用 80 轮滚动采集",
            "",
            "### wd 查 `<关键词> [群名]`",
            "在本地消息库中搜索关键词，自动显示上下文预览。",
            "示例：`wd 查 报名`、`wd 查 群A deadline`",
            "命中后用 `wd 原文 m数字` 查看完整上下文。",
            "",
            "### wd 原文 `<m编号>`",
            "查看某条消息的前后上下文。",
            "示例：`wd 原文 m123`、`wd 原文 123`",
            "",
            "### wd 记忆 `<日期>`",
            "基于当天消息生成结构化记忆（重要事项 / 长期线索 / 待办 / 人物 / 风险）。",
            "日期格式同日报。默认 `昨天`。",
            "示例：`wd 记忆`、`wd 记忆 2026-05-10`",
            "",
            "### wd 定时 `<HH:MM>`",
            "开启每天定时日报。到点自动采集、生成、发送。",
            "需要后台调度器正在运行（`start_wechat_digest.cmd`）。",
            "示例：`wd 定时 22:00`",
            "",
            "### wd 定时 `关`",
            "关闭定时日报。",
            "示例：`wd 定时 关`",
            "",
            "### wd 状态 / wd 帮助",
            "显示当前配置和本帮助。",
            "",
            "---",
            "",
            "**注意**：定时日报需要本机保持开机且 `start_wechat_digest.cmd` 运行中。",
            "一次性总结可双击 `fetch_once.cmd`，不依赖定时。",
        ])

    def _handle_group(self, command: AssistantCommand) -> str:
        if command.group_name:
            self.active_group = command.group_name
            self.store.set_state("active_group", command.group_name)
            return f"当前群聊：{command.group_name}\n后续所有命令（日报/查/记忆）都针对此群。"
        current = self.active_group or self.store.get_state("active_group")
        if current:
            self.active_group = current
            return f"当前群聊：{current}\n发送 wd 群 新群名 切换。"
        return "尚未设置当前群聊。发送 wd 群 群名 开始。"

    def _handle_memory(self, command: AssistantCommand) -> str:
        from .memory import MemoryManager

        target_date = parse_flexible_date(command.date_text, self.config.digest.timezone, default_to_yesterday=False)
        groups = self._resolve_groups(None)

        memory_mgr = MemoryManager(self.config, self.store, self.service.digest_generator.llm_client)
        return memory_mgr.generate_daily_memory(target_date, groups)

    def _handle_schedule(self, command: AssistantCommand) -> str:
        rest = (command.query or "").strip()
        if not rest:
            schedule = self.store.get_schedule()
            if schedule["enabled"]:
                return f"定时日报已开启：每天 {schedule['time']} 通过当前机器人会话发送。\n发送 wd 定时 关 关闭。"
            return "定时日报已关闭。\n发送 wd 定时 HH:MM 开启（如 wd 定时 22:00）。"
        lower = rest.lower()
        if lower in ("关", "停", "off", "disable", "stop"):
            self.store.set_schedule("", enabled=False)
            return "定时日报已关闭。"
        import re
        m = re.match(r"^(\d{1,2}):(\d{2})$", rest)
        if m:
            self.store.set_schedule(rest, enabled=True)
            scheduler_status = _check_or_start_scheduler()
            return (
                f"定时日报已开启：每天 {rest} 通过当前机器人会话发送。\n"
                f"{scheduler_status}"
            )
        return f"无效的时间格式：{rest}。请使用 HH:MM 格式（如 22:00），或发送 wd 定时 关 关闭。"

    def _resolve_groups(self, explicit_group: str | None) -> list[str]:
        if explicit_group:
            return [explicit_group]
        active = self.active_group or self.store.get_state("active_group")
        if active:
            self.active_group = active
            return [active]
        return list(self.config.wechat.groups)


def _format_message_result(message: StoredMessage, timezone_name: str) -> str:
    local_time = message.msg_time.astimezone(app_timezone(timezone_name)).strftime("%Y-%m-%d %H:%M")
    links = f" 链接：{', '.join(message.links)}" if message.links else ""
    return f"- [m{message.id}] {local_time} {message.group_name} / {message.sender}：{message.content}{links}"


def _format_message_result_highlighted(message: StoredMessage, timezone_name: str, query: str) -> str:
    local_time = message.msg_time.astimezone(app_timezone(timezone_name)).strftime("%Y-%m-%d %H:%M")
    content = message.content
    if query:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        content = pattern.sub(lambda m: f"**{m.group(0)}**", content)
    links = f" 链接：{', '.join(message.links)}" if message.links else ""
    return f"- [m{message.id}] {local_time} {message.group_name} / {message.sender}：{content}{links}"


def _format_context_line(message: StoredMessage, timezone_name: str) -> str:
    local_time = message.msg_time.astimezone(app_timezone(timezone_name)).strftime("%H:%M")
    content = message.content[:40] + "..." if len(message.content) > 40 else message.content
    return f"[m{message.id}] {local_time} {message.sender}：{content}"


def _check_or_start_scheduler() -> str:
    import os
    import subprocess
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent.parent
    pid_file = project_root / "data" / "digest-scheduler.pid"
    ps1 = project_root / "scripts" / "start_digest_scheduler.ps1"

    pid = None
    if pid_file.exists():
        try:
            pid_text = pid_file.read_text().strip()
            pid = int(pid_text)
        except (ValueError, OSError):
            pid = None

    if pid is not None:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0400, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return "调度器运行中（PID {}）。".format(pid)
        except Exception:
            pass

    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
            cwd=str(project_root),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return "调度器正在后台启动，请稍候...\n若 10 秒后仍未启动，请双击 start_wechat_digest.cmd。"
    except Exception as exc:
        return "无法自动启动调度器（{}）。请双击 start_wechat_digest.cmd 手动启动。".format(exc)


def _help_text() -> str:
    return "\n".join(
        [
            "# 可用命令（wd 前缀）",
            "",
            "- wd 日报 [昨天|今天|YYYY-MM-DD]：按日期倒查并生成日报",
            "- wd 日报 [数字]：用指定滚动轮数生成默认日期日报，如 wd 日报 50",
            "- wd 查 关键词 [群名]：搜索本地消息库，自动显示上下文",
            "- wd 群 [群名]：查看或切换激活的群（支持任意群名）",
            "- wd 记忆 [日期]：生成结构化记忆摘要",
        ]
    )
