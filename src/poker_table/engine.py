"""The no-limit hold'em hand: seats, blinds, betting rounds, pots and showdown.

A :class:`Hand` is a state machine driven from the outside: whoever runs the table asks
``hand.actor`` who is next, ``hand.legal_actions()`` what they may do, and feeds the
decision back through ``hand.apply(action)`` until ``hand.finished``. Everything that
happens is appended to ``hand.events`` so a hand history can be rebuilt afterwards.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from poker_table.cards import Card, Deck


class ActionType(StrEnum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"


@dataclass(frozen=True, slots=True)
class Action:
    """A seat's decision. ``amount`` is the *total* street bet for bet/raise ("raise to")."""

    type: ActionType
    amount: int = 0

    @classmethod
    def fold(cls) -> Action:
        return cls(ActionType.FOLD)

    @classmethod
    def check(cls) -> Action:
        return cls(ActionType.CHECK)

    @classmethod
    def call(cls) -> Action:
        return cls(ActionType.CALL)

    @classmethod
    def bet(cls, amount: int) -> Action:
        return cls(ActionType.BET, amount)

    @classmethod
    def raise_to(cls, amount: int) -> Action:
        return cls(ActionType.RAISE, amount)

    def __str__(self) -> str:
        if self.type in (ActionType.BET, ActionType.RAISE):
            return f"{self.type.value} {self.amount}"
        return self.type.value


