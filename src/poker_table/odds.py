"""A probability calculator for one spot: how often you win, and what the price asks for.

Two questions, answered from the same place:

* **Without the other hands** — the ordinary case at the table, and the only one a seat may
  ask while a hand is live: your two cards against *n* unknown hands. Sampled (Monte Carlo),
  except on the river against one opponent, where every hand they could hold is enumerated.
* **With the other hands** — a replay, a review, or a calculator you are typing into: your
  cards against theirs. Every remaining board run-out is enumerated when there are few enough
  of them, so the answer is exact rather than sampled.

The arithmetic around it is standard and worth writing down, since the whole point is that
these numbers can be checked:

* **Pot odds** — calling ``to_call`` into a pot of ``pot`` buys a final pot of
  ``pot + to_call``, so you are paying ``to_call / (pot + to_call)`` of it. That fraction is
  also the equity you need to break even, which is why one number carries both names here.
  Quoted the usual way round, the odds are ``pot : to_call``.
* **EV of calling** — ``equity * (pot + to_call) - to_call``, against 0 for folding. This is
  exact for a call that ends the betting (an all-in); on an earlier street it ignores what
  happens on the next one, which cuts both ways (implied odds, reverse implied odds).
* **Outs to a probability** — with ``k`` outs among ``n`` unseen cards, one card to come is
  ``k / n``; two cards to come is ``1 - C(n-k, 2) / C(n, 2)``, i.e. one minus the chance of
  missing twice. The familiar "rule of 2 and 4" (``2k%`` per card, ``4k%`` for two) is an
  approximation that drifts high above about eight outs; it is reported next to the real
  number rather than instead of it.
* **Ties** split the pot, so a tied run-out is worth ``1 / (players sharing it)`` of it, and
  that is what goes into equity — win frequency alone would overstate a chopped hand.
"""

from __future__ import annotations

import itertools
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from math import comb, sqrt

from poker_table.cards import FULL_DECK, Card, cards_str
from poker_table.evaluator import HandCategory, evaluate

Opponent = tuple[Card, Card] | None  # their two cards, or None for "unknown"

# Enumerating run-outs costs one evaluation per player per run-out; past this many the answer
# is sampled instead. The flop (990 run-outs) and turn (44) stay exact; preflop cannot.
EXACT_LIMIT = 60_000
DEFAULT_SAMPLES = 2000


@dataclass(frozen=True, slots=True)
class Chances:
    """How a spot finishes: frequencies, and the share of the pot they add up to."""

    win: float  # outright, no one else with the same hand
    tie: float  # shares the pot with at least one other
    lose: float
    equity: float  # average share of the pot, ties split by how many share it
    exact: bool  # every case counted, rather than sampled
    trials: int

    @property
    def error(self) -> float:
        """One standard error on the sampled equity; 0 when every case was counted."""
        if self.exact or self.trials <= 1:
            return 0.0
        return sqrt(max(self.equity * (1 - self.equity), 0.0) / self.trials)

    def how(self) -> str:
        if self.exact:
            return f"exact, {self.trials} run-outs"
        return f"{self.trials} samples, ±{self.error:.1%}"

    def describe(self) -> str:
        tie = f", tie {self.tie:.1%}" if self.tie >= 0.005 else ""
        return f"{self.equity:.1%} equity (win {self.win:.1%}{tie}; {self.how()})"


def unseen_cards(hole: Sequence[Card], board: Sequence[Card], known: Sequence[Card] = ()) -> list:
    """The deck minus every card this calculation can see."""
    dead = {*hole, *board, *known}
    return [c for c in FULL_DECK if c not in dead]


