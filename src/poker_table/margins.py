"""How sure can a leaderboard be? One calculation, used by the reports, the viewer and the CLI.

Chips say who won. They say much less about who plays better, because poker is noisy: a seat can
run hot for hundreds of hands. So every result here carries a 95% interval, and a hand with nobody
left to decide is counted at each seat's share of the pot, not at who the last cards favoured.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from poker_table.cards import parse_cards
from poker_table.history import HandHistory
from poker_table.odds import table_equities

_BOARD_AT = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
_ALL_IN_SAMPLES = 1000  # run-outs per hand: enough once they are added up over a run
# Below this a 95% interval is far too narrow. With no edge at all, a seat clears zero 30% of the
# time on 2 hands, 12% on 5 and 8% on 10, and only settles near 5% from about 30.
MIN_HANDS_TO_CALL = 30

CAVEAT = (
    "The interval treats hands as independent and looks at one seat at a time, so with many "
    "seats one can clear zero by luck."
)


def margin_of_error(big_blinds_per_hand: Sequence[float]) -> float | None:
    """Half the width of a 95% interval on bb/100, from one seat's result in each hand."""
    if len(big_blinds_per_hand) < 2:
        return None
    error = statistics.stdev(big_blinds_per_hand) / math.sqrt(len(big_blinds_per_hand))
    return 1.96 * error * 100


def _board_when_decided(history: HandHistory) -> int:
    """How many board cards were out at the last action in the hand."""
    actions = history.actions()
    return _BOARD_AT[actions[-1].street] if actions else 0


def all_in_early(history: HandHistory) -> bool:
    """True when a showdown still had cards to come and nobody had a decision left."""
    return len(history.showdown) >= 2 and _board_when_decided(history) < len(history.board)


def all_in_adjusted(history: HandHistory) -> dict[int, float] | None:
    """Chips per seat, counting each pot at its share of the cards rather than who won it.

    Once nobody has a decision left, the cards still to come are pure luck. Scoring the hand at
    each seat's share of the pot at that moment estimates the same win rate with less of that
    luck in it. Returns None for a hand with nothing to adjust, or one whose cards are not all
    known: a site's export only has the hands that were shown.
    """
    if not all_in_early(history) or not history.pots:
        return None
    seats = {p.seat: p for p in history.players}
    if any(len(seats[seat].hole) != 2 for pot in history.pots for seat in pot["eligible"]):
        return None
    board = parse_cards("".join(history.board[: _board_when_decided(history)]))
    # A site's rake leaves less to pay out than went in, and each share is of what was paid.
    paid = sum(history.payouts.values()) / sum(pot["amount"] for pot in history.pots)
    # net = payouts - chips put in, so this starts every seat at minus what it put in.
    worth = {seat: float(p.net - history.payouts.get(seat, 0)) for seat, p in seats.items()}
    for pot in history.pots:
        holes = {seat: parse_cards("".join(seats[seat].hole)) for seat in pot["eligible"]}
        shares, _ = table_equities(holes, board, samples=_ALL_IN_SAMPLES, seed=history.seed)
        for seat, share in shares.items():
            worth[seat] += share * pot["amount"] * paid
    return worth


@dataclass(frozen=True, slots=True)
class SeatMargin:
    name: str
    hands: int
    bb_per_100: float
    margin: float | None
    adjusted_bb_per_100: float
    adjusted_margin: float | None

    @property
    def clear_of_zero(self) -> bool:
        margin = self.adjusted_margin
        if margin is None or self.hands < MIN_HANDS_TO_CALL:
            return False
        return abs(self.adjusted_bb_per_100) > margin


def seat_margins(
    histories: Sequence[HandHistory],
    worth_of: Callable[[HandHistory], dict[int, float] | None] = all_in_adjusted,
) -> dict[str, SeatMargin]:
    """Every seat's result with its interval, plain and adjusted. ``worth_of`` lets a caller that
    sees the same hands again keep the answers, because each all-in costs about 30 ms."""
    nets: dict[str, list[float]] = defaultdict(list)
    adjusted: dict[str, list[float]] = defaultdict(list)
    for history in histories:
        worth = worth_of(history)
        for player in history.players:
            nets[player.name].append(player.net / history.big_blind)
            chips = player.net if worth is None else worth[player.seat]
            adjusted[player.name].append(chips / history.big_blind)
    return {
        name: SeatMargin(
            name=name,
            hands=len(nets[name]),
            bb_per_100=statistics.fmean(nets[name]) * 100,
            margin=margin_of_error(nets[name]),
            adjusted_bb_per_100=statistics.fmean(adjusted[name]) * 100,
            adjusted_margin=margin_of_error(adjusted[name]),
        )
        for name in nets
    }