class Street(StrEnum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


BOARD_CARDS = {Street.FLOP: 3, Street.TURN: 4, Street.RIVER: 5}


class IllegalAction(ValueError):
    """Raised when an action is not among the legal ones for the acting seat."""


@dataclass(slots=True)
class Seat:
    index: int
    name: str
    stack: int
    hole: tuple[Card, Card] | None = None
    folded: bool = False
    street_bet: int = 0
    total_bet: int = 0
    acted: bool = False

    @property
    def in_hand(self) -> bool:
        return not self.folded

    @property
    def all_in(self) -> bool:
        return self.in_hand and self.stack == 0

    @property
    def can_act(self) -> bool:
        return self.in_hand and self.stack > 0

    def put_in(self, amount: int) -> int:
        """Move up to ``amount`` chips from the stack into the pot; returns what moved."""
        amount = min(amount, self.stack)
        self.stack -= amount
        self.street_bet += amount
        self.total_bet += amount
        return amount


@dataclass(frozen=True, slots=True)
class LegalActions:
    """What the acting seat may do right now. Amounts are street totals ("raise to")."""

    can_check: bool
    call_amount: int
    min_raise_to: int
    max_raise_to: int
    current_bet: int

    @property
    def can_call(self) -> bool:
        return self.call_amount > 0

    @property
    def can_raise(self) -> bool:
        return self.max_raise_to > 0

    @property
    def is_bet(self) -> bool:
        """True when a raise would be an opening bet (nobody has bet on this street)."""
        return self.current_bet == 0

    def describe(self) -> str:
        parts = ["fold"]
        if self.can_check:
            parts.append("check")
        if self.can_call:
            parts.append(f"call {self.call_amount}")
        if self.can_raise:
            verb = "bet" if self.is_bet else "raise to"
            parts.append(f"{verb} {self.min_raise_to}..{self.max_raise_to}")
        return ", ".join(parts)


class EventKind(StrEnum):
    HAND_START = "hand_start"
    POST_BLIND = "post_blind"
    DEAL_HOLE = "deal_hole"
    ACTION = "action"
    STREET = "street"
    RETURN_UNCALLED = "return_uncalled"
    SHOWDOWN = "showdown"
    WIN = "win"
    HAND_END = "hand_end"


@dataclass(frozen=True, slots=True)
class Event:
    kind: EventKind
    street: Street
    seat: int | None = None
    action: Action | None = None
    amount: int = 0
    cards: tuple[Card, ...] = ()
    text: str = ""
    all_in: bool = False


@dataclass(frozen=True, slots=True)
class Player:
    """Who sits down for a hand and with how much."""

    name: str
    stack: int


class Hand:
    """One hand of no-limit hold'em from the blinds to the payout."""

    def __init__(
        self,
        players: Sequence[Player],
        *,
        button: int,
        small_blind: int,
        big_blind: int,
        seed: int,
        hand_id: str = "1",
    ) -> None:
        if len(players) < 2:
            raise ValueError("a hand needs at least two players")
        if len(players) > 10:
            raise ValueError("at most ten players")
        if any(p.stack <= 0 for p in players):
            raise ValueError("every player needs chips to be dealt in")
        if not 0 < small_blind <= big_blind:
            raise ValueError("blinds must satisfy 0 < small_blind <= big_blind")
        names = [p.name for p in players]
        if len(set(names)) != len(names):
            raise ValueError("player names must be unique")

        self.hand_id = hand_id
        self.seed = seed
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.button = button % len(players)
        self.seats = [Seat(i, p.name, p.stack) for i, p in enumerate(players)]
        self.starting_stacks = {s.index: s.stack for s in self.seats}
        self.deck = Deck(seed)
        self.board: list[Card] = []
        self.street = Street.PREFLOP
        self.events: list[Event] = []
        self.finished = False
        self.payouts: dict[int, int] = {}
        self.current_bet = 0
        self.last_raise_size = big_blind
        self._actor: int | None = None

        self._log(EventKind.HAND_START, text=f"button seat {self.button}")
        self._post_blinds()
        self._deal_hole_cards()
        self._actor = self._first_to_act(preflop=True)

    # ----- setup ---------------------------------------------------------------------------

    @property
    def num_players(self) -> int:
        return len(self.seats)

    def _next_index(self, index: int) -> int:
        return (index + 1) % self.num_players

    @property
    def sb_index(self) -> int:
        # Heads-up the button posts the small blind.
        return self.button if self.num_players == 2 else self._next_index(self.button)

    @property
    def bb_index(self) -> int:
        return self._next_index(self.sb_index)

    def _post_blinds(self) -> None:
        for index, blind in ((self.sb_index, self.small_blind), (self.bb_index, self.big_blind)):
            seat = self.seats[index]
            posted = seat.put_in(blind)
            self._log(EventKind.POST_BLIND, seat=index, amount=posted, all_in=seat.all_in)
        self.current_bet = self.big_blind

    def _deal_hole_cards(self) -> None:
        # Deal one card at a time starting left of the button, like a real dealer would.
        order = [self._next_index(self.button + i) for i in range(self.num_players)]
        first = {i: self.deck.draw() for i in order}
        second = {i: self.deck.draw() for i in order}
        for i in order:
            self.seats[i].hole = (first[i], second[i])
            self._log(EventKind.DEAL_HOLE, seat=i, cards=self.seats[i].hole)

    def _first_to_act(self, *, preflop: bool) -> int | None:
        start = self.bb_index if preflop else self.button
        index = start
        for _ in range(self.num_players):
            index = self._next_index(index)
            if self.seats[index].can_act:
                return index
        return None

    # ----- queries -------------------------------------------------------------------------

    @property
    def pot(self) -> int:
        return sum(s.total_bet for s in self.seats)

    @property
    def actor(self) -> Seat | None:
        return None if self._actor is None or self.finished else self.seats[self._actor]

    def legal_actions(self) -> LegalActions:
        seat = self.actor
        if seat is None:
            raise IllegalAction("no seat is due to act")
        to_call = min(self.current_bet - seat.street_bet, seat.stack)
        can_check = to_call == 0
        # A seat that already acted this street only gets to act again because of a raise;
        # if that raise was an incomplete all-in it does not reopen the betting for them.
        can_raise = seat.stack > to_call and not seat.acted
        if can_raise:
            max_to = seat.street_bet + seat.stack
            min_to = min(self.current_bet + self.last_raise_size, max_to)
            if self.current_bet == 0:
                min_to = min(self.big_blind, max_to)
        else:
            min_to = max_to = 0
        return LegalActions(
            can_check=can_check,
            call_amount=to_call,
            min_raise_to=min_to,
            max_raise_to=max_to,
            current_bet=self.current_bet,
        )

    # ----- helpers -------------------------------------------------------------------------

    def _log(self, kind: EventKind, **fields: object) -> None:
        self.events.append(Event(kind, self.street, **fields))  # type: ignore[arg-type]
