from __future__ import annotations

import random
from collections import Counter
from datetime import date, timedelta

from picker import bucket_for_draw, pick, pick_detailed
from store import Question

TODAY = date(2026, 11, 9)
ADDED = date(2026, 1, 1)


def q(qid: int, difficulty: str, last: date | None = None) -> Question:
    return Question(
        id=qid,
        difficulty=difficulty,
        added=ADDED,
        last_revisited=last,
        times_revisited=0 if last is None else 1,
        statement=None,
    )


def populated() -> list[Question]:
    questions = [q(i, "easy") for i in range(1, 101)]
    questions += [q(i, "medium") for i in range(101, 201)]
    questions += [q(i, "difficult") for i in range(201, 251)]
    return questions


class ConstRandom(random.Random):
    def __init__(self, value: float) -> None:
        super().__init__(0)
        self._value = value

    def random(self) -> float:
        return self._value


def test_bucket_frequencies_within_one_point() -> None:
    rng = random.Random(42)
    counts = Counter(bucket_for_draw(rng) for _ in range(100_000))
    expected = {"easy": 0.55, "medium": 0.40, "difficult": 0.03, "wildcard": 0.02}
    for name, target in expected.items():
        actual = counts[name] / 100_000
        assert abs(actual - target) < 0.01, f"{name}: {actual} vs {target}"


def test_pick_detailed_bucket_frequencies() -> None:
    rng = random.Random(7)
    questions = populated()
    counts = Counter()
    for _ in range(100_000):
        result = pick_detailed(questions, TODAY, rng)
        assert result is not None
        counts[result[1]] += 1
    expected = {"easy": 0.55, "medium": 0.40, "difficult": 0.03, "wildcard": 0.02}
    for name, target in expected.items():
        actual = counts[name] / 100_000
        assert abs(actual - target) < 0.01, f"{name}: {actual} vs {target}"


def test_wildcard_returns_oldest_medium() -> None:
    questions = [
        q(2, "medium", date(2026, 1, 1)),
        q(1, "medium", date(2026, 1, 1)),
        q(4, "medium", None),
        q(3, "medium", date(2026, 1, 2)),
        q(10, "easy"),
    ]
    picked = pick(questions, TODAY, ConstRandom(0.99))
    assert picked is not None
    assert picked.id == 4


def test_wildcard_tie_break_lowest_id() -> None:
    questions = [
        q(20, "medium", date(2026, 1, 1)),
        q(8, "medium", date(2026, 1, 1)),
        q(9, "medium", date(2026, 1, 2)),
        q(1, "easy"),
    ]
    picked = pick(questions, TODAY, ConstRandom(0.99))
    assert picked is not None
    assert picked.id == 8


def test_never_revisited_weighted_more_than_yesterday() -> None:
    yesterday = TODAY - timedelta(days=1)
    questions = [q(1, "easy", None), q(2, "easy", yesterday)]
    rng = random.Random(0)
    counts = Counter()
    for _ in range(4000):
        picked = pick(questions, TODAY, rng)
        assert picked is not None
        counts[picked.id] += 1
    assert counts[1] > counts[2]


def test_empty_bucket_falls_back_in_order() -> None:
    questions = [q(3, "medium"), q(5, "difficult")]
    picked, bucket = pick_detailed(questions, TODAY, ConstRandom(0.0))  # easy
    assert picked.id == 3
    assert bucket == "medium"


def test_empty_bucket_falls_back_to_difficult() -> None:
    questions = [q(5, "difficult")]
    picked, bucket = pick_detailed(questions, TODAY, ConstRandom(0.0))
    assert picked.id == 5
    assert bucket == "difficult"


def test_empty_questions_returns_none() -> None:
    assert pick([], TODAY, random.Random(0)) is None


def test_exclude_ids_and_revisited_today() -> None:
    questions = [q(1, "easy"), q(2, "easy", TODAY), q(3, "medium")]
    assert pick(questions, TODAY, ConstRandom(0.0), frozenset({1})) is not None
    only_easy = [q(1, "easy"), q(2, "easy", TODAY)]
    assert pick(only_easy, TODAY, ConstRandom(0.0), frozenset({1})) is None
