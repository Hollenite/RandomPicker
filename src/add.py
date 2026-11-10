"""Add catalog ids to green.json. Shared by the CLI and the Telegram /add command."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clock import today_ist
from store import (
    append_log,
    load_dotenv,
    load_questions,
    parse_now_arg,
    repo_root,
    save_green,
)


@dataclass
class AddResult:
    added: list[int] = field(default_factory=list)
    already: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)

    def summary(self) -> str:
        lines: list[str] = []
        if self.added:
            lines.append("Added: " + ", ".join(f"#{i}" for i in self.added))
        if self.already:
            lines.append("Already in green: " + ", ".join(f"#{i}" for i in self.already))
        if self.missing:
            lines.append("Not in catalog: " + ", ".join(f"#{i}" for i in self.missing))
        return "\n".join(lines) if lines else "Nothing to add."


def parse_id_tokens(tokens: list[str]) -> list[int]:
    ids: list[int] = []
    for raw in tokens:
        for piece in raw.replace(",", " ").split():
            token = piece.strip()
            if not token:
                continue
            if _is_range(token):
                start_s, end_s = token.split("-", 1)
                start, end = int(start_s), int(end_s)
                if start > end:
                    start, end = end, start
                ids.extend(range(start, end + 1))
            else:
                ids.append(int(token))
    return ids


def _is_range(token: str) -> bool:
    if token.count("-") != 1 or token.startswith("-") or token.endswith("-"):
        return False
    left, right = token.split("-", 1)
    return left.isdigit() and right.isdigit()


def tokens_from_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [piece for piece in text.replace(",", " ").split() if piece.strip()]


def add_ids(
    catalog_by_id: dict[int, dict],
    records: list[dict],
    ids: list[int],
    today: date,
    titles: dict[int, str] | None = None,
) -> AddResult:
    green_ids = {int(rec["id"]) for rec in records}
    result = AddResult()
    for qid in ids:
        if qid not in catalog_by_id:
            result.missing.append(qid)
            continue
        if qid in green_ids:
            result.already.append(qid)
            continue
        rec: dict = {
            "id": qid,
            "added": today.isoformat(),
            "last_revisited": None,
            "times_revisited": 0,
        }
        title = (titles or {}).get(qid)
        if title:
            rec["title"] = title
        records.append(rec)
        green_ids.add(qid)
        result.added.append(qid)
    return result


def add_from_tokens(
    root: Path,
    tokens: list[str],
    *,
    today: date | None = None,
    now=None,
    source: str = "cli",
) -> AddResult:
    _questions, records, catalog_by_id = load_questions(root)
    ids = parse_id_tokens(tokens)
    day = today if today is not None else today_ist(now)
    result = add_ids(catalog_by_id, records, ids, day)
    if result.added:
        save_green(root, records)
        for qid in result.added:
            difficulty = catalog_by_id[qid]["difficulty"]
            append_log(
                root,
                "added",
                now=now,
                qid=qid,
                difficulty=difficulty,
                source=source,
            )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add solved question ids to green.json")
    parser.add_argument("ids", nargs="*", help="Question ids or ranges like 300-315")
    parser.add_argument("--from-file", dest="from_file", help="File of ids, commas, or ranges")
    parser.add_argument("--root", default=None, help="Repo root (defaults to the project directory)")
    parser.add_argument("--now", default=None, help="ISO datetime in IST, for tests")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else repo_root()
    load_dotenv(root)
    tokens = list(args.ids)
    if args.from_file:
        tokens.extend(tokens_from_file(Path(args.from_file)))
    if not tokens:
        parser.error("provide ids or --from-file")
    try:
        result = add_from_tokens(root, tokens, now=parse_now_arg(args.now), source="cli")
    except ValueError as exc:
        print(f"Invalid id token: {exc}", file=sys.stderr)
        return 1
    print(result.summary())
    return 0 if not result.missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
