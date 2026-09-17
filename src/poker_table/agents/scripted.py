"""Scripted baseline agents that need no model call."""

from __future__ import annotations

import random

from poker_table.agents.base import Decision, SeatView
from poker_table.engine import Action


class RandomAgent:
    """Picks uniformly among legal actions; sizes bets uniformly. The chaos baseline."""

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

    def __init__(self, name: str = "station") -> None:
        self.name = name

    def act(self, view: SeatView) -> Decision:
        if view.legal.can_check:
            return Decision(Action.check(), reasoning="free card")
        return Decision(Action.call(), reasoning="I want to see what you have")
