"""Score a seat on decisions whose answer is known, without playing a session.

A seeded bot session is played and the coach grades every seat's decisions against the
charts and the maths. Each graded decision becomes a question — this exact view, what do
you do? — and an answer counts when its action is one the coach accepts: the correction
for a spot it calls a mistake, the same move for a spot it calls fine. Half the questions
are of each kind, so a seat cannot score well by always folding, and every run prints two
baseline beside the seats: the best score a seat giving one fixed answer could get.

This is how an aid, a prompt or a model is judged before a league is paid for: the same
questions, one number per seat, seconds for bots and a few minutes for a local model.
"""

from __future__ import annotations

import random
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from poker_table.agents.base import Agent, SeatView
from poker_table.engine import Action, ActionType
from poker_table.history import HandHistory
from poker_table.table import sanitize

AGGRESSIVE = frozenset({ActionType.BET, ActionType.RAISE})
CONTINUE = frozenset({ActionType.CALL, ActionType.RAISE})


@dataclass(frozen=True, slots=True)
class Question:
    key: str
    view: SeatView
    accepted: frozenset[ActionType]
    tag: str  # the coach's fact tag: open, call_vs_bet, vs_three_bet ...
    mistake: bool  # the coach called what was played a mistake
    played: str  # what the bot at the table actually did

    def accepts(self, action: Action) -> bool:
        kind = action.type
        if kind in AGGRESSIVE:
            return bool(self.accepted & AGGRESSIVE)
        return kind in self.accepted


def accepted_when_fine(tag: str, action: Action) -> frozenset[ActionType]:
    """What else is right when the coach accepted the move that was played."""
    if tag in ("call_vs_bet", "vs_three_bet") and action.type is not ActionType.FOLD:
        return CONTINUE  # the verdict is "continue", and a raise continues too
    if action.type in AGGRESSIVE:
        return AGGRESSIVE
    return frozenset({action.type})


