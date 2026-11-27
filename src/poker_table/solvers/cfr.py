"""CFR+ and exact best responses for two-player zero-sum games with private states.

A game here is a betting tree plus a chance model over the two players' private states:

* rounds 0..R-1. In round r each player holds one of ``states[r]`` private states: a card, a
  class of starting hands, or a bucket of similar hands;
* ``p0[a, b]`` is the chance that player 0 holds state a and player 1 state b in round 0;
* ``transitions[r][a, b, c, d]`` (r >= 1) is the chance of the pair moving to (c, d) in round r
  from (a, b) in round r-1. Chance is Markov in the pair of states;
* ``share[r][a, b]`` is player 0's share of the pot at a showdown in round r: 1 for a win, a half
  for a tie, 0 for a loss;
* the tree has decision nodes, chance nodes (the next round's cards) and terminals. A terminal
  pays out from what each player has put in.

A strategy gives, at every decision node, a probability for each action for each state of the
player who acts there. Everything is a matrix over the pair of states, so one pass over the tree
covers every deal at once (the "vector form" of CFR). That makes a pass exact, not sampled, and
the same pass shape gives an exact best response.

When a round's state forgets the earlier ones (a bucket of hands that does not say which bucket
the hand was in before), the players have imperfect recall. CFR+ is then a heuristic with no
convergence guarantee, which is also true of the abstractions behind the big poker bots. The
best response here always remembers its own earlier states, so it can only be stronger than one
that forgets.

CFR+ is Tammelin (2014), https://arxiv.org/abs/1407.5042: regret matching with regrets floored
at zero, alternating updates, and an average strategy weighted by iteration number.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

DECISION, CHANCE, FOLD, SHOWDOWN = range(4)


class Node:
    """One node of a betting tree. Build them with the class methods."""

    __slots__ = ("kind", "round", "player", "labels", "children", "folder", "c0", "c1", "index")

    def __init__(self, kind: int, round_: int) -> None:
        self.kind = kind
        self.round = round_
        self.player = -1
        self.labels: tuple[str, ...] = ()
        self.children: list[Node] = []
        self.folder = -1
        self.c0 = 0.0
        self.c1 = 0.0
        self.index = -1  # decision nodes are numbered in depth-first order

    @classmethod
    def decision(cls, round_: int, player: int, choices: Sequence[tuple[str, Node]]) -> Node:
        node = cls(DECISION, round_)
        node.player = player
        node.labels = tuple(label for label, _ in choices)
        node.children = [child for _, child in choices]
        return node

    @classmethod
    def chance(cls, round_: int, child: Node) -> Node:
        """Deal the cards of ``round_`` (the new round), then play ``child``."""
        node = cls(CHANCE, round_)
        node.children = [child]
        return node

    @classmethod
    def fold(cls, round_: int, folder: int, c0: float, c1: float) -> Node:
        node = cls(FOLD, round_)
        node.folder, node.c0, node.c1 = folder, float(c0), float(c1)
        return node

    @classmethod
    def showdown(cls, round_: int, c0: float, c1: float) -> Node:
        node = cls(SHOWDOWN, round_)
        node.c0, node.c1 = float(c0), float(c1)
        return node


def number_decisions(root: Node) -> list[Node]:
    """Number the decision nodes depth-first, children in order. The same tree always gets the
    same numbers, which is what lets a strategy file be a flat list of rows."""
    found: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.kind == DECISION:
            node.index = len(found)
            found.append(node)
        stack.extend(reversed(node.children))
    return found


@dataclass
class Game:
    states: tuple[int, ...]
    p0: np.ndarray
    transitions: tuple[np.ndarray | None, ...]  # [r] leads into round r; [0] is None
    share: dict[int, np.ndarray]
    root: Node
    decisions: list[Node] = field(init=False)

    def __post_init__(self) -> None:
        self.decisions = number_decisions(self.root)
        if not np.isclose(self.p0.sum(), 1.0):
            raise ValueError(f"round-0 chances add up to {self.p0.sum()}, not 1")

    def uniform(self) -> list[np.ndarray]:
        return [
            np.full((self.states[n.round], len(n.children)), 1 / len(n.children))
            for n in self.decisions
        ]


def regret_matching(regrets: np.ndarray) -> np.ndarray:
    """Play each action in proportion to its positive regret; uniformly when none is positive."""
    positive = np.maximum(regrets, 0.0)
    total = positive.sum(axis=1, keepdims=True)
    uniform = np.full_like(regrets, 1.0 / regrets.shape[1])
    return np.where(total > 0, positive / np.where(total > 0, total, 1.0), uniform)


def _utility0(node: Node, share: dict[int, np.ndarray]) -> float | np.ndarray:
    """Player 0's winnings at a terminal: a number for a fold, a matrix over states otherwise."""
    if node.kind == FOLD:
        return -node.c0 if node.folder == 0 else node.c1
    return (node.c0 + node.c1) * share[node.round] - node.c0


