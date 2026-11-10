"""Poll Telegram updates, handle buttons/commands, send reminders, self-heal prepare."""

from __future__ import annotations

import argparse
import os
import random
import signal
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from add import add_from_tokens
from clock import is_at_or_after_prepare, parse_hhmm, parse_iso_datetime, today_ist
from picker import pick_detailed
from run_prepare import run_prepare
from store import (
    append_log,
    env_bool,
    load_dotenv,
    load_questions,
    load_state,
    mark_revisited,
    parse_now_arg,
    question_by_id,
    repo_root,
    save_green,
    save_state,
    today_entry,
)
from telegram import (
    CALLBACK_DONE,
    CALLBACK_EXPLAIN,
    CALLBACK_HINT,
    CALLBACK_REROLL,
    CALLBACK_SOLUTION,
    CALLBACK_SOLUTION_CANCEL,
    CALLBACK_SOLUTION_CONFIRM,
    TelegramClient,
    done_text,
    is_authorized,
    parse_command,
    pending_keyboard,
    question_keyboard,
    reminder_text,
    reroll_text,
    solution_confirm_keyboard,
    split_message,
)
from tutor import (
    GroqError,
    MissingStatementError,
    cache_get,
    explain,
    hint,
    load_cache,
    preferred_lang,
    save_cache,
    solution,
)
from watch_schedule import awake_text, load_windows, offline_text, watch_deadline


STATE_FILES = ("state.json", "green.json", "log.jsonl", "tutor_cache.json")


HttpPost = Callable[..., Any]
SleepFn = Callable[[float], None]


def reminder_settings() -> tuple[Any, Any, int, int]:
    start = parse_hhmm(os.environ.get("REMINDER_START", "09:00"))
    end = parse_hhmm(os.environ.get("REMINDER_END", "22:00"))
    interval = int(os.environ.get("REMINDER_INTERVAL_MIN", "120"))
    maximum = int(os.environ.get("MAX_REMINDERS", "8"))
    return start, end, interval, maximum


def reminder_due(entry: dict[str, Any] | None, now: datetime) -> bool:
    if not entry or entry.get("status") != "pending" or entry.get("id") is None:
        return False
    start, end, interval_min, max_reminders = reminder_settings()
    if int(entry.get("reminders_sent") or 0) >= max_reminders:
        return False
    clock = now.timetz().replace(tzinfo=None, second=0, microsecond=0)
    if clock < start or clock > end:
        return False
    anchor_raw = entry.get("last_reminder_ts") or entry.get("prepared_ts")
    if not anchor_raw:
        return True
    anchor = parse_iso_datetime(str(anchor_raw))
    return now - anchor >= timedelta(minutes=interval_min)


