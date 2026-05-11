from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import sys
import time as time_module

from .assistant import AssistantRuntime
from .backfill import BackfillCollector
from .commands import AssistantCommand, parse_assistant_command
from .config import AppConfig, ConfigError, load_config
from .digest import DigestError, DigestGenerator, OpenAICompatibleClient, fallback_digest, split_message
from .openclaw import InboundCommand, OpenClawError, create_openclaw_command_channel
from .service import DigestService, digest_scope
from .storage import DigestStore
from .timeutils import app_timezone, day_window, parse_date, parse_flexible_date, should_run_digest
from .wechat import LazyWeChatClient, NoopWeChatClient, WeChatError, WxautoWeChatClient


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (ConfigError, WeChatError, DigestError, OpenClawError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Stopped.")
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wechat-digest", description="微信群 AI 日报助手")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML. Default: config.yaml")
    parser.add_argument("--env", default=".env", help="Path to .env file. Default: .env")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check-wechat", help="Check desktop WeChat connectivity")
    check_parser.set_defaults(func=cmd_check_wechat)

    status_parser = subparsers.add_parser("status", help="Print local digest tool status")
    status_parser.add_argument("--max-chars", type=int, default=0, help="Maximum characters to print; 0 means no cap")
    status_parser.set_defaults(func=cmd_status)

    search_parser = subparsers.add_parser("search", help="Search locally stored messages")
    search_parser.add_argument("query", nargs="+", help="Search keywords")
    search_parser.add_argument("--group", help="Restrict search to one configured group")
    search_parser.add_argument("--max-chars", type=int, default=0, help="Maximum characters to print; 0 means no cap")
    search_parser.set_defaults(func=cmd_search)

    context_parser = subparsers.add_parser("context", help="Print context around a message reference like m123")
    context_parser.add_argument("message_ref", help="Message reference, e.g. m123 or 123")
    context_parser.add_argument("--max-chars", type=int, default=0, help="Maximum characters to print; 0 means no cap")
    context_parser.set_defaults(func=cmd_context)

    handle_parser = subparsers.add_parser("handle", help="Handle one assistant command and print the reply text")
    handle_parser.add_argument("--max-chars", type=int, default=0, help="Maximum characters to print; 0 means no cap")
    handle_parser.add_argument("text", nargs="+", help="Command text, e.g. wd 日报 昨天")
    handle_parser.set_defaults(func=cmd_handle)

    send_parser = subparsers.add_parser("send-test", help="Send a test message to the configured recipient")
    send_parser.add_argument("--message", default="微信群 AI 日报助手测试消息。", help="Message to send")
    send_parser.set_defaults(func=cmd_send_test)

    digest_parser = subparsers.add_parser("digest-now", help="Generate a digest for a date")
    digest_parser.add_argument("--date", dest="date_text", help="Date in YYYY-MM-DD. Default: today")
    digest_parser.add_argument("--dry-run", action="store_true", help="Print only, do not send or save")
    digest_parser.add_argument("--collect-once", action="store_true", help="Collect latest messages before digesting")
    digest_parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="Use raw-message fallback digest if LLM is not configured or unavailable",
    )
    digest_parser.set_defaults(func=cmd_digest_now)

    backfill_parser = subparsers.add_parser("digest-backfill", help="Backfill a date window, then generate a digest")
    backfill_parser.add_argument("--date", dest="date_text", default="yesterday", help="Date, today, or yesterday. Default: yesterday")
    backfill_parser.add_argument("--dry-run", action="store_true", help="Print only, do not send or save")
    backfill_parser.add_argument("--print", dest="print_result", action="store_true", help="Print digest content instead of sending via desktop WeChat")
    backfill_parser.add_argument("--max-chars", type=int, default=0, help="Maximum digest characters to print; 0 means no cap")
    backfill_parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="Use raw-message fallback digest if LLM is not configured or unavailable",
    )
    backfill_parser.set_defaults(func=cmd_digest_backfill)

    fetch_once_parser = subparsers.add_parser(
        "fetch-once",
        help="Fetch one group's messages for a date, summarize with the digest prompt, and send it to yourself",
    )
    fetch_once_parser.add_argument("--group", required=True, help="WeChat group name to fetch")
    fetch_once_parser.add_argument("--date", dest="date_text", default="today", help="today, yesterday, or YYYY-MM-DD")
    fetch_once_parser.add_argument("--max-scrolls", type=int, default=None, help="Maximum older-message scroll rounds")
    fetch_once_parser.add_argument(
        "--delivery",
        choices=("openclaw", "wechat", "print"),
        default="openclaw",
        help="How to deliver the summary. Default: openclaw",
    )
    fetch_once_parser.add_argument(
        "--reply-session-key",
        default="",
        help="OpenClaw session key override. Default: last wd command session",
    )
    fetch_once_parser.add_argument(
        "--recipient",
        default="\u6587\u4ef6\u4f20\u8f93\u52a9\u624b",
        help="Desktop WeChat recipient for --delivery wechat. Default: File Transfer Assistant",
    )
    fetch_once_parser.add_argument("--print", dest="print_result", action="store_true", help="Also print the summary")
    fetch_once_parser.add_argument("--no-send", action="store_true", help="Do not send; print only")
    fetch_once_parser.add_argument("--max-chars", type=int, default=0, help="Maximum characters to print; 0 means no cap")
    fetch_once_parser.set_defaults(func=cmd_fetch_once)

    assistant_parser = subparsers.add_parser("assistant", help="Listen for OpenClaw WeChat commands and reply")
    assistant_parser.add_argument("--once", action="store_true", help="Wait for one command batch and exit")
    assistant_parser.set_defaults(func=cmd_assistant)

    run_parser = subparsers.add_parser("run", help="Continuously collect messages and send scheduled digest")
    run_parser.add_argument("--once", action="store_true", help="Collect once and exit")
    run_parser.set_defaults(func=cmd_run)

    scheduled_parser = subparsers.add_parser("scheduled-digest", help="Check schedule and send digest if it is time")
    scheduled_parser.set_defaults(func=cmd_scheduled_digest)
    return parser