def chances(
    hole: Sequence[Card],
    board: Sequence[Card] = (),
    opponents: int | Sequence[Opponent] = 1,
    *,
    samples: int = DEFAULT_SAMPLES,
    seed: int = 0,
) -> Chances:
    """Win/tie/lose for ``hole`` against ``opponents``, which may be known, unknown or mixed.

    ``opponents`` is a count of unknown hands, or a sequence with an entry per opponent:
    their two cards, or None for one whose cards you cannot see.
    """
    hole = tuple(hole)
    board = tuple(board)
    if len(hole) != 2:
        raise ValueError("a hold'em hand is two cards")
    if len(board) > 5:
        raise ValueError("a board is at most five cards")
    seats: list[Opponent] = [None] * opponents if isinstance(opponents, int) else list(opponents)
    if not seats:
        raise ValueError("there is nobody to be ahead of")
    known = [c for seat in seats if seat is not None for c in seat]
    repeated = [*hole, *board, *known]
    if len(set(repeated)) != len(repeated):
        raise ValueError("the same card cannot be in two places")

    live = unseen_cards(hole, board, known)
    to_come = 5 - len(board)
    unknown = sum(1 for seat in seats if seat is None)
    if unknown == 0 and comb(len(live), to_come) <= EXACT_LIMIT:
        return _enumerate_runouts(hole, board, [s for s in seats if s is not None], live, to_come)
    if unknown == 1 and to_come == 0:
        return _enumerate_one_hand(hole, board, seats, live)
    return _sample(hole, board, seats, live, samples, seed)


def _finish(mine, others: Sequence, weight: float, tally: list[float]) -> None:
    """Score one finished board into [win, tie, lose, equity], weighted by ``weight``."""
    best = max(others)
    if mine > best:
        tally[0] += weight
        tally[3] += weight
    elif mine < best:
        tally[2] += weight
    else:
        sharing = 1 + sum(1 for other in others if other == mine)
        tally[1] += weight
        tally[3] += weight / sharing


def _enumerate_runouts(hole, board, seats, live, to_come) -> Chances:
    """Every remaining board, when all the other hands are known."""
    tally = [0.0, 0.0, 0.0, 0.0]
    total = 0
    for run_out in itertools.combinations(live, to_come):
        full = (*board, *run_out)
        mine = evaluate([*hole, *full])
        _finish(mine, [evaluate([*seat, *full]) for seat in seats], 1.0, tally)
        total += 1
    return _chances(tally, total, exact=True)


def _enumerate_one_hand(hole, board, seats, live) -> Chances:
    """A finished board and one unknown opponent: count every hand they could hold."""
    known = [seat for seat in seats if seat is not None]
    mine = evaluate([*hole, *board])
    others = [evaluate([*seat, *board]) for seat in known]
    tally = [0.0, 0.0, 0.0, 0.0]
    total = 0
    for a, b in itertools.combinations(live, 2):
        _finish(mine, [*others, evaluate([a, b, *board])], 1.0, tally)
        total += 1
    return _chances(tally, total, exact=True)


def _sample(hole, board, seats, live, samples: int, seed: int) -> Chances:
    rng = random.Random(seed)
    to_come = 5 - len(board)
    tally = [0.0, 0.0, 0.0, 0.0]
    for _ in range(samples):
        drawn = rng.sample(live, 2 * sum(1 for s in seats if s is None) + to_come)
        at = 0
        hands = []
        for seat in seats:
            if seat is None:
                hands.append((drawn[at], drawn[at + 1]))
                at += 2
            else:
                hands.append(seat)
        full = (*board, *drawn[at:])
        mine = evaluate([*hole, *full])
        _finish(mine, [evaluate([*hand, *full]) for hand in hands], 1.0, tally)
    return _chances(tally, samples, exact=False)


def _chances(tally: list[float], total: int, *, exact: bool) -> Chances:
    if total == 0:
        raise ValueError("nothing to count")
    win, tie, lose, equity = (value / total for value in tally)
    return Chances(win=win, tie=tie, lose=lose, equity=equity, exact=exact, trials=total)


# ----- outs -------------------------------------------------------------------------------


def hit_probability(outs: int, unseen: int, cards_to_come: int) -> float:
    """Chance at least one of ``outs`` cards arrives in the next ``cards_to_come``."""
    if outs <= 0 or cards_to_come <= 0 or unseen <= 0:
        return 0.0
    missing = comb(max(unseen - outs, 0), cards_to_come)
    return 1.0 - missing / comb(unseen, cards_to_come)