class CFRPlus:
    """CFR+ with alternating updates and a linearly weighted average strategy."""

    def __init__(self, game: Game) -> None:
        self.game = game
        self.regrets = [np.zeros((game.states[n.round], len(n.children))) for n in game.decisions]
        self.weights = [np.zeros_like(r) for r in self.regrets]
        self.iterations = 0

    def run(
        self,
        iterations: int,
        on_iteration: Callable[[int], None] | None = None,
        players: Sequence[int] = (0, 1),
    ) -> None:
        """``players`` are the ones whose regrets and average are updated; the others keep
        playing whatever their regrets say (``scripts/diagnose_blueprint.py`` holds one seat
        fixed that way and trains an exploiter for the other)."""
        for _ in range(iterations):
            self.iterations += 1
            for player in players:
                self._walk(self.game.root, self.game.p0, self.game.p0, player)
            if on_iteration is not None:
                on_iteration(self.iterations)

    def current(self) -> list[np.ndarray]:
        return [regret_matching(r) for r in self.regrets]

    def average(self) -> list[np.ndarray]:
        out = []
        for weights in self.weights:
            total = weights.sum(axis=1, keepdims=True)
            uniform = np.full_like(weights, 1.0 / weights.shape[1])
            out.append(np.where(total > 0, weights / np.where(total > 0, total, 1.0), uniform))
        return out

    def _walk(self, node: Node, r0: np.ndarray, r1: np.ndarray, player: int):
        """Player 0's expected winnings below ``node`` for each pair of states.

        ``r0`` is the chance of each pair times player 0's own reach, ``r1`` the same with
        player 1's. The updating player's regrets use the other one: what it gains if it plays
        to get here and everyone else plays as they do.
        """
        kind = node.kind
        if kind == FOLD or kind == SHOWDOWN:
            return _utility0(node, self.game.share)
        if kind == CHANCE:
            move = self.game.transitions[node.round]
            value = self._walk(
                node.children[0],
                np.einsum("ab,abcd->cd", r0, move),
                np.einsum("ab,abcd->cd", r1, move),
                player,
            )
            if np.ndim(value) == 0:
                return value
            return np.einsum("abcd,cd->ab", move, value)

        sigma = regret_matching(self.regrets[node.index])
        actor = node.player
        n = r0.shape[0]
        total = np.zeros((n, n))
        values = []
        for a, child in enumerate(node.children):
            column = sigma[:, a]
            if actor == 0:
                weight = column[:, None]
                value = self._walk(child, r0 * weight, r1, player)
            else:
                weight = column[None, :]
                value = self._walk(child, r0, r1 * weight, player)
            total += weight * value
            values.append(value)
        if actor == player:
            if actor == 0:
                gains = np.stack([(r1 * v).sum(axis=1) for v in values], axis=1)
                reach = r0.sum(axis=1)
            else:
                gains = np.stack([-(r0 * v).sum(axis=0) for v in values], axis=1)
                reach = r1.sum(axis=0)
            expected = (sigma * gains).sum(axis=1, keepdims=True)
            regrets = self.regrets[node.index]
            np.maximum(regrets + gains - expected, 0.0, out=regrets)
            self.weights[node.index] += self.iterations * reach[:, None] * sigma
        return total


