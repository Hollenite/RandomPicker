"""Load and save catalog, green, state, and the append-only log."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from clock import now_ist, today_ist

ALLOWED_DIFFICULTIES = frozenset({"easy", "medium", "difficult"})
DEFAULT_STATE: dict[str, Any] = {"telegram_update_offset": 0, "today": None}


class StoreError(ValueError):
    """Invalid catalog, green, or state data."""


@dataclass(frozen=True)
class Question:
    id: int
    difficulty: str
    added: date
    last_revisited: date | None
    times_revisited: int
    statement: str | None = None
    title: str | None = None


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_dotenv(root: Path | None = None) -> None:
    path = (root or repo_root()) / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file() or path.stat().st_size == 0:
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_catalog(root: Path) -> list[dict[str, Any]]:
    data = _read_json(root / "catalog.json", [])
    if not isinstance(data, list):
        raise StoreError("catalog.json must be a JSON array")
    return data


def validate_catalog(catalog: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}
    for item in catalog:
        try:
            qid = int(item["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError(f"Catalog entry missing a valid id: {item!r}") from exc
        if qid in by_id:
            raise StoreError(f"Duplicate catalog id: {qid}")
        difficulty = str(item.get("difficulty", "")).strip().lower()
        if difficulty not in ALLOWED_DIFFICULTIES:
            raise StoreError(f"Invalid difficulty {item.get('difficulty')!r} for catalog id {qid}")
        statement = item.get("statement")
        by_id[qid] = {
            "id": qid,
            "difficulty": difficulty,
            "statement": statement if isinstance(statement, str) and statement.strip() else None,
        }
    return by_id


def load_green(root: Path) -> list[dict[str, Any]]:
    data = _read_json(root / "green.json", [])
    if not isinstance(data, list):
        raise StoreError("green.json must be a JSON array")
    return data


def save_green(root: Path, records: list[dict[str, Any]]) -> None:
    _write_json(root / "green.json", records)


def validate_green(records: list[dict[str, Any]], catalog_by_id: dict[int, dict[str, Any]]) -> None:
    seen: set[int] = set()
    for rec in records:
        try:
            qid = int(rec["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError(f"Green entry missing a valid id: {rec!r}") from exc
        if qid in seen:
            raise StoreError(f"Duplicate green id: {qid}")
        if qid not in catalog_by_id:
            raise StoreError(f"green.json id {qid} is not in catalog.json")
        seen.add(qid)


def join_questions(
    records: list[dict[str, Any]], catalog_by_id: dict[int, dict[str, Any]]
) -> list[Question]:
    questions: list[Question] = []
    for rec in records:
        qid = int(rec["id"])
        cat = catalog_by_id[qid]
        last = rec.get("last_revisited")
        title = rec.get("title") or cat.get("title")
        questions.append(
            Question(
                id=qid,
                difficulty=cat["difficulty"],
                added=date.fromisoformat(rec["added"]),
                last_revisited=date.fromisoformat(last) if last else None,
                times_revisited=int(rec.get("times_revisited") or 0),
                statement=cat.get("statement"),
                title=title.strip() if isinstance(title, str) and title.strip() else None,
            )
        )
    return questions


def load_questions(root: Path) -> tuple[list[Question], list[dict[str, Any]], dict[int, dict[str, Any]]]:
    catalog_by_id = validate_catalog(load_catalog(root))
    records = load_green(root)
    validate_green(records, catalog_by_id)
    return join_questions(records, catalog_by_id), records, catalog_by_id


def question_by_id(questions: list[Question], qid: int) -> Question | None:
    for question in questions:
        if question.id == qid:
            return question
    return None


def mark_revisited(records: list[dict[str, Any]], qid: int, today: date) -> bool:
    for rec in records:
        if int(rec["id"]) == qid:
            rec["last_revisited"] = today.isoformat()
            rec["times_revisited"] = int(rec.get("times_revisited") or 0) + 1
            return True
    return False


def load_state(root: Path) -> dict[str, Any]:
    data = _read_json(root / "state.json", DEFAULT_STATE)
    if not isinstance(data, dict):
        raise StoreError("state.json must be a JSON object")
    data.setdefault("telegram_update_offset", 0)
    data.setdefault("today", None)
    return data


def save_state(root: Path, state: dict[str, Any]) -> None:
    _write_json(root / "state.json", state)


def today_entry(state: dict[str, Any], today: date) -> dict[str, Any] | None:
    entry = state.get("today")
    if not entry:
        return None
    if entry.get("date") != today.isoformat():
        return None
    return entry


def append_log(
    root: Path,
    event: str,
    *,
    now: datetime | None = None,
    qid: int | None = None,
    difficulty: str | None = None,
    bucket: str | None = None,
    source: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    row: dict[str, Any] = {
        "ts": now_ist(now).isoformat(),
        "event": event,
        "id": qid,
        "difficulty": difficulty,
        "bucket": bucket,
        "source": source,
    }
    if extra:
        row.update(extra)
    path = root / "log.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_logs(root: Path) -> list[dict[str, Any]]:
    path = root / "log.jsonl"
    if not path.is_file() or path.stat().st_size == 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def new_today_state(
    *,
    today: date,
    question: Question | None,
    bucket: str | None,
    message_id: int | None,
    now: datetime,
    status: str = "pending",
) -> dict[str, Any]:
    offered = [question.id] if question else []
    return {
        "date": today.isoformat(),
        "id": question.id if question else None,
        "difficulty": question.difficulty if question else None,
        "title": question.title if question else None,
        "bucket": bucket,
        "offered_ids": offered,
        "message_id": message_id,
        "button_message_ids": [message_id] if message_id is not None and question else [],
        "status": status,
        "reminders_sent": 0,
        "last_reminder_ts": None,
        "hint_level": 0,
        "solution_confirm_pending": False,
        "prepared_ts": now_ist(now).isoformat(),
    }


def parse_now_arg(value: str | None) -> datetime:
    if not value:
        return now_ist()
    return now_ist(datetime.fromisoformat(value))


def current_date(now: datetime | None = None) -> date:
    return today_ist(now)
