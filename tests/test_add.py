from __future__ import annotations

from datetime import date
from pathlib import Path

from add import add_from_tokens, add_ids, main, parse_id_tokens, tokens_from_file
from store import load_green
from tests.conftest import write_json


def test_parse_single_several_and_range() -> None:
    assert parse_id_tokens(["316"]) == [316]
    assert parse_id_tokens(["316", "317", "320"]) == [316, 317, 320]
    assert parse_id_tokens(["300-315"]) == list(range(300, 316))
    assert parse_id_tokens(["316", "300-302"]) == [316, 300, 301, 302]


def test_add_single_several_range_unknown_duplicate(repo: Path) -> None:
    from store import load_questions

    _questions, records, catalog = load_questions(repo)
    today = date(2026, 11, 9)
    result = add_ids(catalog, records, [316, 317, 320, 1, 999], today)
    assert result.added == [316, 317, 320]
    assert result.already == [1]
    assert result.missing == [999]

    ranged = add_ids(catalog, records, list(range(300, 316)), today)
    assert 300 in ranged.added
    assert 315 in ranged.added
    again = add_ids(catalog, records, [316, 999], today)
    assert again.already == [316]
    assert again.missing == [999]


def test_add_from_tokens_persists_and_logs(repo: Path) -> None:
    result = add_from_tokens(repo, ["316", "1", "999"], today=date(2026, 11, 9), source="cli")
    assert result.summary().startswith("Added: #316")
    assert "Already in green: #1" in result.summary()
    assert "Not in catalog: #999" in result.summary()
    green = load_green(repo)
    assert any(int(r["id"]) == 316 for r in green)
    log = (repo / "log.jsonl").read_text(encoding="utf-8")
    assert '"event": "added"' in log
    assert '"id": 316' in log


def test_from_file(repo: Path, tmp_path: Path) -> None:
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("316, 317\n320\n300-302\n", encoding="utf-8")
    tokens = tokens_from_file(ids_file)
    result = add_from_tokens(repo, tokens, today=date(2026, 11, 9))
    assert 316 in result.added
    assert 317 in result.added
    assert 320 in result.added
    assert 300 in result.added


def test_cli_from_file(repo: Path, tmp_path: Path, capsys) -> None:
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("316\n", encoding="utf-8")
    code = main(["--root", str(repo), "--from-file", str(ids_file), "--now", "2026-11-09T08:00:00"])
    assert code == 0
    assert "#316" in capsys.readouterr().out
    assert any(int(r["id"]) == 316 for r in load_green(repo))


def test_empty_green_starts_clean(tmp_path: Path) -> None:
    write_json(tmp_path / "catalog.json", [{"id": 7, "difficulty": "easy"}])
    write_json(tmp_path / "green.json", [])
    write_json(tmp_path / "state.json", {"telegram_update_offset": 0, "today": None})
    (tmp_path / "log.jsonl").write_text("", encoding="utf-8")
    result = add_from_tokens(tmp_path, ["7"], today=date(2026, 11, 9))
    assert result.added == [7]
    assert result.already == []
    assert result.missing == []
