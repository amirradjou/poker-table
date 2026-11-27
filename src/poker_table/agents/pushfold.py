"""nash:pushfold — a heads-up push/fold equilibrium that this project computes itself.

The game it solves: two players with the same stack, the small blind half the big blind, and
each player posting the same ante, or none. The small blind (the button, heads-up) shoves all in
or folds; the big blind calls or folds. ``scripts/solve_pushfold.py`` works out the exact all-in
equity of all 169 classes of starting hands against each other and runs CFR+ on that game at
every half big blind from 1 to 20: once with no ante, and once for each ante in the file (a
tenth and an eighth of the big blind, the two the default blind schedule uses). That is the
chart in ``data/pushfold.json``. Miltersen and Sørensen (2007) solved this game for a heads-up
tournament and found jam/fold near-optimal for short stacks; that paper is the one this seat
links.

Where it plays its chart: exactly two seats, blinds and an ante that one of its charts was
solved for (the ante's exact share of the big blind, never the nearest one), and an effective
stack (the smaller stack before the antes and blinds) of at most ``VALID_UP_TO`` big blinds. The
chart is read at the nearest half big blind. A table of more seats is refused before the first
hand, and so is a table where no level of its blinds has a chart. Any other hand (stacks past 15
big blinds, a level with blinds or an ante no chart has) is played by the scripted tag bot
instead, and the seat counts every decision as its chart's or the fallback's, with the reason
in the decision's ``meta``.

Actions the chart has no answer for, and what it does with them:

* as the big blind facing any raise, of any size: the calling range against a shove. A hand that
  would call a shove shoves (or calls, when that is all in); the rest fold;
* as the big blind facing a limp: the calling range shoves, the rest check;
* after the flop, which only a limped pot reaches: the fallback plays.
"""

from __future__ import annotations

import json
import random
from fractions import Fraction
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
PAPER = "https://doi.org/10.1145/1329125.1329357"  # Miltersen & Sørensen, AAMAS 2007
VALID_UP_TO = 15.0  # big blinds, effective
NO_ANTE = "0"


@cache
def chart() -> dict[str, Any]:
    """The data file. Its top level is the no-ante chart, as it always was; ``antes`` holds the
    same game solved with each ante, keyed by the ante's share of the big blind."""
    text = files("poker_table.agents").joinpath("data/pushfold.json").read_text(encoding="utf-8")
    return json.loads(text)


def charts() -> dict[str, dict[str, Any]]:
    """Every chart, by its ante's share of the big blind: ``"0"``, ``"1/10"``, ``"1/8"``."""
    data = chart()
    return {NO_ANTE: data, **data.get("antes", {})}


def ante_key(ante: int, big_blind: int) -> str:
    """The chart an ante needs: its exact share of the big blind. 2 on 20 and 5 on 50 are both
    ``"1/10"``; 3 on 20 is ``"3/20"``, which no chart has, and is not rounded to one that does."""
    return str(Fraction(ante, big_blind))


def off_chart(small_blind: int, big_blind: int, ante: int) -> str | None:
    """Why no chart was solved for these blinds and this ante; None when one was."""
    if 2 * small_blind != big_blind:
        return f"blinds of {small_blind}/{big_blind}, and the charts have a small blind of half"
    if ante_key(ante, big_blind) not in charts():
        shares = " or ".join(key for key in charts() if key != NO_ANTE)
        return (
            f"an ante of {ante} on a big blind of {big_blind}, and the charts have no ante or "
            f"one of {shares} of the big blind"
        )
    return None


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


def push_probability(hand: str, effective_bb: float, ante: str = NO_ANTE) -> float:
    return charts()[ante]["push_per_mille"][hand][stack_index(effective_bb)] / 1000


def call_probability(hand: str, effective_bb: float, ante: str = NO_ANTE) -> float:
    return charts()[ante]["call_per_mille"][hand][stack_index(effective_bb)] / 1000


