from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from clock import IST
from run_poll import App, reminder_due, run_watch
from run_prepare import main as prepare_main
from run_prepare import run_prepare
from run_poll import main as poll_main
from store import load_green, load_state, read_logs
from telegram import (
    morning_text,
    CALLBACK_DONE,
    CALLBACK_EXPLAIN,
    CALLBACK_HINT,
    CALLBACK_REROLL,
    CALLBACK_SOLUTION,
    CALLBACK_SOLUTION_CANCEL,
    CALLBACK_SOLUTION_CONFIRM,
    callback_data,
    keyboard_labels,
    pending_keyboard,
    question_keyboard,
)
from tests.conftest import (
    CHAT_ID,
    FakeResponse,
    callback_update,
    make_bot,
    message_update,
)

MORNING = datetime(2026, 11, 9, 8, 0, tzinfo=IST)
HALF_EIGHT = datetime(2026, 11, 9, 8, 30, tzinfo=IST)
TEN = datetime(2026, 11, 9, 10, 0, tzinfo=IST)
NOON = datetime(2026, 11, 9, 12, 0, tzinfo=IST)
EVENING = datetime(2026, 11, 9, 21, 0, tzinfo=IST)


def pending_labels() -> list[str]:
    return keyboard_labels(pending_keyboard(1))


def tap(repo: Path, update_id: int, action: str, qid: int | None = None, **kwargs) -> dict:
    if qid is None:
        qid = load_state(repo)["today"]["id"]
    return callback_update(update_id, callback_data(action, qid), **kwargs)


def test_pending_keyboard_has_tutor_done_and_reroll() -> None:
    labels = pending_labels()
    assert labels == [
        "Explain 🧠",
        "Hint 💡",
        "Solution 🔓",
        "Done ✅",
        "Reroll 🎲",
    ]
    rows = pending_keyboard(142)["inline_keyboard"]
    assert len(rows) == 5
    assert all(len(row) == 1 for row in rows)
    assert rows[0][0]["callback_data"] == "explain:142"
    assert question_keyboard("done") is None
    assert question_keyboard("pending") is None
    assert question_keyboard("pending", 142) is not None
    text = morning_text(
        3684,
        "easy",
        "Maximize Sum of At Most K Distinct Elements",
        statement="Pick at most k distinct values.",
        greeting="Good morning.",
    )
    assert "Maximize Sum" in text
    assert "Pick at most k distinct values." in text


def _prepare(repo: Path, now=MORNING, seed: int = 1):
    bot = make_bot()
    run_prepare(repo, now, random.Random(seed), bot)
    return bot, load_state(repo)


def test_prepare_sends_morning_message_with_buttons(repo: Path) -> None:
    bot, state = _prepare(repo)
    assert state["today"]["status"] == "pending"
    assert state["today"]["id"] is not None
    assert bot.sent
    assert "Good morning" in bot.sent[0]["text"]
    assert "Today's revisit" in bot.sent[0]["text"]
    assert "Done ✅" in keyboard_labels(bot.sent[0]["reply_markup"])
    assert "Reroll 🎲" in keyboard_labels(bot.sent[0]["reply_markup"])
    assert all(len(row) == 1 for row in bot.sent[0]["reply_markup"]["inline_keyboard"])
    events = [row["event"] for row in read_logs(repo)]
    assert "picked" in events


def test_prepare_skips_if_today_exists(repo: Path) -> None:
    bot1, _ = _prepare(repo)
    bot2 = make_bot()
    did = run_prepare(repo, MORNING, random.Random(1), bot2)
    assert did is False
    assert bot2.sent == []


def test_missed_yesterday_is_logged_not_backfilled(repo: Path) -> None:
    _prepare(repo, now=datetime(2026, 11, 8, 8, 0, tzinfo=IST), seed=1)
    yesterday_id = load_state(repo)["today"]["id"]
    bot = make_bot()
    run_prepare(repo, MORNING, random.Random(2), bot)
    logs = read_logs(repo)
    missed = [row for row in logs if row["event"] == "missed"]
    assert missed and missed[0]["id"] == yesterday_id
    assert load_state(repo)["today"]["date"] == "2026-11-09"


