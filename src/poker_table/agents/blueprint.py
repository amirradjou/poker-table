"""cfr:blueprint — a CFR+ blueprint for heads-up no-limit, on a coarse abstraction of our own.

The method is the one behind the blueprints of the big heads-up bots: shrink the game until it
can be solved (a few bet sizes, hands grouped into buckets), solve the small game with a CFR
variant, and map whatever happens at the real table onto the small game. Ours is much coarser
than theirs and was trained in minutes on a laptop, so it is a small reimplementation of the
idea, not of any of those bots. ``agents/abstraction.py`` has the game, ``scripts/
train_blueprint.py`` trains it and ``docs/solver-seats.md`` has what it is and what it is not.

Where it plays its own strategy: two seats, no ante, the small blind half the big blind, and an
effective stack (the smaller one as the hand starts) within ``DEPTH_BAND`` of the 100 big blinds
it was trained on. A table that starts anywhere else is refused before the first hand. When
stacks drift outside the band during a session, the scripted tag bot plays those hands. The
seat counts each decision as its own, translated, or the fallback's.

How it keeps the real hand and the abstract one in step:

* an opponent's bet or raise is read as one of the tree's sizes with the pseudo-harmonic mapping
  (Ganzfried and Sandholm, 2013), drawn at random with its probability; an all-in is an all-in;
* when that reading closes the betting while the table still asks us to act (a small bet read as
  a check), we call;
* when the tree is all in and the table is not, we check or call to the end;
* a raise past the tree's cap of three a street hands the rest of the hand to the fallback.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from poker_table.agents.abstraction import (
    ALL_IN,
    CHANCE,
    DECISION,
    SHOWDOWN,
    Spot,
    Strategy,
    bucket,
    pseudo_harmonic,
    raise_target,
    read_strategy,
)
from poker_table.agents.base import Decision, MethodUsage, SeatView, TableShape
from poker_table.agents.handclass import class_index, class_name
from poker_table.agents.pushfold import put_in
from poker_table.agents.scripted import TightAggressive
from poker_table.engine import Action, ActionType, EventKind, Street

KIND = "cfr:blueprint"
DATA = Path(__file__).parent / "data" / "blueprint.bin"
PAPER = "https://arxiv.org/abs/1407.5042"  # Tammelin (2014), CFR+
TRANSLATION_PAPER = "https://www.ijcai.org/Proceedings/13/Papers/028.pdf"
DEPTH_BAND = (90.0, 110.0)  # effective big blinds where the blueprint plays its own hands

_STREETS = {
    Street.PREFLOP: "preflop",
    Street.FLOP: "flop",
    Street.TURN: "turn",
    Street.RIVER: "river",
}


@cache
def shipped() -> Strategy:
    return read_strategy(files("poker_table.agents").joinpath("data/blueprint.bin").read_bytes())


def describe(label: str) -> str:
    if label == "f":
        return "fold"
    if label == "c":
        return "check or call"
    if label == ALL_IN:
        return "all in"
    return f"{label[1:]} pot"


@dataclass(slots=True)
class _Hand:
    """Where one hand is, in the tree and at the table."""

    hand_id: str
    me: int  # 0 when we are the small blind (the button), 1 when we are the big blind
    start: dict[int, int]  # seat -> chips as the hand began
    node: Spot
    seen: int = 0  # events already read
    off: str = ""  # why the fallback has the rest of this hand
    committed: bool = False  # the tree is all in and the table is not
    translated: bool = False  # an opponent action was read as a size it did not make
    mine: str | None = None  # the label of the action we just took, until its event is read


class BlueprintAgent:
    kind = KIND
    heads_up_only = True
    paper = PAPER
    method_name = "blueprint"

    def __init__(
        self,
        name: str = "blueprint",
        *,
        seed: int = 0,
        fallback: Any = None,
        strategy: Strategy | None = None,
    ) -> None:
        self.name = name
        self.seed = seed
        self.usage = MethodUsage()
        self.fallback = fallback if fallback is not None else TightAggressive(name, seed=seed)
        self._strategy = strategy
        self._hand: _Hand | None = None

    @property
    def strategy(self) -> Strategy:
        if self._strategy is None:
            self._strategy = shipped()
        return self._strategy

    # ----- seating ---------------------------------------------------------------------

    def check_table(self, shape: TableShape) -> str | None:
        trained = self.strategy.betting
        depth = trained.stack / trained.big_blind
        if shape.ante:
            return f"{KIND} was trained without antes, and this table has an ante of {shape.ante}"
        if shape.small_blind * 2 != shape.big_blind:
            return (
                f"{KIND} was trained with the small blind half the big blind, and this table "
                f"is {shape.small_blind}/{shape.big_blind}"
            )
        start = shape.stack / shape.big_blind
        if not DEPTH_BAND[0] <= start <= DEPTH_BAND[1]:
            return (
                f"{KIND} was trained for {depth:g} big blinds deep and plays "
                f"{DEPTH_BAND[0]:g}-{DEPTH_BAND[1]:g}; this table starts at {start:g} "
                f"(try --stack {trained.stack // trained.big_blind * shape.big_blind})"
            )
        return None

    # ----- play ------------------------------------------------------------------------

    def act(self, view: SeatView) -> Decision:
        hand = self._hand
        # a new hand: a new id, or the same id again with fewer events (a second league)
        first = hand is None or hand.hand_id != view.hand_id or len(view.events) < hand.seen
        if first:
            hand = self._hand = self._start(view)
        assert hand is not None
        meta: dict[str, Any] = {"paper": PAPER} if first else {}
        if not hand.off:
            self._catch_up(view, hand)
        if hand.off:
            return self._fallback(view, hand.off, meta)
        if hand.committed:
            action = Action.check() if view.legal.can_check else Action.call()
            hand.mine = None
            return self._decide(action, "translated", "all in in the tree: check or call", meta)
        node = hand.node
        if node.kind != DECISION:
            # The tree has the betting closed, the table asks us: the opponent's last action was
            # read as a check or a call, so we close it too.
            action = Action.check() if view.legal.can_check else Action.call()
            hand.mine = None
            if node.kind == SHOWDOWN:
                hand.committed = True
            return self._decide(action, "translated", "the tree closed this street: call", meta)
        if node.actor != hand.me:
            hand.off = "the tree expected the other player to act"
            return self._fallback(view, hand.off, meta)

        state = self._state(view)
        probabilities = self.strategy.probabilities(node, state)
        dealer = random.Random(f"{self.seed}:{view.hand_id}:{len(view.events)}")
        # The likeliest action catches a roll past the rounded total, never an unplayed one.
        roll, label = dealer.random(), node.labels[probabilities.index(max(probabilities))]
        for candidate, p in zip(node.labels, probabilities, strict=True):
            if roll < p:
                label = candidate
                break
            roll -= p
        chance = probabilities[node.labels.index(label)]
        action = self._action(view, label)
        # What the table could not take as the tree's move (a raise when raising is closed, a
        # fold when checking is free) goes into the tree as the check or call it became.
        passive = action.type in (ActionType.CHECK, ActionType.CALL)
        hand.mine = "c" if passive else label
        method = "translated" if hand.translated else "own"
        street = _STREETS[view.street]
        what = class_name(state) if view.street is Street.PREFLOP else f"bucket {state}"
        why = f"{street} {what}: {describe(label)} ({chance:.0%})"
        return self._decide(action, method, why, meta | {"bucket": state})

    def _start(self, view: SeatView) -> _Hand:
        start = {p.seat: p.stack + put_in(view, p.seat) for p in view.players}
        hand = _Hand(view.hand_id, 0 if view.seat == view.button else 1, start, self.strategy.root)
        if len(view.players) != 2:
            hand.off = "more than two seats: the blueprint is heads-up"
        elif view.ante:
            hand.off = "an ante: the blueprint was trained without one"
        elif view.small_blind * 2 != view.big_blind:
            hand.off = "blinds the blueprint was not trained on"
        else:
            depth = min(start.values()) / view.big_blind
            if not DEPTH_BAND[0] <= depth <= DEPTH_BAND[1]:
                hand.off = (
                    f"{depth:g} bb deep, outside the {DEPTH_BAND[0]:g}-{DEPTH_BAND[1]:g} it plays"
                )
        return hand

    def _state(self, view: SeatView) -> int:
        if view.street is Street.PREFLOP:
            return class_index(*view.hole)
        return bucket(self.strategy.cards, view.hole, view.board)

    def _catch_up(self, view: SeatView, hand: _Hand) -> None:
        """Walk the tree through every action since this seat last looked."""
        bets = {seat: 0 for seat in hand.start}
        paid = {seat: 0 for seat in hand.start}
        for n, event in enumerate(view.events):
            if event.kind is EventKind.STREET:
                bets = dict.fromkeys(bets, 0)
                if n >= hand.seen:
                    self._next_street(hand)
            elif event.kind in (EventKind.POST_BLIND, EventKind.ACTION) and event.seat is not None:
                if event.kind is EventKind.ACTION and n >= hand.seen and not hand.off:
                    self._read(view, hand, n, event, bets, paid)
                bets[event.seat] += event.amount
                paid[event.seat] += event.amount
            if hand.off:
                break
        hand.seen = len(view.events)

    def _next_street(self, hand: _Hand) -> None:
        if hand.committed:
            return
        if hand.node.kind == CHANCE:
            hand.node = hand.node.children[0]
        elif hand.node.kind == SHOWDOWN:
            hand.committed = True
        else:
            hand.off = "the table dealt the next street before the tree's betting closed"

    def _read(self, view: SeatView, hand: _Hand, n: int, event, bets, paid) -> None:
        if event.seat == view.seat:
            if hand.mine is not None and hand.node.kind == DECISION:
                hand.node = hand.node.child(hand.mine)
            hand.mine = None
            return
        if hand.committed:
            return
        node = hand.node
        if node.kind != DECISION or node.actor == hand.me:
            hand.off = "the other player acted where the tree has no move for them"
            return
        action = event.action
        kind = action.type
        if kind is ActionType.FOLD:
            hand.off = "the other player folded"
            return
        if kind in (ActionType.CHECK, ActionType.CALL):
            if kind is ActionType.CHECK and "f" in node.labels:
                hand.off = "a check where the tree has a bet to call"
                return
            hand.node = node.child("c")
            return
        sizes = [label for label in node.labels if label not in ("f", "c")]
        if not sizes:
            hand.off = "a raise past the tree's cap"
            return
        seat = event.seat
        current = max(bets.values())
        to_call = current - bets[seat]
        pot = sum(paid.values())
        left = hand.start[seat] - paid[seat]
        if event.all_in and ALL_IN in sizes:
            hand.node = node.child(ALL_IN)
            return
        # The same sizes the tree would bet here, worked out on the real pot.
        candidates: list[tuple[float, str]] = [(0.0, "c")]
        for label in sizes:
            fraction = (left - to_call) / (pot + to_call) if label == ALL_IN else float(label[1:])
            candidates.append((fraction, label))
        candidates.sort()
        exact = {
            raise_target(fraction, current, pot, to_call): label
            for fraction, label in candidates[1:]
            if label != ALL_IN
        }
        if action.amount in exact:
            hand.node = node.child(exact[action.amount])
            return
        size = (action.amount - current) / (pot + to_call)
        label = candidates[-1][1]
        for (low, low_label), (high, high_label) in zip(candidates, candidates[1:], strict=False):
            if low <= size <= high:
                p_low = pseudo_harmonic(low, high, size) if high > low else 1.0
                dealer = random.Random(f"{self.seed}:{view.hand_id}:read:{n}")
                label = low_label if dealer.random() < p_low else high_label
                break
        hand.translated = True
        hand.node = node.child(label)

    def _action(self, view: SeatView, label: str) -> Action:
        legal = view.legal
        if label == "f":
            return Action.check() if legal.can_check else Action.fold()
        if label == "c" or not legal.can_raise:
            return Action.check() if legal.can_check else Action.call()
        if label == ALL_IN:
            amount = legal.max_raise_to
        else:
            target = raise_target(float(label[1:]), view.current_bet, view.pot, view.to_call)
            amount = min(max(target, legal.min_raise_to), legal.max_raise_to)
        return Action.bet(amount) if legal.is_bet else Action.raise_to(amount)

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
