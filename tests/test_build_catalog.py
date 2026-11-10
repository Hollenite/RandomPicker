from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_build_catalog_from_csv_and_json(tmp_path: Path) -> None:
    csv_path = tmp_path / "dump.csv"
    csv_path.write_text("id,difficulty,statement\n10,Easy,Hello\n11,medium,\n", encoding="utf-8")
    out = tmp_path / "catalog.json"
    subprocess.check_call(
        [sys.executable, str(ROOT / "scripts" / "build_catalog.py"), str(csv_path), "-o", str(out)]
    )
    catalog = json.loads(out.read_text(encoding="utf-8"))
    assert catalog == [
        {"id": 10, "difficulty": "easy", "statement": "Hello"},
        {"id": 11, "difficulty": "medium"},
    ]

    json_path = tmp_path / "dump.json"
    json_path.write_text(
        json.dumps([{"id": 3, "difficulty": "difficult", "statement": "Hard"}]),
        encoding="utf-8",
    )
    subprocess.check_call(
        [sys.executable, str(ROOT / "scripts" / "build_catalog.py"), str(json_path), "-o", str(out)]
    )
    assert json.loads(out.read_text(encoding="utf-8")) == [
        {"id": 3, "difficulty": "difficult", "statement": "Hard"}
    ]


def test_leetcode_dump_skips_premium_maps_hard_and_strips_html(tmp_path: Path) -> None:
    dump = tmp_path / "leetcode.json"
    dump.write_text(
        json.dumps(
            [
                {
                    "difficulty": "Hard",
                    "frontendQuestionId": "42",
                    "paidOnly": False,
                    "title": "Trapping Rain Water",
                    "description": "<p>Trap <code>rain</code>.</p>",
                },
                {
                    "difficulty": "Easy",
                    "frontendQuestionId": "156",
                    "paidOnly": True,
                    "title": "Binary Tree Upside Down",
                    "description": "<p>Premium</p>",
                },
                {
                    "difficulty": "Easy",
                    "frontendQuestionId": "704",
                    "paidOnly": False,
                    "title": "Binary Search",
                    "description": "<p>Search.</p>",
                },
            ]
        ),
        encoding="utf-8",
    )
    catalog_path = tmp_path / "catalog.json"
    green_list = tmp_path / "green.txt"
    green_list.write_text("792 - Binary Search\n42 - Trapping Rain Water\n", encoding="utf-8")
    green_path = tmp_path / "green.json"
    subprocess.check_call(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_catalog.py"),
            str(dump),
            "-o",
            str(catalog_path),
            "--green-list",
            str(green_list),
            "--green-output",
            str(green_path),
            "--added-on",
            "2026-11-09",
        ]
    )
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert [item["id"] for item in catalog] == [42, 704]
    assert catalog[0]["difficulty"] == "difficult"
    assert catalog[0]["statement"] == "Trap rain."
    assert 156 not in {item["id"] for item in catalog}
    green = json.loads(green_path.read_text(encoding="utf-8"))
    assert [item["id"] for item in green] == [704, 42]
    assert all(item["added"] == "2026-11-09" for item in green)


def test_missing_dump_titles_stay_in_green_with_title(tmp_path: Path) -> None:
    dump = tmp_path / "leetcode.json"
    dump.write_text(
        json.dumps(
            [
                {
                    "difficulty": "Easy",
                    "frontendQuestionId": "1",
                    "paidOnly": False,
                    "title": "Two Sum",
                    "description": "<p>Two sum.</p>",
                }
            ]
        ),
        encoding="utf-8",
    )
    catalog_path = tmp_path / "catalog.json"
    green_list = tmp_path / "green.txt"
    green_list.write_text(
        "1 - Two Sum\n3997 - Maximize Sum of At Most K Distinct Elements\n",
        encoding="utf-8",
    )
    green_path = tmp_path / "green.json"
    subprocess.check_call(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_catalog.py"),
            str(dump),
            "-o",
            str(catalog_path),
            "--green-list",
            str(green_list),
            "--green-output",
            str(green_path),
            "--added-on",
            "2026-11-09",
        ]
    )
    catalog_ids = {item["id"] for item in json.loads(catalog_path.read_text(encoding="utf-8"))}
    assert 1 in catalog_ids
    assert 3684 in catalog_ids
    green = json.loads(green_path.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in green}
    assert by_id[1]["title"] == "Two Sum"
    assert by_id[3684]["title"] == "Maximize Sum of At Most K Distinct Elements"
