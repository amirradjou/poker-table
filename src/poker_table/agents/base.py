"""The agent protocol and the view of the table a seat is allowed to see."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from poker_table.cards import Card, cards_str
from poker_table.engine import Action, Event, EventKind, Hand, LegalActions, Street

_MIDDLE_NAMES = {
    0: [],
    1: ["UTG"],
    2: ["UTG", "CO"],
    3: ["UTG", "HJ", "CO"],
    4: ["UTG", "MP", "HJ", "CO"],
    5: ["UTG", "UTG+1", "MP", "HJ", "CO"],
    6: ["UTG", "UTG+1", "MP", "LJ", "HJ", "CO"],
    7: ["UTG", "UTG+1", "UTG+2", "MP", "LJ", "HJ", "CO"],
}


def position_name(seat: int, button: int, num_players: int) -> str:
    """Standard position label (BTN, SB, BB, UTG, ..., CO) for a seat."""
    offset = (seat - button) % num_players
    if num_players == 2:
        return "BTN/SB" if offset == 0 else "BB"
    if offset == 0:
        return "BTN"
    if offset == 1:
        return "SB"
    if offset == 2:
        return "BB"
    return _MIDDLE_NAMES[num_players - 3][offset - 3]


@dataclass(frozen=True, slots=True)
class PlayerView:
    seat: int
    name: str
    position: str
    stack: int
    street_bet: int
    folded: bool
    all_in: bool


@dataclass(frozen=True, slots=True)
class SeatView:
    """Everything one seat may know when it is their turn — never another seat's cards."""

    hand_id: str
    seat: int
    name: str
    position: str
    hole: tuple[Card, Card]
    board: tuple[Card, ...]
    street: Street
    pot: int
    stack: int
    street_bet: int
    current_bet: int
    small_blind: int
    big_blind: int
    ante: int
    button: int
    legal: LegalActions
    players: tuple[PlayerView, ...]
    events: tuple[Event, ...]
    talk: tuple[tuple[str, str], ...] = ()  # (seat name, what they said) so far this hand

    @property
    def to_call(self) -> int:
        return self.legal.call_amount

    @property
    def pot_odds(self) -> float:
        """Fraction of the final pot you must put in to call (0 when there is nothing to call)."""
        if self.to_call == 0:
            return 0.0
        return self.to_call / (self.pot + self.to_call)

    @property
    def active_players(self) -> int:
        return sum(1 for p in self.players if not p.folded)

    @property
    def street_actions(self) -> tuple[Event, ...]:
        return tuple(
            e for e in self.events if e.kind is EventKind.ACTION and e.street is self.street
        )

    def preflop_aggressor(self) -> int | None:
        """Seat of the last preflop raiser, if anyone raised."""
        raiser = None
        for e in self.events:
            if e.kind is EventKind.ACTION and e.street is Street.PREFLOP and e.action is not None:
                if e.action.type.value in ("bet", "raise"):
                    raiser = e.seat
        return raiser

    def describe(self) -> str:
        """A compact text rendering, used for logs and as the prompt body for LLM seats."""
        lines = [
            f"Hand {self.hand_id} · {self.street.value} · you are {self.name} ({self.position})",
            f"Your cards: {cards_str(self.hole)}",
            f"Board: {cards_str(self.board) or '(none)'}",
            f"Pot: {self.pot} · to call: {self.to_call} · your stack: {self.stack}",
            "Players:",
        ]
        for p in self.players:
            status = "folded" if p.folded else ("all-in" if p.all_in else f"stack {p.stack}")
            marker = " (you)" if p.seat == self.seat else ""
            lines.append(f"  {p.position:>6} {p.name}{marker}: {status}, in for {p.street_bet}")
        actions = [
            e
            for e in self.events
            if e.kind
            in (EventKind.ACTION, EventKind.POST_ANTE, EventKind.POST_BLIND, EventKind.STREET)
        ]
        if actions:
            lines.append("Action so far:")
            for e in actions:
                lines.append("  " + describe_event(e, self.players))
        if self.talk:
            lines.append("Table talk:")
            lines += [f'  {who}: "{text}"' for who, text in self.talk]
        lines.append(f"Legal: {self.legal.describe()}")
        return "\n".join(lines)


def describe_event(event: Event, players: tuple[PlayerView, ...]) -> str:
    name = players[event.seat].name if event.seat is not None else ""
    match event.kind:
        case EventKind.POST_ANTE:
            return f"{name} antes {event.amount}{' (all-in)' if event.all_in else ''}"
        case EventKind.POST_BLIND:
            return f"{name} posts {event.amount}{' (all-in)' if event.all_in else ''}"
        case EventKind.ACTION:
            suffix = " (all-in)" if event.all_in else ""
            return f"{name} {event.action}{suffix}"
        case EventKind.STREET:
            return f"--- {event.street.value}: {event.text}"
        case _:
            return f"{event.kind.value} {name} {event.text}".strip()


@dataclass(frozen=True, slots=True)
class Decision:
    """What an agent returns. ``reasoning`` is private; ``table_talk`` is shown to the table."""

    action: Action
    reasoning: str = ""
    table_talk: str = ""
    meta: dict[str, Any] = field(default_factory=dict)  # model, tokens, cost — for the record


class Agent(Protocol):
    name: str

    def act(self, view: SeatView) -> Decision: ...


def make_view(hand: Hand, seat_index: int, *, talk: tuple[tuple[str, str], ...] = ()) -> SeatView:
    """Build the view for ``seat_index``; hole-card events of other seats are dropped."""
    seat = hand.seats[seat_index]
    if seat.hole is None:
        raise ValueError("seat has no cards")
    n = hand.num_players
    players = tuple(
        PlayerView(
            seat=s.index,
            name=s.name,
            position=position_name(s.index, hand.button, n),
            stack=s.stack,
            street_bet=s.street_bet,
            folded=s.folded,
            all_in=s.all_in,
        )
        for s in hand.seats
    )
    events = tuple(
        e
        for e in hand.events
        if e.kind not in (EventKind.DEAL_HOLE, EventKind.SHOWDOWN) or e.seat == seat_index
    )
    return SeatView(
        hand_id=hand.hand_id,
        seat=seat_index,
        name=seat.name,
        position=position_name(seat_index, hand.button, n),
        hole=seat.hole,
        board=tuple(hand.board),
        street=hand.street,
        pot=hand.pot,
        stack=seat.stack,
        street_bet=seat.street_bet,
        current_bet=hand.current_bet,
        small_blind=hand.small_blind,
        big_blind=hand.big_blind,
        ante=hand.ante,
        button=hand.button,
        legal=hand.legal_actions() if hand.actor is seat else _no_actions(hand),
        players=players,
        events=events,
        talk=talk,
    )


def _no_actions(hand: Hand) -> LegalActions:
    return LegalActions(
        can_check=False, call_amount=0, min_raise_to=0, max_raise_to=0, current_bet=hand.current_bet
    )
