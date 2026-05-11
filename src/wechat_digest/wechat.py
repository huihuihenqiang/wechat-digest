from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone, tzinfo
import inspect
import re
import time as time_module
from typing import Any, Iterable, Protocol

from .models import ChatMessage, ensure_aware_utc


class WeChatError(RuntimeError):
    """Raised when desktop WeChat automation fails."""


class WeChatClient(Protocol):
    def check_ready(self) -> str:
        ...

    def fetch_recent_messages(self, group_name: str, limit: int) -> list[ChatMessage]:
        ...

    def fetch_messages_between(
        self,
        group_name: str,
        start: datetime,
        end: datetime,
        limit: int,
        max_scrolls: int = 0,
        scroll_pause_seconds: float = 0.2,
    ) -> tuple[list[ChatMessage], int]:
        ...

    def send_message(self, recipient: str, message: str) -> None:
        ...


class NoopWeChatClient:
    def check_ready(self) -> str:
        return "WeChat client is not initialized."

    def fetch_recent_messages(self, group_name: str, limit: int) -> list[ChatMessage]:
        raise WeChatError("WeChat client is required to collect messages.")

    def fetch_messages_between(
        self,
        group_name: str,
        start: datetime,
        end: datetime,
        limit: int,
        max_scrolls: int = 0,
        scroll_pause_seconds: float = 0.2,
    ) -> tuple[list[ChatMessage], int]:
        raise WeChatError("WeChat client is required to backfill messages.")

    def send_message(self, recipient: str, message: str) -> None:
        raise WeChatError("WeChat client is required to send messages.")


