from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from store import StoreError, load_questions, mark_revisited, save_green
from tests.conftest import green_record, write_json


def test_green_title_is_joined(repo: Path) -> None:
    records = json.loads((repo / "green.json").read_text())
    records[0]["title"] = "Two Sum"
    write_json(repo / "green.json", records)
    questions, _records, _catalog = load_questions(repo)
    assert questions[0].title == "Two Sum"


def test_join_green_to_catalog(repo: Path) -> None:
    questions, records, catalog = load_questions(repo)
    by_id = {q.id: q for q in questions}
    assert by_id[142].difficulty == "easy"
    assert by_id[142].statement == "Full question text."
    assert by_id[3].difficulty == "medium"
    assert all(q.last_revisited is None for q in questions)
    assert len(records) == 6
    assert 142 in catalog


def test_duplicate_catalog_id(repo: Path) -> None:
    catalog = json.loads((repo / "catalog.json").read_text())
    catalog.append({"id": 1, "difficulty": "easy"})
    write_json(repo / "catalog.json", catalog)
    with pytest.raises(StoreError, match="Duplicate catalog id: 1"):
        load_questions(repo)


def test_green_id_missing_from_catalog(repo: Path) -> None:
    records = json.loads((repo / "green.json").read_text())
    records.append(green_record(9999))
    write_json(repo / "green.json", records)
    with pytest.raises(StoreError, match="9999 is not in catalog.json"):
        load_questions(repo)


def test_bad_catalog_difficulty(repo: Path) -> None:
    catalog = json.loads((repo / "catalog.json").read_text())
    catalog[0]["difficulty"] = "hard"
    write_json(repo / "catalog.json", catalog)
    with pytest.raises(StoreError, match="Invalid difficulty"):
        load_questions(repo)


def test_duplicate_green_id(repo: Path) -> None:
    records = json.loads((repo / "green.json").read_text())
    records.append(green_record(1))
    write_json(repo / "green.json", records)
    with pytest.raises(StoreError, match="Duplicate green id: 1"):
        load_questions(repo)


def test_mark_revisited_increments_once(repo: Path) -> None:
    _questions, records, _catalog = load_questions(repo)
    assert mark_revisited(records, 142, date(2026, 11, 9))
    save_green(repo, records)
    questions, records, _ = load_questions(repo)
    q = next(x for x in questions if x.id == 142)
    assert q.last_revisited == date(2026, 11, 9)
    assert q.times_revisited == 1
    assert next(r for r in records if r["id"] == 142)["last_revisited"] == "2026-11-09"
