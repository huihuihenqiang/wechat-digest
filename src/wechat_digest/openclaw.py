from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import threading
import time
import uuid
from typing import Any, BinaryIO
from urllib import error as urlerror
from urllib import request as urlrequest

from .config import OpenClawConfig


class OpenClawError(RuntimeError):
    """Raised when the OpenClaw sidecar bridge cannot be used."""


@dataclass(frozen=True)
class InboundCommand:
    text: str
    session_key: str
    source_message_id: str | None = None
    cursor: int | None = None


def create_openclaw_command_channel(config: OpenClawConfig) -> OpenClawCommandChannel | OpenClawWeixinDirectChannel:
    transport = config.transport.strip().lower()
    if transport in {"mcp", "openclaw-mcp", ""}:
        return OpenClawCommandChannel(config)
    if transport in {"weixin-direct", "direct", "ilink"}:
        return OpenClawWeixinDirectChannel(config)
    raise OpenClawError(f"Unsupported openclaw.transport: {config.transport}")


def _normalize_weixin_session_key(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    if text.startswith("agent:") or ":openclaw-weixin:" in text:
        return text.rsplit(":", 1)[-1].strip()
    return text


def _resolve_weixin_target(
    session_key: str,
    context_tokens: dict[str, str],
) -> tuple[str, str | None]:
    target_user_id = _normalize_weixin_session_key(session_key)
    context_token = context_tokens.get(session_key) or context_tokens.get(target_user_id)
    if context_token:
        return target_user_id, context_token

    lowered_target = target_user_id.lower()
    for key, token in context_tokens.items():
        normalized_key = _normalize_weixin_session_key(key)
        if normalized_key.lower() == lowered_target:
            return normalized_key, token
    return target_user_id, None


class OpenClawCommandChannel:
    def __init__(self, config: OpenClawConfig) -> None:
        if not config.enabled:
            raise OpenClawError("OpenClaw is disabled. Set openclaw.enabled: true before running assistant mode.")
        self.config = config
        self.client = McpStdioClient(
            [config.command, *config.args],
            request_timeout_seconds=config.request_timeout_seconds,
        )
        self.cursor = 0

    def __enter__(self) -> OpenClawCommandChannel:
        self.client.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def wait_commands(self) -> list[InboundCommand]:
        payload = self.client.call_tool(
            "events_wait",
            {
                "after_cursor": self.cursor,
                "timeout_ms": self.config.poll_timeout_seconds * 1000,
            },
        )
        commands, cursor = _commands_from_event_payload(payload, self.config.channel)
        if cursor is not None:
            self.cursor = cursor
        return commands

    def send_reply(self, command: InboundCommand, text: str) -> None:
        self.client.call_tool(
            "messages_send",
            {
                "session_key": command.session_key,
                "text": text,
            },
        )


@dataclass(frozen=True)
class _WeixinAccount:
    account_id: str
    token: str
    base_url: str
    user_id: str | None = None


class OpenClawWeixinDirectChannel:
    def __init__(self, config: OpenClawConfig) -> None:
        if not config.enabled:
            raise OpenClawError("OpenClaw is disabled. Set openclaw.enabled: true before running assistant mode.")
        self.config = config
        self.base_dir = _resolve_openclaw_base_dir(config.base_dir)
        self.account: _WeixinAccount | None = None
        self.package_info = _load_weixin_package_info(self.base_dir)
        self.sync_path = Path(config.sync_path)
        self.sync_buf = ""
        self.context_tokens: dict[str, str] = {}
        self.seen_ids: set[str] = set()

    def __enter__(self) -> OpenClawWeixinDirectChannel:
        self.account = self._load_account()
        self.sync_buf = self._load_sync_buf(self.account.account_id)
        self.context_tokens.update(self._load_context_tokens(self.account.account_id))
        self._notify_start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self.account is not None:
            self._notify_stop()

    def wait_commands(self) -> list[InboundCommand]:
        account = self._require_account()
        response = self._post_json(
            "ilink/bot/getupdates",
            {
                "get_updates_buf": self.sync_buf,
                "base_info": self._base_info(),
            },
            timeout_seconds=self.config.poll_timeout_seconds + 5,
        )
        self._raise_for_weixin_error(response, "getUpdates")
        new_sync_buf = response.get("get_updates_buf")
        if isinstance(new_sync_buf, str) and new_sync_buf:
            self.sync_buf = new_sync_buf
            self._save_sync_buf(account.account_id, new_sync_buf)
        commands: list[InboundCommand] = []
        for message in response.get("msgs") or []:
            command = self._command_from_weixin_message(message)
            if command is not None:
                commands.append(command)
        return commands

    def send_reply(self, command: InboundCommand, text: str) -> None:
        target_user_id, context_token = _resolve_weixin_target(command.session_key, self.context_tokens)
        client_id = f"wechat-digest-{uuid.uuid4()}"
        body: dict[str, Any] = {
            "msg": {
                "from_user_id": "",
                "to_user_id": target_user_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
            "base_info": self._base_info(),
        }
        if context_token:
            body["msg"]["context_token"] = context_token
        self._raise_for_weixin_error(self._post_json("ilink/bot/sendmessage", body, timeout_seconds=15), "sendMessage")

    def _load_account(self) -> _WeixinAccount:
        accounts_dir = self.base_dir / "openclaw-weixin" / "accounts"
        account_id = self.config.account_id.strip()
        if not account_id:
            account_id = self._default_account_id()
        account_path = accounts_dir / f"{account_id}.json"
        if not account_path.exists():
            raise OpenClawError(f"OpenClaw Weixin account file not found: {account_path}")
        loaded = _load_json_file(account_path)
        token = _required_str(loaded, "token", account_path)
        base_url = _required_str(loaded, "baseUrl", account_path)
        user_id = loaded.get("userId")
        return _WeixinAccount(
            account_id=account_id,
            token=token,
            base_url=base_url,
            user_id=str(user_id) if user_id else None,
        )

    def _default_account_id(self) -> str:
        accounts_path = self.base_dir / "openclaw-weixin" / "accounts.json"
        loaded = _load_json_file(accounts_path)
        if not isinstance(loaded, list):
            raise OpenClawError(f"OpenClaw Weixin accounts file must be a list: {accounts_path}")
        account_ids = [str(item).strip() for item in loaded if str(item).strip()]
        if len(account_ids) == 1:
            return account_ids[0]
        if not account_ids:
            raise OpenClawError("No OpenClaw Weixin accounts found. Run `openclaw channels login --channel openclaw-weixin` first.")
        raise OpenClawError("Multiple OpenClaw Weixin accounts found. Set openclaw.account_id in config.yaml.")

    def _load_sync_buf(self, account_id: str) -> str:
        direct_sync = _load_optional_json_file(self.sync_path)
        if isinstance(direct_sync, dict):
            account_bufs = direct_sync.get("accounts")
            if isinstance(account_bufs, dict) and isinstance(account_bufs.get(account_id), str):
                return str(account_bufs[account_id])
            if isinstance(direct_sync.get("get_updates_buf"), str):
                return str(direct_sync["get_updates_buf"])

        plugin_sync_path = self.base_dir / "openclaw-weixin" / "accounts" / f"{account_id}.sync.json"
        plugin_sync = _load_optional_json_file(plugin_sync_path)
        if isinstance(plugin_sync, dict) and isinstance(plugin_sync.get("get_updates_buf"), str):
            return str(plugin_sync["get_updates_buf"])
        return ""

    def _save_sync_buf(self, account_id: str, sync_buf: str) -> None:
        parent = self.sync_path.parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)
        payload = {"accounts": {account_id: sync_buf}, "updated_at": int(time.time())}
        self.sync_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_context_tokens(self, account_id: str) -> dict[str, str]:
        path = self.base_dir / "openclaw-weixin" / "accounts" / f"{account_id}.context-tokens.json"
        loaded = _load_optional_json_file(path)
        if not isinstance(loaded, dict):
            return {}
        return {str(key): str(value) for key, value in loaded.items() if value}

    def _command_from_weixin_message(self, message: Any) -> InboundCommand | None:
        if not isinstance(message, dict):
            return None
        text = _extract_weixin_text(message)
        sender = _first_weixin_str(message, "from_user_id", "fromUserId")
        if not text or not sender:
            return None
        message_id = _first_weixin_str(message, "message_id", "messageId", "client_id", "clientId", "seq")
        dedupe_id = message_id or f"{sender}:{text}:{message.get('create_time_ms')}"
        if dedupe_id in self.seen_ids:
            return None
        self.seen_ids.add(dedupe_id)
        context_token = _first_weixin_str(message, "context_token", "contextToken")
        if context_token:
            self.context_tokens[sender] = context_token
        return InboundCommand(text=text, session_key=sender, source_message_id=message_id)

    def _notify_start(self) -> None:
        try:
            self._post_json("ilink/bot/msg/notifystart", {"base_info": self._base_info()}, timeout_seconds=10)
        except OpenClawError:
            pass

    def _notify_stop(self) -> None:
        try:
            self._post_json("ilink/bot/msg/notifystop", {"base_info": self._base_info()}, timeout_seconds=10)
        except OpenClawError:
            pass

    def _post_json(self, endpoint: str, body: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        account = self._require_account()
        url = f"{account.base_url.rstrip('/')}/{endpoint}"
        data = json.dumps(body).encode("utf-8")
        req = urlrequest.Request(url, data=data, headers=self._headers(account.token), method="POST")
        try:
            with urlrequest.urlopen(req, timeout=timeout_seconds) as response:
                loaded = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, socket.timeout) as exc:
            if endpoint.endswith("getupdates"):
                return {"ret": 0, "msgs": [], "get_updates_buf": self.sync_buf}
            raise OpenClawError(f"Weixin API timed out: {endpoint}") from exc
        except urlerror.URLError as exc:
            raise OpenClawError(f"Weixin API request failed: {endpoint}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OpenClawError(f"Weixin API returned invalid JSON: {endpoint}") from exc
        if not isinstance(loaded, dict):
            raise OpenClawError(f"Weixin API returned unexpected payload: {endpoint}")
        return loaded

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {token.strip()}",
            "X-WECHAT-UIN": _random_wechat_uin(),
            "iLink-App-Id": self.package_info["appid"],
            "iLink-App-ClientVersion": str(self.package_info["client_version"]),
        }

    def _base_info(self) -> dict[str, str]:
        return {
            "channel_version": self.package_info["version"],
            "bot_agent": "OpenClaw",
        }

    def _raise_for_weixin_error(self, response: dict[str, Any], label: str) -> None:
        ret = response.get("ret")
        errcode = response.get("errcode")
        if ret not in (None, 0) or errcode not in (None, 0):
            errmsg = response.get("errmsg") or response.get("message") or response
            raise OpenClawError(f"Weixin {label} failed: ret={ret} errcode={errcode} errmsg={errmsg}")

    def _require_account(self) -> _WeixinAccount:
        if self.account is None:
            raise OpenClawError("OpenClaw Weixin direct channel is not started.")
        return self.account


class McpStdioClient:
    def __init__(self, command: list[str], request_timeout_seconds: int = 45) -> None:
        self.command = _resolve_command(command)
        self.request_timeout_seconds = request_timeout_seconds
        self.process: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._stderr_lines: deque[str] = deque(maxlen=80)
        self._stderr_lock = threading.Lock()

    def start(self) -> None:
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise OpenClawError(f"Could not start OpenClaw command: {self.command[0]}") from exc
        self._start_reader_threads()
        self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "wechat-digest", "version": "0.1.0"},
            },
            timeout_seconds=self.request_timeout_seconds,
        )
        self._notify("notifications/initialized", {})

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        timeout_seconds = self._tool_timeout(arguments)
        response = self._request("tools/call", {"name": name, "arguments": arguments}, timeout_seconds=timeout_seconds)
        if isinstance(response, dict) and response.get("isError"):
            raise OpenClawError(_tool_response_text(response) or f"OpenClaw tool failed: {name}")
        return _decode_tool_response(response)

    def _request(self, method: str, params: dict[str, Any], timeout_seconds: float | None = None) -> Any:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            self._write_message({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            deadline = time.monotonic() + (timeout_seconds or self.request_timeout_seconds)
            while True:
                message = self._read_queued_message(method, deadline)
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise OpenClawError(str(message["error"]))
                return message.get("result")

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write_message({"jsonrpc": "2.0", "method": method, "params": params})

    def _write_message(self, message: dict[str, Any]) -> None:
        process = self._require_process()
        assert process.stdin is not None
        body = (json.dumps(message) + "\n").encode("utf-8")
        process.stdin.write(body)
        process.stdin.flush()

    def _read_queued_message(self, method: str, deadline: float) -> dict[str, Any]:
        while True:
            process = self._require_process()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OpenClawError(f"Timed out waiting for OpenClaw MCP response to {method}. {self._stderr_tail()}")
            if process.poll() is not None and self._messages.empty():
                raise OpenClawError(f"OpenClaw MCP bridge exited while waiting for {method}. {self._stderr_tail()}")
            try:
                item = self._messages.get(timeout=min(0.5, remaining))
            except queue.Empty:
                continue
            if isinstance(item, BaseException):
                raise OpenClawError(f"OpenClaw MCP read failed while waiting for {method}: {item}. {self._stderr_tail()}") from item
            return item

    def _start_reader_threads(self) -> None:
        process = self._require_process()
        if process.stdout is not None:
            threading.Thread(target=self._stdout_reader, args=(process.stdout,), daemon=True).start()
        if process.stderr is not None:
            threading.Thread(target=self._stderr_reader, args=(process.stderr,), daemon=True).start()

    def _stdout_reader(self, stream: BinaryIO) -> None:
        while True:
            try:
                message = _read_mcp_message(stream)
            except BaseException as exc:
                self._messages.put(exc)
                return
            self._messages.put(message)

    def _stderr_reader(self, stream: BinaryIO) -> None:
        while True:
            line = stream.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            with self._stderr_lock:
                self._stderr_lines.append(text)

    def _stderr_tail(self) -> str:
        with self._stderr_lock:
            text = "\n".join(self._stderr_lines)[-2000:]
        return f"stderr: {text}" if text else ""

    def _tool_timeout(self, arguments: dict[str, Any]) -> float:
        timeout = float(self.request_timeout_seconds)
        raw_timeout_ms = arguments.get("timeoutMs", arguments.get("timeout_ms"))
        if raw_timeout_ms is not None:
            try:
                timeout = max(timeout, float(raw_timeout_ms) / 1000 + 10)
            except (TypeError, ValueError):
                pass
        return timeout

    def _require_process(self) -> subprocess.Popen[bytes]:
        if self.process is None:
            raise OpenClawError("OpenClaw MCP bridge is not started.")
        if self.process.poll() is not None:
            raise OpenClawError("OpenClaw MCP bridge exited.")
        return self.process


def _read_content_length(stream: BinaryIO) -> int:
    content_length: int | None = None
    while True:
        line = stream.readline()
        if line in (b"", b"\r\n", b"\n"):
            break
        key, _, value = line.decode("ascii", errors="replace").partition(":")
        if key.lower() == "content-length":
            content_length = int(value.strip())
    if content_length is None:
        raise OpenClawError("MCP message did not include Content-Length.")
    return content_length


def _read_mcp_message(stream: BinaryIO) -> dict[str, Any]:
    line = stream.readline()
    if not line:
        raise OpenClawError("OpenClaw MCP bridge closed stdout.")
    loaded = json.loads(line.decode("utf-8"))
    if not isinstance(loaded, dict):
        raise OpenClawError(f"Unexpected MCP message: {loaded}")
    return loaded


def _resolve_command(command: list[str]) -> list[str]:
    if not command:
        return command
    executable = command[0]
    if os.name != "nt" or os.path.splitext(executable)[1]:
        return command
    for suffix in (".cmd", ".exe", ".bat"):
        resolved = shutil.which(executable + suffix)
        if resolved:
            return [resolved, *command[1:]]
    resolved = shutil.which(executable)
    if resolved:
        return [resolved, *command[1:]]
    return command


def _resolve_openclaw_base_dir(configured: str) -> Path:
    if configured.strip():
        return Path(configured).expanduser()
    return Path.home() / ".openclaw"


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OpenClawError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise OpenClawError(f"Invalid JSON file: {path}") from exc


def _load_optional_json_file(path: Path) -> Any:
    if not path.exists():
        return None
    return _load_json_file(path)


def _required_str(mapping: Any, key: str, path: Path) -> str:
    if not isinstance(mapping, dict):
        raise OpenClawError(f"JSON file must contain an object: {path}")
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OpenClawError(f"Missing {key} in {path}")
    return value.strip()


def _load_weixin_package_info(base_dir: Path) -> dict[str, str | int]:
    candidates = [
        base_dir / "npm" / "node_modules" / "@tencent-weixin" / "openclaw-weixin" / "package.json",
        Path(os.getenv("APPDATA", "")) / "npm" / "node_modules" / "openclaw" / "node_modules" / "@tencent-weixin" / "openclaw-weixin" / "package.json",
    ]
    for path in candidates:
        if not str(path) or not path.exists():
            continue
        loaded = _load_json_file(path)
        if isinstance(loaded, dict):
            version = str(loaded.get("version") or "0.0.0")
            appid = str(loaded.get("ilink_appid") or "bot")
            return {"version": version, "appid": appid, "client_version": _client_version_number(version)}
    version = "0.0.0"
    return {"version": version, "appid": "bot", "client_version": _client_version_number(version)}


def _client_version_number(version: str) -> int:
    parts: list[int] = []
    for item in version.split(".")[:3]:
        try:
            parts.append(int(item))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts[:3]
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


def _random_wechat_uin() -> str:
    value = int.from_bytes(os.urandom(4), "big")
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _extract_weixin_text(message: dict[str, Any]) -> str | None:
    for item in message.get("item_list") or message.get("itemList") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != 1:
            continue
        text_item = item.get("text_item") or item.get("textItem")
        if isinstance(text_item, dict):
            text = text_item.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _first_weixin_str(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _decode_tool_response(response: Any) -> Any:
    if not isinstance(response, dict):
        return response
    if "structuredContent" in response:
        return response["structuredContent"]
    texts = [item.get("text", "") for item in response.get("content", []) if isinstance(item, dict)]
    if not texts:
        return response
    if len(texts) == 1:
        return _maybe_json(texts[0])
    return [_maybe_json(text) for text in texts]


def _tool_response_text(response: dict[str, Any]) -> str:
    texts = [item.get("text", "") for item in response.get("content", []) if isinstance(item, dict)]
    return "\n".join(text for text in texts if text)


def _maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _commands_from_event_payload(payload: Any, channel: str) -> tuple[list[InboundCommand], int | None]:
    events = _extract_events(payload)
    cursor = _extract_cursor(payload)
    commands: list[InboundCommand] = []
    for event in events:
        command = _command_from_event(event, channel)
        if command is not None:
            commands.append(command)
        event_cursor = _extract_cursor(event)
        if event_cursor is not None:
            cursor = max(cursor or 0, event_cursor)
    return commands, cursor


def _extract_events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        event = payload.get("event")
        if isinstance(event, dict):
            return [event]
        for key in ("events", "items", "messages"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if payload.get("type") == "message":
            return [payload]
    return []


def _command_from_event(event: dict[str, Any], channel: str) -> InboundCommand | None:
    if event.get("type") not in {None, "message"}:
        return None
    role = _first_text(event, "role")
    if role and role != "user":
        return None
    event_channel = _first_text(event, "channel", "channel_id", "channelId")
    if event_channel and event_channel != channel:
        return None
    text = _find_text(event)
    session_key = _first_text(event, "session_key", "sessionKey", "conversation", "conversation_id")
    if not text or not session_key:
        return None
    source_message_id = _first_text(event, "message_id", "messageId", "id")
    return InboundCommand(text=text, session_key=session_key, source_message_id=source_message_id, cursor=_extract_cursor(event))


def _find_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            text = _find_text(item)
            if text:
                return text
    if isinstance(value, dict):
        for key in ("text", "content", "message", "body"):
            text = _find_text(value.get(key))
            if text:
                return text
        blocks = value.get("blocks") or value.get("contentBlocks")
        text = _find_text(blocks)
        if text:
            return text
    return None


def _first_text(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value)
    for nested_key in ("route", "conversation", "deliveryContext", "origin"):
        route = mapping.get(nested_key)
        if not isinstance(route, dict):
            continue
        for key in keys:
            value = route.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def _extract_cursor(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in ("cursor", "nextCursor", "next_cursor", "event_id", "eventId"):
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None
