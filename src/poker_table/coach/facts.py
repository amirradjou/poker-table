"""Tag every decision of one player with a fact the math supports.

A hand history is replayed through the engine (stacked deck + recorded actions) so the
pot, the price and the legal actions at each decision are exact. Preflop decisions are
judged against the reference charts; postflop ones against equity versus the opponents'
preflop ranges and the pot odds offered.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from typing import Any

from poker_table.agents.base import SeatView, make_view
from poker_table.cards import Card, Deck
from poker_table.coach.equity import board_texture, equity, pot_odds
from poker_table.coach.ranges import (
    call_range,
    defend_range,
    expand,
    hand_class,
    open_range,
    three_bet_range,
)
from poker_table.engine import Action, ActionType, Hand, Player, Street
from poker_table.history import HandHistory, parse_action

ANY = expand("*")
VS_THREE_BET = expand("88+, ATs+, KQs, AJo+, KQo")  # continue after opening and getting 3-bet
COLD_VS_THREE_BET = expand("QQ+, AKs, AKo")  # continue facing a 3-bet you did not open into
CALL_MARGIN = 0.03  # equity may sit this far under the price (implied odds) before we complain
FOLD_MARGIN = 0.05  # folding with more equity than the price plus this is giving up too much
STRONG = 0.80

TITLES = {
    "limp": "Open-limping instead of raising or folding",
    "open": "Opening hands outside the chart",
    "fold_first_in": "Folding hands the chart opens",
    "call_open": "Calling a raise with hands outside the range",
    "three_bet": "3-betting hands outside the chart",
    "fold_vs_open": "Folding hands that should continue against a raise",
    "vs_three_bet": "Wrong decision facing a 3-bet",
    "call_vs_bet": "Calling without the right price",
    "fold_vs_bet": "Folding with the right price",
    "check_strong_river": "Missing value on the river",
    "cbet": "Continuation bets",
    "bet": "Bets when checked to",
    "raise_vs_bet": "Raises facing a bet",
    "check": "Checks",
    "check_option": "Big-blind option checks",
}


@dataclass(frozen=True, slots=True)
class Fact:
    hand_id: str
    seat: int
    street: str
    position: str
    tag: str
    ok: bool | None  # False = a leak by the chart or the math; True = fine; None = observation
    action: str
    hand: str
    detail: str
    equity: float | None = None
    price: float | None = None
    texture: str = ""
    pot: int = 0
    to_call: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ----- replaying a history through the engine ---------------------------------------------


def replay(history: HandHistory) -> Iterator[tuple[SeatView, Action]]:
    """Yield (view, action) for every decision in the hand, in order, on a real Hand."""
    n = len(history.players)
    order = [(history.button + 1 + i) % n for i in range(n)]
    holes = {p.seat: [Card.parse(c) for c in p.hole] for p in history.players}
    stacked = [holes[i][0] for i in order] + [holes[i][1] for i in order]
    stacked += [Card.parse(c) for c in history.board]
    hand = Hand(
        [Player(p.name, p.stack) for p in history.players],
        button=history.button,
        small_blind=history.small_blind,
        big_blind=history.big_blind,
        seed=history.seed,
        hand_id=history.hand_id,
        deck=Deck.stacked(stacked),
    )
    for e in history.actions():
        actor = hand.actor
        if actor is None or actor.index != e.seat or e.action is None:
            raise ValueError(f"hand {history.hand_id}: history does not replay ({e})")
        action = parse_action(e.action)
        yield make_view(hand, actor.index), action
        hand.apply(action)


# ----- tagging ----------------------------------------------------------------------------


class _PreflopLine:
    """Who raised, who called, so each opponent gets a range for the equity estimate."""

    def __init__(self) -> None:
        self.raises = 0
        self.opener: int | None = None
        self.ranges: dict[int, frozenset[str]] = {}

    def note(self, view: SeatView, action: Action) -> None:
        seat, position = view.seat, view.position
        if action.type in (ActionType.BET, ActionType.RAISE):
            if self.raises == 0:
                self.opener = seat
                self.ranges[seat] = open_range(position)
            else:
                self.ranges[seat] = three_bet_range()
            self.raises += 1
        elif action.type is ActionType.CALL:
            self.ranges[seat] = call_range(position) if self.raises else ANY
        elif action.type is ActionType.CHECK:
            self.ranges.setdefault(seat, ANY)


def tag_hand(history: HandHistory, player: str, *, samples: int = 300) -> list[Fact]:
    seat_of = {p.name: p.seat for p in history.players}
    if player not in seat_of:
        return []
    me = seat_of[player]
    line = _PreflopLine()
    facts: list[Fact] = []
    cbet_done = False
    for view, action in replay(history):
        if view.street is Street.PREFLOP:
            if view.seat == me:
                facts.append(_preflop_fact(view, action, line))
            line.note(view, action)
            continue
        if view.seat != me:
            continue
        fact, cbet_done = _postflop_fact(view, action, line, cbet_done, samples)
        facts.append(fact)
    return facts


def tag_hands(histories: Iterable[HandHistory], player: str, *, samples: int = 300) -> list[Fact]:
    facts: list[Fact] = []
    for history in histories:
        facts.extend(tag_hand(history, player, samples=samples))
    return facts


def _base(view: SeatView, action: Action, tag: str, ok: bool | None, detail: str, **kw) -> Fact:
    return Fact(
        hand_id=view.hand_id,
        seat=view.seat,
        street=view.street.value,
        position=view.position,
        tag=tag,
        ok=ok,
        action=str(action),
        hand=hand_class(view.hole),
        detail=detail,
        pot=view.pot,
        to_call=view.to_call,
        **kw,
    )


def _preflop_fact(view: SeatView, action: Action, line: _PreflopLine) -> Fact:
    cls = hand_class(view.hole)
    pos = view.position
    kind = action.type
    aggressive = kind in (ActionType.BET, ActionType.RAISE)
    opener_pos = next((p.position for p in view.players if p.seat == line.opener), "?")

    if line.raises == 0:
        if kind is ActionType.CHECK:
            return _base(view, action, "check_option", None, f"{cls} checks the option")
        if kind is ActionType.CALL:
            return _base(view, action, "limp", False, f"{cls} limps from {pos}")
        in_chart = cls in open_range(pos)
        if aggressive:
            why = "in" if in_chart else "outside"
            return _base(view, action, "open", in_chart, f"{cls} opens from {pos}, {why} the chart")
        return _base(
            view,
            action,
            "fold_first_in",
            not in_chart,
            f"{cls} folds from {pos}" + (", a hand the chart opens" if in_chart else ""),
        )

    if line.raises == 1 and view.seat != line.opener:
        if kind is ActionType.CALL:
            ok = cls in defend_range(pos)
            return _base(
                view,
                action,
                "call_open",
                ok,
                f"{cls} calls a {opener_pos} open from {pos}"
                + ("" if ok else ", outside the calling range"),
            )
        if aggressive:
            ok = cls in three_bet_range()
            return _base(
                view,
                action,
                "three_bet",
                ok,
                f"{cls} 3-bets a {opener_pos} open from {pos}"
                + ("" if ok else ", outside the 3-bet chart"),
            )
        ok = cls not in defend_range(pos)
        return _base(
            view,
            action,
            "fold_vs_open",
            ok,
            f"{cls} folds to a {opener_pos} open from {pos}"
            + ("" if ok else ", a hand that should continue"),
        )

    # Facing a 3-bet (or more)
    keep = VS_THREE_BET if view.seat == line.opener else COLD_VS_THREE_BET
    should_continue = cls in keep
    continued = kind is not ActionType.FOLD
    ok = continued == should_continue
    verb = "continues" if continued else "folds"
    return _base(
        view,
        action,
        "vs_three_bet",
        ok,
        f"{cls} {verb} facing a 3-bet from {pos}"
        + ("" if ok else (", too loose" if continued else ", too tight")),
    )


def _opponent_ranges(view: SeatView, line: _PreflopLine) -> list[frozenset[str]]:
    return [
        line.ranges.get(p.seat, ANY) for p in view.players if not p.folded and p.seat != view.seat
    ]


def _postflop_fact(
    view: SeatView, action: Action, line: _PreflopLine, cbet_done: bool, samples: int
) -> tuple[Fact, bool]:
    kind = action.type
    board = list(view.board)
    texture = board_texture(board)
    ranges = _opponent_ranges(view, line)
    eq = equity(view.hole, board, ranges, samples=samples)
    pct = f"{eq:.0%} equity"
    common = {"equity": round(eq, 3), "texture": texture}

    if view.to_call > 0:
        price = pot_odds(view.to_call, view.pot)
        odds = f"{(view.pot / view.to_call):.1f}:1"
        common["price"] = round(price, 3)
        if kind is ActionType.CALL:
            ok = eq >= price - CALL_MARGIN
            return _base(
                view,
                action,
                "call_vs_bet",
                ok,
                f"calls {view.to_call} into {view.pot} ({odds}) with {pct}"
                + ("" if ok else ", not enough"),
                **common,
            ), cbet_done
        if kind is ActionType.FOLD:
            ok = eq <= price + FOLD_MARGIN
            return _base(
                view,
                action,
                "fold_vs_bet",
                ok,
                f"folds getting {odds} with {pct}" + ("" if ok else ", a profitable call"),
                **common,
            ), cbet_done
        flavour = "bluff-raise" if eq < 0.3 else "value-raise" if eq > 0.6 else "raise"
        return _base(
            view, action, "raise_vs_bet", None, f"{flavour} with {pct}", **common
        ), cbet_done

    if kind in (ActionType.BET, ActionType.RAISE):
        flavour = "bluff" if eq < 0.3 else "value" if eq > 0.6 else "thin"
        if view.street is Street.FLOP and line.opener == view.seat and not cbet_done:
            return _base(
                view, action, "cbet", None, f"c-bets a {texture} flop ({flavour}, {pct})", **common
            ), True
        return _base(
            view,
            action,
            "bet",
            None,
            f"{flavour} bet on the {view.street.value} with {pct}",
            **common,
        ), cbet_done

    # a check
    if view.street is Street.FLOP and line.opener == view.seat and not cbet_done:
        return _base(
            view,
            action,
            "cbet",
            None,
            f"checks instead of c-betting a {texture} flop ({pct})",
            **common,
        ), True
    if view.street is Street.RIVER and eq >= STRONG:
        return _base(
            view, action, "check_strong_river", False, f"checks the river with {pct}", **common
        ), cbet_done
    return _base(view, action, "check", None, f"checks with {pct}", **common), cbet_done
