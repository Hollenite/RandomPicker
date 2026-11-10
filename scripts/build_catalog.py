#!/usr/bin/env python3
"""Convert a CSV or JSON dump into catalog.json.

Accepts the simple `{id,difficulty,statement}` shape, or a LeetCode dump with
`frontendQuestionId`, `paidOnly`, `difficulty` (Easy/Medium/Hard), and `description`.
Premium (`paidOnly`) rows are skipped unless `--include-premium` is set.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from add import add_ids  # noqa: E402
from clock import today_ist  # noqa: E402
from store import ALLOWED_DIFFICULTIES, validate_catalog  # noqa: E402

DIFFICULTY_MAP = {
    "easy": "easy",
    "medium": "medium",
    "hard": "difficult",
    "difficult": "difficult",
}


def load_dump(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in ("questions", "items", "data"):
                if key in data and isinstance(data[key], list):
                    data = data[key]
                    break
            else:
                raise SystemExit("JSON dump must be an array or an object with a questions list")
        if not isinstance(data, list):
            raise SystemExit("JSON dump must be an array")
        return data
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def is_paid(row: dict) -> bool:
    value = row.get("paidOnly", row.get("paid_only", False))
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?i)</li>", "\n", text)
    text = re.sub(r"(?i)</h[1-6]>", "\n", text)
    text = re.sub(r"(?i)</pre>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def row_id(row: dict) -> int | None:
    raw = row.get("frontendQuestionId", row.get("id"))
    if raw is None or str(raw).strip() == "":
        return None
    return int(raw)


def row_title(row: dict) -> str:
    return str(row.get("title") or "").strip()


def row_difficulty(row: dict) -> str:
    raw = str(row.get("difficulty", "")).strip().lower()
    mapped = DIFFICULTY_MAP.get(raw)
    if mapped not in ALLOWED_DIFFICULTIES:
        qid = row.get("frontendQuestionId", row.get("id"))
        raise SystemExit(f"Invalid difficulty {row.get('difficulty')!r} for id {qid}")
    return mapped


def row_statement(row: dict) -> str | None:
    raw = row.get("statement")
    if not isinstance(raw, str) or not raw.strip():
        raw = row.get("description")
    if not isinstance(raw, str) or not raw.strip():
        return None
    if "<" in raw:
        text = html_to_text(raw)
    else:
        text = raw.strip()
    return text or None


def normalize_title(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", title.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def to_item(row: dict) -> dict:
    qid = row_id(row)
    if qid is None:
        raise SystemExit(f"Row missing id: {row!r}")
    item: dict = {"id": qid, "difficulty": row_difficulty(row)}
    statement = row_statement(row)
    if statement:
        item["statement"] = statement
    return item


def catalog_for_disk(catalog: list[dict]) -> list[dict]:
    disk: list[dict] = []
    for item in catalog:
        out = {"id": int(item["id"]), "difficulty": item["difficulty"]}
        if item.get("statement"):
            out["statement"] = item["statement"]
        disk.append(out)
    return disk


def normalize(rows: list[dict], *, include_premium: bool = False) -> list[dict]:
    catalog: list[dict] = []
    for row in rows:
        if not include_premium and is_paid(row):
            continue
        catalog.append(to_item(row))
    catalog.sort(key=lambda item: int(item["id"]))
    validate_catalog(catalog_for_disk(catalog))
    return catalog


def parse_green_line(line: str) -> tuple[int | None, str]:
    text = line.strip()
    if not text or text.startswith("#"):
        return None, ""
    match = re.match(r"^(\d+)\s*[-:]\s*(.+)$", text)
    if match:
        return int(match.group(1)), match.group(2).strip()
    if text.isdigit():
        return int(text), ""
    return None, text


def load_extra_problems() -> list[dict]:
    path = Path(__file__).resolve().parent / "extra_problems.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def extra_by_title() -> dict[str, dict]:
    return {normalize_title(item["title"]): item for item in load_extra_problems() if item.get("title")}


def merge_extras_into_catalog(disk: list[dict], extras: list[dict]) -> list[dict]:
    by_id = {int(item["id"]): item for item in disk}
    for extra in extras:
        qid = int(extra["id"])
        item = {
            "id": qid,
            "difficulty": extra["difficulty"],
        }
        if extra.get("statement"):
            item["statement"] = extra["statement"]
        by_id[qid] = item
    merged = list(by_id.values())
    merged.sort(key=lambda item: int(item["id"]))
    validate_catalog(merged)
    return merged


def apply_titles(records: list[dict], titles: dict[int, str]) -> None:
    for rec in records:
        qid = int(rec["id"])
        if qid in titles and not rec.get("title"):
            rec["title"] = titles[qid]


def resolve_green(
    lines: list[str],
    dump_rows: list[dict],
    catalog_ids: set[int],
) -> tuple[list[int], list[str], list[str], dict[int, str]]:
    by_title: dict[str, list[dict]] = {}
    by_id: dict[int, dict] = {}
    for row in dump_rows:
        if is_paid(row):
            continue
        qid = row_id(row)
        if qid is None:
            continue
        by_id[qid] = row
        title = row_title(row)
        if title:
            by_title.setdefault(normalize_title(title), []).append(row)

    extras = extra_by_title()
    resolved: list[int] = []
    seen: set[int] = set()
    remapped: list[str] = []
    missing: list[str] = []
    titles: dict[int, str] = {}
    for line in lines:
        listed_id, title = parse_green_line(line)
        if listed_id is None and not title:
            continue
        row = None
        if title:
            matches = by_title.get(normalize_title(title), [])
            if matches:
                row = matches[0]
        if row is None and listed_id is not None:
            row = by_id.get(listed_id)
            if row is not None and title and normalize_title(row_title(row)) != normalize_title(title):
                row = None
        extra = extras.get(normalize_title(title)) if title else None
        if row is None and extra is not None:
            qid = int(extra["id"])
            if listed_id is not None and listed_id != qid:
                remapped.append(f"{listed_id} {title} -> #{qid}")
            titles[qid] = str(extra.get("title") or title)
            if qid not in seen:
                seen.add(qid)
                resolved.append(qid)
            continue
        if row is None:
            if listed_id is not None and title:
                titles[listed_id] = title
                if listed_id not in seen:
                    seen.add(listed_id)
                    resolved.append(listed_id)
            else:
                missing.append(f"{listed_id} - {title}".strip(" -"))
            continue
        qid = row_id(row)
        assert qid is not None
        if qid not in catalog_ids and extra is None:
            missing.append(f"{qid} - {row_title(row)} (not in catalog)")
            continue
        if listed_id is not None and listed_id != qid:
            remapped.append(f"{listed_id} {title} -> #{qid}")
        if title:
            titles[qid] = title
        if qid not in seen:
            seen.add(qid)
            resolved.append(qid)
    return resolved, remapped, missing, titles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build catalog.json from a CSV or JSON dump")
    parser.add_argument("source", type=Path, help="CSV or JSON dump")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=ROOT / "catalog.json",
        help="Where to write catalog.json",
    )
    parser.add_argument(
        "--include-premium",
        action="store_true",
        help="Keep paidOnly=true questions (default: drop them)",
    )
    parser.add_argument(
        "--green-list",
        type=Path,
        help="Optional 'id - title' list to seed green.json (titles win over stale ids)",
    )
    parser.add_argument(
        "--green-output",
        type=Path,
        default=ROOT / "green.json",
        help="Where to write green.json when --green-list is set",
    )
    parser.add_argument("--added-on", default=None, help="ISO date for green.json added field")
    args = parser.parse_args(argv)

    rows = load_dump(args.source)
    catalog = normalize(rows, include_premium=args.include_premium)
    disk = catalog_for_disk(catalog)

    if args.green_list:
        lines = args.green_list.read_text(encoding="utf-8").splitlines()
        wanted = {
            normalize_title(title)
            for _listed, title in (parse_green_line(line) for line in lines)
            if title
        }
        extras = [item for item in load_extra_problems() if normalize_title(item["title"]) in wanted]
        disk = merge_extras_into_catalog(disk, extras)

    args.output.write_text(json.dumps(disk, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(disk)} questions to {args.output}")

    if args.green_list:
        catalog_ids = {int(item["id"]) for item in disk}
        resolved, remapped, missing, titles = resolve_green(lines, rows, catalog_ids)
        added_on = date.fromisoformat(args.added_on) if args.added_on else today_ist()
        catalog_by_id = {int(item["id"]): item for item in disk}
        records: list[dict] = []
        if args.green_output.is_file() and args.green_output.stat().st_size:
            existing = json.loads(args.green_output.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                records = existing
        result = add_ids(catalog_by_id, records, resolved, added_on, titles=titles)
        apply_titles(records, titles)
        args.green_output.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(
            f"Green added {len(result.added)}, already {len(result.already)}, "
            f"missing from catalog {len(result.missing)}"
        )
        titled = sum(1 for rec in records if rec.get("title"))
        print(f"Green records with title: {titled}")
        if remapped:
            print(f"Remapped {len(remapped)} ids by title (listed id did not match dump title)")
        if missing:
            print(f"Not in dump/catalog ({len(missing)}):")
            for item in missing:
                print(f"  {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
