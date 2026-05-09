from __future__ import annotations

import argparse
from datetime import datetime
import sys

from .config import AppConfig, ConfigError, load_config
from .digest import DigestError, DigestGenerator, OpenAICompatibleClient
from .service import DigestService
from .storage import DigestStore
from .timeutils import parse_date
from .wechat import NoopWeChatClient, WeChatError, WxautoWeChatClient


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (ConfigError, WeChatError, DigestError, ValueError) as exc:
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

    run_parser = subparsers.add_parser("run", help="Continuously collect messages and send scheduled digest")
    run_parser.add_argument("--once", action="store_true", help="Collect once and exit")
    run_parser.set_defaults(func=cmd_run)
    return parser


def check_wechat_entry() -> int:
    return _entry("check-wechat")


def send_test_entry() -> int:
    return _entry("send-test")


def digest_now_entry() -> int:
    return _entry("digest-now")


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


def cmd_run(args: argparse.Namespace) -> int:
    _, service = _build_service(args)
    print(f"Started at {datetime.now().isoformat(timespec='seconds')}. Press Ctrl+C to stop.")
    service.run_forever(once=args.once)
    return 0


def _build_service(args: argparse.Namespace, with_wechat: bool = True) -> tuple[AppConfig, DigestService]:
    config = load_config(args.config, args.env)
    store = DigestStore(config.storage.path)
    wechat_client = WxautoWeChatClient() if with_wechat else NoopWeChatClient()
    llm_client = OpenAICompatibleClient(config.llm)
    generator = DigestGenerator(
        llm_client,
        max_input_messages=config.llm.max_input_messages,
        timezone_name=config.digest.timezone,
    )
    return config, DigestService(config, store, wechat_client, generator)


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
