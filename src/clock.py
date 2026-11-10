"""Asia/Kolkata (IST) clock helpers. Inject `now` at call sites for tests."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
PREPARE_TIME = time(8, 0)


def now_ist(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def today_ist(now: datetime | None = None) -> date:
    return now_ist(now).date()


def parse_iso_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return now_ist(dt)


def parse_hhmm(value: str) -> time:
    hour_s, minute_s = value.strip().split(":", 1)
    return time(int(hour_s), int(minute_s))


def is_at_or_after_prepare(now: datetime) -> bool:
    return now_ist(now).timetz().replace(tzinfo=None) >= PREPARE_TIME
