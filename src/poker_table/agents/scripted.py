"""Scripted baseline agents that need no model call."""

from __future__ import annotations

import random

from poker_table.agents.base import Decision, SeatView
from poker_table.agents.strength import (
    MadeHand,
    chen_score,
    classify,
    has_flush_draw,
    has_open_ender,
    outs_equity,
)
from poker_table.engine import Action, ActionType, Street


class RandomAgent:
    """Picks uniformly among legal actions; sizes bets uniformly. The chaos baseline."""

    kind = "random"

    def __init__(self, name: str = "random", seed: int = 0) -> None:
        self.name = name
        self._rng = random.Random(seed)

    def act(self, view: SeatView) -> Decision:
        legal = view.legal
        options: list[Action] = [Action.check()] if legal.can_check else [Action.fold()]
        if legal.can_call:
            options.append(Action.call())
        if legal.can_raise:
            amount = self._rng.randint(legal.min_raise_to, legal.max_raise_to)
            options.append(Action.bet(amount) if legal.is_bet else Action.raise_to(amount))
        return Decision(self._rng.choice(options), reasoning="coin flip")


class CallingStation:
    """Never folds, never raises: checks when it can and calls anything."""

    kind = "station"

    def __init__(self, name: str = "station") -> None:
        self.name = name

    def act(self, view: SeatView) -> Decision:
        if view.legal.can_check:
            return Decision(Action.check(), reasoning="free card")
        return Decision(Action.call(), reasoning="I want to see what you have")


def clamp(amount: int, low: int, high: int) -> int:
    return max(low, min(amount, high))


def raise_or_bet(view: SeatView, amount: int) -> Action:
    legal = view.legal
    amount = clamp(amount, legal.min_raise_to, legal.max_raise_to)
    return Action.bet(amount) if legal.is_bet else Action.raise_to(amount)


_EARLY = {"UTG", "UTG+1", "UTG+2", "MP", "LJ"}
_LATE = {"BTN", "BTN/SB", "SB"}


