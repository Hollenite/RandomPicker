"""Weighted daily question picker. Pure functions; pass in `rng` for determinism."""

from __future__ import annotations

import random
from datetime import date

from store import Question

BUCKET_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("easy", 0.55),
    ("medium", 0.40),
    ("difficult", 0.03),
    ("wildcard", 0.02),
)
FALLBACK_ORDER = ("easy", "medium", "difficult")


def bucket_for_draw(rng: random.Random) -> str:
    draw = rng.random()
    cumulative = 0.0
    for name, probability in BUCKET_WEIGHTS:
        cumulative += probability
        if draw < cumulative:
            return name
    return "wildcard"


def pick(
    questions: list[Question],
    today: date,
    rng: random.Random,
    exclude_ids: frozenset[int] = frozenset(),
) -> Question | None:
    result = pick_detailed(questions, today, rng, exclude_ids)
    return result[0] if result else None


def pick_detailed(
    questions: list[Question],
    today: date,
    rng: random.Random,
    exclude_ids: frozenset[int] = frozenset(),
) -> tuple[Question, str] | None:
    if not questions:
        return None
    pool = [
        q
        for q in questions
        if q.id not in exclude_ids and q.last_revisited != today
    ]
    if not pool:
        return None
    drawn = bucket_for_draw(rng)
    if drawn == "wildcard":
        wildcard = _oldest_medium(pool)
        if wildcard is not None:
            return wildcard, "wildcard"
        return _fallback_pick(pool, today, rng)
    chosen = _weighted_pick(_of_difficulty(pool, drawn), today, rng)
    if chosen is not None:
        return chosen, drawn
    return _fallback_pick(pool, today, rng)


def _of_difficulty(questions: list[Question], difficulty: str) -> list[Question]:
    return [q for q in questions if q.difficulty == difficulty]


def _days_since(question: Question, today: date) -> int | None:
    if question.last_revisited is None:
        return None
    return (today - question.last_revisited).days


def _max_days_in_bucket(bucket: list[Question], today: date) -> int:
    seen = [_days_since(q, today) for q in bucket]
    revisited = [d for d in seen if d is not None]
    return max(revisited) if revisited else 0


def _weight(question: Question, today: date, max_days: int) -> int:
    days = _days_since(question, today)
    if days is None:
        return max_days + 2
    return days + 1


def _weighted_pick(
    bucket: list[Question], today: date, rng: random.Random
) -> Question | None:
    if not bucket:
        return None
    max_days = _max_days_in_bucket(bucket, today)
    weights = [_weight(q, today, max_days) for q in bucket]
    return rng.choices(bucket, weights=weights, k=1)[0]


def _oldest_medium(pool: list[Question]) -> Question | None:
    mediums = _of_difficulty(pool, "medium")
    if not mediums:
        return None

    def sort_key(question: Question) -> tuple[date, int]:
        stamp = question.last_revisited if question.last_revisited is not None else date.min
        return stamp, question.id

    return min(mediums, key=sort_key)


def _fallback_pick(
    pool: list[Question], today: date, rng: random.Random
) -> tuple[Question, str] | None:
    for difficulty in FALLBACK_ORDER:
        chosen = _weighted_pick(_of_difficulty(pool, difficulty), today, rng)
        if chosen is not None:
            return chosen, difficulty
    return None
