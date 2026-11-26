"""Kuhn and Leduc poker in the solver's shape, to check CFR+ on games whose answers are known.

Kuhn poker (Kuhn, 1950): a three-card deck, an ante of 1, one bet of 1. Its value for the first
player is -1/18 per hand. Leduc hold'em: a six-card deck (two each of J, Q, K), an ante of 1, a
private card, a betting round with bets of 2, a public card, a round with bets of 4, at most two
bets or raises a round. A pair with the public card wins, otherwise the higher card.
"""

from __future__ import annotations

import numpy as np

from poker_table.solvers.cfr import Game, Node


def _betting(round_: int, last_round: int, bets: tuple[float, ...], max_raises: int):
    """The betting tree of one round, player 0 first, then the next round or the showdown."""

    def close(c0: float, c1: float) -> Node:
        if round_ == last_round:
            return Node.showdown(round_, c0, c1)
        return Node.chance(round_ + 1, _betting(round_ + 1, last_round, bets, max_raises)(c0, c1))

    def node(c: tuple[float, float], actor: int, raises: int) -> Node:
        other = 1 - actor
        bet = bets[round_]
        if c[actor] < c[other]:  # facing a bet
            called = (c[other], c[other])
            choices = [
                ("fold", Node.fold(round_, actor, *c)),
                ("call", close(*called)),
            ]
            if raises < max_raises:
                raised = list(c)
                raised[actor] = c[other] + bet
                choices.append(("raise", node(tuple(raised), other, raises + 1)))
            return Node.decision(round_, actor, choices)
        checked = close(*c) if actor == 1 else node(c, other, raises)
        raised = list(c)
        raised[actor] = c[actor] + bet
        return Node.decision(
            round_, actor, [("check", checked), ("bet", node(tuple(raised), other, raises + 1))]
        )

    return lambda c0, c1: node((c0, c1), 0, 0)


def kuhn() -> Game:
    cards = 3
    p0 = (np.ones((cards, cards)) - np.eye(cards)) / (cards * (cards - 1))
    share = {0: (np.arange(cards)[:, None] > np.arange(cards)[None, :]).astype(float)}
    root = _betting(0, 0, (1.0,), max_raises=1)(1.0, 1.0)
    return Game(states=(cards,), p0=p0, transitions=(None,), share=share, root=root)


KUHN_VALUE = -1 / 18


def leduc() -> Game:
    ranks = 3  # two of each in the deck
    p0 = np.array(
        [[(2 / 6) * ((1 if a == b else 2) / 5) for b in range(ranks)] for a in range(ranks)]
    )
    # Round 1 states are (private rank, public rank) as rank * 3 + public.
    move = np.zeros((ranks, ranks, ranks * ranks, ranks * ranks))
    share = np.full((ranks * ranks, ranks * ranks), 0.5)
    for a in range(ranks):
        for b in range(ranks):
            for public in range(ranks):
                left = 2 - (a == public) - (b == public)
                move[a, b, a * ranks + public, b * ranks + public] = left / 4
                if a == b:
                    result = 0.5
                elif a == public:
                    result = 1.0
                elif b == public:
                    result = 0.0
                else:
                    result = float(a > b)
                share[a * ranks + public, b * ranks + public] = result
    root = _betting(0, 1, (2.0, 4.0), max_raises=2)(1.0, 1.0)
    return Game(
        states=(ranks, ranks * ranks),
        p0=p0,
        transitions=(None, move),
        share={1: share},
        root=root,
    )