def rule_of_two_and_four(outs: int, cards_to_come: int) -> float:
    """The table estimate: 2% an out per card to come, doubled for two cards."""
    return min(1.0, outs * (0.04 if cards_to_come >= 2 else 0.02))


def winning_outs(
    hole: Sequence[Card], board: Sequence[Card], opponents: Sequence[tuple[Card, Card]]
) -> tuple[Card, ...]:
    """Next cards that would leave you with the best hand — needs the other hands.

    Counted the way a player counts at the table: cards that put you ahead on the very next
    card. A hand that is already ahead has no outs by this definition; it has the pot.
    """
    hole, board = tuple(hole), tuple(board)
    if len(board) >= 5 or not opponents:
        return ()
    if len(board) >= 3 and all(
        evaluate([*hole, *board]) > evaluate([*seat, *board]) for seat in opponents
    ):
        return ()  # already the best hand: nothing to draw to
    live = unseen_cards(hole, board, [c for seat in opponents for c in seat])
    found = []
    for card in live:
        full = (*board, card)
        mine = evaluate([*hole, *full])
        if all(mine > evaluate([*seat, *full]) for seat in opponents):
            found.append(card)
    return tuple(found)


def board_category(cards: Sequence[Card]) -> HandCategory:
    """The best kind of hand the board makes on its own, with no help from anybody's cards."""
    cards = tuple(cards)
    if len(cards) >= 5:
        return evaluate(cards).category
    counts = sorted(Counter(c.rank for c in cards).values(), reverse=True)
    top = counts[0] if counts else 1
    if top >= 4:
        return HandCategory.FOUR_OF_A_KIND
    if top == 3:
        return HandCategory.THREE_OF_A_KIND
    if top == 2:
        return HandCategory.TWO_PAIR if counts[1:2] == [2] else HandCategory.PAIR
    return HandCategory.HIGH_CARD  # four cards cannot make a straight or a flush


def improving_cards(hole: Sequence[Card], board: Sequence[Card]) -> tuple[Card, ...]:
    """Next cards that raise your hand to a better *kind* of hand — no opponents needed.

    This is what a player counts on a draw with nobody's cards to look at: the flush card,
    the straight card, the card that pairs you. A card has to beat the board as well as your
    own hand, so one that only pairs the board does not count — it hands everybody the same
    pair. Two overcards therefore come to 6 and a flush draw with two overcards to 15, the
    way they are counted at the table.

    It says nothing about whether the improvement is enough to win, which is the honest limit
    of counting outs blind; that is what the equity is for.
    """
    hole, board = tuple(hole), tuple(board)
    if len(board) < 3 or len(board) >= 5:
        return ()  # preflop everything "improves"; on the river nothing is to come
    now = evaluate([*hole, *board]).category
    better = []
    for card in unseen_cards(hole, board):
        after = evaluate([*hole, *board, card]).category
        if after > now and after > board_category([*board, card]):
            better.append(card)
    return tuple(better)


