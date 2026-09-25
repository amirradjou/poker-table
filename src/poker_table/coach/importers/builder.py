"""The part every site parser shares: turning a stream of table events into a HandHistory.

A parser recognises its site's lines and calls the builder; the builder keeps the seats,
tracks per-street and total contributions, validates that the blinds match the button (the
engine derives them, so the file must agree), returns uncalled bets, and produces the same
``HandHistory`` shape the league writes — rake-adjusted nets, showdown descriptions from the
evaluator, villains' cards when they were shown.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from poker_table.agents.base import position_name
from poker_table.cards import Card
from poker_table.engine import Action, Street
from poker_table.evaluator import evaluate
from poker_table.history import DecisionTrace, EventRecord, HandHistory, PlayerRecord
from poker_table.pots import uncalled_amount


class ImportError_(ValueError):
    """A hand (or file) that cannot be turned into a HandHistory."""


def money_scale(big_blind: str) -> int:
    """100 when the blinds are in dollars and cents, else 1 (tournament chips, play money)."""
    return 100 if "." in big_blind else 1


def amount(token: str | None, scale: int) -> int:
    if token is None:
        return 0
    return int((Decimal(token.replace(",", "").replace("$", "")) * scale).to_integral_value())


def cards(text: str) -> list[str]:
    """``"Ah Kd"`` or ``"Ah, Kd"`` -> canonical card strings."""
    return [str(Card.parse(c)) for c in text.replace(",", " ").split()]


class HandBuilder:
    """Accumulates one hand. Seats are given in site seat order with their site seat numbers."""

    def __init__(
        self,
        hand_id: str,
        *,
        small_blind: int,
        big_blind: int,
        seats: Sequence[tuple[int, str, int]],  # (site seat number, name, stack)
        button_seat: int,
        played_at: str = "",
    ) -> None:
        if len(seats) < 2:
            raise ImportError_("fewer than two players dealt in")
        index_of = {seat: i for i, (seat, _, _) in enumerate(seats)}
        if button_seat not in index_of:
            raise ImportError_("dead button")
        self.hand_id = hand_id
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.played_at = played_at
        self.names = [name for _, name, _ in seats]
        self.stacks = [stack for _, _, stack in seats]
        self.button = index_of[button_seat]
        self.n = len(seats)
        self._seat_of = {name: i for i, name in enumerate(self.names)}
        self.street = Street.PREFLOP
        self.street_bets = dict.fromkeys(range(self.n), 0)
        self.total_bets = dict.fromkeys(range(self.n), 0)
        self.stack_left = dict(enumerate(self.stacks))
        self.holes: dict[int, list[str]] = {i: [] for i in range(self.n)}
        self.board: list[str] = []
        self.payouts: dict[int, int] = {}
        self.folded: set[int] = set()
        self.events: list[EventRecord] = []
        self.posts: list[tuple[int, str]] = []
        self.antes: dict[int, int] = {}
        self._returned = False

    # -- who --

    def seat(self, name: str) -> int | None:
        return self._seat_of.get(name)

    def knows(self, name: str) -> bool:
        return name in self._seat_of

    # -- events --

    def _put(self, seat: int, chips: int) -> None:
        self.street_bets[seat] += chips
        self.total_bets[seat] += chips
        self.stack_left[seat] -= chips

    def post(self, name: str, kind: str, chips: int) -> None:
        """A blind or an ante, in the site's own wording ("the ante", "ante", "big blind")."""
        who = self.seat(name)
        if who is None:
            raise ImportError_("blind posted by an unknown player")
        if kind in ("the ante", "ante"):
            # Dead money: in the pot, but it buys no part of the blind, so it must not
            # land in street_bets or every later raise would be read short.
            self.antes[who] = self.antes.get(who, 0) + chips
            self.total_bets[who] += chips
            self.stack_left[who] -= chips
            self.events.append(
                EventRecord(
                    "post_ante", "preflop", who, None, chips, [], "", self.stack_left[who] <= 0
                )
            )
            return
        if kind not in ("small blind", "big blind"):
            raise ImportError_(f"unsupported post: {kind}")
        self.posts.append((who, kind))
        self._put(who, chips)
        self.events.append(
            EventRecord(
                "post_blind", "preflop", who, None, chips, [], "", self.stack_left[who] <= 0
            )
        )

    def _ante(self) -> int:
        """The one ante every seat posted, or a refusal when the file says otherwise.

        The engine posts the same ante for every seat, so a big blind ante (one seat pays
        for the table) or a dead ante cannot be replayed faithfully — better to say so than
        to import a hand whose chips do not add up. A seat all-in for part of its ante is
        fine: that is what the engine does too.
        """
        ante = max(self.antes.values(), default=0)
        for seat in range(self.n):
            posted = self.antes.get(seat, 0)
            if posted < ante and self.stack_left[seat] > 0:
                raise ImportError_(f"uneven antes: {self.names[seat]} posted {posted} of {ante}")
        return ante

    def deal(self, name: str, hole: list[str]) -> None:
        who = self.seat(name)
        if who is None:
            return
        self.holes[who] = hole
        self.events.append(EventRecord("deal_hole", "preflop", who, None, 0, hole, ""))

    def new_street(self, street: Street, new_cards: list[str]) -> None:
        self.street = street
        self.board += new_cards
        for seat in self.street_bets:
            self.street_bets[seat] = 0
        self.events.append(
            EventRecord("street", street.value, None, None, 0, new_cards, " ".join(self.board))
        )

    def action(
        self,
        name: str,
        verb: str,
        *,
        chips: int = 0,
        to: int | None = None,
        all_in: bool = False,
    ) -> None:
        """``verb`` in folds/checks/calls/bets/raises; ``chips`` is what the line says was put in
        (calls/bets), ``to`` the street total for a raise."""
        who = self.seat(name)
        if who is None:
            return
        if verb == "folds":
            action, paid = Action.fold(), 0
            self.folded.add(who)
        elif verb == "checks":
            action, paid = Action.check(), 0
        elif verb == "calls":
            action, paid = Action.call(), chips
        elif verb == "bets":
            action, paid = Action.bet(chips), chips
        elif verb == "raises":
            total = to if to is not None else self.street_bets[who] + chips
            action, paid = Action.raise_to(total), total - self.street_bets[who]
        else:
            raise ImportError_(f"unknown action {verb!r}")
        self._put(who, paid)
        self.events.append(
            EventRecord(
                "action",
                self.street.value,
                who,
                str(action),
                paid,
                [],
                "",
                all_in or self.stack_left[who] <= 0,
            )
        )

    def uncalled(self, name: str, chips: int) -> None:
        who = self.seat(name)
        if who is None:
            return
        self.street_bets[who] -= chips
        self.total_bets[who] -= chips
        self.stack_left[who] += chips
        self._returned = True
        self.events.append(EventRecord("return_uncalled", self.street.value, who, None, chips))

    def collect(self, name: str, chips: int, pot: str = "pot") -> None:
        who = self.seat(name)
        if who is None:
            return
        self.payouts[who] = self.payouts.get(who, 0) + chips
        self.stack_left[who] += chips
        self.events.append(EventRecord("win", self.street.value, who, None, chips, [], pot))

    def shows(self, name: str, hole: list[str]) -> None:
        who = self.seat(name)
        if who is not None:
            self.holes[who] = hole

    # -- the record --

    def finish(self, hero: str | None, *, infer_uncalled: bool = False) -> HandHistory:
        ante = self._ante()
        expected_sb = self.button if self.n == 2 else (self.button + 1) % self.n
        expected_bb = (expected_sb + 1) % self.n
        if self.posts != [(expected_sb, "small blind"), (expected_bb, "big blind")]:
            raise ImportError_("blinds do not match the button (missed or dead blinds)")
        if infer_uncalled and not self._returned:
            # Sites that never print "uncalled bet returned": the excess of the top
            # contributor over the next one never went into the pot.
            live = {s: c - self.antes.get(s, 0) for s, c in self.total_bets.items()}
            back = uncalled_amount(live)
            if back is not None:
                seat, chips = back
                # place the return before any win events so a replay reads naturally
                wins = [e for e in self.events if e.kind == "win"]
                self.events = [e for e in self.events if e.kind != "win"]
                self.total_bets[seat] -= chips
                self.stack_left[seat] += chips
                self.events.append(
                    EventRecord("return_uncalled", self.street.value, seat, None, chips)
                )
                self.events += wins

        showdown: dict[int, str] = {}
        if len(self.board) == 5:
            for seat, hole in self.holes.items():
                if len(hole) == 2 and seat not in self.folded:
                    rank = evaluate([Card.parse(c) for c in [*hole, *self.board]])
                    showdown[seat] = rank.describe()
                    self.events.append(
                        EventRecord("showdown", "showdown", seat, None, 0, hole, rank.describe())
                    )
        self.events.append(
            EventRecord("hand_end", self.street.value if len(self.board) < 5 else "showdown")
        )
        players = [
            PlayerRecord(
                seat=i,
                name=self.names[i],
                position=position_name(i, self.button, self.n),
                stack=self.stacks[i],
                hole=self.holes[i],
                net=self.payouts.get(i, 0) - self.total_bets[i],
            )
            for i in range(self.n)
        ]
        return HandHistory(
            hand_id=self.hand_id,
            seed=0,
            small_blind=self.small_blind,
            big_blind=self.big_blind,
            button=self.button,
            players=players,
            board=self.board,
            events=self.events,
            decisions=[
                DecisionTrace(
                    e.seat or 0, e.street, e.action or "", e.action or "", False, "", "", 0.0
                )
                for e in self.events
                if e.kind == "action"
            ],
            pots=[{"amount": sum(self.payouts.values()), "eligible": sorted(self.payouts)}],
            payouts=self.payouts,
            showdown=showdown,
            ante=ante,
            played_at=self.played_at,
            hero=hero or "",
        )