def build_questions(
    histories: Sequence[HandHistory],
    players: Iterable[str],
    *,
    count: int | None = None,
    samples: int = 150,
    seed: int = 0,
) -> list[Question]:
    """Graded decisions of ``players`` as questions: half mistakes, half fine, shuffled."""
    from poker_table.coach.drills import accepted_actions
    from poker_table.coach.facts import replay, tag_hand

    mistakes: list[Question] = []
    fine: list[Question] = []
    for player in players:
        for history in histories:
            # tag_hand grades the player's decisions in order, one fact each, up to a fold
            facts = iter(tag_hand(history, player, samples=samples))
            turn = 0
            for view, action in replay(history):
                if view.name != player:
                    continue
                fact = next(facts, None)
                if fact is None:
                    break
                turn += 1
                if fact.ok is None:
                    continue
                accepted = (
                    accepted_when_fine(fact.tag, action)
                    if fact.ok
                    else accepted_actions(fact, view)
                )
                if not accepted:
                    continue
                key = f"{history.hand_id}:{view.seat}:{turn}"
                q = Question(key, view, accepted, fact.tag, not fact.ok, str(action))
                (fine if fact.ok else mistakes).append(q)
    rng = random.Random(seed)
    rng.shuffle(mistakes)
    rng.shuffle(fine)
    half = min(len(mistakes), len(fine))
    if count is not None:
        half = min(half, count // 2)
    chosen = mistakes[:half] + fine[:half]
    rng.shuffle(chosen)
    return chosen


@dataclass(slots=True)
class Score:
    seat: str
    asked: int = 0
    agreed: int = 0
    fixed: int = 0  # right answers on the coach's mistake spots
    kept: int = 0  # right answers on the spots it accepts
    mistakes: int = 0
    illegal: int = 0
    errors: int = 0  # the seat raised, or its model call failed and it fell back
    latencies_ms: list[float] = field(default_factory=list)
    cost_usd: float = 0.0
    by_tag: Counter[str] = field(default_factory=Counter)
    asked_by_tag: Counter[str] = field(default_factory=Counter)

    @property
    def rate(self) -> float:
        return self.agreed / self.asked if self.asked else 0.0

    def row(self) -> dict[str, Any]:
        fine = self.asked - self.mistakes
        return {
            "seat": self.seat,
            "asked": self.asked,
            "agrees": self.rate,
            "fixes mistakes": self.fixed / self.mistakes if self.mistakes else None,
            "keeps good plays": self.kept / fine if fine else None,
            "illegal": self.illegal / self.asked if self.asked else None,
            "errors": self.errors,
            "ms": statistics.median(self.latencies_ms) if self.latencies_ms else None,
            "$/decision": self.cost_usd / self.asked if self.asked and self.cost_usd else None,
        }


def score(
    agent: Agent,
    questions: Sequence[Question],
    *,
    on_answer: Callable[[Question, Action, bool], None] | None = None,
) -> Score:
    """Ask ``agent`` every question, each one fresh: a notebook does not carry between them."""
    result = Score(agent.name)
    usage = getattr(agent, "usage", None)
    spent_before = getattr(usage, "cost_usd", 0.0) or 0.0
    errors_before = getattr(usage, "errors", 0) or 0
    for q in questions:
        notes = getattr(agent, "notes", None)
        if notes is not None:
            notes.clear()
        started = time.perf_counter()
        try:
            decision = agent.act(q.view)
            action = decision.action
        except Exception:  # noqa: BLE001 - a crash is a wrong answer, not the end of the run
            result.errors += 1
            action = Action.fold()
        result.latencies_ms.append((time.perf_counter() - started) * 1000)
        applied = sanitize(action, q.view.legal)
        right = applied is not None and q.accepts(applied)
        result.asked += 1
        result.agreed += right
        result.illegal += applied is None
        result.asked_by_tag[q.tag] += 1
        result.by_tag[q.tag] += right
        if q.mistake:
            result.mistakes += 1
            result.fixed += right
        else:
            result.kept += right
        if on_answer is not None:
            on_answer(q, applied or action, right)
    if usage is not None:
        result.cost_usd = (getattr(usage, "cost_usd", 0.0) or 0.0) - spent_before
        result.errors += (getattr(usage, "errors", 0) or 0) - errors_before
    return result


def majority_rate(questions: Sequence[Question]) -> tuple[str, float]:
    """The best a seat that gives one fixed answer to every question could score."""
    best, rate = "fold", 0.0
    for kind in (ActionType.FOLD, ActionType.CHECK, ActionType.CALL, ActionType.RAISE):
        hits = sum(q.accepts(Action(kind, 1)) for q in questions)
        if questions and hits / len(questions) > rate:
            best, rate = kind.value, hits / len(questions)
    return best, rate


def format_scores(scores: Sequence[Score], questions: Sequence[Question]) -> str:
    rows = [s.row() for s in scores]
    head = ["seat", "agrees", "fixes mistakes", "keeps good plays", "illegal", "errors", "ms"]
    if any(r["$/decision"] for r in rows):
        head.append("$/decision")

    def cell(key: str, value: Any) -> str:
        if value is None:
            return "-"
        if key in ("agrees", "fixes mistakes", "keeps good plays", "illegal"):
            return f"{value:.0%}"
        if key == "ms":
            return f"{value:.0f}" if value >= 1 else f"{value:.2f}"
        if key == "$/decision":
            return f"{value:.5f}"
        return str(value)

    table = [head] + [[cell(k, r[k]) for k in head] for r in rows]
    answer, rate = majority_rate(questions)
    table.append([f"always {answer}", f"{rate:.0%}"] + ["-"] * (len(head) - 2))
    widths = [max(len(row[i]) for row in table) for i in range(len(head))]
    lines = [
        "  ".join(
            c.ljust(w) if i == 0 else c.rjust(w)
            for i, (c, w) in enumerate(zip(row, widths, strict=True))
        )
        for row in table
    ]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)
