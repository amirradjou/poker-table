"""The heads-up push/fold game: exact all-in equities between the 169 classes, and the game.

The small blind (the button) either shoves all in or folds; the big blind calls or folds. Both
start the hand with the same stack, ``stack`` big blinds before the antes and blinds go in.
Chips are counted in big blinds: each player posts the ante (none by default), then the small
blind posts 1/2 and the big blind 1.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import combinations, permutations

import numpy as np

from poker_table.agents.handclass import CLASSES, COMBOS, class_combos, compatible_counts
from poker_table.solvers.cfr import Game, Node
from poker_table.solvers.fasteval import combo_class_array, combo_masks, values_on


def canonical_boards(size: int) -> tuple[np.ndarray, np.ndarray]:
    """One board per class of boards that only differ by a renaming of suits, with how many
    boards are in each class. Hands summed over a whole class are the same whatever the
    renaming, so summing over these with the counts as weights is summing over every board."""
    boards = np.array(list(combinations(range(52), size)), dtype=np.int64)
    ranks, suits = boards // 4, boards % 4
    best = None
    for order in permutations(range(4)):
        renamed = np.sort(ranks * 4 + np.asarray(order)[suits], axis=1)
        key = (renamed * (52 ** np.arange(size)[::-1])).sum(axis=1)
        best = key if best is None else np.minimum(best, key)
    keys, counts = np.unique(best, return_counts=True)
    digits = (keys[:, None] // (52 ** np.arange(size)[::-1])) % 52
    return digits.astype(np.int64), counts.astype(np.int64)


def _pairs() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Every pair of two-card hands with no card in common (a < b), with its class pair."""
    masks = combo_masks()
    first, second = np.triu_indices(COMBOS, k=1)
    keep = (masks[first] & masks[second]) == 0
    first, second = first[keep], second[keep]
    classes = combo_class_array()
    return first, second, classes[first] * CLASSES + classes[second]


def equity_counts(
    boards: np.ndarray, weights: np.ndarray, progress: Callable[[int], None] | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Twice the wins plus the ties of class i against class j, and how many deals that was.

    Integers throughout, so the sum is exact and does not depend on the order of the boards.
    """
    first, second, cell = _pairs()
    masks = combo_masks()
    upper_wins = np.zeros(CLASSES * CLASSES, dtype=np.int64)
    upper_deals = np.zeros(CLASSES * CLASSES, dtype=np.int64)
    every = np.arange(COMBOS)
    for n, (board, weight) in enumerate(zip(boards, weights, strict=True)):
        used = 0
        for card in board:
            used |= 1 << int(card)
        valid = (masks & used) == 0
        values = np.full(COMBOS, -1, dtype=np.int64)
        values[valid] = values_on(board, every[valid])
        live = valid[first] & valid[second]
        a, b = values[first[live]], values[second[live]]
        wins2 = 2 * (a > b) + (a == b)
        here = cell[live]
        upper_wins += weight * np.bincount(here, weights=wins2, minlength=CLASSES**2).astype(
            np.int64
        )
        upper_deals += weight * np.bincount(here, minlength=CLASSES**2)
        if progress is not None:
            progress(n + 1)
    wins = upper_wins.reshape(CLASSES, CLASSES)
    deals = upper_deals.reshape(CLASSES, CLASSES)
    # A pair (a, b) of classes (i, j) seen from b's side is a deal of j against i.
    return wins + (2 * deals.T - wins.T), deals + deals.T


def equity_matrix(wins2: np.ndarray, deals: np.ndarray) -> np.ndarray:
    return wins2 / (2 * deals)


def deal_chances() -> np.ndarray:
    """``[i, j]``: the chance that the small blind holds class i and the big blind class j."""
    sizes = np.array([len(c) for c in class_combos()], dtype=float)
    compatible = np.array(compatible_counts(), dtype=float)
    return sizes[:, None] * compatible / (COMBOS * 1225)  # 1225 hands are left for the other


def posted(stack: float, ante: float) -> tuple[float, float]:
    """What the small blind and the big blind have in before anyone acts: the ante and the
    blind, or the whole stack when it is shorter than that."""
    return min(stack, ante + 0.5), min(stack, ante + 1.0)


def game(
    stack: float, equity: np.ndarray, chances: np.ndarray | None = None, ante: float = 0.0
) -> Game:
    """The push/fold game at ``stack`` big blinds. Player 0 is the small blind.

    ``ante`` is what each player posts before the blinds, in big blinds. It is dead money: a
    fold loses it with the blind, and a shove that is not called wins the other ante and the big
    blind. A shove that is called puts each whole stack in, antes included.
    """
    small, big = posted(stack, ante)
    root = Node.decision(
        0,
        0,
        [
            ("fold", Node.fold(0, 0, small, big)),
            (
                "shove",
                Node.decision(
                    0,
                    1,
                    [
                        ("fold", Node.fold(0, 1, stack, big)),
                        ("call", Node.showdown(0, stack, stack)),
                    ],
                ),
            ),
        ],
    )
    return Game(
        states=(len(equity),),
        p0=deal_chances() if chances is None else chances,
        transitions=(None,),
        share={0: equity},
        root=root,
    )
