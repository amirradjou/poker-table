"""Run a hand with agents in the seats, validating every decision before it is applied."""

from __future__ import annotations

import time
from collections.abc import Callable, Generator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from poker_table.agents.base import Agent, Decision, SeatView, make_view
from poker_table.engine import Action, Hand, LegalActions, Player, Street


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One decision as requested by the agent and as actually applied."""

    seat: int
    street: Street
    requested: Action
    applied: Action
    illegal: bool
    reasoning: str
    table_talk: str
    latency_ms: float
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlayedHand:
    hand: Hand
    decisions: list[DecisionRecord] = field(default_factory=list)


def fallback_action(legal: LegalActions) -> Action:
    """What the table does for a seat whose decision was illegal: check if possible, else fold."""
    return Action.check() if legal.can_check else Action.fold()


def sanitize(action: Action, legal: LegalActions) -> Action | None:
    """Return a legal action equivalent to ``action``, or None when nothing reasonable fits.

    Agents (LLMs especially) confuse bet/raise and drift off the min/max; those slips are
    mapped to the nearest legal action instead of being punished. Anything else is illegal.
    """
    match action.type.value:
        case "fold":
            return action
        case "check":
            return action if legal.can_check else None
        case "call":
            if legal.can_call:
                return action
            return Action.check() if legal.can_check else None
        case "bet" | "raise":
            if not legal.can_raise:
                return None
            amount = min(max(action.amount, legal.min_raise_to), legal.max_raise_to)
            return Action.bet(amount) if legal.is_bet else Action.raise_to(amount)
    return None


def play_hand(
    hand: Hand,
    agents: Mapping[int, Agent],
    *,
    on_view: Callable[[SeatView], None] | None = None,
    on_decision: Callable[[DecisionRecord, Hand], None] | None = None,
) -> PlayedHand:
    """Drive ``hand`` to completion, asking ``agents[seat]`` at every decision point.

    ``on_view`` is called just before a seat is asked (who is thinking), ``on_decision`` right
    after its decision has been applied — the hooks a live table streams from.
    """
    return answer_all(play_turns(hand, on_view=on_view, on_decision=on_decision), agents)


def play_turns(
    hand: Hand,
    *,
    on_view: Callable[[SeatView], None] | None = None,
    on_decision: Callable[[DecisionRecord, Hand], None] | None = None,
) -> Generator[SeatView, Decision, PlayedHand]:
    """:func:`play_hand` with the asking left to the caller: the hand is played up to each
    decision, the seat's view is yielded, and the :class:`Decision` sent back is checked and
    applied. Returns the played hand.

    This is the one loop every table plays. ``play_hand`` answers each view from the seats; the
    browser demo answers its player's views from the page, one at a time, because a page cannot
    wait inside a call for a click.
    """
    played = PlayedHand(hand)
    talk: list[tuple[str, str]] = []
    while not hand.finished:
        seat = hand.actor
        assert seat is not None
        view = make_view(hand, seat.index, talk=tuple(talk))
        if on_view is not None:
            on_view(view)
        started = time.perf_counter()
        decision = yield view
        latency = (time.perf_counter() - started) * 1000
        applied = sanitize(decision.action, view.legal)
        illegal = applied is None
        if applied is None:
            applied = fallback_action(view.legal)
        hand.apply(applied)  # raises IllegalAction only if sanitize() itself is wrong
        record = DecisionRecord(
            seat=seat.index,
            street=view.street,
            requested=decision.action,
            applied=applied,
            illegal=illegal,
            reasoning=decision.reasoning,
            table_talk=decision.table_talk,
            latency_ms=latency,
            meta=dict(decision.meta),
        )
        played.decisions.append(record)
        if decision.table_talk:
            talk.append((seat.name, decision.table_talk))
        if on_decision is not None:
            on_decision(record, hand)
    return played


def ask(agent: Agent, view: SeatView) -> Decision:
    """One seat's decision. An agent crash must not take the table down: it folds."""
    try:
        return agent.act(view)
    except Exception as exc:
        return Decision(Action.fold(), reasoning=f"agent error: {exc!r}")


def answer_all[T](
    turns: Generator[SeatView, Decision, T], agents: Mapping[int, Agent] | Sequence[Agent]
) -> T:
    """Play ``turns`` to the end, asking ``agents[view.seat]`` for every view; its result."""
    try:
        view = next(turns)
        while True:
            view = turns.send(ask(agents[view.seat], view))
    except StopIteration as done:
        return done.value


def new_hand(
    agents: Sequence[Agent],
    stacks: Sequence[int],
    *,
    button: int,
    small_blind: int,
    big_blind: int,
    seed: int,
    ante: int = 0,
    hand_id: str = "1",
) -> tuple[Hand, dict[int, Agent]]:
    """Seat ``agents`` (in order) with ``stacks`` and deal a new hand."""
    if len(agents) != len(stacks):
        raise ValueError("one stack per agent")
    players = [
        Player(a.name, s, kind=getattr(a, "kind", "")) for a, s in zip(agents, stacks, strict=True)
    ]
    hand = Hand(
        players,
        button=button,
        small_blind=small_blind,
        big_blind=big_blind,
        ante=ante,
        seed=seed,
        hand_id=hand_id,
    )
    return hand, dict(enumerate(agents))
