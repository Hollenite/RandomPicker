from __future__ import annotations

import requests

from store import Question
from telegram import split_message
from tests.conftest import FakeResponse
from tutor import (
    SOLUTION_INSTRUCTIONS,
    MissingStatementError,
    cache_get,
    explain,
    explain_prompt,
    groq_chat,
    hint,
    hint_prompt,
    solution,
    solution_prompt,
    statement_hash,
)

Q = Question(
    id=142,
    difficulty="easy",
    added=__import__("datetime").date(2026, 1, 1),
    last_revisited=None,
    times_revisited=0,
    statement="Given n, return n + 1.",
)


def test_hint_prompt_never_contains_solution_instructions() -> None:
    for level in (1, 2, 3):
        blob = "\n".join(m["content"] for m in hint_prompt(Q, level)).lower()
        assert "commented code" not in blob
        assert "time and space complexity" not in blob
        assert SOLUTION_INSTRUCTIONS.format(lang="python").lower() not in blob
        assert "do not reveal later stages" in blob


def test_explain_prompt_has_no_code_request() -> None:
    blob = explain_prompt(Q)[1]["content"].lower()
    assert "do not give a solution" in blob
    assert "commented code" not in blob


def test_cache_hit_skips_http() -> None:
    calls = {"n": 0}

    def post(*_args, **_kwargs):
        calls["n"] += 1
        return FakeResponse("plain restatement")

    cache: dict = {}
    first = explain(Q, cache, http_post=post, sleep=lambda _d: None)
    second = explain(Q, cache, http_post=post, sleep=lambda _d: None)
    assert first == second == "plain restatement"
    assert calls["n"] == 1
    assert cache_get(cache, Q, "explain") == first


def test_cache_invalidates_when_statement_changes() -> None:
    cache: dict = {}
    explain(Q, cache, http_post=lambda *a, **k: FakeResponse("old"), sleep=lambda _d: None)
    changed = Question(**{**Q.__dict__, "statement": "A different statement."})
    calls = {"n": 0}

    def post(*_a, **_k):
        calls["n"] += 1
        return FakeResponse("new")

    text = explain(changed, cache, http_post=post, sleep=lambda _d: None)
    assert text == "new"
    assert calls["n"] == 1
    assert cache[str(changed.id)]["statement_hash"] == statement_hash("A different statement.")


def test_missing_statement() -> None:
    blank = Question(**{**Q.__dict__, "statement": None})
    try:
        hint_prompt(blank, 1)
        assert False, "expected MissingStatementError"
    except MissingStatementError as exc:
        assert "#142" in str(exc)


def test_split_long_message_and_fences() -> None:
    text = "para\n\n" + ("x" * 5000)
    parts = split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert sum(p.count("x") for p in parts) == 5000

    fenced = "intro\n```python\n" + ("a = 1\n" * 800) + "```\noutro"
    parts = split_message(fenced, limit=200)
    assert all(len(p) <= 200 for p in parts)
    assert any("```" in p for p in parts)


def test_groq_retries_then_succeeds() -> None:
    attempts = {"n": 0}

    def post(*_a, **_k):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise requests.Timeout("nope")
        return FakeResponse("ok")

    text = groq_chat(
        [{"role": "user", "content": "hi"}],
        100,
        http_post=post,
        sleep=lambda _d: None,
        api_key="k",
        model="llama",
    )
    assert text == "ok"
    assert attempts["n"] == 3


def test_groq_retries_empty_content() -> None:
    attempts = {"n": 0}

    def post(*_a, **_k):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return FakeResponse("")
        return FakeResponse("ok")

    text = groq_chat(
        [{"role": "user", "content": "hi"}],
        100,
        http_post=post,
        sleep=lambda _d: None,
        api_key="k",
        model="llama",
    )
    assert text == "ok"
    assert attempts["n"] == 2


def test_solution_prompt_asks_for_code() -> None:
    blob = solution_prompt(Q, "python")[1]["content"].lower()
    assert "commented code" in blob
    assert "python" in blob


def test_hint_includes_previous_levels() -> None:
    messages = hint_prompt(Q, 2, previous_hints=["think stacks"])
    blob = messages[1]["content"]
    assert "think stacks" in blob
    assert "Already given hint 1" in blob
