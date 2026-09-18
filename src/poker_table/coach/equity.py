"""Equity by Monte Carlo (or exact enumeration when cheap) and board-texture helpers."""

from __future__ import annotations

import itertools
import random
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache

from poker_table.agents.strength import MadeHand, classify, has_flush_draw, has_open_ender
from poker_table.cards import FULL_DECK, Card
from poker_table.coach.ranges import range_combos, sample_from_range
from poker_table.evaluator import evaluate

Combo = tuple[Card, Card]
Range = frozenset[str] | tuple[Combo, ...] | None  # hand classes, explicit combos, or any two


def equity(
    hole: Sequence[Card],
    board: Sequence[Card],
    opponents: Sequence[Range] | int = 1,
    *,
    samples: int = 400,
    seed: int = 0,
) -> float:
    """Chance to win the pot (ties split) against ``opponents`` holding random hands or ranges.

    ``opponents`` is a count (random hands) or one range per opponent. With one opponent on
    a full board the answer is enumerated exactly; everything else is sampled.
    """
    ranges: list[Range] = [None] * opponents if isinstance(opponents, int) else list(opponents)
    if not ranges:
        return 1.0
    hole_t = tuple(hole)
    board_t = tuple(board)
    if len(board_t) == 5 and len(ranges) == 1 and ranges[0] is None:
        return _exact_river_heads_up(hole_t, board_t)
    return _sample(hole_t, board_t, tuple(ranges), samples, seed)


@lru_cache(maxsize=4096)
def _exact_river_heads_up(hole: tuple[Card, ...], board: tuple[Card, ...]) -> float:
    dead = set(hole) | set(board)
    live = [c for c in FULL_DECK if c not in dead]
    mine = evaluate([*hole, *board])
    score = 0.0
    total = 0
    for a, b in itertools.combinations(live, 2):
        theirs = evaluate([a, b, *board])
        total += 1
        if mine > theirs:
            score += 1
        elif mine == theirs:
            score += 0.5
    return score / total


@lru_cache(maxsize=4096)
def _sample(
    hole: tuple[Card, ...],
    board: tuple[Card, ...],
    ranges: tuple[Range, ...],
    samples: int,
    seed: int,
) -> float:
    rng = random.Random(seed)
    dead_base = set(hole) | set(board)
    live_base = [c for c in FULL_DECK if c not in dead_base]
    score = 0.0
    done = 0
    attempts = 0
    while done < samples and attempts < samples * 4:
        attempts += 1
        dead = set(dead_base)
        hands: list[tuple[Card, Card]] = []
        ok = True
        for rng_cls in ranges:
            if rng_cls is None:
                pool = [c for c in live_base if c not in dead]
                a, b = rng.sample(pool, 2)
            else:
                combo = sample_from_range(rng_cls, dead, rng)
                if combo is None:
                    ok = False
                    break
                a, b = combo
            hands.append((a, b))
            dead |= {a, b}
        if not ok:
            continue
        pool = [c for c in live_base if c not in dead]
        run_out = rng.sample(pool, 5 - len(board))
        full = [*board, *run_out]
        mine = evaluate([*hole, *full])
        best_other = max(evaluate([a, b, *full]) for a, b in hands)
        if mine > best_other:
            score += 1
        elif mine == best_other:
            ties = 1 + sum(1 for a, b in hands if evaluate([a, b, *full]) == mine)
            score += 1 / ties
        done += 1
    return score / done if done else 0.0


# ----- narrowing a range by what its owner did on the board ---------------------------------


def narrow_range(
    rng: Range,
    board: Sequence[Card],
    dead: set[Card],
    *,
    keep_at_least: MadeHand,
    keep_air: float,
) -> tuple[Combo, ...] | None:
    """Combos of ``rng`` consistent with a bet or a call on this board.

    Keeps every combo that has ``keep_at_least`` or a draw, plus a fixed share of the rest
    (bluffs and floats), chosen deterministically so a report is reproducible. Returns None
    for "any two cards" when nothing is known, and the original combos when the filter would
    empty the range.
    """
    if len(board) < 3:
        return None if rng is None else tuple(range_combos(rng))
    return _narrow(rng, tuple(board), frozenset(dead), keep_at_least, keep_air)


@lru_cache(maxsize=4096)
def _narrow(
    rng: Range,
    board: tuple[Card, ...],
    dead: frozenset[Card],
    keep_at_least: MadeHand,
    keep_air: float,
) -> tuple[Combo, ...]:
    combos = list(range_combos(rng)) if rng is not None else _all_combos()
    combos = [c for c in combos if c[0] not in dead and c[1] not in dead]
    kept: list[Combo] = []
    for combo in combos:
        strong = classify(combo, board) >= keep_at_least
        draw = has_flush_draw(combo, board) or has_open_ender(combo, board)
        if strong or draw or _keep(combo, board, keep_air):
            kept.append(combo)
    return tuple(kept) if kept else tuple(combos)


def _keep(combo: Combo, board: Sequence[Card], share: float) -> bool:
    key = (str(combo[0]) + str(combo[1]) + "".join(str(c) for c in board)).encode()
    return (sum(key) * 2654435761 % 1000) < share * 1000


@lru_cache(maxsize=1)
def _all_combos() -> list[Combo]:
    return [(a, b) for a, b in itertools.combinations(FULL_DECK, 2)]


def pot_odds(to_call: int, pot: int) -> float:
    """Share of the final pot you are paying to call: to_call / (pot + to_call)."""
    return 0.0 if to_call <= 0 else to_call / (pot + to_call)


# ----- board texture --------------------------------------------------------------------


def board_texture(board: Sequence[Card]) -> str:
    """``"dry"``, ``"wet"`` or ``"paired"`` — the coarse split a coach talks about."""
    if len(board) < 3:
        return "preflop"
    ranks = sorted({int(c.rank) for c in board})
    if len(ranks) < len(board):
        return "paired"
    suits = Counter(c.suit for c in board)
    flushy = max(suits.values()) >= 2 if len(board) == 3 else max(suits.values()) >= 3
    connected = any(ranks[i + 2] - ranks[i] <= 4 for i in range(len(ranks) - 2))
    if 14 in ranks:  # the ace also plays low for wheel draws
        low = sorted(set(ranks) | {1})
        connected = connected or any(low[i + 2] - low[i] <= 4 for i in range(len(low) - 2))
    return "wet" if flushy or connected else "dry"
