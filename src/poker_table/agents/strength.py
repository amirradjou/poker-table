"""Cheap hand-strength heuristics shared by the scripted bots.

None of this is solver-grade; it is the kind of rule of thumb a decent live player uses.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from enum import IntEnum

from poker_table.cards import Card, Rank
from poker_table.evaluator import HandCategory, evaluate


def chen_score(hole: Sequence[Card]) -> float:
    """Bill Chen's preflop formula: AA=20, KK=16, AKs=12, 72o=-1 (roughly)."""
    a, b = sorted(hole, key=lambda c: c.rank, reverse=True)
    high = a.rank
    base = {Rank.ACE: 10, Rank.KING: 8, Rank.QUEEN: 7, Rank.JACK: 6}.get(high, high / 2)
    if a.rank == b.rank:
        return max(5.0, base * 2)
    score = base
    if a.suit == b.suit:
        score += 2
    gap = a.rank - b.rank - 1
    score -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
    if gap <= 1 and high < Rank.QUEEN:
        score += 1
    return float(math.ceil(score))


class MadeHand(IntEnum):
    """How much of a hand you have on this board, from the point of view of your hole cards."""

    NOTHING = 0
    WEAK_PAIR = 1  # a pair below the top board card, or a pocket pair below it
    TOP_PAIR = 2  # top pair or an overpair
    STRONG = 3  # two pair or better where your hole cards matter


# How many leading cards of HandRank.best form the pattern (the rest are kickers).
_CORE_CARDS = {
    HandCategory.PAIR: 2,
    HandCategory.TWO_PAIR: 4,
    HandCategory.THREE_OF_A_KIND: 3,
    HandCategory.STRAIGHT: 5,
    HandCategory.FLUSH: 5,
    HandCategory.FULL_HOUSE: 5,
    HandCategory.FOUR_OF_A_KIND: 4,
    HandCategory.STRAIGHT_FLUSH: 5,
}


def classify(hole: Sequence[Card], board: Sequence[Card]) -> MadeHand:
    if len(board) < 3:
        raise ValueError("classify needs a flop")
    rank = evaluate([*hole, *board])
    if rank.category is HandCategory.HIGH_CARD:
        return MadeHand.NOTHING
    core = rank.best[: _CORE_CARDS[rank.category]]
    if not any(c in core for c in hole):
        return MadeHand.NOTHING  # the board made it, everyone has it
    if rank.category >= HandCategory.TWO_PAIR:
        return MadeHand.STRONG
    paired = core[0].rank
    top_board = max(c.rank for c in board)
    if hole[0].rank == hole[1].rank:
        return MadeHand.TOP_PAIR if paired > top_board else MadeHand.WEAK_PAIR
    return MadeHand.TOP_PAIR if paired == top_board else MadeHand.WEAK_PAIR


def has_flush_draw(hole: Sequence[Card], board: Sequence[Card]) -> bool:
    if len(board) >= 5:
        return False
    suits = Counter(c.suit for c in [*hole, *board])
    return any(count == 4 and any(c.suit == suit for c in hole) for suit, count in suits.items())


def has_open_ender(hole: Sequence[Card], board: Sequence[Card]) -> bool:
    """Four consecutive ranks (not at the ace ends) that use at least one hole card."""
    if len(board) >= 5:
        return False
    ranks = {int(c.rank) for c in [*hole, *board]}
    hole_ranks = {int(c.rank) for c in hole}
    for low in range(3, 11):  # runs 3-6 .. 10-K keep both ends open
        run = set(range(low, low + 4))
        if run <= ranks and run & hole_ranks and (low - 1) not in ranks and (low + 4) not in ranks:
            return True
    return False


def outs_equity(outs: int, cards_to_come: int) -> float:
    """Rule of 2 and 4, capped: rough chance to hit by the river."""
    return min(0.95, outs * (0.04 if cards_to_come == 2 else 0.02))