class LazyWeChatClient:
    def __init__(self) -> None:
        self._client: WxautoWeChatClient | None = None

    def _get_client(self) -> WxautoWeChatClient:
        if self._client is None:
            self._client = WxautoWeChatClient()
        return self._client

    def check_ready(self) -> str:
        return self._get_client().check_ready()

    def fetch_recent_messages(self, group_name: str, limit: int) -> list[ChatMessage]:
        return self._get_client().fetch_recent_messages(group_name, limit)

    def fetch_messages_between(
        self,
        group_name: str,
        start: datetime,
        end: datetime,
        limit: int,
        max_scrolls: int = 0,
        scroll_pause_seconds: float = 0.05,
    ) -> tuple[list[ChatMessage], int]:
        return self._get_client().fetch_messages_between(
            group_name,
            start,
            end,
            limit,
            max_scrolls=max_scrolls,
            scroll_pause_seconds=scroll_pause_seconds,
        )

    def send_message(self, recipient: str, message: str) -> None:
        self._get_client().send_message(recipient, message)


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
        time_module.sleep(0.5)
        if not self._ensure_at_chat_bottom():
            raise WeChatError(f"Could not scroll to latest messages in {group_name}. Make sure the chat window is visible and active.")
        raw_messages = self._read_messages(limit)
        now = datetime.now(timezone.utc)
        parsed = _coerce_messages(group_name, raw_messages, now, naive_tz=_local_timezone())
        parsed = [message for message in parsed if message.normalized_content]
        return parsed[-limit:]

    def fetch_messages_between(
        self,
        group_name: str,
        start: datetime,
        end: datetime,
        limit: int,
        max_scrolls: int = 0,
        scroll_pause_seconds: float = 0.2,
    ) -> tuple[list[ChatMessage], int]:
        self._switch_to_chat(group_name)
        time_module.sleep(0.5)
        if not self._ensure_at_chat_bottom():
            raise WeChatError(f"Could not scroll to latest messages in {group_name}. Make sure the chat window is visible and active.")
        start_utc = ensure_aware_utc(start)
        end_utc = ensure_aware_utc(end)
        seen: dict[str, ChatMessage] = {}
        scroll_count = 0

        for attempt in range(max_scrolls + 1):
            now = datetime.now(timezone.utc)
            raw_messages = self._read_messages(limit)
            parsed = _coerce_messages(group_name, raw_messages, now, naive_tz=_local_timezone())
            for message in parsed:
                if not message.normalized_content:
                    continue
                msg_time = ensure_aware_utc(message.msg_time)
                if start_utc <= msg_time < end_utc:
                    seen[message.content_hash] = message
            dated = [message for message in parsed if message.msg_time is not None]
            if dated:
                earliest = min(ensure_aware_utc(message.msg_time) for message in dated)
                if earliest <= start_utc:
                    break
            if attempt >= max_scrolls or not self._load_older_messages():
                break
            scroll_count += 1
            if scroll_pause_seconds > 0:
                time_module.sleep(scroll_pause_seconds)

        return sorted(seen.values(), key=lambda item: ensure_aware_utc(item.msg_time)), scroll_count

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

    def _load_older_messages(self) -> bool:
        for method_name in (
            "LoadMoreCache",
            "LoadMoreMessage",
            "LoadMoreMessages",
            "LoadMore",
            "ScrollUp",
            "scroll_up",
        ):
            method = getattr(self.wx, method_name, None)
            if callable(method):
                try:
                    if _call_accepts(method, "load_times"):
                        method(load_times=10)
                    else:
                        method()
                    return True
                except TypeError:
                    try:
                        method(n=10)
                        return True
                    except Exception:
                        continue
                except Exception:
                    continue
        return self._wheel_up_message_list()

    def _ensure_at_chat_bottom(self) -> bool:
        attempts = 0
        while attempts < 4:
            attempts += 1
            if attempts == 1:
                self._force_scroll_to_bottom()
                time_module.sleep(0.5)
                continue
            before_msgs = self._read_messages(30)
            before_keys = _message_keys(before_msgs, limit=5)
            self._force_scroll_to_bottom()
            time_module.sleep(0.5)
            after_msgs = self._read_messages(30)
            after_keys = _message_keys(after_msgs, limit=5)
            if before_keys == after_keys and before_keys:
                return True
        return False

    def _force_scroll_to_bottom(self) -> None:
        chatbox = getattr(self.wx, "ChatBox", None)
        if chatbox is not None:
            for attr_name in ("msgbox", "control"):
                ctrl = getattr(chatbox, attr_name, None)
                if ctrl is None:
                    continue
                # SetFocus (no click) avoids accidentally triggering links in chat messages.
                set_focus = getattr(ctrl, "SetFocus", None)
                if callable(set_focus):
                    try:
                        set_focus()
                    except Exception:
                        pass
                send_keys = getattr(ctrl, "SendKeys", None)
                if callable(send_keys):
                    try:
                        send_keys("{End}", api=False)
                        return
                    except Exception:
                        try:
                            send_keys("{End}")
                            return
                        except Exception:
                            pass
                wheel = getattr(ctrl, "WheelDown", None)
                if callable(wheel):
                    try:
                        if _call_accepts(wheel, "wheelTimes"):
                            wheel(wheelTimes=300, waitTime=0.03)
                        else:
                            wheel()
                        return
                    except Exception:
                        pass
        for method_name in ("EndKey", "GoBottom", "ToBottom", "ScrollToBottom", "LoadLatest", "ScrollDown", "scroll_down"):
            method = getattr(self.wx, method_name, None)
            if callable(method):
                try:
                    method()
                    return
                except Exception:
                    continue
        self._wheel_down_message_list()

    def _wheel_up_message_list(self) -> bool:
        chatbox = getattr(self.wx, "ChatBox", None)
        candidates = (
            getattr(chatbox, "msgbox", None),
            getattr(chatbox, "control", None),
        )
        for control in candidates:
            method = getattr(control, "WheelUp", None)
            if callable(method):
                try:
                    if _call_accepts(method, "wheelTimes"):
                        method(wheelTimes=40, waitTime=0.05)
                    else:
                        method()
                    return True
                except Exception:
                    continue
        return False

    def _wheel_down_message_list(self) -> bool:
        chatbox = getattr(self.wx, "ChatBox", None)
        candidates = (
            getattr(chatbox, "msgbox", None),
            getattr(chatbox, "control", None),
        )
        for control in candidates:
            method = getattr(control, "WheelDown", None)
            if callable(method):
                try:
                    if _call_accepts(method, "wheelTimes"):
                        method(wheelTimes=120, waitTime=0.02)
                    else:
                        method()
                    return True
                except Exception:
                    continue
            send_keys = getattr(control, "SendKeys", None)
            if callable(send_keys):
                try:
                    send_keys("{End}")
                    return True
                except Exception:
                    continue
        return False


