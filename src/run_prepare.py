"""08:00 IST morning pick. Also imported by the poll job for self-healing."""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clock import today_ist
from greeting import fetch_greeting
from picker import pick_detailed
from store import (
    append_log,
    env_bool,
    load_dotenv,
    load_questions,
    load_state,
    new_today_state,
    parse_now_arg,
    repo_root,
    save_state,
    today_entry,
)
from telegram import TelegramClient, morning_text, empty_green_text, question_keyboard


def run_prepare(
    root: Path,
    now: datetime,
    rng: random.Random,
    bot: TelegramClient,
    *,
    source: str = "prepare",
) -> bool:
    """Pick and send today's question. Returns True if a new today entry was written."""
    state = load_state(root)
    today = today_ist(now)
    if today_entry(state, today) is not None:
        return False

    previous = state.get("today")
    if (
        previous
        and previous.get("status") == "pending"
        and previous.get("id") is not None
    ):
        append_log(
            root,
            "missed",
            now=now,
            qid=previous["id"],
            difficulty=previous.get("difficulty"),
            bucket=previous.get("bucket"),
            source=source,
        )

    questions, _records, _catalog = load_questions(root)
    picked = pick_detailed(questions, today, rng)
    if picked is None:
        bot.send_message(empty_green_text())
        state["today"] = new_today_state(
            today=today,
            question=None,
            bucket=None,
            message_id=None,
            now=now,
            status="empty",
        )
        save_state(root, state)
        return True

    question, bucket = picked
    message_id = bot.send_message(
        morning_text(
            question.id,
            question.difficulty,
            question.title,
            statement=question.statement,
            greeting=fetch_greeting(),
        ),
        question_keyboard("pending", question.id),
    )
    state["today"] = new_today_state(
        today=today,
        question=question,
        bucket=bucket,
        message_id=message_id,
        now=now,
        status="pending",
    )
    save_state(root, state)
    append_log(
        root,
        "picked",
        now=now,
        qid=question.id,
        difficulty=question.difficulty,
        bucket=bucket,
        source=source,
    )
    return True


def _build_bot(dry_run: bool) -> TelegramClient:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "0")
    if not dry_run and (not token or not chat_id or chat_id == "0"):
        raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    return TelegramClient(token, chat_id, dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pick today's revisit and send it on Telegram")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--now", default=None, help="ISO datetime interpreted as IST")
    parser.add_argument("--root", default=None)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else repo_root()
    load_dotenv(root)
    dry_run = args.dry_run or env_bool("DRY_RUN")
    now = parse_now_arg(args.now)
    rng = random.Random(args.seed)
    bot = _build_bot(dry_run)
    run_prepare(root, now, rng, bot, source="prepare")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