class TightAggressive:
    """A solid, unimaginative regular: Chen-formula preflop, made-hand rules postflop.

    ``tightness`` shifts every preflop threshold (positive = tighter); ``aggression`` below 1
    makes it call where it would have bet. ``TightAggressive(tightness=2, aggression=0.3)``
    is a rock; the defaults are a plain TAG.
    """

    def __init__(
        self,
        name: str = "tag",
        *,
        tightness: float = 0.0,
        aggression: float = 1.0,
        seed: int = 0,
        kind: str = "tag",
    ) -> None:
        self.name = name
        self.kind = kind
        self.tightness = tightness
        self.aggression = aggression
        self._rng = random.Random(seed)

    def act(self, view: SeatView) -> Decision:
        if view.street is Street.PREFLOP:
            return self._preflop(view)
        return self._postflop(view)

    # -- preflop --

    def _open_threshold(self, view: SeatView) -> float:
        # Chen's thresholds are for full-ring tables; a point looser when six or fewer.
        if view.position in _EARLY:
            base = 9
        elif view.position in _LATE:
            base = 7
        else:
            base = 8
        if len(view.players) <= 6:
            base -= 1
        return base + self.tightness

    def _preflop(self, view: SeatView) -> Decision:
        score = chen_score(view.hole)
        legal = view.legal
        bb = view.big_blind
        facing_raise = view.current_bet > bb
        limpers = sum(
            1
            for e in view.street_actions
            if e.action is not None and e.action.type is ActionType.CALL
        )
        why = f"Chen {score:g} from {view.position}"

        if view.stack + view.street_bet <= 15 * bb and legal.can_raise:
            if score >= 9 + self.tightness:
                return Decision(raise_or_bet(view, legal.max_raise_to), f"{why}: short, shove")
            if legal.can_check:
                return Decision(Action.check(), f"{why}: short, check")
            return Decision(Action.fold(), f"{why}: short, fold")

        if not facing_raise:
            if score >= self._open_threshold(view) and legal.can_raise:
                if self._rng.random() < self.aggression:
                    size = 3 * bb + limpers * bb
                    return Decision(raise_or_bet(view, size), f"{why}: open to {size}")
                if legal.can_call:
                    return Decision(Action.call(), f"{why}: limp")
            if legal.can_check:
                return Decision(Action.check(), f"{why}: check option")
            return Decision(Action.fold(), f"{why}: fold")

        already_raised = any(
            e.seat == view.seat and e.action is not None and e.action.type is ActionType.RAISE
            for e in view.street_actions
        )
        if score >= 12 + self.tightness and legal.can_raise:
            if already_raised and score < 16 + self.tightness:
                return Decision(Action.call(), f"{why}: call the re-raise")
            if self._rng.random() < self.aggression:
                return Decision(raise_or_bet(view, view.current_bet * 3), f"{why}: re-raise")
            return Decision(Action.call(), f"{why}: flat")
        if score >= 7 + self.tightness and view.to_call <= max(0.25 * view.stack, 4 * bb):
            return Decision(Action.call(), f"{why}: call a raise")
        if legal.can_check:
            return Decision(Action.check(), f"{why}: check")
        return Decision(Action.fold(), f"{why}: fold to a raise")

    # -- postflop --

    def _postflop(self, view: SeatView) -> Decision:
        made = classify(view.hole, view.board)
        legal = view.legal
        pot = view.pot
        to_call = view.to_call
        flush = has_flush_draw(view.hole, view.board)
        straight = has_open_ender(view.hole, view.board)
        outs = (9 if flush else 0) + (8 if straight else 0)
        to_come = 2 if view.street is Street.FLOP else 1
        equity = outs_equity(outs, to_come)
        why = f"{made.name.lower().replace('_', ' ')} on {view.street.value}"
        aggressive = self._rng.random() < self.aggression

        if made is MadeHand.STRONG:
            if legal.can_raise and aggressive:
                target = view.current_bet * 3 if to_call else round(pot * 0.66)
                return Decision(raise_or_bet(view, target), f"{why}: value")
            return Decision(Action.call() if to_call else Action.check(), f"{why}: slow")

        if made is MadeHand.TOP_PAIR:
            if not to_call:
                if legal.can_raise and aggressive:
                    return Decision(raise_or_bet(view, round(pot * 0.5)), f"{why}: bet")
                return Decision(Action.check(), f"{why}: check")
            if to_call <= pot * 0.6:
                return Decision(Action.call(), f"{why}: call")
            return Decision(Action.fold(), f"{why}: too expensive")

        if made is MadeHand.WEAK_PAIR:
            if not to_call:
                return Decision(Action.check(), f"{why}: check")
            if to_call <= pot * 0.35:
                return Decision(Action.call(), f"{why}: cheap call")
            return Decision(Action.fold(), f"{why}: fold")

        if outs:
            why = f"draw ({outs} outs, {equity:.0%}) on {view.street.value}"
            if not to_call:
                if legal.can_raise and self._rng.random() < 0.3 * self.aggression:
                    return Decision(raise_or_bet(view, round(pot * 0.5)), f"{why}: semi-bluff")
                return Decision(Action.check(), f"{why}: check")
            if view.pot_odds <= equity:
                return Decision(Action.call(), f"{why}: priced in")
            return Decision(Action.fold(), f"{why}: not priced in")

        if not to_call:
            is_aggressor = view.preflop_aggressor() == view.seat
            if (
                view.street is Street.FLOP
                and is_aggressor
                and view.active_players <= 3
                and legal.can_raise
                and aggressive
            ):
                return Decision(raise_or_bet(view, round(pot * 0.5)), f"{why}: c-bet")
            return Decision(Action.check(), f"{why}: check")
        return Decision(Action.fold(), f"{why}: fold")


class Maniac:
    """Raises most of the time, calls the rest, almost never folds. Loud, expensive, fun."""

    kind = "maniac"

    def __init__(self, name: str = "maniac", seed: int = 0, raise_rate: float = 0.7) -> None:
        self.name = name
        self.raise_rate = raise_rate
        self._rng = random.Random(seed)

    def act(self, view: SeatView) -> Decision:
        legal = view.legal
        roll = self._rng.random()
        if legal.can_raise and roll < self.raise_rate:
            target = max(legal.min_raise_to, round(view.pot * self._rng.uniform(0.5, 1.5)))
            return Decision(raise_or_bet(view, target), reasoning="pressure", table_talk="raise!")
        if roll < 0.95 or legal.can_check:
            action = Action.check() if legal.can_check else Action.call()
            return Decision(action, reasoning="never fold")
        return Decision(Action.fold(), reasoning="fine, take it")