def _message_keys(raw_messages, limit: int = 5):
    items: list[str] = []
    for raw in raw_messages:
        if isinstance(raw, dict):
            key = f"{raw.get('sender', '')}|{raw.get('content', '')[:80]}"
        elif isinstance(raw, (tuple, list)):
            key = f"{raw[0] if len(raw) >= 1 else ''}|{str(raw[1])[:80] if len(raw) >= 2 else ''}"
        else:
            key = f"{getattr(raw, 'sender', '')}|{str(getattr(raw, 'content', ''))[:80]}"
        if key.strip() == "|":
            continue
        items.append(key)
        if len(items) >= limit:
            break
    return tuple(items)


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
    if isinstance(value, dict):
        return list(value.values())
    try:
        return list(value)
    except TypeError:
        return [value]


def _coerce_messages(
    group_name: str,
    raw_messages: Iterable[Any],
    default_time: datetime,
    naive_tz: tzinfo | None = None,
) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    current_time: datetime | None = None
    for raw in raw_messages:
        message = _coerce_message(group_name, raw, default_time, naive_tz=naive_tz)
        if _is_time_marker(raw, message):
            current_time = message.msg_time
            continue
        if current_time is not None and _raw_time_value(raw) in (None, ""):
            message = replace(message, msg_time=current_time)
        messages.append(message)
    return messages


def _coerce_message(
    group_name: str,
    raw: Any,
    default_time: datetime,
    naive_tz: tzinfo | None = None,
) -> ChatMessage:
    if isinstance(raw, ChatMessage):
        return raw
    if isinstance(raw, dict):
        sender = _first(raw, "sender", "sender_name", "who", "name", "from") or "未知"
        content = _first(raw, "content", "text", "msg", "message") or ""
        msg_type = _first(raw, "type", "msg_type") or "text"
        msg_time = _parse_datetime(_raw_time_value(raw), default_time, naive_tz=naive_tz)
        raw_id = _first(raw, "id", "msg_id", "message_id")
        return ChatMessage(
            group_name,
            str(sender),
            str(content),
            str(msg_type),
            msg_time,
            default_time,
            _none_or_str(raw_id),
            _links_from_raw(raw),
        )
    if isinstance(raw, (tuple, list)):
        sender = raw[0] if len(raw) >= 2 else "未知"
        content = raw[1] if len(raw) >= 2 else raw[0] if raw else ""
        msg_time = _parse_datetime(_raw_time_value(raw), default_time, naive_tz=naive_tz)
        return ChatMessage(group_name, str(sender), str(content), "text", msg_time, default_time)

    sender = _first_attr(raw, "sender", "sender_name", "who", "name", "from_user") or "未知"
    content = _first_attr(raw, "content", "text", "msg", "message") or str(raw)
    msg_type = _first_attr(raw, "type", "msg_type") or "text"
    msg_time = _parse_datetime(_raw_time_value(raw), default_time, naive_tz=naive_tz)
    raw_id = _first_attr(raw, "id", "msg_id", "message_id")
    return ChatMessage(
        group_name,
        str(sender),
        str(content),
        str(msg_type),
        msg_time,
        default_time,
        _none_or_str(raw_id),
        _links_from_object(raw),
    )


def _is_time_marker(raw: Any, message: ChatMessage) -> bool:
    msg_type = str(message.msg_type or "").strip().lower()
    if msg_type in {"time", "timestamp"}:
        return True
    return type(raw).__name__.lower().endswith("timemessage")


def _raw_time_value(raw: Any) -> Any:
    time_keys = ("time", "msg_time", "create_time", "created_at", "timestamp", "datetime", "date")
    if isinstance(raw, dict):
        value = _first(raw, *time_keys)
        if value not in (None, ""):
            return value
        msg_type = str(_first(raw, "type", "msg_type") or "").strip().lower()
        if msg_type in {"time", "timestamp"}:
            return _first(raw, "content", "text", "msg", "message", "hash_text")
        return None
    if isinstance(raw, (tuple, list)):
        return raw[2] if len(raw) >= 3 else None

    value = _first_attr(raw, *time_keys)
    if value not in (None, ""):
        return value
    msg_type = str(_first_attr(raw, "type", "msg_type") or "").strip().lower()
    if msg_type in {"time", "timestamp"}:
        return _first_attr(raw, "content", "text", "msg", "message", "hash_text")
    return None


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


def _links_from_raw(raw: dict[str, Any]) -> tuple[str, ...]:
    links = _first(raw, "links", "urls", "url")
    return _coerce_links(links)


def _links_from_object(raw: Any) -> tuple[str, ...]:
    return _coerce_links(_first_attr(raw, "links", "urls", "url"))


def _coerce_links(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if item not in (None, ""))
    return (str(value),)


