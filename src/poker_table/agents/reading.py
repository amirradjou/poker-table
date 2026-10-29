"""Hand reading for a seat: what each opponent's actions this hand say they hold.

The probability calculator (``odds.py``) answers "how often do I win against random
hands". Nobody at a real table holds a random hand after raising preflop and betting two
streets. This module narrows each live opponent's range from the public action — the same
narrowing the coach grades with (``coach.facts.read_ranges``) — and prices the seat's hand
against those ranges. It reads only what a view contains: the seat's own cards, the board
and everyone's actions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from poker_table.agents.base import SeatView
from poker_table.engine import ActionType, EventKind, Street

VERBS = {
    ActionType.BET: "bet",
    ActionType.RAISE: "raised",
    ActionType.CALL: "called",
    ActionType.CHECK: "checked",
}


@dataclass(frozen=True, slots=True)
class RangeRead:
    seat: int
    name: str
    story: str  # "opened from the CO, bet the flop"
    share: float  # of all 1326 starting hands


@dataclass(frozen=True, slots=True)
class HandReading:
    ranges: tuple[RangeRead, ...]
    equity: float  # against those ranges, ties shared
    samples: int

    @property
    def error(self) -> float:
        return math.sqrt(self.equity * (1 - self.equity) / self.samples) if self.samples else 0.0

    @property
    def informative(self) -> bool:
        """False while nobody has done anything that narrows a range."""
        return any(r.share < 1.0 for r in self.ranges)

    def lines(self) -> list[str]:
        out = [f"{r.name}: {r.story} — about {_pct(r.share)} of hands" for r in self.ranges]
        out.append(
            f"Your equity against those ranges: {self.equity:.1%} "
            f"(±{self.error:.1%}, {self.samples} samples)"
        )
        return out


def read_hand(view: SeatView, *, samples: int = 600, seed: int = 0) -> HandReading:
    """Narrow every live opponent's range from the action, then price the hand against them."""
    from poker_table.coach.equity import equity  # the coach imports the agents package
    from poker_table.coach.facts import read_ranges
    from poker_table.coach.ranges import range_fraction

    ranges = read_ranges(view)
    reads = []
    for seat, rng in ranges.items():
        if rng is None:
            share = 1.0
        elif isinstance(rng, frozenset):
            share = range_fraction(rng)
        else:
            share = len(rng) / 1326
        reads.append(RangeRead(seat, view.players[seat].name, _story(view, seat), share))
    eq = equity(view.hole, view.board, list(ranges.values()), samples=samples, seed=seed)
    return HandReading(tuple(reads), eq, samples)


def _story(view: SeatView, seat: int) -> str:
    """What one seat has done this hand, in words: "opened from the CO, bet the flop"."""
    raises = 0
    parts: list[str] = []
    for e in view.events:
        if e.kind is not EventKind.ACTION or e.action is None:
            continue
        kind = e.action.type
        aggressive = kind in (ActionType.BET, ActionType.RAISE)
        if e.seat == seat:
            if e.street is Street.PREFLOP:
                if aggressive:
                    parts.append(
                        f"opened from the {view.players[seat].position}"
                        if raises == 0
                        else f"{raises + 2}-bet"
                        if raises < 3
                        else "re-raised"
                    )
                elif kind is ActionType.CALL:
                    parts.append("limped" if raises == 0 else "called a raise")
                elif kind is ActionType.CHECK:
                    parts.append("checked the big blind")
            elif kind in VERBS:
                parts.append(f"{VERBS[kind]} the {e.street.value}")
        if e.street is Street.PREFLOP and aggressive:
            raises += 1
    return ", ".join(parts) if parts else "has not acted yet"


def _pct(share: float) -> str:
    return f"{share:.0%}" if share >= 0.1 else f"{share:.1%}"
