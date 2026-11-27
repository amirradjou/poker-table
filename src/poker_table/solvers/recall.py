"""Two ways to change a game's private states, for ``scripts/diagnose_blueprint.py``.

* ``coarsen`` merges states into groups, round by round: a smaller game of the same shape.
* ``with_recall`` gives both players perfect recall: a player's state in round r becomes the
  whole list of states it has held, so nothing is forgotten. Chance and payoffs are unchanged,
  because they only ever depend on the current pair of states.

A strategy of the forgetting game is also a strategy of the remembering one (``lift``: every
history plays what its last state plays). The best response in ``solvers/cfr.py`` already
remembers its own earlier states, so it must win exactly as much against a strategy as against
that strategy lifted. A test checks this, and it is what says that the only thing the best
response knows beyond the strategy's buckets is its own memory.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from poker_table.solvers.cfr import Game


def chance_pairs(game: Game) -> list[np.ndarray]:
    """``[r][a, b]``: the chance of the pair of states (a, b) in round r, whatever is played."""
    pairs = [game.p0]
    for move in game.transitions[1:]:
        pairs.append(np.einsum("ab,abcd->cd", pairs[-1], move))
    return pairs


def coarsen(game: Game, groups: Sequence[np.ndarray]) -> Game:
    """Merge the states of round r into ``groups[r]`` (a group number for each state).

    A merged move or showdown share is the average over the states in the groups, weighted by
    how often chance alone deals each pair. The result is Markov in the pair of groups, like the
    game it came from, on the same betting tree.
    """
    pairs = chance_pairs(game)
    onehot = []
    for r, group in enumerate(groups):
        group = np.asarray(group)
        if len(group) != game.states[r]:
            raise ValueError(f"round {r} has {game.states[r]} states, not {len(group)}")
        onehot.append(np.eye(group.max() + 1)[group])
    mass = [
        np.einsum("ab,aA,bB->AB", p, g, g, optimize=True)
        for p, g in zip(pairs, onehot, strict=True)
    ]
    transitions: list[np.ndarray | None] = [None]
    for r in range(1, len(groups)):
        before, after = onehot[r - 1], onehot[r]
        joint = np.einsum(
            "ab,abcd,aA,bB,cC,dD->ABCD",
            pairs[r - 1],
            game.transitions[r],
            before,
            before,
            after,
            after,
            optimize=True,
        )
        total = mass[r - 1][:, :, None, None]
        size = after.shape[1] ** 2
        transitions.append(np.where(total > 0, joint / np.where(total > 0, total, 1), 1 / size))
    share = {}
    for r, values in game.share.items():
        g = onehot[r]
        weighted = np.einsum("ab,ab,aA,bB->AB", pairs[r], values, g, g, optimize=True)
        share[r] = np.where(mass[r] > 0, weighted / np.where(mass[r] > 0, mass[r], 1), 0.5)
    return Game(
        states=tuple(g.shape[1] for g in onehot),
        p0=mass[0],
        transitions=tuple(transitions),
        share=share,
        root=game.root,
    )


def histories(game: Game) -> list[int]:
    """How many histories of states a player can have in each round."""
    counts, total = [], 1
    for n in game.states:
        total *= n
        counts.append(total)
    return counts


def with_recall(game: Game) -> Game:
    """The same game, with each state replaced by the list of states held so far.

    History h of round r is numbered ``parent * states[r] + current``, so ``h % states[r]`` is
    its current state and ``h // states[r]`` the history it came from.
    """
    sizes = histories(game)
    transitions: list[np.ndarray | None] = [None]
    for r in range(1, len(game.states)):
        before, n_before, n = sizes[r - 1], game.states[r - 1], game.states[r]
        move = game.transitions[r]
        lifted = np.zeros((before, before, before, n, before, n))
        for a in range(before):
            for b in range(before):
                lifted[a, b, a, :, b, :] = move[a % n_before, b % n_before]
        transitions.append(lifted.reshape(before, before, before * n, before * n))
    share = {}
    for r, values in game.share.items():
        current = np.arange(sizes[r]) % game.states[r]
        share[r] = values[np.ix_(current, current)]
    return Game(
        states=tuple(sizes),
        p0=game.p0,
        transitions=tuple(transitions),
        share=share,
        root=game.root,
    )


def lift(game: Game, strategy: Sequence[np.ndarray]) -> list[np.ndarray]:
    """A strategy of ``game`` as a strategy of ``with_recall(game)``: each history plays what its
    current state plays."""
    sizes = histories(game)
    return [
        rows[np.arange(sizes[node.round]) % game.states[node.round]]
        for node, rows in zip(game.decisions, strategy, strict=True)
    ]
