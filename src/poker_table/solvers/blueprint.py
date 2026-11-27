"""The cfr:blueprint seat's abstract game, built for the solver.

The betting tree comes from ``agents/abstraction.py``. The chance model is the part that
needs numpy: how the pair of buckets moves from street to street, and who wins a showdown
between two buckets, counted over real deals (``scripts/train_blueprint.py``).

That chance model is Markov in the pair of buckets: the next street's pair depends only on this
street's pair. Real cards remember more than that, so the abstract game is a model of hold'em,
not hold'em, and its exploitability is measured inside the model.
"""

from __future__ import annotations

from functools import cache

import numpy as np

from poker_table.agents.abstraction import (
    CHANCE,
    DECISION,
    FLOP,
    FOLD,
    RIVER,
    TURN,
    Cards,
    Spot,
    straight_outs,
)
from poker_table.agents.handclass import CLASSES, COMBOS
from poker_table.solvers.cfr import Game, Node
from poker_table.solvers.fasteval import combo_cards, combo_class_array, disjoint, strengths
from poker_table.solvers.pushfold import canonical_boards


def solver_tree(spot: Spot, big_blind: float = 1.0) -> Node:
    """The same tree as solver nodes, chips in big blinds, numbered in the same order."""
    c0, c1 = (amount / big_blind for amount in spot.put_in)
    if spot.kind == DECISION:
        return Node.decision(
            spot.street,
            spot.actor,
            [
                (label, solver_tree(child, big_blind))
                for label, child in zip(spot.labels, spot.children, strict=True)
            ],
        )
    if spot.kind == CHANCE:
        return Node.chance(spot.street, solver_tree(spot.children[0], big_blind))
    if spot.kind == FOLD:
        return Node.fold(spot.street, spot.folder, c0, c1)
    return Node.showdown(spot.street, c0, c1)


def game(
    root: Spot,
    p0: np.ndarray,
    transitions: tuple[np.ndarray, np.ndarray, np.ndarray],
    share: dict[int, np.ndarray],
    big_blind: float,
) -> Game:
    return Game(
        states=(p0.shape[0], *(t.shape[2] for t in transitions)),
        p0=p0,
        transitions=(None, *transitions),
        share=share,
        root=solver_tree(root, big_blind),
    )


# ----- the chance model ----------------------------------------------------------------------


@cache
def _straight_outs() -> np.ndarray:
    return np.array([straight_outs(mask) for mask in range(1 << 13)], dtype=np.int64)


