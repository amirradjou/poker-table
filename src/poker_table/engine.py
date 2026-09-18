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

from poker_table.cards import Card, Deck, cards_str
from poker_table.evaluator import HandRank, evaluate
from poker_table.pots import Pot, build_pots, split_amount, uncalled_amount


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
        deck: Deck | None = None,
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
        self.deck = deck if deck is not None else Deck(seed)
        self.board: list[Card] = []
        self.street = Street.PREFLOP
        self.events: list[Event] = []
        self.finished = False
        self.payouts: dict[int, int] = {}
        self.pots: list[Pot] = []
        self.showdown_ranks: dict[int, HandRank] = {}
        self.current_bet = 0
        self.last_raise_size = big_blind
        self._actor: int | None = None

        self._log(EventKind.HAND_START, text=f"button seat {self.button}")
        self._post_blinds()
        self._deal_hole_cards()
        self._begin_betting(after=self.bb_index)

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

    def _find_actor(self, after: int) -> int | None:
        """The next seat clockwise from ``after`` that still owes a decision, if any."""
        can_act = [s for s in self.seats if s.can_act]
        if len(can_act) == 1 and can_act[0].street_bet >= self.current_bet:
            return None  # nobody left to bet against
        index = after
        for _ in range(self.num_players):
            index = self._next_index(index)
            seat = self.seats[index]
            if seat.can_act and (not seat.acted or seat.street_bet < self.current_bet):
                return index
        return None

    # ----- queries -------------------------------------------------------------------------

    @property
    def pot(self) -> int:
        return sum(s.total_bet for s in self.seats)

    @property
    def in_hand(self) -> list[Seat]:
        return [s for s in self.seats if s.in_hand]

    def net(self) -> dict[int, int]:
        """Chips won (positive) or lost per seat index, valid once the hand is finished."""
        return {s.index: s.stack - self.starting_stacks[s.index] for s in self.seats}

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

    # ----- actions -------------------------------------------------------------------------

    def apply(self, action: Action) -> None:
        """Apply the acting seat's decision and move the hand forward."""
        seat = self.actor
        if seat is None:
            raise IllegalAction("the hand is over" if self.finished else "no seat is due to act")
        legal = self.legal_actions()
        self._validate(action, legal)

        paid = 0
        match action.type:
            case ActionType.FOLD:
                seat.folded = True
            case ActionType.CHECK:
                pass
            case ActionType.CALL:
                paid = seat.put_in(legal.call_amount)
            case ActionType.BET | ActionType.RAISE:
                raise_size = action.amount - self.current_bet
                paid = seat.put_in(action.amount - seat.street_bet)
                if raise_size >= self.last_raise_size:
                    # A full raise reopens the action for everyone else; an all-in for
                    # less does not (they may only call the extra or fold).
                    self.last_raise_size = raise_size
                    for other in self.seats:
                        other.acted = False
                self.current_bet = action.amount
        seat.acted = True
        self._log(EventKind.ACTION, seat=seat.index, action=action, amount=paid, all_in=seat.all_in)
        self._advance()

    def _validate(self, action: Action, legal: LegalActions) -> None:
        match action.type:
            case ActionType.FOLD:
                return
            case ActionType.CHECK:
                if not legal.can_check:
                    raise IllegalAction(f"cannot check facing a bet of {legal.current_bet}")
            case ActionType.CALL:
                if not legal.can_call:
                    raise IllegalAction("nothing to call")
            case ActionType.BET | ActionType.RAISE:
                if not legal.can_raise:
                    raise IllegalAction("raising is not allowed here")
                if action.type is ActionType.BET and not legal.is_bet:
                    raise IllegalAction("there is already a bet; use raise")
                if action.type is ActionType.RAISE and legal.is_bet:
                    raise IllegalAction("nothing to raise; use bet")
                if not legal.min_raise_to <= action.amount <= legal.max_raise_to:
                    raise IllegalAction(
                        f"amount {action.amount} outside {legal.min_raise_to}..{legal.max_raise_to}"
                    )

    # ----- flow ----------------------------------------------------------------------------

    def _begin_betting(self, after: int) -> None:
        self._actor = self._find_actor(after)
        if self._actor is None:
            self._end_street()

    def _advance(self) -> None:
        live = self.in_hand
        if len(live) == 1:
            self._finish_by_fold(live[0])
            return
        assert self._actor is not None
        self._actor = self._find_actor(self._actor)
        if self._actor is None:
            self._end_street()

    def _end_street(self) -> None:
        self._actor = None
        if self.street is Street.RIVER:
            self._showdown()
            return
        for seat in self.seats:
            seat.street_bet = 0
            seat.acted = False
        self.current_bet = 0
        self.last_raise_size = self.big_blind
        self.street = {
            Street.PREFLOP: Street.FLOP,
            Street.FLOP: Street.TURN,
            Street.TURN: Street.RIVER,
        }[self.street]
        dealt = self.deck.deal(BOARD_CARDS[self.street] - len(self.board))
        self.board.extend(dealt)
        self._log(EventKind.STREET, cards=tuple(dealt), text=cards_str(self.board))
        self._begin_betting(after=self.button)

    def _return_uncalled(self) -> None:
        returned = uncalled_amount({s.index: s.total_bet for s in self.seats})
        if returned is None:
            return
        index, amount = returned
        seat = self.seats[index]
        seat.stack += amount
        seat.total_bet -= amount
        seat.street_bet = max(0, seat.street_bet - amount)
        self._log(EventKind.RETURN_UNCALLED, seat=index, amount=amount)

    def _finish_by_fold(self, winner: Seat) -> None:
        self._actor = None
        self._return_uncalled()
        amount = self.pot
        self.pots = [Pot(amount, (winner.index,))]
        winner.stack += amount
        self.payouts = {winner.index: amount}
        self._log(EventKind.WIN, seat=winner.index, amount=amount, text="everyone else folded")
        self._finish()

    def _showdown(self) -> None:
        self.street = Street.SHOWDOWN
        self._return_uncalled()
        self.pots = build_pots(
            {s.index: s.total_bet for s in self.seats}, [s.index for s in self.in_hand]
        )
        for seat in self.in_hand:
            assert seat.hole is not None
            rank = evaluate([*self.board, *seat.hole])
            self.showdown_ranks[seat.index] = rank
            self._log(EventKind.SHOWDOWN, seat=seat.index, cards=seat.hole, text=rank.describe())

        payouts: dict[int, int] = {}
        for number, pot in enumerate(self.pots):
            best = max(self.showdown_ranks[i] for i in pot.eligible)
            winners = [i for i in pot.eligible if self.showdown_ranks[i] == best]
            label = "main pot" if number == 0 else f"side pot {number}"
            shares = split_amount(
                pot.amount, winners, self._next_index(self.button), self.num_players
            )
            for index, amount in shares.items():
                payouts[index] = payouts.get(index, 0) + amount
                self._log(
                    EventKind.WIN,
                    seat=index,
                    amount=amount,
                    text=f"{label} with {self.showdown_ranks[index].describe()}",
                )
        for index, amount in payouts.items():
            self.seats[index].stack += amount
        self.payouts = payouts
        self._finish()

    def _finish(self) -> None:
        self.finished = True
        self._actor = None
        self._log(EventKind.HAND_END)

    # ----- helpers -------------------------------------------------------------------------

    def _log(self, kind: EventKind, **fields: object) -> None:
        self.events.append(Event(kind, self.street, **fields))  # type: ignore[arg-type]
