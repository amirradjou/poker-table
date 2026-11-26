"""nash:pushfold — a heads-up push/fold equilibrium that this project computes itself.

The game it solves: two players with the same stack, no ante. The small blind (the button,
heads-up) shoves all in or folds; the big blind calls or folds. ``scripts/solve_pushfold.py``
works out the exact all-in equity of all 169 classes of starting hands against each other and
runs CFR+ on that game at every half big blind from 1 to 20, which is the chart in
``data/pushfold.json``. Miltersen and Sørensen (2007) solved this game for a heads-up tournament
and found jam/fold near-optimal for short stacks; that paper is the one this seat links.

Where it plays its chart: exactly two seats, no ante, an effective stack (the smaller stack
before the blinds) of at most ``VALID_UP_TO`` big blinds. The chart is read at the nearest half
big blind. A table of more seats or with an ante is refused before the first hand. When stacks
drift past 15 big blinds in a session, or an ante appears, the hand is played by the scripted
tag bot instead, and the seat counts every decision as its chart's or the fallback's.

Actions the chart has no answer for, and what it does with them:

* as the big blind facing any raise, of any size: the calling range against a shove. A hand that
  would call a shove shoves (or calls, when that is all in); the rest fold;
* as the big blind facing a limp: the calling range shoves, the rest check;
* after the flop, which only a limped pot reaches: the fallback plays.
"""

from __future__ import annotations

import json
import random
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from poker_table.agents.base import Decision, MethodUsage, SeatView, TableShape
from poker_table.agents.handclass import (
    CLASSES,
    class_combos,
    class_index,
    class_name,
    compatible_counts,
)
from poker_table.agents.scripted import TightAggressive
from poker_table.engine import Action, EventKind, Street

KIND = "nash:pushfold"
DATA = Path(__file__).parent / "data" / "pushfold.json"
EQUITY = Path(__file__).parent / "data" / "pushfold_equity.json"
PAPER = "https://www.itu.dk/~trbj/papers/poker.pdf"  # Miltersen & Sørensen, AAMAS 2007
VALID_UP_TO = 15.0  # big blinds, effective


@cache
def chart() -> dict[str, Any]:
    text = files("poker_table.agents").joinpath("data/pushfold.json").read_text(encoding="utf-8")
    return json.loads(text)


@cache
def equities() -> list[list[float]]:
    """``[i][j]``: the all-in equity of class i against class j (wins plus half the ties)."""
    source = files("poker_table.agents").joinpath("data/pushfold_equity.json")
    data = json.loads(source.read_text(encoding="utf-8"))
    scale = 10 ** data["decimals"]
    upper = iter(data["upper_triangle"])
    table = [[0.0] * CLASSES for _ in range(CLASSES)]
    for i in range(CLASSES):
        for j in range(i, CLASSES):
            table[i][j] = next(upper) / scale
            table[j][i] = 1.0 - table[i][j]
    return table


def stack_index(effective_bb: float) -> int:
    """The chart column for an effective stack: the nearest half big blind, from 1 up."""
    stacks = chart()["stacks_bb"]
    index = round((max(effective_bb, stacks[0]) - stacks[0]) / 0.5)
    return min(index, len(stacks) - 1)


def push_probability(hand: str, effective_bb: float) -> float:
    return chart()["push_per_mille"][hand][stack_index(effective_bb)] / 1000


def call_probability(hand: str, effective_bb: float) -> float:
    return chart()["call_per_mille"][hand][stack_index(effective_bb)] / 1000


def best_response_gain_mbb(index: int) -> float:
    """What a best response wins against the stored chart at one stack, per hand, averaged over
    the two seats, in thousandths of a big blind. Exact inside the push/fold game.

    Pure Python, from the shipped equities, so anyone can check the chart without numpy.
    """
    stack = chart()["stacks_bb"][index]
    push = [chart()["push_per_mille"][class_name(i)][index] / 1000 for i in range(CLASSES)]
    call = [chart()["call_per_mille"][class_name(j)][index] / 1000 for j in range(CLASSES)]
    sizes = [len(c) for c in class_combos()]
    compatible = compatible_counts()
    equity = equities()
    total = 1326 * 1225
    small_blind = 0.0  # the best response's winnings as the small blind
    big_blind = 0.0
    calls = [0.0] * CLASSES  # what calling is worth to each big blind hand, summed over deals
    folds = [0.0] * CLASSES
    for i in range(CLASSES):
        shove = fold = 0.0
        for j in range(CLASSES):
            chance = sizes[i] * compatible[i][j] / total
            if not chance:
                continue
            showdown = stack * (2 * equity[i][j] - 1)
            fold += chance * -0.5
            shove += chance * ((1 - call[j]) * 1.0 + call[j] * showdown)
            big_blind += chance * (1 - push[i]) * 0.5
            calls[j] += chance * push[i] * -showdown
            folds[j] += chance * push[i] * -1.0
        small_blind += max(fold, shove)
    big_blind += sum(max(c, f) for c, f in zip(calls, folds, strict=True))
    return (small_blind + big_blind) / 2 * 1000