class App:
    def __init__(
        self,
        root: Path,
        bot: TelegramClient,
        now: datetime,
        rng: random.Random,
        *,
        http_post: HttpPost | None = None,
        sleep: SleepFn | None = None,
        source: str = "poll",
    ) -> None:
        self.root = root
        self.bot = bot
        self.now = now
        self.rng = rng
        self.http_post = http_post
        self.sleep = sleep
        self.source = source
        self.state = load_state(root)

    def run(self, *, poll_timeout: int = 0) -> None:
        if is_at_or_after_prepare(self.now):
            run_prepare(self.root, self.now, self.rng, self.bot, source=self.source)
            self.state = load_state(self.root)

        offset = int(self.state.get("telegram_update_offset") or 0)
        try:
            updates = self.bot.get_updates(offset, timeout=poll_timeout)
        except Exception as exc:
            print(f"getUpdates failed: {_redact(exc)}", file=sys.stderr)
            updates = []

        deferred: list[Callable[[], None]] = []
        for update in updates:
            self.state["telegram_update_offset"] = int(update["update_id"]) + 1
            if not self._authorized(update):
                continue
            job = self._handle_update(update)
            if job is not None:
                deferred.append(job)

        save_state(self.root, self.state)
        for job in deferred:
            job()
        self._maybe_remind()
        save_state(self.root, self.state)

    def _authorized(self, update: dict[str, Any]) -> bool:
        try:
            expected = int(self.bot.chat_id)
        except (TypeError, ValueError):
            return False
        return is_authorized(update, expected)

    def _entry(self) -> dict[str, Any] | None:
        return today_entry(self.state, today_ist(self.now))

    def _handle_update(self, update: dict[str, Any]) -> Callable[[], None] | None:
        if "callback_query" in update:
            return self._handle_callback(update["callback_query"])
        if "message" in update:
            self._handle_message(update["message"])
        return None

    def _handle_message(self, message: dict[str, Any]) -> None:
        parsed = parse_command(message.get("text"))
        if not parsed:
            return
        name, args = parsed
        if name == "add":
            self._cmd_add(args)
        elif name == "done":
            self._cmd_done(args)
        elif name == "reroll":
            self._reroll()
        save_state(self.root, self.state)

    def _handle_callback(self, callback: dict[str, Any]) -> Callable[[], None] | None:
        data = str(callback.get("data") or "")
        callback_id = str(callback.get("id") or "")
        entry = self._entry()
        job: Callable[[], None] | None = None

        if data == CALLBACK_DONE:
            self._done(source="telegram_done")
            self.bot.answer_callback(callback_id)
        elif data == CALLBACK_REROLL:
            self._reroll()
            self.bot.answer_callback(callback_id)
        elif data == CALLBACK_EXPLAIN:
            job = self._explain(entry, callback_id)
        elif data == CALLBACK_HINT:
            job = self._hint(entry, callback_id)
        elif data == CALLBACK_SOLUTION:
            self._solution_ask(entry, callback_id)
        elif data == CALLBACK_SOLUTION_CONFIRM:
            job = self._solution_confirm(entry, callback_id)
        elif data == CALLBACK_SOLUTION_CANCEL:
            self._solution_cancel(entry, callback_id)
        else:
            self.bot.answer_callback(callback_id)
        save_state(self.root, self.state)
        return job

    def _cmd_add(self, args: list[str]) -> None:
        if not args:
            self.bot.send_message("Usage: /add 316  or  /add 300-315")
            return
        result = add_from_tokens(
            self.root,
            args,
            today=today_ist(self.now),
            now=self.now,
            source="telegram",
        )
        self.bot.send_message(result.summary())

    def _cmd_done(self, args: list[str]) -> None:
        entry = self._entry()
        if args:
            try:
                requested = int(args[0])
            except ValueError:
                self.bot.send_message(f"Not a question id: {args[0]}")
                return
            if not entry or entry.get("id") is None:
                self.bot.send_message("No pending revisit today.")
                return
            if requested != int(entry["id"]):
                self.bot.send_message(
                    f"#{requested} is not today's pending pick (#{entry['id']})."
                )
                return
        self._done(source="telegram_done")

    def _done(self, source: str) -> bool:
        entry = self._entry()
        if not entry or entry.get("id") is None:
            self.bot.send_message("No pending revisit today.")
            return False
        if entry.get("status") == "done":
            self.bot.send_message("Already done today.")
            return False
        if entry.get("status") != "pending":
            self.bot.send_message("No pending revisit today.")
            return False

        qid = int(entry["id"])
        questions, records, _catalog = load_questions(self.root)
        question = question_by_id(questions, qid)
        mark_revisited(records, qid, today_ist(self.now))
        save_green(self.root, records)
        entry["status"] = "done"
        entry["solution_confirm_pending"] = False

        original = entry.get("message_id")
        if original is not None:
            self.bot.edit_message(
                int(original),
                done_text(qid, question.title if question else None),
                None,
            )
        for mid in list(entry.get("button_message_ids") or []):
            if mid != original:
                self.bot.edit_reply_markup(int(mid), None)
        entry["button_message_ids"] = []
        append_log(
            self.root,
            "done",
            now=self.now,
            qid=qid,
            difficulty=question.difficulty if question else entry.get("difficulty"),
            bucket=entry.get("bucket"),
            source=source,
        )
        return True

    def _reroll(self) -> None:
        entry = self._entry()
        if not entry or entry.get("id") is None:
            self.bot.send_message("No pending revisit today.")
            return
        if entry.get("status") == "done":
            self.bot.send_message("Already done today.")
            return
        if entry.get("status") != "pending":
            self.bot.send_message("No pending revisit today.")
            return

        questions, _records, _catalog = load_questions(self.root)
        exclude = frozenset(int(i) for i in (entry.get("offered_ids") or []))
        picked = pick_detailed(questions, today_ist(self.now), self.rng, exclude)
        if picked is None:
            self.bot.send_message("No other questions left to reroll today.")
            return

        question, bucket = picked
        offered = list(exclude)
        offered.append(question.id)
        entry["id"] = question.id
        entry["difficulty"] = question.difficulty
        entry["title"] = question.title
        entry["bucket"] = bucket
        entry["offered_ids"] = offered
        entry["hint_level"] = 0
        entry["solution_confirm_pending"] = False

        original = entry.get("message_id")
        if original is not None:
            self.bot.edit_message(
                int(original),
                reroll_text(
                    question.id,
                    question.difficulty,
                    question.title,
                    statement=question.statement,
                ),
                pending_keyboard(),
            )
        append_log(
            self.root,
            "rerolled",
            now=self.now,
            qid=question.id,
            difficulty=question.difficulty,
            bucket=bucket,
            source="telegram_reroll",
        )

    def _require_pending(self, entry: dict[str, Any] | None, callback_id: str) -> dict[str, Any] | None:
        if not entry or entry.get("id") is None or entry.get("status") != "pending":
            text = "Already done today." if entry and entry.get("status") == "done" else "No pending revisit today."
            self.bot.answer_callback(callback_id, text)
            return None
        return entry

    def _heard_you(self, callback_id: str, what: str) -> None:
        self.bot.answer_callback(callback_id, "On it")
        self.bot.send_message(f"Okay, I heard you — {what}")

    def _explain(self, entry: dict[str, Any] | None, callback_id: str) -> Callable[[], None] | None:
        entry = self._require_pending(entry, callback_id)
        if entry is None:
            return None
        self._heard_you(callback_id, "writing the explanation.")

        def finish() -> None:
            question = self._today_question(entry)
            if question is None:
                return
            text = self._tutor_or_error(lambda cache: explain(
                question, cache, http_post=self.http_post, sleep=self.sleep
            ), question, entry)
            if text:
                self._send_question_reply(text, entry)
                self._log_tutor("explain", question, entry)

        return finish

    def _hint(self, entry: dict[str, Any] | None, callback_id: str) -> Callable[[], None] | None:
        entry = self._require_pending(entry, callback_id)
        if entry is None:
            return None
        next_level = int(entry.get("hint_level") or 0) + 1
        if next_level > 3:
            self.bot.answer_callback(callback_id, "No more hints.")
            self._send_question_reply("No more hints.", entry)
            return None
        self._heard_you(callback_id, f"fetching hint {next_level}/3.")
        entry["hint_level"] = next_level

        def finish() -> None:
            question = self._today_question(entry)
            if question is None:
                return
            cache = load_cache(self.root)
            previous = []
            for level in range(1, next_level):
                prior = cache_get(cache, question, "hint", level=level)
                if prior:
                    previous.append(prior)
            text = self._tutor_or_error(
                lambda cache: hint(
                    question,
                    next_level,
                    cache,
                    previous,
                    http_post=self.http_post,
                    sleep=self.sleep,
                ),
                question,
                entry,
                cache=cache,
            )
            if text:
                self._send_question_reply(f"Hint {next_level}/3\n\n{text}", entry)
                self._log_tutor(f"hint_{next_level}", question, entry)

        return finish

    def _solution_ask(self, entry: dict[str, Any] | None, callback_id: str) -> None:
        entry = self._require_pending(entry, callback_id)
        if entry is None:
            return
        self.bot.answer_callback(callback_id)
        entry["solution_confirm_pending"] = True
        message_id = self.bot.send_message(
            "Sure? Try a hint first",
            solution_confirm_keyboard(),
        )
        entry.setdefault("button_message_ids", []).append(message_id)

    def _solution_confirm(self, entry: dict[str, Any] | None, callback_id: str) -> Callable[[], None] | None:
        entry = self._require_pending(entry, callback_id)
        if entry is None:
            return None
        if not entry.get("solution_confirm_pending"):
            self.bot.answer_callback(callback_id, "Tap Solution first.")
            return None
        self._heard_you(callback_id, "writing the solution.")
        entry["solution_confirm_pending"] = False

        def finish() -> None:
            question = self._today_question(entry)
            if question is None:
                return
            text = self._tutor_or_error(
                lambda cache: solution(
                    question,
                    cache,
                    preferred_lang(),
                    http_post=self.http_post,
                    sleep=self.sleep,
                ),
                question,
                entry,
            )
            if text:
                self._send_question_reply(text, entry)
                self._log_tutor("solution", question, entry, extra={"needed_solution": True})

        return finish

    def _solution_cancel(self, entry: dict[str, Any] | None, callback_id: str) -> None:
        entry = self._require_pending(entry, callback_id)
        if entry is None:
            return
        self.bot.answer_callback(callback_id, "Cancelled.")
        entry["solution_confirm_pending"] = False
        self._send_question_reply("Cancelled.", entry)

    def _today_question(self, entry: dict[str, Any]):
        questions, _records, _catalog = load_questions(self.root)
        question = question_by_id(questions, int(entry["id"]))
        if question is None:
            self._send_question_reply("That question is no longer in the green set.", entry)
        return question

    def _tutor_or_error(
        self,
        fn,
        question,
        entry: dict[str, Any],
        cache: dict[str, Any] | None = None,
    ) -> str | None:
        try:
            cache = load_cache(self.root) if cache is None else cache
            text = fn(cache)
            save_cache(self.root, cache)
            return text
        except MissingStatementError:
            self._send_question_reply(
                f"No statement in the catalog for #{question.id}.",
                entry,
            )
            return None
        except GroqError:
            self._send_question_reply("Tutor unavailable, try again later", entry)
            return None

    def _send_question_reply(self, text: str, entry: dict[str, Any]) -> None:
        chunks = split_message(text)
        markup = question_keyboard(entry.get("status", "pending"))
        last = len(chunks) - 1
        for index, chunk in enumerate(chunks):
            keyboard = markup if index == last else None
            message_id = self.bot.send_message(chunk, keyboard)
            if keyboard is not None:
                entry.setdefault("button_message_ids", []).append(message_id)

    def _log_tutor(
        self,
        event: str,
        question,
        entry: dict[str, Any],
        extra: dict[str, Any] | None = None,
    ) -> None:
        append_log(
            self.root,
            event,
            now=self.now,
            qid=question.id,
            difficulty=question.difficulty,
            bucket=entry.get("bucket"),
            source="telegram_tutor",
            extra=extra,
        )

    def _maybe_remind(self) -> None:
        entry = self._entry()
        if not reminder_due(entry, self.now):
            return
        assert entry is not None
        qid = int(entry["id"])
        number = int(entry.get("reminders_sent") or 0) + 1
        message_id = self.bot.send_message(
            reminder_text(qid, number, entry.get("title")),
            question_keyboard("pending"),
        )
        entry["reminders_sent"] = number
        entry["last_reminder_ts"] = self.now.isoformat()
        entry.setdefault("button_message_ids", []).append(message_id)
        append_log(
            self.root,
            "reminder_sent",
            now=self.now,
            qid=qid,
            difficulty=entry.get("difficulty"),
            bucket=entry.get("bucket"),
            source=self.source,
            extra={"reminder": number},
        )