def calls(margins: Sequence[SeatMargin]) -> list[str]:
    """The verdict in words: who clears break-even, and who is too close to call."""
    if margins and all(m.hands < MIN_HANDS_TO_CALL for m in margins):
        return [f"Too few hands to call anyone: that takes at least {MIN_HANDS_TO_CALL}."]
    clear = [m.name for m in margins if m.clear_of_zero]
    unclear = [m.name for m in margins if not m.clear_of_zero]
    lines = []
    if clear:
        lines.append(f"Clear of break-even: {', '.join(clear)}.")
    if unclear:
        lines.append(f"Too close to break-even to call: {', '.join(unclear)}.")
    return lines


# ----- freezeouts: the sample is the tournament, not the hand ------------------------------

# Bounded places settle much faster than chips per hand, and a t interval allows for few
# tournaments, so the bar to call anyone is lower than MIN_HANDS_TO_CALL.
MIN_TOURNAMENTS_TO_CALL = 10

# The 97.5th percentile of Student's t by degrees of freedom. Between rows the row below is
# used, which can only make an interval a little wider.
_T975 = {
    **{1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306},
    **{9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131},
    **{16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074},
    **{23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045},
    **{30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980},
}


def t_margin(values: Sequence[float]) -> float | None:
    """Half the width of a 95% t interval on the mean of ``values``."""
    if len(values) < 2:
        return None
    freedom = max(df for df in _T975 if df <= len(values) - 1)
    return _T975[freedom] * statistics.stdev(values) / math.sqrt(len(values))


def wilson(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """A 95% Wilson score interval on a share. It stays between 0 and 1, and 0 wins out of a
    few tournaments still leaves room above zero, which a plain normal interval does not."""
    if trials == 0:
        return 0.0, 1.0
    share = successes / trials
    scale = 1 + z * z / trials
    centre = (share + z * z / (2 * trials)) / scale
    half = z * math.sqrt(share * (1 - share) / trials + z * z / (4 * trials * trials)) / scale
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True, slots=True)
class Finish:
    """One seat's places over a run of freezeouts (1 is a win), with their intervals."""

    name: str
    places: tuple[int, ...]

    @property
    def tournaments(self) -> int:
        return len(self.places)

    @property
    def average(self) -> float:
        return statistics.fmean(self.places)

    @property
    def margin(self) -> float | None:
        return t_margin(self.places)

    @property
    def wins(self) -> int:
        return self.places.count(1)

    @property
    def win_interval(self) -> tuple[float, float]:
        return wilson(self.wins, self.tournaments)


def finish_calls(finishes: Sequence[Finish], seats: int) -> list[str]:
    """The verdict on a run of freezeouts, best average first: who finishes clear of chance,
    and which seats next to each other in the table are too close to call."""
    if finishes and all(f.tournaments < MIN_TOURNAMENTS_TO_CALL for f in finishes):
        return [
            f"Too few tournaments to call anyone: that takes at least {MIN_TOURNAMENTS_TO_CALL}."
        ]
    chance = (seats + 1) / 2

    def bounds(finish: Finish) -> tuple[float, float]:
        margin = finish.margin if finish.margin is not None else math.inf
        return finish.average - margin, finish.average + margin

    better = [f.name for f in finishes if bounds(f)[1] < chance]
    worse = [f.name for f in finishes if bounds(f)[0] > chance]
    close = [f.name for f in finishes if f.name not in better + worse]
    lines = []
    if better:
        lines.append(f"Better than chance: {', '.join(better)}.")
    if worse:
        lines.append(f"Worse than chance: {', '.join(worse)}.")
    if close:
        lines.append(f"Too close to chance to call: {', '.join(close)}.")
    pairs = [
        f"{a.name} and {b.name}"
        for a, b in zip(finishes, finishes[1:], strict=False)
        if bounds(a)[1] >= bounds(b)[0]
    ]
    if pairs:
        lines.append(f"Too close to call against the seat below: {', '.join(pairs)}.")
    return lines


def all_in_note(histories: Sequence[HandHistory]) -> str:
    early = sum(all_in_early(history) for history in histories)
    return (
        f"{early} of the {len(histories)} hands were all in before the last card. All-in adj. "
        "counts those at each seat's share of the pot at that moment, which takes out the luck "
        "of the cards still to come. It estimates the same win rate as bb/100 with less noise, "
        "so the calls use it."
    )
