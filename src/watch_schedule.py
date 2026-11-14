"""Daily Telegram watch windows in IST: 4 hours on, 2 hours off, 4 hours on."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, time

from clock import IST, now_ist, parse_hhmm

DEFAULT_WINDOWS = "08:00-12:00,14:00-18:00"


@dataclass(frozen=True)
class Window:
    start: time
    end: time


def load_windows(raw: str | None = None) -> list[Window]:
    text = (raw if raw is not None else os.environ.get("WATCH_WINDOWS", DEFAULT_WINDOWS)).strip()
    windows: list[Window] = []
    for chunk in text.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        start_s, end_s = piece.split("-", 1)
        start, end = parse_hhmm(start_s), parse_hhmm(end_s)
        if end <= start:
            raise ValueError(f"watch window must end after it starts: {piece}")
        windows.append(Window(start, end))
    if not windows:
        raise ValueError("WATCH_WINDOWS must list at least one HH:MM-HH:MM window")
    return windows


def _clock(now: datetime) -> time:
    local = now_ist(now)
    return local.timetz().replace(tzinfo=None, second=0, microsecond=0)


def _on_date(day, hhmm: time) -> datetime:
    return datetime.combine(day, hhmm, tzinfo=IST)


def current_window(now: datetime, windows: list[Window] | None = None) -> Window | None:
    windows = windows if windows is not None else load_windows()
    stamp = _clock(now)
    for window in windows:
        if window.start <= stamp < window.end:
            return window
    return None


def session_end(now: datetime, windows: list[Window] | None = None) -> datetime | None:
    windows = windows if windows is not None else load_windows()
    window = current_window(now, windows)
    if window is None:
        return None
    return _on_date(now_ist(now).date(), window.end)


def done_sleep_grace() -> timedelta:
    raw = os.environ.get("DONE_SLEEP_GRACE_MIN", "5").strip()
    try:
        minutes = float(raw)
    except ValueError:
        minutes = 5
    return timedelta(minutes=max(0.0, minutes))


def next_wake(
    now: datetime,
    windows: list[Window] | None = None,
    *,
    skip_remaining_today: bool = False,
) -> datetime:
    windows = windows if windows is not None else load_windows()
    local = now_ist(now)
    if skip_remaining_today:
        return _on_date(local.date() + timedelta(days=1), windows[0].start)
    for window in windows:
        start = _on_date(local.date(), window.start)
        if start > local:
            return start
    return _on_date(local.date() + timedelta(days=1), windows[0].start)


def watch_deadline(now: datetime, duration: timedelta | None, windows: list[Window] | None = None) -> datetime:
    windows = windows if windows is not None else load_windows()
    local = now_ist(now)
    end = session_end(local, windows)
    if duration is None:
        return end if end is not None else local + timedelta(days=3650)
    capped = local + duration
    if end is not None:
        return min(capped, end)
    return capped


def format_delay(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 45:
        return "a moment"
    minutes = max(1, round(seconds / 60))
    if minutes < 90:
        return "1 minute" if minutes == 1 else f"{minutes} minutes"
    hours = max(1, round(minutes / 60))
    return "1 hour" if hours == 1 else f"{hours} hours"


def format_when(when: datetime, now: datetime) -> str:
    local = now_ist(when)
    current = now_ist(now)
    clock = local.strftime("%H:%M IST")
    if local.date() == current.date():
        return clock
    if local.date() == current.date() + timedelta(days=1):
        return f"tomorrow {clock}"
    return local.strftime("%Y-%m-%d %H:%M IST")


def awake_text(until: datetime) -> str:
    return (
        f"I'm awake until {now_ist(until).strftime('%H:%M IST')}. "
        "Buttons reply immediately until then."
    )


def packing_up_text(now: datetime, windows: list[Window] | None = None) -> str:
    windows = windows if windows is not None else load_windows()
    local = now_ist(now)
    wake = next_wake(local, windows, skip_remaining_today=True)
    grace = done_sleep_grace()
    wait = format_delay(grace) if grace > timedelta(0) else "a moment"
    when = format_when(wake, local)
    return (
        f"Today's revisit is done. I'll go offline in {wait} "
        f"and wake {when}."
    )


def offline_text(
    now: datetime,
    windows: list[Window] | None = None,
    *,
    until_tomorrow: bool = False,
) -> str:
    windows = windows if windows is not None else load_windows()
    local = now_ist(now)
    wake = next_wake(local, windows, skip_remaining_today=until_tomorrow)
    delay = format_delay(wake - local)
    when = format_when(wake, local)
    return f"I'm going offline. I'll be awake after {delay} ({when})."
