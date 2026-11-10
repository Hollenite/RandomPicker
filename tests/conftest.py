from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
import requests

from clock import IST
from telegram import TelegramClient

CHAT_ID = 12345
NOW = datetime(2026, 11, 9, 8, 0, tzinfo=IST)
ADDED = "2026-01-01"


@pytest.fixture(autouse=True)
def _stable_greeting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("run_prepare.fetch_greeting", lambda *args, **kwargs: "Good morning.")

CATALOG = [
    {"id": 1, "difficulty": "easy", "statement": "Sum two numbers."},
    {"id": 2, "difficulty": "easy", "statement": "Find the maximum."},
    {"id": 3, "difficulty": "medium", "statement": "Longest substring without repeat."},
    {"id": 4, "difficulty": "medium", "statement": "Search in rotated array."},
    {"id": 5, "difficulty": "difficult", "statement": "Hard DP on trees."},
    {"id": 142, "difficulty": "easy", "statement": "Full question text."},
    {"id": 300, "difficulty": "easy", "statement": "Range start."},
    {"id": 301, "difficulty": "easy"},
    {"id": 302, "difficulty": "easy"},
    {"id": 315, "difficulty": "medium"},
    {"id": 316, "difficulty": "medium", "statement": "Graph traversal."},
    {"id": 317, "difficulty": "easy"},
    {"id": 320, "difficulty": "difficult"},
]


def green_record(qid: int, last_revisited: str | None = None, times: int = 0) -> dict:
    return {
        "id": qid,
        "added": ADDED,
        "last_revisited": last_revisited,
        "times_revisited": times,
    }


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write_json(tmp_path / "catalog.json", CATALOG)
    write_json(
        tmp_path / "green.json",
        [green_record(1), green_record(2), green_record(3), green_record(4), green_record(5), green_record(142)],
    )
    write_json(tmp_path / "state.json", {"telegram_update_offset": 0, "today": None})
    (tmp_path / "log.jsonl").write_text("", encoding="utf-8")
    write_json(tmp_path / "tutor_cache.json", {})
    return tmp_path


def make_bot(injected: list[dict] | None = None) -> TelegramClient:
    return TelegramClient("token", CHAT_ID, dry_run=True, injected_updates=injected or [])


def message_update(update_id: int, text: str, user_id: int = CHAT_ID, chat_id: int = CHAT_ID) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 10,
            "from": {"id": user_id},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def callback_update(
    update_id: int,
    data: str,
    user_id: int = CHAT_ID,
    chat_id: int = CHAT_ID,
) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}",
            "from": {"id": user_id},
            "message": {
                "message_id": 1,
                "chat": {"id": chat_id, "type": "private"},
            },
            "data": data,
        },
    }


class FakeResponse:
    def __init__(self, content: str, status_code: int = 200) -> None:
        self.status_code = status_code
        self._content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}