_FULL_DATETIME_RE = re.compile(
    r"(?P<year>\d{4})[-/年](?P<month>\d{1,2})[-/月](?P<day>\d{1,2})日?\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
)
_MONTH_DAY_RE = re.compile(
    r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日?\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
)
_FULL_DATE_RE = re.compile(r"(?P<year>\d{4})[-/年](?P<month>\d{1,2})[-/月](?P<day>\d{1,2})日?")
_MONTH_DAY_ONLY_RE = re.compile(r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日?")
_RELATIVE_TIME_RE = re.compile(
    r"(?P<label>今天|昨天|前天)\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
)
_WEEKDAY_RE = re.compile(
    r"(?P<label>星期[一二三四五六日天1-7]|周[一二三四五六日天1-7])"
    r"(?:\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?"
)
_TIME_ONLY_RE = re.compile(r"^\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?\s*$")


def _parse_datetime(value: Any, default: datetime, naive_tz: tzinfo | None = None) -> datetime:
    if value in (None, ""):
        return default
    naive_tz = naive_tz or timezone.utc
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=naive_tz).astimezone(timezone.utc)
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=naive_tz)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        parsed = _parse_wechat_time_text(text, default, naive_tz)
        return parsed or default


def _parse_wechat_time_text(text: str, default: datetime, naive_tz: tzinfo) -> datetime | None:
    match = _FULL_DATETIME_RE.search(text)
    if match:
        return _datetime_from_match(match, default, naive_tz, include_year=True).astimezone(timezone.utc)

    match = _MONTH_DAY_RE.search(text)
    if match:
        return _datetime_from_match(match, default, naive_tz, include_year=False).astimezone(timezone.utc)

    match = _RELATIVE_TIME_RE.search(text)
    if match:
        base = default.astimezone(naive_tz)
        days_back = {"今天": 0, "昨天": 1, "前天": 2}.get(match.group("label"), 0)
        local_date = base.date() - timedelta(days=days_back)
        local_dt = datetime(
            local_date.year,
            local_date.month,
            local_date.day,
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second") or 0),
            tzinfo=naive_tz,
        )
        return local_dt.astimezone(timezone.utc)

    match = _FULL_DATE_RE.search(text)
    if match:
        local_dt = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            0,
            0,
            tzinfo=naive_tz,
        )
        return local_dt.astimezone(timezone.utc)

    match = _MONTH_DAY_ONLY_RE.search(text)
    if match:
        base = default.astimezone(naive_tz)
        local_dt = datetime(
            base.year,
            int(match.group("month")),
            int(match.group("day")),
            0,
            0,
            tzinfo=naive_tz,
        )
        return local_dt.astimezone(timezone.utc)

    match = _WEEKDAY_RE.search(text)
    if match:
        base = default.astimezone(naive_tz)
        target_weekday = _weekday_number(match.group("label"))
        days_back = (base.weekday() - target_weekday) % 7
        local_date = base.date() - timedelta(days=days_back)
        local_dt = datetime(
            local_date.year,
            local_date.month,
            local_date.day,
            int(match.group("hour") or 0),
            int(match.group("minute") or 0),
            int(match.group("second") or 0),
            tzinfo=naive_tz,
        )
        return local_dt.astimezone(timezone.utc)

    match = _TIME_ONLY_RE.match(text)
    if match:
        base = default.astimezone(naive_tz)
        local_dt = base.replace(
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            second=int(match.group("second") or 0),
            microsecond=0,
        )
        return local_dt.astimezone(timezone.utc)
    return None


def _datetime_from_match(match: re.Match[str], default: datetime, naive_tz: tzinfo, include_year: bool) -> datetime:
    year = int(match.group("year")) if include_year else default.astimezone(naive_tz).year
    return datetime(
        year,
        int(match.group("month")),
        int(match.group("day")),
        int(match.group("hour")),
        int(match.group("minute")),
        int(match.group("second") or 0),
        tzinfo=naive_tz,
    )


def _local_timezone() -> tzinfo:
    return datetime.now().astimezone().tzinfo or timezone.utc


def _weekday_number(label: str) -> int:
    last = label[-1]
    mapping = {
        "一": 0,
        "1": 0,
        "二": 1,
        "2": 1,
        "三": 2,
        "3": 2,
        "四": 3,
        "4": 3,
        "五": 4,
        "5": 4,
        "六": 5,
        "6": 5,
        "日": 6,
        "天": 6,
        "7": 6,
    }
    return mapping[last]