def expected_value(game: Game, strategy: Sequence[np.ndarray]) -> float:
    """Player 0's expected winnings per game when both players follow ``strategy``."""

    def walk(node: Node, reach: np.ndarray) -> float:
        if node.kind == FOLD or node.kind == SHOWDOWN:
            return float((reach * _utility0(node, game.share)).sum())
        if node.kind == CHANCE:
            move = game.transitions[node.round]
            return walk(node.children[0], np.einsum("ab,abcd->cd", reach, move))
        sigma = strategy[node.index]
        total = 0.0
        for a, child in enumerate(node.children):
            weight = sigma[:, a][:, None] if node.player == 0 else sigma[:, a][None, :]
            total += walk(child, reach * weight)
        return total

    return walk(game.root, game.p0)


def best_response_value(
    game: Game,
    strategy: Sequence[np.ndarray],
    br_player: int,
    responder: Sequence[np.ndarray] | None = None,
) -> float:
    """What a best response playing seat ``br_player`` wins per game against ``strategy``.

    The best response knows the strategy and remembers every private state it has held, so in
    a game with imperfect recall it is stronger than any player who forgets. Exact: no sampling.

    With ``responder``, seat ``br_player`` plays that strategy by its current state instead of
    best responding. The walk is the same, so its value must match ``expected_value``: that is
    how a test checks this walk sees the trainer's tree, chances and payoffs.
    """

    def walk(node: Node, mass: np.ndarray, last: np.ndarray) -> np.ndarray:
        # mass[q, o]: chance and our reach for the responder's history of states q (whose
        # current state is last[q]) and our current state o.
        if node.kind == FOLD:
            if node.folder == br_player:
                gain = -(node.c0 if br_player == 0 else node.c1)
            else:
                gain = node.c1 if br_player == 0 else node.c0
            return gain * mass.sum(axis=1)
        if node.kind == SHOWDOWN:
            u0 = _utility0(node, game.share)  # [player 0 state, player 1 state]
            ours_by_theirs = u0 if br_player == 0 else -u0.T  # [responder state, our state]
            return (mass * ours_by_theirs[last]).sum(axis=1)
        if node.kind == CHANCE:
            move = game.transitions[node.round]
            # arrange as [our old, their old, our new, their new]
            move = move.transpose(1, 0, 3, 2) if br_player == 0 else move
            n_old, n_ours, n_theirs = move.shape[0], move.shape[2], move.shape[3]
            histories = mass.shape[0]
            moved = np.empty((histories, n_theirs, n_ours))
            # Histories that end in the same state move the same way: one product per state.
            for state in range(move.shape[1]):
                rows = np.flatnonzero(last == state)
                if len(rows):
                    block = mass[rows] @ move[:, state].reshape(n_old, n_ours * n_theirs)
                    moved[rows] = block.reshape(len(rows), n_ours, n_theirs).transpose(0, 2, 1)
            value = walk(
                node.children[0],
                moved.reshape(histories * n_theirs, n_ours),
                np.tile(np.arange(n_theirs), histories),
            )
            return value.reshape(histories, n_theirs).sum(axis=1)
        if node.player == br_player:
            values = [walk(child, mass, last) for child in node.children]
            if responder is None:
                return np.maximum.reduce(values)
            mixed = responder[node.index][last]  # [history, action]
            return sum(mixed[:, a] * v for a, v in enumerate(values))
        sigma = strategy[node.index]
        total = np.zeros(mass.shape[0])
        for a, child in enumerate(node.children):
            column = sigma[:, a]
            if not column.any():
                continue
            total += walk(child, mass * column[None, :], last)
        return total

    start = game.p0 if br_player == 0 else game.p0.T
    return float(walk(game.root, start, np.arange(start.shape[0])).sum())


def exploitability(game: Game, strategy: Sequence[np.ndarray]) -> float:
    """How much a best response wins per game against ``strategy``, averaged over the two seats.

    Zero at an equilibrium and positive otherwise, in the game's own units.
    """
    return (best_response_value(game, strategy, 0) + best_response_value(game, strategy, 1)) / 2