def draws(board, combos: np.ndarray) -> np.ndarray:
    """``agents.abstraction.has_draw`` for many hands at once."""
    hole = combo_cards()[combos]
    table = np.broadcast_to(np.asarray(board, dtype=np.int64), (len(combos), len(board)))
    cards = np.concatenate([hole, table], axis=1)
    suits = cards % 4
    counts = np.stack([(suits == s).sum(axis=1) for s in range(4)], axis=1)
    holds = np.stack([((hole % 4) == s).any(axis=1) for s in range(4)], axis=1)
    flush = ((counts == 4) & holds).any(axis=1) & ~(counts >= 5).any(axis=1)
    mine = _straight_outs()[np.bitwise_or.reduce(np.int64(1) << (cards // 4), axis=1)]
    board_mask = 0
    for card in board:
        board_mask |= 1 << (int(card) // 4)
    straight = (mine >= 2) & (mine > _straight_outs()[board_mask])
    return flush | straight


def street_buckets(cards: Cards, board) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``agents.abstraction.bucket`` for every hand on ``board``: the hands, their buckets and
    their values. The same whole-number counts and the same division, so the same buckets."""
    combos, values, wins2, totals = strengths(board)
    strength = wins2 / (2 * totals)
    street = {3: FLOP, 4: TURN, 5: RIVER}[len(board)]
    cuts = {FLOP: cards.flop, TURN: cards.turn, RIVER: cards.river}[street]
    level = np.searchsorted(np.asarray(cuts, dtype=float), strength, side="right")
    if street != RIVER:
        level = level + (len(cuts) + 1) * draws(board, combos)
    return combos, level, values


def strength_cuts(boards: np.ndarray, quantiles: dict[int, tuple[float, ...]]) -> Cards:
    """Cut points that split the hands dealt on ``boards`` at these quantiles of strength."""
    found: dict[int, list[np.ndarray]] = {FLOP: [], TURN: [], RIVER: []}
    for board in boards:
        for street, size in ((FLOP, 3), (TURN, 4), (RIVER, 5)):
            _, _, wins2, totals = strengths(board[:size])
            found[street].append(wins2 / (2 * totals))
    cuts = {
        street: tuple(round(float(q), 4) for q in np.quantile(np.concatenate(v), quantiles[street]))
        for street, v in found.items()
    }
    return Cards(flop=cuts[FLOP], turn=cuts[TURN], river=cuts[RIVER])


def _normalised(counts: np.ndarray) -> np.ndarray:
    total = counts.sum(axis=(2, 3), keepdims=True)
    uniform = np.full(counts.shape, 1.0 / (counts.shape[2] * counts.shape[3]))
    return np.where(total > 0, counts / np.where(total > 0, total, 1), uniform)


def preflop_to_flop(cards: Cards, progress=None, limit: int | None = None) -> np.ndarray:
    """``[i, j, a, b]``: the chance that classes i and j land in flop buckets a and b. Exact:
    every flop, through its class of suit-renamed flops (``limit`` keeps the first few, which
    only the quick determinism check uses)."""
    size = cards.buckets(FLOP)
    flops, weights = canonical_boards(3)
    flops, weights = flops[:limit], weights[:limit]
    counts = np.zeros(CLASSES * CLASSES * size * size, dtype=np.int64)
    classes = combo_class_array()
    for n, (flop, weight) in enumerate(zip(flops, weights, strict=True)):
        combos, level, _ = street_buckets(cards, flop)
        first, second = np.nonzero(disjoint()[np.ix_(combos, combos)])
        cell = (classes[combos[first]] * CLASSES + classes[combos[second]]) * size
        cell = (cell + level[first]) * size + level[second]
        counts += weight * np.bincount(cell, minlength=counts.size)
        if progress is not None:
            progress(n + 1, len(flops))
    return _normalised(counts.reshape(CLASSES, CLASSES, size, size).astype(float))


def later_streets(cards: Cards, boards: np.ndarray, progress=None) -> dict[str, np.ndarray]:
    """Flop to turn and turn to river chances between pairs of buckets, and the showdown share
    for a pair of flop, turn or river buckets, counted over every pair of hands on ``boards``."""
    sizes = {street: cards.buckets(street) for street in (FLOP, TURN, RIVER)}
    nf, nt, nr = sizes[FLOP], sizes[TURN], sizes[RIVER]
    flop_turn = np.zeros(nf * nf * nt * nt, dtype=np.int64)
    turn_river = np.zeros(nt * nt * nr * nr, dtype=np.int64)
    wins = {street: np.zeros(n * n, dtype=np.int64) for street, n in sizes.items()}
    deals = {street: np.zeros(n * n, dtype=np.int64) for street, n in sizes.items()}
    for count, board in enumerate(boards):
        level: dict[int, np.ndarray] = {}
        value = np.full(COMBOS, -1, dtype=np.int64)
        for street, length in ((FLOP, 3), (TURN, 4), (RIVER, 5)):
            combos, levels, values = street_buckets(cards, board[:length])
            full = np.full(COMBOS, -1, dtype=np.int64)
            full[combos] = levels
            level[street] = full
        river_combos = combos  # the hands that miss every card of the board
        value[river_combos] = values
        first, second = np.nonzero(disjoint()[np.ix_(river_combos, river_combos)])
        a, b = river_combos[first], river_combos[second]
        f, t, r = level[FLOP], level[TURN], level[RIVER]
        cell = ((f[a] * nf + f[b]) * nt + t[a]) * nt + t[b]
        flop_turn += np.bincount(cell, minlength=flop_turn.size)
        cell = ((t[a] * nt + t[b]) * nr + r[a]) * nr + r[b]
        turn_river += np.bincount(cell, minlength=turn_river.size)
        won = 2 * (value[a] > value[b]) + (value[a] == value[b])
        for street, lv in ((FLOP, f), (TURN, t), (RIVER, r)):
            n = sizes[street]
            cell = lv[a] * n + lv[b]
            wins[street] += np.bincount(cell, weights=won, minlength=n * n).astype(np.int64)
            deals[street] += np.bincount(cell, minlength=n * n)
        if progress is not None:
            progress(count + 1, len(boards))
    out = {
        "flop_turn": _normalised(flop_turn.reshape(nf, nf, nt, nt).astype(float)),
        "turn_river": _normalised(turn_river.reshape(nt, nt, nr, nr).astype(float)),
    }
    for street, n in sizes.items():
        dealt = deals[street].reshape(n, n)
        won = wins[street].reshape(n, n)
        out[f"share_{street}"] = np.where(dealt > 0, won / np.where(dealt > 0, 2 * dealt, 1), 0.5)
    return out
