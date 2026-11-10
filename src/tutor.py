"""LLM tutor via Groq. Explain, hint, then solution — never submits anything."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import requests

from store import Question
from telegram import split_message

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SYSTEM_PROMPT = (
    "You are a patient tutor. Stay strictly within the requested stage. "
    "Do not reveal later stages. "
    "Format with simple Markdown only: **bold**, *italic*, `inline code`, "
    "and fenced code blocks. No HTML, tables, images, or # headings."
)
EXPLAIN_MAX_TOKENS = 800
HINT_MAX_TOKENS = 500
SOLUTION_MAX_TOKENS = 1800
TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 3

EXPLAIN_INSTRUCTIONS = (
    "Restate the problem in plain words. Define the inputs and outputs. "
    "Walk through one small example. Name the core concept being tested. "
    "Do not give a solution, algorithm, pseudocode, or code."
)
HINT_INSTRUCTIONS = {
    1: (
        "Give hint level 1 only: which pattern or data structure to think about. "
        "Do not reveal the key insight, the algorithm, or any code."
    ),
    2: (
        "Give hint level 2 only: the key insight or invariant. "
        "Do not outline the full algorithm and do not write code."
    ),
    3: (
        "Give hint level 3 only: an outline of the algorithm in words. "
        "No code, no full solution, no complexity analysis."
    ),
}
SOLUTION_INSTRUCTIONS = (
    "Give the full solution: the approach, clean commented code in {lang}, "
    "time and space complexity, and one common mistake."
)

HttpPost = Callable[..., Any]
SleepFn = Callable[[float], None]


class GroqError(RuntimeError):
    pass


class MissingStatementError(ValueError):
    pass


def statement_hash(statement: str) -> str:
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()


def load_cache(root: Path) -> dict[str, Any]:
    path = root / "tutor_cache.json"
    if not path.is_file() or path.stat().st_size == 0:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save_cache(root: Path, cache: dict[str, Any]) -> None:
    path = root / "tutor_cache.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def cache_key(stage: str, level: int | None = None, lang: str | None = None) -> str:
    if stage == "hint":
        return f"hint_{level}"
    if stage == "solution":
        return f"solution_{lang or 'python'}"
    return stage


def _entry_for(cache: dict[str, Any], question: Question) -> dict[str, Any] | None:
    if not question.statement:
        return None
    entry = cache.get(str(question.id))
    if not isinstance(entry, dict):
        return None
    if entry.get("statement_hash") != statement_hash(question.statement):
        return None
    return entry


def cache_get(
    cache: dict[str, Any],
    question: Question,
    stage: str,
    level: int | None = None,
    lang: str | None = None,
) -> str | None:
    entry = _entry_for(cache, question)
    if not entry:
        return None
    value = entry.get(cache_key(stage, level, lang))
    return value if isinstance(value, str) else None


def cache_put(
    cache: dict[str, Any],
    question: Question,
    stage: str,
    text: str,
    level: int | None = None,
    lang: str | None = None,
) -> None:
    if not question.statement:
        return
    digest = statement_hash(question.statement)
    entry = cache.get(str(question.id))
    if not isinstance(entry, dict) or entry.get("statement_hash") != digest:
        entry = {"statement_hash": digest}
        cache[str(question.id)] = entry
    entry[cache_key(stage, level, lang)] = text


def _require_statement(question: Question) -> str:
    if not question.statement:
        raise MissingStatementError(f"No statement in the catalog for #{question.id}.")
    return question.statement


def explain_prompt(question: Question) -> list[dict[str, str]]:
    statement = _require_statement(question)
    user = (
        f"Question #{question.id} ({question.difficulty}):\n{statement}\n\n"
        f"{EXPLAIN_INSTRUCTIONS}"
    )
    return _messages(user)


def hint_prompt(
    question: Question, level: int, previous_hints: list[str] | None = None
) -> list[dict[str, str]]:
    if level not in HINT_INSTRUCTIONS:
        raise ValueError("hint level must be 1, 2, or 3")
    statement = _require_statement(question)
    parts = [
        f"Question #{question.id} ({question.difficulty}):\n{statement}",
        HINT_INSTRUCTIONS[level],
    ]
    if previous_hints:
        earlier = "\n\n".join(
            f"Already given hint {i}:\n{text}" for i, text in enumerate(previous_hints, start=1)
        )
        parts.append("Do not repeat these earlier hints:\n" + earlier)
    return _messages("\n\n".join(parts))


def solution_prompt(question: Question, lang: str) -> list[dict[str, str]]:
    statement = _require_statement(question)
    user = (
        f"Question #{question.id} ({question.difficulty}):\n{statement}\n\n"
        f"{SOLUTION_INSTRUCTIONS.format(lang=lang)}"
    )
    return _messages(user)


def _messages(user: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def groq_chat(
    messages: list[dict[str, str]],
    max_tokens: int,
    *,
    http_post: HttpPost | None = None,
    sleep: SleepFn | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY", "").strip()
    model_name = model if model is not None else os.environ.get("GROQ_MODEL", "").strip()
    post = http_post or requests.post
    if post is requests.post and (not key or not model_name):
        raise GroqError("GROQ_API_KEY and GROQ_MODEL are required")
    key = key or "test-key"
    model_name = model_name or "test-model"
    nap = sleep or time.sleep
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    if "gpt-oss" in model_name:
        payload["reasoning_effort"] = "low"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    delay = 0.5
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = post(GROQ_URL, json=payload, headers=headers, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
            message = data["choices"][0]["message"]
            content = str(message.get("content") or "").strip()
            if not content:
                last_error = GroqError("empty tutor response")
                if attempt < MAX_ATTEMPTS - 1:
                    nap(delay)
                    delay *= 2
                continue
            return content
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS - 1:
                nap(delay)
                delay *= 2
    raise GroqError(str(last_error) if last_error else "Groq request failed")


def _cached_call(
    cache: dict[str, Any],
    question: Question,
    stage: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    *,
    level: int | None = None,
    lang: str | None = None,
    http_post: HttpPost | None = None,
    sleep: SleepFn | None = None,
) -> str:
    hit = cache_get(cache, question, stage, level, lang)
    if hit is not None:
        return hit
    text = groq_chat(messages, max_tokens, http_post=http_post, sleep=sleep)
    cache_put(cache, question, stage, text, level, lang)
    return text


def explain(
    question: Question,
    cache: dict[str, Any],
    *,
    http_post: HttpPost | None = None,
    sleep: SleepFn | None = None,
) -> str:
    return _cached_call(
        cache,
        question,
        "explain",
        explain_prompt(question),
        EXPLAIN_MAX_TOKENS,
        http_post=http_post,
        sleep=sleep,
    )


def hint(
    question: Question,
    level: int,
    cache: dict[str, Any],
    previous_hints: list[str] | None = None,
    *,
    http_post: HttpPost | None = None,
    sleep: SleepFn | None = None,
) -> str:
    return _cached_call(
        cache,
        question,
        "hint",
        hint_prompt(question, level, previous_hints),
        HINT_MAX_TOKENS,
        level=level,
        http_post=http_post,
        sleep=sleep,
    )


def solution(
    question: Question,
    cache: dict[str, Any],
    lang: str | None = None,
    *,
    http_post: HttpPost | None = None,
    sleep: SleepFn | None = None,
) -> str:
    language = lang or os.environ.get("PREFERRED_LANG", "python")
    return _cached_call(
        cache,
        question,
        "solution",
        solution_prompt(question, language),
        SOLUTION_MAX_TOKENS,
        lang=language,
        http_post=http_post,
        sleep=sleep,
    )


def preferred_lang() -> str:
    return os.environ.get("PREFERRED_LANG", "python").strip() or "python"
