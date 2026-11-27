"""The 169 classes of starting hands, laid out like the usual 13 x 13 chart.

Row and column 0 are aces, 12 are deuces. A pair sits on the diagonal, a suited hand above it
(row = its higher rank) and an offsuit hand below it (column = its higher rank), which is how
published push/fold charts lay them out. Cards are also numbered 0..51 here, rank-major
(``(rank - 2) * 4 + suit``), which is what the solvers use.
"""

from __future__ import annotations

from functools import cache
from itertools import combinations

from poker_table.cards import Card, Rank, Suit

RANKS = "AKQJT98765432"  # chart order: row and column 0 are aces
SUITS = "cdhs"
CLASSES = 169
COMBOS = 1326


def card_index(card: Card) -> int:
    return (int(card.rank) - 2) * 4 + SUITS.index(card.suit.value)


def card_of(index: int) -> Card:
    return Card(Rank(index // 4 + 2), Suit(SUITS[index % 4]))


def class_index(first: Card, second: Card) -> int:
    """The chart cell of a two-card hand."""
    high, low = (first, second) if first.rank >= second.rank else (second, first)
    row, col = RANKS.index(high.rank.char), RANKS.index(low.rank.char)
    if row == col:
        return row * 13 + row
    if high.suit == low.suit:
        return row * 13 + col  # suited: above the diagonal
    return col * 13 + row  # offsuit: below it


def class_name(index: int) -> str:
    row, col = divmod(index, 13)
    if row == col:
        return RANKS[row] * 2
    if col > row:
        return f"{RANKS[row]}{RANKS[col]}s"
    return f"{RANKS[col]}{RANKS[row]}o"


def class_of_name(name: str) -> int:
    for index in range(CLASSES):
        if class_name(index) == name:
            return index
    raise ValueError(f"not a starting hand: {name!r}")


@cache
def all_combos() -> tuple[tuple[int, int], ...]:
    """Every two-card hand as card numbers (lower first), in a fixed order."""
    return tuple(combinations(range(52), 2))


@cache
def combo_classes() -> tuple[int, ...]:
    return tuple(class_index(card_of(a), card_of(b)) for a, b in all_combos())


@cache
def class_combos() -> tuple[tuple[tuple[int, int], ...], ...]:
    found: list[list[tuple[int, int]]] = [[] for _ in range(CLASSES)]
    for combo, cls in zip(all_combos(), combo_classes(), strict=True):
        found[cls].append(combo)
    return tuple(tuple(c) for c in found)


@cache
def compatible_counts() -> tuple[tuple[int, ...], ...]:
    """``[i][j]``: how many hands of class j share no card with one given hand of class i.

    It is the same for every hand of class i, because suits are interchangeable.
    """
    combos = class_combos()
    out = []
    for i in range(CLASSES):
        a, b = combos[i][0]
        out.append(
            tuple(sum(1 for c, d in combos[j] if len({a, b, c, d}) == 4) for j in range(CLASSES))
        )
    return tuple(out)
