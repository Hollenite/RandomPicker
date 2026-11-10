from datetime import datetime, timedelta

from clock import IST
from watch_schedule import (
    awake_text,
    current_window,
    load_windows,
    next_wake,
    offline_text,
    watch_deadline,
)


WINDOWS = load_windows("08:00-12:00,14:00-18:00")


def ist(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 9, hour, minute, tzinfo=IST)


def test_windows_cover_eight_hours() -> None:
    total = timedelta()
    for window in WINDOWS:
        start = datetime.combine(ist(0).date(), window.start)
        end = datetime.combine(ist(0).date(), window.end)
        total += end - start
    assert total == timedelta(hours=8)


def test_current_window_and_gap() -> None:
    assert current_window(ist(8), WINDOWS) is WINDOWS[0]
    assert current_window(ist(11, 59), WINDOWS) is WINDOWS[0]
    assert current_window(ist(12), WINDOWS) is None
    assert current_window(ist(13), WINDOWS) is None
    assert current_window(ist(14), WINDOWS) is WINDOWS[1]
    assert current_window(ist(18), WINDOWS) is None


def test_next_wake() -> None:
    assert next_wake(ist(7, 59), WINDOWS) == ist(8)
    assert next_wake(ist(8, 1), WINDOWS) == ist(14)
    assert next_wake(ist(12), WINDOWS) == ist(14)
    assert next_wake(ist(18), WINDOWS) == datetime(2026, 11, 10, 8, 0, tzinfo=IST)


def test_deadline_caps_to_window() -> None:
    assert watch_deadline(ist(8), timedelta(hours=4), WINDOWS) == ist(12)
    assert watch_deadline(ist(10), timedelta(hours=4), WINDOWS) == ist(12)
    assert watch_deadline(ist(14), timedelta(hours=4), WINDOWS) == ist(18)
    assert watch_deadline(ist(16), timedelta(hours=4), WINDOWS) == ist(18)


def test_awake_and_offline_copy() -> None:
    assert "12:00 IST" in awake_text(ist(12))
    assert "Buttons reply immediately" in awake_text(ist(12))
    morning_off = offline_text(ist(12), WINDOWS)
    assert "I'm going offline." in morning_off
    assert "2 hours" in morning_off
    assert "14:00 IST" in morning_off
    evening_off = offline_text(ist(18), WINDOWS)
    assert "14 hours" in evening_off
    assert "tomorrow 08:00 IST" in evening_off