# ----- the whole spot ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Spot:
    """A calculated spot: the chances, the price, and what the two say together."""

    chances: Chances
    pot: int
    to_call: int
    opponents: int
    cards_to_come: int
    unseen: int
    outs: tuple[Card, ...] | None  # None when the other hands are unknown
    improving: tuple[Card, ...]
    known_hands: bool

    @property
    def equity(self) -> float:
        return self.chances.equity

    @property
    def pot_odds(self) -> float:
        """The share of the final pot you are paying — and so the equity you need."""
        return 0.0 if self.to_call <= 0 else self.to_call / (self.pot + self.to_call)

    @property
    def required_equity(self) -> float:
        return self.pot_odds

    @property
    def ratio(self) -> str:
        """Pot odds the way they are spoken: 3.2:1."""
        return "-" if self.to_call <= 0 else f"{self.pot / self.to_call:.1f}:1"

    @property
    def ev_call(self) -> float:
        """Chips won or lost by calling instead of folding, if the betting ends here."""
        return self.equity * (self.pot + self.to_call) - self.to_call

    @property
    def verdict(self) -> str:
        if self.to_call <= 0:
            return "nothing to call"
        margin = self.equity - self.required_equity
        if abs(margin) < 0.01:
            return "the price is what the hand is worth"
        side = "more" if margin > 0 else "less"
        return f"{abs(margin):.1%} {side} equity than the price asks"

    def hit(self) -> float | None:
        """Chance an out arrives by the river, or None when the outs are unknown."""
        if self.outs is None:
            return None
        return hit_probability(len(self.outs), self.unseen, self.cards_to_come)

    def summary(self) -> str:
        """One line, for a prompt or a caption."""
        against = (
            f"{self.opponents} known hand{'s' if self.opponents != 1 else ''}"
            if self.known_hands
            else f"{self.opponents} unknown hand{'s' if self.opponents != 1 else ''}"
        )
        text = f"{self.equity:.0%} equity against {against}"
        if self.to_call > 0:
            text += (
                f"; pot odds {self.ratio}, so calling needs {self.required_equity:.0%}"
                f" (EV {self.ev_call:+.1f})"
            )
        return text

    def lines(self) -> list[str]:
        """The full calculation, a line at a time."""
        c = self.chances
        out = [
            f"Win {c.win:.1%} · tie {c.tie:.1%} · lose {c.lose:.1%}",
            f"Equity {c.equity:.1%} ({c.how()})",
        ]
        if self.outs is not None and self.cards_to_come:
            hit = self.hit() or 0.0
            rough = rule_of_two_and_four(len(self.outs), self.cards_to_come)
            cards = cards_str(self.outs) or "none"
            out.append(
                f"Outs to the best hand: {len(self.outs)} ({cards}) — "
                f"{hit:.1%} by the river, {rough:.0%} by the rule of 2 and 4"
            )
        elif self.improving and self.cards_to_come and len(self.improving) < self.unseen:
            hit = hit_probability(len(self.improving), self.unseen, self.cards_to_come)
            out.append(
                f"Cards that better your hand: {len(self.improving)} — {hit:.1%} by the river "
                "(whether that wins depends on what they hold)"
            )
        if self.to_call > 0:
            out.append(
                f"Pot {self.pot}, to call {self.to_call}: pot odds {self.ratio}, "
                f"break-even equity {self.required_equity:.1%}"
            )
            out.append(f"Calling is {self.ev_call:+.1f} chips against folding — {self.verdict}")
        return out


def calculate(
    hole: Sequence[Card],
    board: Sequence[Card] = (),
    opponents: int | Sequence[Opponent] = 1,
    *,
    pot: int = 0,
    to_call: int = 0,
    samples: int = DEFAULT_SAMPLES,
    seed: int = 0,
) -> Spot:
    """The whole calculation for one spot, with or without the other hands."""
    hole, board = tuple(hole), tuple(board)
    seats: list[Opponent] = [None] * opponents if isinstance(opponents, int) else list(opponents)
    known = [seat for seat in seats if seat is not None]
    all_known = bool(seats) and len(known) == len(seats)
    result = chances(hole, board, seats, samples=samples, seed=seed)
    live = unseen_cards(hole, board, [c for seat in known for c in seat])
    return Spot(
        chances=result,
        pot=pot,
        to_call=to_call,
        opponents=len(seats),
        cards_to_come=5 - len(board),
        unseen=len(live),
        outs=winning_outs(hole, board, known) if all_known else None,
        improving=improving_cards(hole, board),
        known_hands=all_known,
    )


def for_view(view, *, samples: int = 1200, seed: int = 0) -> Spot:
    """The calculation a seat is allowed to make: its own cards against unknown hands.

    A ``SeatView`` never carries another seat's cards, so this is always the blind version —
    which is the point. The other one is for a replay, or for a calculator you type into.
    """
    opponents = sum(1 for p in view.players if p.seat != view.seat and not p.folded)
    return calculate(
        view.hole,
        view.board,
        max(1, opponents),
        pot=view.pot,
        to_call=view.to_call,
        samples=samples,
        seed=seed,
    )