def test_empty_green_message(tmp_path: Path) -> None:
    from tests.conftest import CATALOG, write_json

    write_json(tmp_path / "catalog.json", CATALOG)
    write_json(tmp_path / "green.json", [])
    write_json(tmp_path / "state.json", {"telegram_update_offset": 0, "today": None})
    (tmp_path / "log.jsonl").write_text("", encoding="utf-8")
    bot = make_bot()
    run_prepare(tmp_path, MORNING, random.Random(0), bot)
    assert bot.sent[0]["text"] == "No questions in the green set yet."
    assert load_state(tmp_path)["today"]["status"] == "empty"


def test_done_is_idempotent(repo: Path) -> None:
    _prepare(repo)
    qid = load_state(repo)["today"]["id"]
    bot = make_bot([tap(repo, 1, CALLBACK_DONE), tap(repo, 2, CALLBACK_DONE)])
    App(repo, bot, HALF_EIGHT, random.Random(0)).run()
    records = load_green(repo)
    rec = next(r for r in records if int(r["id"]) == qid)
    assert rec["times_revisited"] == 1
    assert rec["last_revisited"] == "2026-11-09"
    assert load_state(repo)["today"]["status"] == "done"
    done_logs = [row for row in read_logs(repo) if row["event"] == "done"]
    assert len(done_logs) == 1
    assert done_logs[0]["source"] == "telegram_done"
    assert any(edit.get("text") == f"Done ✅ #{qid}" for edit in bot.edits)
    assert question_keyboard("done") is None


def test_done_strips_buttons_from_other_messages(repo: Path) -> None:
    bot = make_bot()
    run_prepare(repo, MORNING, random.Random(1), bot)
    app = App(repo, bot, HALF_EIGHT, random.Random(0))
    app.state = load_state(repo)
    app._send_question_reply("tutor text", app._entry())
    extra_id = bot.sent[-1]["message_id"]
    original = app._entry()["message_id"]
    assert extra_id != original
    from store import save_state

    save_state(repo, app.state)
    app._done(source="telegram_done")
    stripped = [e for e in bot.edits if e.get("message_id") == extra_id]
    assert stripped
    assert stripped[0]["reply_markup"]["inline_keyboard"] == []


def test_done_command_rejects_wrong_id(repo: Path) -> None:
    _prepare(repo)
    pending = load_state(repo)["today"]["id"]
    other = pending + 1
    bot = make_bot([message_update(3, f"/done {other}")])
    App(repo, bot, HALF_EIGHT, random.Random(0)).run()
    assert load_state(repo)["today"]["status"] == "pending"
    assert any("not today's pending pick" in m["text"] for m in bot.sent)
    recs = {int(r["id"]): r for r in load_green(repo)}
    assert recs[pending]["times_revisited"] == 0


def test_done_command_without_args(repo: Path) -> None:
    _prepare(repo)
    bot = make_bot([message_update(3, "/done")])
    App(repo, bot, HALF_EIGHT, random.Random(0)).run()
    assert load_state(repo)["today"]["status"] == "done"


def test_reroll_excludes_offered_and_keeps_buttons(repo: Path) -> None:
    _prepare(repo, seed=1)
    first = load_state(repo)["today"]["id"]
    bot = make_bot([tap(repo, 1, CALLBACK_REROLL)])
    App(repo, bot, HALF_EIGHT, random.Random(2)).run()
    state = load_state(repo)
    assert state["today"]["id"] != first
    assert first in state["today"]["offered_ids"]
    assert state["today"]["id"] in state["today"]["offered_ids"]
    assert any("Today's revisit" in e.get("text", "") for e in bot.edits)
    assert any("Rerolled." in e.get("text", "") for e in bot.edits)
    assert any("Reroll 🎲" in keyboard_labels(e.get("reply_markup")) for e in bot.edits if e.get("reply_markup"))
    assert any("picking another question" in m["text"] for m in bot.sent)
    assert any(row["event"] == "rerolled" for row in read_logs(repo))


def test_reroll_after_done_is_noop(repo: Path) -> None:
    _prepare(repo)
    App(repo, make_bot([tap(repo, 1, CALLBACK_DONE)]), HALF_EIGHT, random.Random(0)).run()
    qid = load_state(repo)["today"]["id"]
    bot = make_bot([tap(repo, 2, CALLBACK_REROLL, qid)])
    App(repo, bot, HALF_EIGHT, random.Random(0)).run()
    assert load_state(repo)["today"]["id"] == qid
    assert any("Already done today." in m["text"] for m in bot.sent)


