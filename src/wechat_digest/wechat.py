from __future__ import annotations

from datetime import datetime, timezone
import inspect
from typing import Any, Protocol

from .models import ChatMessage


class WeChatError(RuntimeError):
    """Raised when desktop WeChat automation fails."""


class WeChatClient(Protocol):
    def check_ready(self) -> str:
        ...

    def fetch_recent_messages(self, group_name: str, limit: int) -> list[ChatMessage]:
        ...

    def send_message(self, recipient: str, message: str) -> None:
        ...


class NoopWeChatClient:
    def check_ready(self) -> str:
        return "WeChat client is not initialized."

    def fetch_recent_messages(self, group_name: str, limit: int) -> list[ChatMessage]:
        raise WeChatError("WeChat client is required to collect messages.")

    def send_message(self, recipient: str, message: str) -> None:
        raise WeChatError("WeChat client is required to send messages.")


class WxautoWeChatClient:
    def __init__(self) -> None:
        try:
            try:
                from wxauto4 import WeChat  # type: ignore
            except ImportError:
                from wxauto import WeChat  # type: ignore
        except ImportError as exc:
            raise WeChatError(
                "wxauto4 is not installed. Run `python -m pip install -e .` on Windows, "
                "or install wxauto4 manually."
            ) from exc
        try:
            self.wx = WeChat(ads=False)
        except Exception as exc:
            raise WeChatError(
                "Could not find a logged-in desktop WeChat main window. "
                "Open PC WeChat, make sure it is logged in, and keep the main window visible before retrying."
            ) from exc

    def check_ready(self) -> str:
        for method_name in ("GetSessionList", "GetAllSessions", "GetSession"):
            method = getattr(self.wx, method_name, None)
            if callable(method):
                try:
                    result = method()
                    return f"Connected to desktop WeChat via {method_name}; sessions: {_safe_len(result)}"
                except Exception:
                    continue
        return "Connected to desktop WeChat"

    def fetch_recent_messages(self, group_name: str, limit: int) -> list[ChatMessage]:
        self._switch_to_chat(group_name)
        raw_messages = self._read_messages(limit)
        now = datetime.now(timezone.utc)
        parsed = [_coerce_message(group_name, raw, now) for raw in raw_messages]
        parsed = [message for message in parsed if message.content.strip()]
        return parsed[-limit:]

    def send_message(self, recipient: str, message: str) -> None:
        method = getattr(self.wx, "SendMsg", None) or getattr(self.wx, "send_msg", None)
        if callable(method):
            if _call_accepts(method, "who"):
                method(message, who=recipient)
                return
        self._switch_to_chat(recipient)
        if callable(method):
            if _call_accepts(method, "msg"):
                method(msg=message)
            else:
                method(message)
            return
        raise WeChatError("Could not find a wxauto SendMsg method")

    def _switch_to_chat(self, name: str) -> None:
        for method_name in ("ChatWith", "SwitchToChat", "Search"):
            method = getattr(self.wx, method_name, None)
            if callable(method):
                try:
                    method(name)
                    return
                except TypeError:
                    try:
                        method(who=name)
                        return
                    except Exception:
                        continue
                except Exception:
                    continue
        raise WeChatError(f"Could not switch to chat: {name}")

    def _read_messages(self, limit: int) -> list[Any]:
        for method_name in ("GetAllMessage", "GetAllMessages", "GetMessage", "GetLastMessage"):
            method = getattr(self.wx, method_name, None)
            if callable(method):
                try:
                    result = _call_with_optional_limit(method, limit)
                    return _as_list(result)
                except Exception:
                    continue
        raise WeChatError("Could not find a wxauto message reading method")


def _call_with_optional_limit(method: Any, limit: int) -> Any:
    if _call_accepts(method, "n"):
        return method(n=limit)
    if _call_accepts(method, "limit"):
        return method(limit=limit)
    try:
        return method(limit)
    except TypeError:
        return method()


def _call_accepts(method: Any, parameter: str) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    return parameter in signature.parameters or any(
        item.kind == inspect.Parameter.VAR_KEYWORD for item in signature.parameters.values()
    )


def _safe_len(value: Any) -> str:
    try:
        return str(len(value))
    except Exception:
        return "unknown"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return [value]


def _coerce_message(group_name: str, raw: Any, default_time: datetime) -> ChatMessage:
    if isinstance(raw, ChatMessage):
        return raw
    if isinstance(raw, dict):
        sender = _first(raw, "sender", "sender_name", "who", "name", "from") or "未知"
        content = _first(raw, "content", "text", "msg", "message") or ""
        msg_type = _first(raw, "type", "msg_type") or "text"
        msg_time = _parse_datetime(_first(raw, "time", "msg_time", "create_time", "created_at"), default_time)
        raw_id = _first(raw, "id", "msg_id", "message_id")
        return ChatMessage(group_name, str(sender), str(content), str(msg_type), msg_time, default_time, _none_or_str(raw_id))
    if isinstance(raw, (tuple, list)):
        sender = raw[0] if len(raw) >= 2 else "未知"
        content = raw[1] if len(raw) >= 2 else raw[0] if raw else ""
        msg_time = _parse_datetime(raw[2], default_time) if len(raw) >= 3 else default_time
        return ChatMessage(group_name, str(sender), str(content), "text", msg_time, default_time)

    sender = _first_attr(raw, "sender", "sender_name", "who", "name", "from_user") or "未知"
    content = _first_attr(raw, "content", "text", "msg", "message") or str(raw)
    msg_type = _first_attr(raw, "type", "msg_type") or "text"
    msg_time = _parse_datetime(_first_attr(raw, "time", "msg_time", "create_time", "created_at"), default_time)
    raw_id = _first_attr(raw, "id", "msg_id", "message_id")
    return ChatMessage(group_name, str(sender), str(content), str(msg_type), msg_time, default_time, _none_or_str(raw_id))


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _first_attr(value: Any, *names: str) -> Any:
    for name in names:
        attr = getattr(value, name, None)
        if attr not in (None, ""):
            return attr() if callable(attr) and name in {"content", "text"} else attr
    return None


def _none_or_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _parse_datetime(value: Any, default: datetime) -> datetime:
    if value in (None, ""):
        return default
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return default
