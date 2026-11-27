"""Three ways to change a game's private states.

* ``coarsen`` merges states into groups, round by round: a smaller game of the same shape.
* ``with_recall`` gives both players perfect recall: a player's state in round r becomes the
  whole list of states it has held, so nothing is forgotten. Chance and payoffs are unchanged,
  because they only ever depend on the current pair of states.
* ``remember_groups`` gives both players a little recall: a state after round 0 becomes the pair
  (group of the round-0 state, current state). It is the second cfr:blueprint's game. Chance and
  payoffs are again unchanged: the group is carried along as a label.

A strategy of the forgetting game is also a strategy of the remembering ones (``lift`` and
``lift_groups``: each state plays what its current state played). The best response in
``solvers/cfr.py`` already remembers its own earlier states, so it must win exactly as much
against a strategy as against that strategy lifted. Tests check this: it is what says that the
only thing the best response knows beyond the strategy's buckets is its own memory, and that a
number measured in the game with groups is on the same scale as one measured without them.
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


# ----- a preflop group carried along -----------------------------------------------------------


def equity_groups(game: Game, count: int) -> np.ndarray:
    """The round-0 states in ``count`` groups of about equal weight, by equity against a random
    hand (the share at a round-0 showdown, weighted by how often chance deals each pair)."""
    dealt = game.p0.sum(axis=1)
    equity = (game.p0 * game.share[0]).sum(axis=1) / dealt
    order = np.argsort(-equity, kind="stable")
    position = np.empty(len(order), dtype=np.int64)
    position[order] = np.arange(len(order))
    share_before = np.cumsum(dealt[order]) - dealt[order]
    return np.minimum((share_before * count).astype(np.int64), count - 1)[position]


class GroupDeal:
    """Round 0 to round 1 when round 1's state is (group, state): states a and b move to
    (group of a, x) and (group of b, y) with the chance ``move[a, b, x, y]``. State (g, x) is
    numbered ``g * n + x``."""

    def __init__(self, move: np.ndarray, groups: np.ndarray) -> None:
        self.move = move
        self.groups = np.asarray(groups, dtype=np.int64)
        self.count = int(self.groups.max()) + 1
        self.n = move.shape[2]
        self.members = [np.flatnonzero(self.groups == g) for g in range(self.count)]
        n2 = self.n * self.n
        self.blocks = {
            (g, h): move[np.ix_(mg, mh)].reshape(len(mg) * len(mh), n2)
            for g, mg in enumerate(self.members)
            for h, mh in enumerate(self.members)
        }

    def deal(self, reach: np.ndarray) -> np.ndarray:
        g_n, n = self.count, self.n
        out = np.zeros((g_n, n, g_n, n))
        for (g, h), block in self.blocks.items():
            out[g, :, h, :] = (
                reach[np.ix_(self.members[g], self.members[h])].ravel() @ block
            ).reshape(n, n)
        return out.reshape(g_n * n, g_n * n)

    def undeal(self, value: np.ndarray) -> np.ndarray:
        g_n, n = self.count, self.n
        grouped = value.reshape(g_n, n, g_n, n)
        out = np.empty(self.move.shape[:2])
        for (g, h), block in self.blocks.items():
            mg, mh = self.members[g], self.members[h]
            out[np.ix_(mg, mh)] = (block @ grouped[g, :, h, :].ravel()).reshape(len(mg), len(mh))
        return out

    def responder_block(self, br_player: int, state: int) -> tuple[np.ndarray, np.ndarray]:
        g_n, n, old = self.count, self.n, self.move.shape[0]
        support = self.groups[state] * n + np.arange(n)
        # [the other's old state, the responder's new state, the other's new state]
        values = self.move[:, state].transpose(0, 2, 1) if br_player == 1 else self.move[state]
        matrix = np.zeros((old, n, g_n, n))
        matrix[np.arange(old), :, self.groups, :] = values
        return matrix.reshape(old, n * g_n * n), support

    def dense(self) -> np.ndarray:
        """The same step as a dense ``[a, b, c, d]`` array, for tests on small games."""
        g_n, n, old = self.count, self.n, self.move.shape[0]
        out = np.zeros((old, old, g_n, n, g_n, n))
        for a in range(old):
            for b in range(old):
                out[a, b, self.groups[a], :, self.groups[b], :] = self.move[a, b]
        return out.reshape(old, old, g_n * n, g_n * n)


class GroupKeep:
    """A later round when both rounds' states are (group, state): the groups stay, and the
    states move with the chance ``move[x, y, x', y']``."""

    def __init__(self, move: np.ndarray, count: int) -> None:
        self.move = move
        self.count = count
        self.n, self.m = move.shape[0], move.shape[2]
        self.flat = move.reshape(self.n * self.n, self.m * self.m)

    def deal(self, reach: np.ndarray) -> np.ndarray:
        g_n, n, m = self.count, self.n, self.m
        pairs = reach.reshape(g_n, n, g_n, n).transpose(0, 2, 1, 3).reshape(g_n * g_n, n * n)
        out = (pairs @ self.flat).reshape(g_n, g_n, m, m).transpose(0, 2, 1, 3)
        return out.reshape(g_n * m, g_n * m)

    def undeal(self, value: np.ndarray) -> np.ndarray:
        g_n, n, m = self.count, self.n, self.m
        pairs = value.reshape(g_n, m, g_n, m).transpose(0, 2, 1, 3).reshape(g_n * g_n, m * m)
        out = (pairs @ self.flat.T).reshape(g_n, g_n, n, n).transpose(0, 2, 1, 3)
        return out.reshape(g_n * n, g_n * n)

    def responder_block(self, br_player: int, state: int) -> tuple[np.ndarray, np.ndarray]:
        g_n, n, m = self.count, self.n, self.m
        group, current = divmod(state, n)
        support = group * m + np.arange(m)
        # [the other's old state, the responder's new state, the other's new state]
        values = self.move[:, current].transpose(0, 2, 1) if br_player == 1 else self.move[current]
        matrix = np.zeros((g_n, n, m, g_n, m))
        for g in range(g_n):
            matrix[g, :, :, g, :] = values
        return matrix.reshape(g_n * n, m * g_n * m), support

    def dense(self) -> np.ndarray:
        g_n, n, m = self.count, self.n, self.m
        out = np.zeros((g_n, n, g_n, n, g_n, m, g_n, m))
        for g in range(g_n):
            for h in range(g_n):
                out[g, :, h, :, g, :, h, :] = self.move
        return out.reshape(g_n * n, g_n * n, g_n * m, g_n * m)


def remember_groups(game: Game, groups: np.ndarray) -> Game:
    """The same game, with each state after round 0 replaced by (group of the round-0 state,
    current state). ``groups`` gives a group to each round-0 state."""
    groups = np.asarray(groups, dtype=np.int64)
    if len(groups) != game.states[0]:
        raise ValueError(f"round 0 has {game.states[0]} states, not {len(groups)}")
    count = int(groups.max()) + 1
    transitions = (
        None,
        GroupDeal(game.transitions[1], groups),
        *(GroupKeep(move, count) for move in game.transitions[2:]),
    )
    share = {
        r: values if r == 0 else np.tile(values, (count, count)) for r, values in game.share.items()
    }
    return Game(
        states=(game.states[0], *(count * n for n in game.states[1:])),
        p0=game.p0,
        transitions=transitions,
        share=share,
        root=game.root,
    )


def dense(game: Game) -> Game:
    """A game whose chance steps are objects, with them written out as dense arrays."""
    return Game(
        states=game.states,
        p0=game.p0,
        transitions=tuple(
            move if move is None or isinstance(move, np.ndarray) else move.dense()
            for move in game.transitions
        ),
        share=game.share,
        root=game.root,
    )


def lift_groups(game: Game, strategy: Sequence[np.ndarray], count: int) -> list[np.ndarray]:
    """A strategy of ``game`` as a strategy of ``remember_groups(game, groups)`` with ``count``
    groups: each (group, state) plays what the state played."""
    return [
        rows if node.round == 0 else rows[np.arange(count * len(rows)) % len(rows)]
        for node, rows in zip(game.decisions, strategy, strict=True)
    ]