def _redact(exc: object) -> str:
    text = str(exc)
    for name in ("TELEGRAM_BOT_TOKEN", "GROQ_API_KEY"):
        secret = os.environ.get(name, "").strip()
        if secret:
            text = text.replace(secret, "***")
    return text


def commit_state_files(root: Path) -> None:
    if not env_bool("COMMIT_STATE"):
        return
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return
    subprocess.run(["git", "add", *STATE_FILES], cwd=root, check=False)
    dirty = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=root)
    if dirty.returncode == 0:
        return
    committed = subprocess.run(
        ["git", "commit", "-m", "chore: poll telegram updates"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if committed.returncode != 0:
        print(_redact(committed.stderr or committed.stdout), file=sys.stderr)
        return
    subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=root, check=False)
    pushed = subprocess.run(["git", "push"], cwd=root, capture_output=True, text=True)
    if pushed.returncode != 0:
        print(_redact(pushed.stderr or pushed.stdout), file=sys.stderr)


def run_watch(
    root: Path,
    bot: TelegramClient,
    rng: random.Random,
    *,
    clock: Callable[[], datetime],
    duration: timedelta | None,
    commit: Callable[[], None] | None = None,
) -> None:
    windows = load_windows()
    start = clock()
    deadline = watch_deadline(start, duration, windows)
    bot.send_message(awake_text(deadline))
    if commit:
        commit()

    stop = {"flag": False}

    def _stop(_signum: int, _frame: object) -> None:
        stop["flag"] = True

    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        while not stop["flag"]:
            now = clock()
            if now >= deadline:
                break
            remaining = (deadline - now).total_seconds()
            timeout = min(25, max(1, int(remaining)))
            App(root, bot, now, rng).run(poll_timeout=timeout)
            if commit:
                commit()
            if duration == timedelta(0):
                break
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
        bot.send_message(offline_text(clock(), windows))
        if commit:
            commit()


def _build_bot(dry_run: bool, injected: list[dict] | None = None) -> TelegramClient:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "0")
    if not dry_run and (not token or not chat_id or chat_id == "0"):
        raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    return TelegramClient(token, chat_id, dry_run=dry_run, injected_updates=injected or [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poll Telegram and send revisit reminders")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--watch", action="store_true", help="Long-poll until interrupted")
    parser.add_argument(
        "--watch-for",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Stop watching after this many seconds (capped by the current IST window)",
    )
    parser.add_argument("--now", default=None, help="ISO datetime interpreted as IST")
    parser.add_argument("--root", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--commit-only", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else repo_root()
    load_dotenv(root)
    if args.commit_only:
        commit_state_files(root)
        return 0
    dry_run = args.dry_run or env_bool("DRY_RUN")
    rng = random.Random(args.seed)
    bot = _build_bot(dry_run)
    watching = args.watch or args.watch_for is not None
    if watching:
        frozen = args.now is not None
        if frozen:
            duration = timedelta(0)
        elif args.watch_for is not None:
            duration = timedelta(seconds=args.watch_for)
        else:
            duration = None

        def clock() -> datetime:
            return parse_now_arg(args.now) if frozen else parse_now_arg(None)

        if duration is None:
            print("Watching Telegram. Ctrl+C to stop.", flush=True)
        run_watch(
            root,
            bot,
            rng,
            clock=clock,
            duration=duration,
            commit=(lambda: commit_state_files(root)),
        )
        return 0
    App(root, bot, parse_now_arg(args.now), rng).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
