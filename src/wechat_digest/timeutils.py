from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_FIXED_TIMEZONES: dict[str, tzinfo] = {
    "Asia/Hong_Kong": timezone(timedelta(hours=8), "Asia/Hong_Kong"),
    "Asia/Shanghai": timezone(timedelta(hours=8), "Asia/Shanghai"),
    "Asia/Chongqing": timezone(timedelta(hours=8), "Asia/Chongqing"),
    "Asia/Harbin": timezone(timedelta(hours=8), "Asia/Harbin"),
    "PRC": timezone(timedelta(hours=8), "PRC"),
}


def app_timezone(name: str) -> tzinfo:
    if name.upper() in {"UTC", "Z"}:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in _FIXED_TIMEZONES:
            return _FIXED_TIMEZONES[name]
        raise


def parse_digest_time(value: str) -> time:
    try:
        hour_text, minute_text = value.split(":", 1)
        return time(hour=int(hour_text), minute=int(minute_text))
    except Exception as exc:
        raise ValueError("digest.time must use HH:MM format") from exc


def parse_date(value: str | None, tz_name: str) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(app_timezone(tz_name)).date()


def day_window(target_date: date, tz_name: str) -> tuple[datetime, datetime]:
    tz = app_timezone(tz_name)
    start_local = datetime.combine(target_date, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def should_run_digest(now: datetime, configured_time: str) -> bool:
    digest_time = parse_digest_time(configured_time)
    return now.timetz().replace(tzinfo=None) >= digest_time
