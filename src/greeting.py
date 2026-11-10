"""Daily greeting: prefer a live quote, fall back to a local line."""

from __future__ import annotations

import random
from typing import Any, Callable

import requests

ZEN_TODAY = "https://zenquotes.io/api/today"
ZEN_RANDOM = "https://zenquotes.io/api/random"
TIMEOUT_SECONDS = 4

FALLBACKS = (
    "Good morning. One problem, fully present.",
    "Good morning. Small reps compound.",
    "Good morning. Show up for yesterday-you.",
    "Good morning. Clarity comes after the first ten minutes.",
    "Good morning. Make this one count.",
)

HttpGet = Callable[..., Any]


def fetch_greeting(http_get: HttpGet | None = None) -> str:
    getter = http_get or requests.get
    for url in (ZEN_TODAY, ZEN_RANDOM):
        quote = _quote_from(getter, url)
        if quote:
            return f"Good morning.\n\n{quote}"
    return random.choice(FALLBACKS)


def _quote_from(getter: HttpGet, url: str) -> str | None:
    try:
        response = getter(url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, dict):
        return None
    text = str(payload.get("q") or payload.get("quote") or "").strip()
    if not text:
        return None
    author = str(payload.get("a") or payload.get("author") or "").strip()
    if author and author.lower() != "unknown":
        return f'"{text}" — {author}'
    return f'"{text}"'
