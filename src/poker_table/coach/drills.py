"""Drills: replay your flagged spots as quizzes and bring back the ones you keep failing."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from poker_table.agents.base import SeatView
from poker_table.agents.human import parse_command
from poker_table.coach.facts import Fact, replay
from poker_table.coach.ranges import call_range, hand_class, open_range, three_bet_range
from poker_table.engine import ActionType
from poker_table.history import HandHistory

# Leitner boxes: a correct answer moves the spot up a box and pushes it further out.
INTERVALS_DAYS = (0, 1, 3, 7, 14, 30)


@dataclass(frozen=True, slots=True)
class Spot:
    key: str
    fact: Fact
    view: SeatView
    accepted: frozenset[ActionType]

    @property
    def answer_text(self) -> str:
        names = sorted(a.value for a in self.accepted)
        return " or ".join(names)


def accepted_actions(fact: Fact, view: SeatView) -> frozenset[ActionType]:
    """What the chart or the math says you should have done in this spot."""
    cls = hand_class(view.hole)
    pos = view.position
    raise_ok = view.legal.can_raise
    match fact.tag:
        case "limp":
            return frozenset({ActionType.RAISE if cls in open_range(pos) else ActionType.FOLD})
        case "open":
            return frozenset({ActionType.FOLD})
        case "fold_first_in":
            return frozenset({ActionType.RAISE})
        case "call_open":
            out = {ActionType.FOLD}
            if cls in three_bet_range() and raise_ok:
                out.add(ActionType.RAISE)
            return frozenset(out)
        case "three_bet":
            return frozenset({ActionType.CALL if cls in call_range(pos) else ActionType.FOLD})
        case "fold_vs_open":
            if cls in three_bet_range() and raise_ok:
                return frozenset({ActionType.RAISE, ActionType.CALL})
            return frozenset({ActionType.CALL})
        case "vs_three_bet":
            if fact.action == "fold":
                return frozenset({ActionType.CALL, ActionType.RAISE})
            return frozenset({ActionType.FOLD})
        case "call_vs_bet":
            return frozenset({ActionType.FOLD})
        case "fold_vs_bet":
            return frozenset({ActionType.CALL, ActionType.RAISE})
        case "check_strong_river":
            return frozenset({ActionType.BET})
    return frozenset()


def spots_from(histories: Sequence[HandHistory], facts: Sequence[Fact]) -> list[Spot]:
    """Rebuild the view for every flagged fact so it can be asked again."""
    by_id = {h.hand_id: h for h in histories}
    wanted = {(f.hand_id, f.street, f.seat, f.action): f for f in facts if f.ok is False}
    spots: list[Spot] = []
    for (hand_id, _street, _seat, _action), fact in wanted.items():
        history = by_id.get(hand_id)
        if history is None:
            continue
        for view, action in replay(history):
            if (
                view.seat == fact.seat
                and view.street.value == fact.street
                and str(action) == fact.action
            ):
                accepted = accepted_actions(fact, view)
                if accepted:
                    key = f"{hand_id}:{fact.street}:{fact.seat}"
                    spots.append(Spot(key, fact, view, accepted))
                break
    return spots


@dataclass(slots=True)
class DrillLog:
    """Per-spot progress, saved as JSON next to the hands file."""

    path: Path
    spots: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> DrillLog:
        if path.exists():
            return cls(path, json.loads(path.read_text()))
        return cls(path)

    def save(self) -> None:
        self.path.write_text(json.dumps(self.spots, indent=1, sort_keys=True))

    def due(self, key: str, today: date) -> bool:
        entry = self.spots.get(key)
        return entry is None or date.fromisoformat(entry["due"]) <= today

    def record(self, key: str, correct: bool, today: date) -> dict[str, Any]:
        entry = self.spots.setdefault(
            key, {"box": 0, "attempts": 0, "correct": 0, "due": today.isoformat()}
        )
        entry["attempts"] += 1
        if correct:
            entry["correct"] += 1
            entry["box"] = min(entry["box"] + 1, len(INTERVALS_DAYS) - 1)
        else:
            entry["box"] = 0
        entry["due"] = (today + timedelta(days=INTERVALS_DAYS[entry["box"]])).isoformat()
        return entry

    def order(self, spots: Sequence[Spot], today: date) -> list[Spot]:
        """Failed and due spots first (lowest box first), then never-seen ones."""

        def rank(spot: Spot) -> tuple[int, int, str]:
            entry = self.spots.get(spot.key)
            if entry is None:
                return (1, 0, spot.key)
            return (0, entry["box"], entry["due"])

        return sorted((s for s in spots if self.due(s.key, today)), key=rank)


@dataclass(slots=True)
class DrillResult:
    asked: int = 0
    correct: int = 0
    keys: list[str] = field(default_factory=list)


def run_drill(
    spots: Sequence[Spot],
    log: DrillLog,
    *,
    count: int = 10,
    today: date | None = None,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], None] = print,
) -> DrillResult:
    today = today or date.today()
    ask = input_fn or input  # resolved now, so tests can patch builtins.input
    queue = log.order(spots, today)[:count]
    result = DrillResult()
    if not queue:
        output_fn("Nothing is due. Play more hands or come back tomorrow.")
        return result
    for number, spot in enumerate(queue, 1):
        output_fn(f"\n--- Spot {number} of {len(queue)} (hand #{spot.fact.hand_id}) ---")
        output_fn(spot.view.describe())
        action = None
        while action is None:
            try:
                raw = ask("your move> ").strip()
            except EOFError:
                log.save()
                return result
            if not raw:
                continue
            action, problem = parse_command(raw, spot.view)
            if action is None:
                output_fn(f"{problem} — legal: {spot.view.legal.describe()}")
        correct = action.type in spot.accepted
        entry = log.record(spot.key, correct, today)
        result.asked += 1
        result.correct += correct
        result.keys.append(spot.key)
        verdict = "Right." if correct else f"Not this time — the answer is {spot.answer_text}."
        output_fn(f"{verdict} At the table you chose {spot.fact.action}: {spot.fact.detail}")
        output_fn(f"(box {entry['box']}, next due {entry['due']})")
    log.save()
    output_fn(f"\nScore {result.correct}/{result.asked}. Progress saved to {log.path}")
    return result