def test_add_command(repo: Path) -> None:
    _prepare(repo)
    bot = make_bot([message_update(8, "/add 316 317 1 999")])
    App(repo, bot, HALF_EIGHT, random.Random(0)).run()
    text = "\n".join(m["text"] for m in bot.sent)
    assert "Added: #316, #317" in text
    assert "Already in green: #1" in text
    assert "Not in catalog: #999" in text
    ids = {int(r["id"]) for r in load_green(repo)}
    assert {316, 317}.issubset(ids)


def test_add_range_command(repo: Path) -> None:
    bot = make_bot([message_update(8, "/add 300-302")])
    App(repo, bot, HALF_EIGHT, random.Random(0)).run()
    ids = {int(r["id"]) for r in load_green(repo)}
    assert {300, 301, 302}.issubset(ids)


def test_chat_id_filter_ignores_strangers(repo: Path) -> None:
    _prepare(repo)
    bot = make_bot([tap(repo, 1, CALLBACK_DONE, user_id=999, chat_id=999)])
    App(repo, bot, HALF_EIGHT, random.Random(0)).run()
    assert load_state(repo)["today"]["status"] == "pending"
    assert load_state(repo)["telegram_update_offset"] == 2


def test_self_heal_prepare_after_eight(repo: Path) -> None:
    assert load_state(repo)["today"] is None
    bot = make_bot()
    App(repo, bot, TEN, random.Random(1)).run()
    state = load_state(repo)
    assert state["today"] is not None
    assert state["today"]["date"] == "2026-11-09"
    assert any("Good morning" in m["text"] for m in bot.sent)


def test_no_self_heal_before_eight(repo: Path) -> None:
    early = datetime(2026, 11, 9, 7, 30, tzinfo=IST)
    bot = make_bot()
    App(repo, bot, early, random.Random(1)).run()
    assert load_state(repo)["today"] is None
    assert bot.sent == []