def check_wechat_entry() -> int:
    return _entry("check-wechat")


def send_test_entry() -> int:
    return _entry("send-test")


def digest_now_entry() -> int:
    return _entry("digest-now")


def digest_backfill_entry() -> int:
    return _entry("digest-backfill")


def fetch_once_entry() -> int:
    return _entry("fetch-once")


def assistant_entry() -> int:
    return _entry("assistant")


def run_entry() -> int:
    return _entry("run")


def cmd_check_wechat(args: argparse.Namespace) -> int:
    load_config(args.config, args.env)
    client = WxautoWeChatClient()
    print(client.check_ready())
    return 0


def cmd_send_test(args: argparse.Namespace) -> int:
    config = load_config(args.config, args.env)
    client = WxautoWeChatClient()
    client.send_message(config.wechat.recipient, args.message)
    print(f"Sent test message to {config.wechat.recipient}.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config, service = _build_service(args, with_wechat=False)
    runtime = AssistantRuntime(config, service.store, service.wechat_client, service)
    response = runtime.handle_command(AssistantCommand("status", "status"))
    print(_cap_output(response, args.max_chars))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    config, service = _build_service(args, with_wechat=False)
    query = " ".join(args.query).strip()
    command = parse_assistant_command(f"查 {query}", config.wechat.groups)
    if args.group:
        command = AssistantCommand("search", query, group_name=args.group, query=query)
    runtime = AssistantRuntime(config, service.store, service.wechat_client, service)
    response = runtime.handle_command(command)
    print(_cap_output(response, args.max_chars))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    config, service = _build_service(args, with_wechat=False)
    message_id = _parse_message_ref(args.message_ref)
    runtime = AssistantRuntime(config, service.store, service.wechat_client, service)
    response = runtime.handle_command(AssistantCommand("context", args.message_ref, message_id=message_id))
    print(_cap_output(response, args.max_chars))
    return 0


def cmd_handle(args: argparse.Namespace) -> int:
    config = load_config(args.config, args.env)
    text = " ".join(args.text).strip()
    command = parse_assistant_command(text, config.wechat.groups)
    service = _create_service(config, with_wechat=command.kind == "digest")
    _remember_openclaw_reply_target(service.store)
    runtime = AssistantRuntime(config, service.store, service.wechat_client, service)
    response = runtime.handle_command(command)
    print(_cap_output(response, args.max_chars))
    return 0


def cmd_digest_now(args: argparse.Namespace) -> int:
    needs_wechat = args.collect_once or not args.dry_run
    config, service = _build_service(args, with_wechat=needs_wechat)
    if args.collect_once:
        inserted = service.collect_once()
        print(f"Collected {inserted} new messages before digest.")
    target_date = parse_date(args.date_text, config.digest.timezone)
    result = service.generate_daily_digest(
        target_date,
        dry_run=args.dry_run,
        allow_fallback=args.allow_fallback or args.dry_run,
    )
    if result.dry_run:
        print(result.content)
        print(f"\n---\nMessages included: {result.message_count}")
    else:
        print(f"Sent digest for {result.digest_date} to {config.wechat.recipient}; messages included: {result.message_count}.")
    return 0


def cmd_digest_backfill(args: argparse.Namespace) -> int:
    config, service = _build_service(args, with_wechat=not args.dry_run)
    target_date = parse_flexible_date(args.date_text, config.digest.timezone, default_to_yesterday=True)
    if not args.dry_run:
        collector = BackfillCollector(config, service.store, service.wechat_client)
        result = collector.collect_date(target_date)
        print(f"Backfilled {result.fetched} messages for {target_date}; inserted {result.inserted}.")
    digest_result = service.generate_daily_digest(
        target_date,
        dry_run=args.dry_run,
        allow_fallback=args.allow_fallback or args.dry_run or args.print_result,
        send=not args.print_result,
    )
    if digest_result.dry_run or args.print_result:
        print(_cap_output(digest_result.content, args.max_chars))
        print(f"\n---\nMessages included: {digest_result.message_count}")
    else:
        print(
            f"Sent digest for {digest_result.digest_date} to {config.wechat.recipient}; "
            f"messages included: {digest_result.message_count}."
        )
    return 0


def cmd_fetch_once(args: argparse.Namespace) -> int:
    config, service = _build_service(args, with_wechat=True)
    group_name = str(args.group or "").strip()
    if not group_name:
        raise ValueError("--group cannot be empty")
    target_date = parse_flexible_date(args.date_text, config.digest.timezone, default_to_yesterday=False)
    max_scrolls = args.max_scrolls if args.max_scrolls is not None else config.collection.backfill_max_scrolls
    if max_scrolls < 0:
        raise ValueError("--max-scrolls must be 0 or greater")

    collector = BackfillCollector(config, service.store, service.wechat_client)
    backfill = collector.collect_date(target_date, max_scrolls=max_scrolls, groups=[group_name])
    start, end = day_window(target_date, config.digest.timezone)
    messages = service.store.get_messages(start, end, [group_name])
    content = _format_fetch_once_summary(config, service, group_name, target_date, backfill, messages)
    service.store.save_digest(target_date.isoformat(), content, scope=f"once:{digest_scope([group_name])}")

    delivery = "print" if args.no_send else args.delivery
    if args.print_result or delivery == "print":
        print(_cap_output(content, args.max_chars))
    if delivery == "openclaw":
        session_key = str(args.reply_session_key or service.store.get_state("openclaw_reply_session_key") or "").strip()
        if not session_key:
            raise ValueError(
                "No OpenClaw reply target. Send `wd 状态` to the bot once, "
                "or rerun with --delivery wechat."
            )
        _send_openclaw_reply(config, session_key, content)
        print(
            f"Fetched {len(messages)} messages from {group_name} for {target_date.isoformat()}; summarized and "
            "sent via OpenClaw."
        )
    elif delivery == "wechat":
        recipient = str(args.recipient or "").strip() or "\u6587\u4ef6\u4f20\u8f93\u52a9\u624b"
        for chunk in split_message(content, config.wechat.message_chunk_size):
            service.wechat_client.send_message(recipient, chunk)
        print(
            f"Fetched {len(messages)} messages from {group_name} for {target_date.isoformat()}; summarized and "
            f"sent to {recipient}."
        )
    return 0


def cmd_assistant(args: argparse.Namespace) -> int:
    config, service = _build_service(args, with_wechat=False)
    service.wechat_client = LazyWeChatClient()
    runtime = AssistantRuntime(config, service.store, service.wechat_client, service)
    print(f"Assistant started at {datetime.now().isoformat(timespec='seconds')}.", flush=True)
    with create_openclaw_command_channel(config.openclaw) as channel:
        print(f"OpenClaw command channel connected ({config.openclaw.transport}).", flush=True)
        while True:
            commands = channel.wait_commands()
            for command in commands:
                command_id = service.store.record_command(
                    f"openclaw:{config.openclaw.transport}",
                    command.text,
                    source_message_id=command.source_message_id,
                    session_key=command.session_key,
                )
                if command_id is None:
                    continue
                try:
                    service.store.set_state("openclaw_reply_session_key", command.session_key)
                    if command.source_message_id:
                        service.store.set_state("openclaw_reply_source_message_id", command.source_message_id)
                    service.store.set_state("openclaw_reply_updated_at", datetime.now(timezone.utc).isoformat())
                    response = runtime.handle_text(command.text)
                    for chunk in response.chunks:
                        channel.send_reply(command, chunk)
                    service.store.finish_command(command_id, result_summary=response.content[:300])
                    print(f"Handled OpenClaw command: {command.text[:80]}", flush=True)
                except Exception as exc:
                    service.store.finish_command(command_id, error=str(exc))
                    channel.send_reply(command, f"执行失败：{exc}")
                    print(f"OpenClaw command failed: {exc}", flush=True)
            if args.once:
                return 0


def cmd_run(args: argparse.Namespace) -> int:
    config, service = _build_service(args, with_wechat=args.once)
    if not args.once:
        service.wechat_client = LazyWeChatClient()
    print(f"Started at {datetime.now().isoformat(timespec='seconds')}. Press Ctrl+C to stop.", flush=True)
    if args.once:
        service.run_forever(once=True)
        return 0
    _run_scheduler_loop(config, service)
    return 0


def cmd_scheduled_digest(args: argparse.Namespace) -> int:
    from datetime import datetime as dt
    from pathlib import Path
    import traceback

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "scheduled.log"

    def log(msg: str) -> None:
        ts = dt.now().isoformat(timespec="seconds")
        line = f"[{ts}] {msg}"
        print(line)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    try:
        config = load_config(args.config, args.env)
        log("Checking scheduled digest...")
        service = _create_service(config, with_wechat=False)
        service.wechat_client = LazyWeChatClient()
        scheduled = _run_scheduled_delivery(config, service, log)
        if scheduled["status"] != "sent":
            log(f"No send needed: {scheduled['status']}.")
            return 0
        try:
            from .memory import MemoryManager
            groups = scheduled["groups"]
            memory_mgr = MemoryManager(config, service.store, service.digest_generator.llm_client)
            memory_mgr.generate_daily_memory(scheduled["date"], groups)
            log("Daily memory generated.")
        except Exception as exc:
            log(f"Memory generation failed (non-fatal): {exc}")
        log("Scheduled digest complete.")
    except Exception:
        log(f"FATAL: {traceback.format_exc()}")
        return 1
    return 0


def scheduled_digest_entry() -> int:
    return _entry("scheduled-digest")


def _build_service(args: argparse.Namespace, with_wechat: bool = True) -> tuple[AppConfig, DigestService]:
    config = load_config(args.config, args.env)
    return config, _create_service(config, with_wechat=with_wechat)


def _create_service(config: AppConfig, with_wechat: bool = True) -> DigestService:
    store = DigestStore(config.storage.path)
    wechat_client = WxautoWeChatClient() if with_wechat else NoopWeChatClient()
    llm_client = OpenAICompatibleClient(config.llm)
    generator = DigestGenerator(
        llm_client,
        max_input_messages=config.llm.max_input_messages,
        timezone_name=config.digest.timezone,
        prompt_path=config.digest.prompt_path,
    )
    return DigestService(config, store, wechat_client, generator)


def _remember_openclaw_reply_target(store: DigestStore) -> None:
    session_key = _first_env("WECHAT_DIGEST_REPLY_SESSION_KEY", "WECHAT_DIGEST_SESSION_KEY")
    if not session_key:
        return
    store.set_state("openclaw_reply_session_key", session_key)
    source_id = _first_env("WECHAT_DIGEST_REPLY_SOURCE_MESSAGE_ID", "WECHAT_DIGEST_SOURCE_MESSAGE_ID")
    if source_id:
        store.set_state("openclaw_reply_source_message_id", source_id)
    store.set_state("openclaw_reply_updated_at", datetime.now(timezone.utc).isoformat())


def _first_env(*names: str) -> str:
    import os

    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _run_scheduler_loop(config: AppConfig, service: DigestService) -> None:
    last_status = ""
    last_logged_no_target = False
    while True:
        try:
            scheduled = _run_scheduled_delivery(config, service, print, quiet=True)
            status = str(scheduled["status"])
            if status != last_status:
                ts = datetime.now().isoformat(timespec="seconds")
                print(f"[{ts}] Scheduler: {status}", flush=True)
                if status == "no_reply_target":
                    print(
                        "[!] No OpenClaw reply target. Send any 'wd' command (e.g. 'wd 状态') "
                        "to the bot via WeChat first, then the scheduler can deliver digests.",
                        flush=True,
                    )
                last_logged_no_target = (status == "no_reply_target")
            last_status = status
        except Exception as exc:
            ts = datetime.now().isoformat(timespec="seconds")
            print(f"[{ts}] Scheduler error: {exc}", flush=True)
        time_module.sleep(_scheduler_sleep_seconds(config, service.store))


def _run_scheduled_delivery(
    config: AppConfig,
    service: DigestService,
    log,
    quiet: bool = False,
) -> dict:
    store = service.store
    schedule = store.get_schedule()
    if not schedule["enabled"]:
        return {"status": "disabled"}

    tz = app_timezone(config.digest.timezone)
    now = datetime.now(tz)
    scheduled_time = str(schedule["time"])
    if not should_run_digest(now, scheduled_time):
        return {"status": "not_time"}

    groups = _active_groups(config, store)
    target_date = now.date()
    trigger_key = _scheduled_trigger_key(target_date.isoformat(), scheduled_time, schedule, groups)
    if store.get_state("last_scheduled_digest_key") == trigger_key:
        return {"status": "already_ran"}

    session_key = store.get_state("openclaw_reply_session_key")
    if not session_key:
        if not quiet:
            log("No OpenClaw reply target yet. Send any wd command first, then set wd schedule HH:MM.")
        return {"status": "no_reply_target"}

    if not quiet:
        log(f"Starting scheduled digest for {', '.join(groups)}...")

    # Mark before touching WeChat, so a transient failure will not keep stealing
    # focus in a tight retry loop. Changing the schedule creates a new key.
    store.set_state("last_scheduled_digest_key", trigger_key)
    store.set_state("last_scheduled_digest_started_at", datetime.now(timezone.utc).isoformat())
    try:
        collector = BackfillCollector(config, store, service.wechat_client)
        backfill = collector.collect_date(target_date, groups=groups)
        digest = service.generate_daily_digest(
            target_date,
            dry_run=False,
            allow_fallback=True,
            send=False,
            groups=groups,
        )
        _send_openclaw_reply(config, session_key, digest.content)
        store.mark_digest_sent(digest.digest_date, datetime.now(timezone.utc), scope=digest.scope)
        store.set_state("last_scheduled_digest_error", "")
    except Exception as exc:
        store.set_state("last_scheduled_digest_error", str(exc))
        if not quiet:
            log(f"Scheduled digest failed; it will not retry until the schedule changes: {exc}")
        raise
    if not quiet:
        log(
            f"Scheduled digest sent via OpenClaw; group={', '.join(groups)}, "
            f"backfilled={backfill.fetched}, inserted={backfill.inserted}, messages={digest.message_count}."
        )
    return {
        "status": "sent",
        "date": target_date,
        "groups": groups,
        "backfilled": backfill.fetched,
        "inserted": backfill.inserted,
        "message_count": digest.message_count,
    }


def _send_openclaw_reply(config: AppConfig, session_key: str, content: str) -> None:
    command = InboundCommand(text="", session_key=session_key)
    with create_openclaw_command_channel(config.openclaw) as channel:
        for chunk in split_message(content, config.wechat.message_chunk_size):
            channel.send_reply(command, chunk)


def _active_groups(config: AppConfig, store: DigestStore) -> list[str]:
    active = (store.get_state("active_group") or "").strip()
    if active:
        return [active]
    return list(config.wechat.groups)


def _scheduled_trigger_key(date_text: str, scheduled_time: str, schedule: dict, groups: list[str]) -> str:
    updated_at = str(schedule.get("updated_at") or "")
    return "|".join([date_text, scheduled_time, updated_at, ",".join(groups)])


def _format_fetch_once_summary(
    config: AppConfig,
    service: DigestService,
    group_name: str,
    target_date: date,
    backfill,
    messages,
) -> str:
    lines = [
        "# \u4e00\u6b21\u6027\u83b7\u53d6",
        "",
        f"\u7fa4\u804a: {group_name}",
        f"\u65e5\u671f: {target_date.isoformat()}",
        (
            f"\u626b\u63cf: {backfill.scroll_count} \u8f6e; "
            f"\u8bfb\u53d6: {backfill.fetched} \u6761; "
            f"\u65b0\u589e: {backfill.inserted} \u6761; "
            f"\u5165\u5e93: {len(messages)} \u6761"
        ),
        "",
    ]
    if not messages:
        lines.append("\u6ca1\u6709\u8bfb\u53d6\u5230\u8fd9\u4e2a\u7fa4\u7684\u6d88\u606f\u3002")
        return "\n".join(lines)

    try:
        summary = service.digest_generator.generate(target_date, messages)
    except DigestError:
        summary = fallback_digest(target_date, messages)
    lines.extend(["---", summary])
    return "\n".join(lines)


def _scheduler_sleep_seconds(config: AppConfig, store: DigestStore) -> float:
    schedule = store.get_schedule()
    if not schedule["enabled"]:
        return 30.0
    tz = app_timezone(config.digest.timezone)
    now = datetime.now(tz)
    try:
        hour_text, minute_text = str(schedule["time"]).split(":", 1)
        due_today = datetime.combine(now.date(), time(int(hour_text), int(minute_text)), tzinfo=tz)
    except Exception:
        return 30.0
    if now < due_today:
        return max(5.0, min(30.0, (due_today - now).total_seconds()))
    due_tomorrow = due_today + timedelta(days=1)
    return max(5.0, min(30.0, (due_tomorrow - now).total_seconds()))


def _parse_message_ref(value: str) -> int:
    text = value.strip()
    if text.lower().startswith("m"):
        text = text[1:]
    if not text.isdigit():
        raise ValueError(f"Invalid message reference: {value}")
    return int(text)


def _cap_output(value: str, max_chars: int) -> str:
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    suffix = f"\n\n[输出已截断，仅显示前 {max_chars} 字。请缩小查询范围或使用 /原文 查看上下文。]"
    return value[:max_chars].rstrip() + suffix


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                continue


def _entry(command: str) -> int:
    global_args: list[str] = []
    sub_args: list[str] = []
    raw_args = sys.argv[1:]
    index = 0
    while index < len(raw_args):
        item = raw_args[index]
        if item in {"--config", "--env"}:
            global_args.append(item)
            if index + 1 < len(raw_args):
                global_args.append(raw_args[index + 1])
                index += 2
                continue
        sub_args.append(item)
        index += 1
    return main([*global_args, command, *sub_args])