def put_in(view: SeatView, seat: int) -> int:
    """Chips ``seat`` has put into this hand so far: antes, blinds and bets."""
    return sum(
        e.amount
        for e in view.events
        if e.seat == seat
        and e.kind in (EventKind.POST_ANTE, EventKind.POST_BLIND, EventKind.ACTION)
    )


def effective_big_blinds(view: SeatView) -> float:
    """The smaller of the two stacks as the hand began, before the blinds, in big blinds."""
    return min(p.stack + put_in(view, p.seat) for p in view.players) / view.big_blind


class PushFoldAgent:
    """The push/fold chart in a seat, with the tag bot for what the chart does not cover."""

    kind = KIND
    heads_up_only = True
    paper = PAPER
    method_name = "chart"

    def __init__(self, name: str = "pushfold", *, seed: int = 0, fallback: Any = None) -> None:
        self.name = name
        self.seed = seed
        self.usage = MethodUsage()
        self.fallback = fallback if fallback is not None else TightAggressive(name, seed=seed)
        self._hand = ""

    # ----- seating ---------------------------------------------------------------------

    def check_table(self, shape: TableShape) -> str | None:
        if shape.ante:
            return (
                f"{KIND} was solved without antes, and this table has an ante of {shape.ante}: "
                "play it with --ante 0"
            )
        return None

    def table_note(self, shape: TableShape) -> str | None:
        depth = shape.stack / shape.big_blind
        if depth > VALID_UP_TO:
            return (
                f"{depth:g} big blinds is deeper than the chart's {VALID_UP_TO:g}, so the tag "
                "fallback plays every hand until the stacks come down"
            )
        return None

    # ----- play ------------------------------------------------------------------------

    def act(self, view: SeatView) -> Decision:
        first = view.hand_id != self._hand
        self._hand = view.hand_id
        meta: dict[str, Any] = {"paper": PAPER} if first else {}
        if len(view.players) != 2:
            return self._fallback(view, "more than two seats: the chart is heads-up", meta)
        if view.ante:
            return self._fallback(view, "an ante: the chart was solved without one", meta)
        depth = effective_big_blinds(view)
        meta["effective_bb"] = round(depth, 2)
        if depth > VALID_UP_TO:
            return self._fallback(view, f"{depth:g} bb deep, past the chart's 15", meta)
        if view.street is not Street.PREFLOP:
            return self._fallback(view, "after the flop in a limped pot: past the chart", meta)

        hand = class_name(class_index(*view.hole))
        legal = view.legal
        dealer = random.Random(f"{self.seed}:{view.hand_id}:{len(view.events)}")
        acted = any(e.kind is EventKind.ACTION and e.seat == view.seat for e in view.events)
        small_blind = view.seat == view.button
        if small_blind and not acted:
            p = push_probability(hand, depth)
            shove = dealer.random() < p
            why = f"{hand} at {depth:g} bb: shove {p:.0%} of the time"
            action = self._all_in(view) if shove else self._fold(view)
            return self._decide(action, "own", f"{why}, so {'shove' if shove else 'fold'}", meta)
        if not small_blind and not acted:
            p = call_probability(hand, depth)
            in_range = dealer.random() < p
            why = f"{hand} at {depth:g} bb: calls a shove {p:.0%} of the time"
            opponent = next(pl for pl in view.players if pl.seat != view.seat)
            if view.current_bet > view.big_blind:  # any raise is read as a shove
                on_tree = opponent.all_in or view.to_call >= view.stack
                method = "own" if on_tree else "translated"
                if not in_range:
                    return self._decide(self._fold(view), method, f"{why}, so fold", meta)
                action = Action.call() if on_tree or not legal.can_raise else self._all_in(view)
                return self._decide(action, method, f"{why}, so {action}", meta)
            if legal.can_check:  # a limp
                if in_range and legal.can_raise:
                    return self._decide(
                        self._all_in(view), "translated", f"{why}; a limp, so shove", meta
                    )
                return self._decide(Action.check(), "translated", f"{why}; a limp, check", meta)
        return self._fallback(view, "a spot the push/fold game does not have", meta)

    def _decide(self, action: Action, method: str, why: str, meta: dict[str, Any]) -> Decision:
        self.usage.count(method)
        return Decision(action, reasoning=why, meta=meta | {"method": method})

    def _fallback(self, view: SeatView, why: str, meta: dict[str, Any]) -> Decision:
        self.usage.count("fallback")
        decision = self.fallback.act(view)
        return Decision(
            decision.action,
            reasoning=f"{why}; tag plays it: {decision.reasoning}",
            meta=meta | {"method": "fallback"},
        )

    @staticmethod
    def _all_in(view: SeatView) -> Action:
        legal = view.legal
        if not legal.can_raise:
            return Action.call() if legal.can_call else Action.check()
        amount = legal.max_raise_to
        return Action.bet(amount) if legal.is_bet else Action.raise_to(amount)

    @staticmethod
    def _fold(view: SeatView) -> Action:
        return Action.check() if view.legal.can_check else Action.fold()