def test_reminders_copy_interval_and_buttons(repo: Path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_INTERVAL_MIN", "120")
    monkeypatch.setenv("REMINDER_START", "09:00")
    monkeypatch.setenv("REMINDER_END", "22:00")
    monkeypatch.setenv("MAX_REMINDERS", "8")
    _prepare(repo)
    entry = load_state(repo)["today"]
    assert reminder_due(entry, datetime(2026, 11, 9, 8, 30, tzinfo=IST)) is False
    assert reminder_due(entry, TEN) is True

    bot = make_bot()
    App(repo, bot, TEN, random.Random(0)).run()
    assert "Still pending" in bot.sent[-1]["text"]
    assert "Done ✅" in keyboard_labels(bot.sent[-1]["reply_markup"])
    assert load_state(repo)["today"]["reminders_sent"] == 1

    App(repo, make_bot(), datetime(2026, 11, 9, 11, 0, tzinfo=IST), random.Random(0)).run()
    assert load_state(repo)["today"]["reminders_sent"] == 1

    bot3 = make_bot()
    App(repo, bot3, NOON, random.Random(0)).run()
    assert "Still pending" in bot3.sent[-1]["text"]
    assert load_state(repo)["today"]["reminders_sent"] == 2

    state = load_state(repo)
    state["today"]["reminders_sent"] = 2
    state["today"]["last_reminder_ts"] = datetime(2026, 11, 9, 14, 0, tzinfo=IST).isoformat()
    from store import save_state

    save_state(repo, state)
    bot4 = make_bot()
    App(repo, bot4, datetime(2026, 11, 9, 16, 0, tzinfo=IST), random.Random(0)).run()
    assert "Reminder 3:" in bot4.sent[-1]["text"]

    state = load_state(repo)
    state["today"]["reminders_sent"] = 5
    state["today"]["last_reminder_ts"] = datetime(2026, 11, 9, 19, 0, tzinfo=IST).isoformat()
    save_state(repo, state)
    bot5 = make_bot()
    App(repo, bot5, EVENING, random.Random(0)).run()
    assert "Last stretch" in bot5.sent[-1]["text"]
    assert "Reroll 🎲" in keyboard_labels(bot5.sent[-1]["reply_markup"])


def test_no_reminders_once_done(repo: Path) -> None:
    _prepare(repo)
    App(repo, make_bot([tap(repo, 1, CALLBACK_DONE)]), TEN, random.Random(0)).run()
    bot = make_bot()
    App(repo, bot, NOON, random.Random(0)).run()
    assert bot.sent == []
    assert not any(row["event"] == "reminder_sent" for row in read_logs(repo))


def test_solution_confirm_gate(repo: Path) -> None:
    _prepare(repo)
    calls = {"n": 0}

    def post(*_a, **_k):
        calls["n"] += 1
        return FakeResponse("full solution code")

    bot = make_bot([tap(repo, 1, CALLBACK_SOLUTION)])
    App(repo, bot, HALF_EIGHT, random.Random(0), http_post=post, sleep=lambda _d: None).run()
    assert calls["n"] == 0
    assert any("Sure? Try a hint first" in m["text"] for m in bot.sent)
    assert load_state(repo)["today"]["solution_confirm_pending"] is True

    bot2 = make_bot([tap(repo, 2, CALLBACK_SOLUTION_CANCEL)])
    App(repo, bot2, HALF_EIGHT, random.Random(0), http_post=post, sleep=lambda _d: None).run()
    assert calls["n"] == 0
    assert load_state(repo)["today"]["solution_confirm_pending"] is False
    assert any(m["text"] == "Cancelled." for m in bot2.sent)
    assert "Done ✅" in keyboard_labels(bot2.sent[-1]["reply_markup"])

    bot3 = make_bot(
        [tap(repo, 3, CALLBACK_SOLUTION), tap(repo, 4, CALLBACK_SOLUTION_CONFIRM)]
    )
    App(repo, bot3, HALF_EIGHT, random.Random(0), http_post=post, sleep=lambda _d: None).run()
    assert calls["n"] == 1
    assert any("full solution code" in m["text"] for m in bot3.sent)
    sol_logs = [row for row in read_logs(repo) if row["event"] == "solution"]
    assert sol_logs and sol_logs[0]["needed_solution"] is True


def test_explain_and_hint_keep_buttons(repo: Path) -> None:
    _prepare(repo)
    contents = {"explain": "plain words", "hint": "think about a stack"}

    def post(_url, json=None, **_k):
        user = json["messages"][1]["content"]
        if "hint level 1" in user:
            return FakeResponse(contents["hint"])
        return FakeResponse(contents["explain"])

    bot = make_bot([tap(repo, 1, CALLBACK_EXPLAIN), tap(repo, 2, CALLBACK_HINT)])
    App(repo, bot, HALF_EIGHT, random.Random(0), http_post=post, sleep=lambda _d: None).run()
    texts = [m["text"] for m in bot.sent]
    assert any("writing the explanation" in t for t in texts)
    assert any("fetching hint 1/3" in t for t in texts)
    assert any("plain words" in t for t in texts)
    assert any("Hint 1/3" in t for t in texts)
    assert all("Done ✅" in keyboard_labels(m.get("reply_markup")) for m in bot.sent if m.get("reply_markup"))
    assert load_state(repo)["today"]["hint_level"] == 1
    assert any(row["event"] == "explain" for row in read_logs(repo))
    assert any(row["event"] == "hint_1" for row in read_logs(repo))


def test_tutor_acks_before_groq(repo: Path) -> None:
    _prepare(repo)
    order: list[str] = []

    def post(*_a, **_k):
        order.append("groq")
        return FakeResponse("body")

    bot = make_bot([tap(repo, 1, CALLBACK_EXPLAIN), tap(repo, 2, CALLBACK_HINT)])
    original = bot.send_message

    def wrapped(text: str, reply_markup=None):
        order.append("ack" if "Okay, I heard you" in text else "body")
        return original(text, reply_markup)

    bot.send_message = wrapped  # type: ignore[method-assign]
    App(repo, bot, HALF_EIGHT, random.Random(0), http_post=post, sleep=lambda _d: None).run()
    assert order[:2] == ["ack", "ack"]
    assert order.count("groq") == 2
    assert order.index("ack") < order.index("groq")


def test_missing_statement_keeps_done_working(repo: Path) -> None:
    catalog = json.loads((repo / "catalog.json").read_text(encoding="utf-8"))
    for item in catalog:
        item.pop("statement", None)
    (repo / "catalog.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    _prepare(repo)
    qid = load_state(repo)["today"]["id"]
    bot = make_bot([tap(repo, 1, CALLBACK_EXPLAIN, qid), tap(repo, 2, CALLBACK_DONE, qid)])
    App(repo, bot, HALF_EIGHT, random.Random(0)).run()
    assert any(f"No statement in the catalog for #{qid}." in m["text"] for m in bot.sent)
    assert load_state(repo)["today"]["status"] == "done"


def test_tutor_failure_does_not_block_done(repo: Path) -> None:
    import requests

    _prepare(repo)

    def post(*_a, **_k):
        raise requests.Timeout("down")

    bot = make_bot([tap(repo, 1, CALLBACK_EXPLAIN), tap(repo, 2, CALLBACK_DONE)])
    App(repo, bot, HALF_EIGHT, random.Random(0), http_post=post, sleep=lambda _d: None).run()
    assert any("Tutor unavailable" in m["text"] for m in bot.sent)
    assert load_state(repo)["today"]["status"] == "done"


def test_no_more_hints_after_three(repo: Path) -> None:
    _prepare(repo)
    state = load_state(repo)
    state["today"]["hint_level"] = 3
    from store import save_state

    save_state(repo, state)
    bot = make_bot([tap(repo, 1, CALLBACK_HINT)])
    App(repo, bot, HALF_EIGHT, random.Random(0)).run()
    assert any(m["text"] == "No more hints." for m in bot.sent)
    assert load_state(repo)["today"]["hint_level"] == 3


def test_dry_run_prepare_and_poll_simulated_day(repo: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", str(CHAT_ID))
    monkeypatch.setenv("REMINDER_INTERVAL_MIN", "120")
    monkeypatch.setenv("REMINDER_START", "09:00")
    monkeypatch.setenv("REMINDER_END", "22:00")
    monkeypatch.setenv("MAX_REMINDERS", "8")
    prepare_main(["--dry-run", "--root", str(repo), "--now", "2026-11-09T08:00:00", "--seed", "1"])
    out1 = capsys.readouterr().out
    assert "[dry-run] sendMessage" in out1
    assert "Good morning" in out1
    assert "Done" in out1
    poll_main(["--dry-run", "--root", str(repo), "--now", "2026-11-09T10:00:00", "--seed", "1"])
    out2 = capsys.readouterr().out
    assert "Still pending" in out2
    poll_main(["--dry-run", "--root", str(repo), "--now", "2026-11-09T12:00:00", "--seed", "1"])
    out3 = capsys.readouterr().out
    assert "Still pending" in out3
    assert load_state(repo)["today"]["reminders_sent"] == 2


def test_watch_outside_window_does_not_stay_awake(repo: Path) -> None:
    _prepare(repo)
    bot = make_bot()
    now = datetime(2026, 11, 9, 13, 0, tzinfo=IST)
    run_watch(
        repo,
        bot,
        random.Random(0),
        clock=lambda: now,
        duration=timedelta(hours=4),
    )
    texts = [m["text"] for m in bot.sent]
    assert any("I'm going offline." in t for t in texts)
    assert not any("I'm awake until" in t for t in texts)
    assert any("14:00 IST" in t for t in texts)


def test_watch_for_zero_sends_awake_and_offline(repo: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", str(CHAT_ID))
    monkeypatch.setenv("WATCH_WINDOWS", "08:00-12:00,14:00-18:00")
    poll_main(
        [
            "--dry-run",
            "--root",
            str(repo),
            "--now",
            "2026-11-09T16:00:00",
            "--watch",
            "--watch-for",
            "0",
        ]
    )
    out = capsys.readouterr().out
    assert "I'm awake until" in out
    assert "I'm going offline." in out
    assert "tomorrow 08:00 IST" in out


def test_reroll_after_explain_strips_tutor_buttons(repo: Path) -> None:
    _prepare(repo)
    App(
        repo,
        make_bot([tap(repo, 1, CALLBACK_EXPLAIN)]),
        HALF_EIGHT,
        random.Random(0),
        http_post=lambda *_a, **_k: FakeResponse("plain words"),
        sleep=lambda _d: None,
    ).run()
    state = load_state(repo)
    original = state["today"]["message_id"]
    extras = [mid for mid in state["today"]["button_message_ids"] if mid != original]
    assert extras
    first = state["today"]["id"]
    bot = make_bot([tap(repo, 2, CALLBACK_REROLL)])
    App(repo, bot, HALF_EIGHT, random.Random(3)).run()
    state = load_state(repo)
    assert state["today"]["id"] != first
    assert state["today"]["hint_level"] == 0
    assert state["today"]["solution_confirm_pending"] is False
    assert state["today"]["button_message_ids"] == [original]
    stripped = [e for e in bot.edits if e.get("message_id") in extras]
    assert stripped
    assert stripped[0]["reply_markup"]["inline_keyboard"] == []
    live = [e for e in bot.edits if e.get("message_id") == original and e.get("text")]
    assert live
    assert keyboard_labels(live[-1]["reply_markup"]) == pending_labels()


def test_same_batch_explain_then_reroll_skips_tutor(repo: Path) -> None:
    _prepare(repo, seed=1)
    first = load_state(repo)["today"]["id"]
    calls = {"n": 0}

    def post(*_a, **_k):
        calls["n"] += 1
        return FakeResponse("plain words")

    bot = make_bot([tap(repo, 1, CALLBACK_EXPLAIN), tap(repo, 2, CALLBACK_REROLL, first)])
    App(repo, bot, HALF_EIGHT, random.Random(2), http_post=post, sleep=lambda _d: None).run()
    state = load_state(repo)
    assert state["today"]["id"] != first
    assert calls["n"] == 0
    assert not any("plain words" in m["text"] for m in bot.sent)
    assert any("writing the explanation" in m["text"] for m in bot.sent)
    assert any("picking another question" in m["text"] for m in bot.sent)


def test_stale_done_after_reroll_is_ignored(repo: Path) -> None:
    _prepare(repo, seed=1)
    first = load_state(repo)["today"]["id"]
    App(repo, make_bot([tap(repo, 1, CALLBACK_REROLL)]), HALF_EIGHT, random.Random(2)).run()
    new_id = load_state(repo)["today"]["id"]
    assert new_id != first
    bot = make_bot([tap(repo, 2, CALLBACK_DONE, first)])
    App(repo, bot, HALF_EIGHT, random.Random(0)).run()
    state = load_state(repo)
    assert state["today"]["status"] == "pending"
    assert state["today"]["id"] == new_id
    recs = {int(r["id"]): r for r in load_green(repo)}
    assert recs[new_id]["times_revisited"] == 0
    assert recs[first]["times_revisited"] == 0
    assert any(a.get("text") == "That's not today's question." for a in bot.callback_answers)


def test_reroll_clears_solution_confirm(repo: Path) -> None:
    _prepare(repo, seed=1)
    first = load_state(repo)["today"]["id"]
    App(repo, make_bot([tap(repo, 1, CALLBACK_SOLUTION)]), HALF_EIGHT, random.Random(0)).run()
    assert load_state(repo)["today"]["solution_confirm_pending"] is True
    App(repo, make_bot([tap(repo, 2, CALLBACK_REROLL)]), HALF_EIGHT, random.Random(2)).run()
    state = load_state(repo)
    assert state["today"]["id"] != first
    assert state["today"]["solution_confirm_pending"] is False
    bot = make_bot([tap(repo, 3, CALLBACK_SOLUTION_CONFIRM, first)])
    App(
        repo,
        bot,
        HALF_EIGHT,
        random.Random(0),
        http_post=lambda *_a, **_k: FakeResponse("full solution code"),
        sleep=lambda _d: None,
    ).run()
    assert not any("full solution code" in m["text"] for m in bot.sent)
    assert load_state(repo)["today"]["status"] == "pending"


def test_reroll_command_matches_button(repo: Path) -> None:
    _prepare(repo, seed=1)
    first = load_state(repo)["today"]["id"]
    bot = make_bot([message_update(8, "/reroll")])
    App(repo, bot, HALF_EIGHT, random.Random(2)).run()
    state = load_state(repo)
    assert state["today"]["id"] != first
    assert first in state["today"]["offered_ids"]
    assert any("Rerolled." in e.get("text", "") for e in bot.edits)
    assert not any("Okay, I heard you" in m["text"] for m in bot.sent)


def test_reroll_exhausted_pool_keeps_current(repo: Path) -> None:
    _prepare(repo)
    from store import save_state

    state = load_state(repo)
    current = state["today"]["id"]
    state["today"]["offered_ids"] = [int(r["id"]) for r in load_green(repo)]
    save_state(repo, state)
    bot = make_bot([tap(repo, 1, CALLBACK_REROLL)])
    App(repo, bot, HALF_EIGHT, random.Random(0)).run()
    assert load_state(repo)["today"]["id"] == current
    assert any("No other questions left to reroll today." in m["text"] for m in bot.sent)
