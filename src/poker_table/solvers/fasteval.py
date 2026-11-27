"""A numpy hand evaluator: millions of five- to seven-card hands a second, for the solvers.

It agrees with ``poker_table.evaluator.evaluate`` by construction: its tables are built by
calling that evaluator once on every multiset of ranks and on every flush. A hand's value is an
integer, higher is better, equal is a split (``encode``).

Cards are numbered 0..51 as in ``agents/handclass.py``: ``(rank - 2) * 4 + suit``.
"""

from __future__ import annotations

from functools import cache

import numpy as np

from poker_table.agents.handclass import all_combos, card_of, combo_classes
from poker_table.cards import Card, Rank, Suit
from poker_table.evaluator import HandRank, evaluate


def encode(rank: HandRank) -> int:
    """A hand's strength as one integer: category, then each kicker, in base 13."""
    kickers = [k - 2 for k in rank.kickers] + [0] * (5 - len(rank.kickers))
    value = int(rank.category)
    for kicker in kickers:
        value = value * 13 + kicker
    return value


def _rank_multisets(size: int, top: int = 12, left: tuple[int, ...] = ()):
    """Every way to hold ``size`` cards by rank alone, at most four of a rank."""
    if size == 0:
        yield left
        return
    for rank in range(top, -1, -1):
        for count in range(min(4, size), 0, -1):
            yield from _rank_multisets(size - count, rank - 1, left + (rank,) * count)


@cache
def _tables() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    suits = list(Suit)
    keys, values = [], []
    for size in (5, 6, 7):
        for ranks in _rank_multisets(size):
            # Deal the suits round-robin, so no suit gets five cards and nothing is a flush.
            cards = [Card(Rank(r + 2), suits[i % 4]) for i, r in enumerate(ranks)]
            keys.append(sum(5**r for r in ranks))
            values.append(encode(evaluate(cards)))
    order = np.argsort(keys, kind="stable")
    sorted_keys = np.asarray(keys, dtype=np.int64)[order]
    if len(np.unique(sorted_keys)) != len(sorted_keys):
        raise AssertionError("rank keys collide")
    flush = np.zeros(1 << 13, dtype=np.int64)
    for mask in range(1 << 13):
        count = mask.bit_count()
        if 5 <= count <= 7:
            cards = [Card(Rank(r + 2), Suit.SPADES) for r in range(13) if mask >> r & 1]
            flush[mask] = encode(evaluate(cards))
    return sorted_keys, np.asarray(values, dtype=np.int64)[order], flush


_POW5 = 5 ** np.arange(13, dtype=np.int64)
_BIT = 1 << np.arange(13, dtype=np.int64)


def evaluate_many(cards: np.ndarray) -> np.ndarray:
    """Values of many hands at once: ``cards`` has one row of 5 to 7 card numbers per hand."""
    keys, values, flush = _tables()
    cards = np.asarray(cards, dtype=np.int64)
    ranks, suits = cards // 4, cards % 4
    plain = values[np.searchsorted(keys, _POW5[ranks].sum(axis=1))]
    bits = _BIT[ranks]
    best = plain
    for suit in range(4):
        mask = np.where(suits == suit, bits, 0).sum(axis=1)
        # Five or more of one suit is a flush, and with seven cards a flush and a full house
        # cannot both be there, so the larger value is the hand.
        best = np.maximum(best, flush[mask])
    return best


@cache
def combo_cards() -> np.ndarray:
    return np.asarray(all_combos(), dtype=np.int64)


@cache
def combo_class_array() -> np.ndarray:
    return np.asarray(combo_classes(), dtype=np.int64)


@cache
def combo_masks() -> np.ndarray:
    cards = combo_cards()
    return (np.int64(1) << cards[:, 0]) | (np.int64(1) << cards[:, 1])


@cache
def disjoint() -> np.ndarray:
    """``[a, b]``: hands a and b share no card."""
    masks = combo_masks()
    return (masks[:, None] & masks[None, :]) == 0


def board_mask(board) -> int:
    mask = 0
    for card in board:
        mask |= 1 << int(card)
    return mask


def valid_combos(board) -> np.ndarray:
    """Indices of the hands that share no card with ``board``."""
    return np.flatnonzero((combo_masks() & board_mask(board)) == 0)


def values_on(board, combos: np.ndarray) -> np.ndarray:
    cards = combo_cards()[combos]
    table = np.broadcast_to(np.asarray(board, dtype=np.int64), (len(combos), len(board)))
    return evaluate_many(np.concatenate([cards, table], axis=1))


def strengths(board) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Every hand's strength against one random hand on ``board``, as whole numbers.

    Returns the hands, their values, ``wins2`` (twice the wins plus the ties against every
    other hand that shares no card with it or the board) and ``totals`` (how many such hands).
    ``wins2 / (2 * totals)`` is the hand strength; ``agents/abstraction.py`` computes the same
    numbers one hand at a time.
    """
    combos = valid_combos(board)
    values = values_on(board, combos)
    apart = disjoint()[np.ix_(combos, combos)]
    above = (values[:, None] > values[None, :]) & apart
    level = (values[:, None] == values[None, :]) & apart
    wins2 = 2 * above.sum(axis=1) + level.sum(axis=1)
    return combos, values, wins2, apart.sum(axis=1)


def cards_of(indices) -> list[Card]:
    return [card_of(int(i)) for i in indices]