def best_response_gain_mbb(index: int, ante: str = NO_ANTE) -> float:
    """What a best response wins against the stored chart at one stack, per hand, averaged over
    the two seats, in thousandths of a big blind. Exact inside the push/fold game.

    Pure Python, from the shipped equities, so anyone can check the chart without numpy. The
    payoffs are the solver's (``solvers/pushfold.py``): each player posts the ante and then its
    blind, and never more than its stack.
    """
    stack = chart()["stacks_bb"][index]
    table = charts()[ante]
    push = [table["push_per_mille"][class_name(i)][index] / 1000 for i in range(CLASSES)]
    call = [table["call_per_mille"][class_name(j)][index] / 1000 for j in range(CLASSES)]
    dead = float(Fraction(ante))
    small, big = min(stack, dead + 0.5), min(stack, dead + 1.0)  # what each has in to begin
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
            fold += chance * -small
            shove += chance * ((1 - call[j]) * big + call[j] * showdown)
            big_blind += chance * (1 - push[i]) * small
            calls[j] += chance * push[i] * -showdown
            folds[j] += chance * push[i] * -big
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
    """The smaller of the two stacks as the hand began, before the antes and blinds, in big
    blinds."""
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
        """Refuse a table only when no hand at it could ever be played from a chart: a cash
        table whose blinds and ante have none, or a freezeout where no level has one."""
        levels = shape.levels or ((shape.small_blind, shape.big_blind, shape.ante),)
        problems = [off_chart(*level) for level in levels]
        if not all(problems):
            return None
        if not shape.levels:
            return f"{KIND} has no chart for this table: {problems[0]}"
        return f"{KIND} has no chart for any level of this schedule: level 1 has {problems[0]}"

    def table_note(self, shape: TableShape) -> str | None:
        notes = []
        depth = shape.stack / shape.big_blind
        if depth > VALID_UP_TO:
            notes.append(
                f"{depth:g} big blinds is deeper than the chart's {VALID_UP_TO:g}, so the tag "
                "fallback plays every hand until the stacks come down"
            )
        missing = [str(n) for n, level in enumerate(shape.levels, 1) if off_chart(*level)]
        if missing:
            levels = "level " + missing[0] if len(missing) == 1 else "levels " + ", ".join(missing)
            notes.append(f"no chart has the blinds and ante of {levels}, so tag plays those")
        return "; ".join(notes) or None

    # ----- play ------------------------------------------------------------------------

    def act(self, view: SeatView) -> Decision:
        first = view.hand_id != self._hand
        self._hand = view.hand_id
        meta: dict[str, Any] = {"paper": PAPER} if first else {}
        if len(view.players) != 2:
            why = "more than two seats: the chart is heads-up"
            return self._fallback(view, why, meta, "seats")
        problem = off_chart(view.small_blind, view.big_blind, view.ante)
        if problem:
            reason = "blinds" if problem.startswith("blinds") else "ante"
            return self._fallback(view, problem, meta, reason)
        ante = ante_key(view.ante, view.big_blind)
        if ante != NO_ANTE:
            meta["ante"] = ante  # which chart; a hand without an ante leaves it out, as before
        depth = effective_big_blinds(view)
        meta["effective_bb"] = round(depth, 2)
        if depth > VALID_UP_TO:
            return self._fallback(view, f"{depth:g} bb deep, past the chart's 15", meta, "deep")
        if view.street is not Street.PREFLOP:
            why = "after the flop in a limped pot: past the chart"
            return self._fallback(view, why, meta, "flop")

        hand = class_name(class_index(*view.hole))
        legal = view.legal
        dealer = random.Random(f"{self.seed}:{view.hand_id}:{len(view.events)}")
        acted = any(e.kind is EventKind.ACTION and e.seat == view.seat for e in view.events)
        small_blind = view.seat == view.button
        if small_blind and not acted:
            p = push_probability(hand, depth, ante)
            shove = dealer.random() < p
            why = f"{hand} at {depth:g} bb: shove {p:.0%} of the time"
            action = self._all_in(view) if shove else self._fold(view)
            return self._decide(action, "own", f"{why}, so {'shove' if shove else 'fold'}", meta)
        if not small_blind and not acted:
            p = call_probability(hand, depth, ante)
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
        return self._fallback(view, "a spot the push/fold game does not have", meta, "spot")

    def _decide(self, action: Action, method: str, why: str, meta: dict[str, Any]) -> Decision:
        self.usage.count(method)
        return Decision(action, reasoning=why, meta=meta | {"method": method})

    def _fallback(self, view: SeatView, why: str, meta: dict[str, Any], reason: str) -> Decision:
        """Tag plays it. ``reason`` goes in ``meta`` as ``off_chart``, one word to count by:
        seats, blinds, ante, deep, flop or spot."""
        self.usage.count("fallback")
        decision = self.fallback.act(view)
        return Decision(
            decision.action,
            reasoning=f"{why}; tag plays it: {decision.reasoning}",
            meta=meta | {"method": "fallback", "off_chart": reason},
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
