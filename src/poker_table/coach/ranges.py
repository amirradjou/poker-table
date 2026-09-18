"""Preflop hand classes ("AKs", "T9o", "77"), range notation and the reference charts.

Charts are simplified 100bb 6-max ranges of the kind published in every training site's
free material — good enough to tag "outside the chart" without pretending to be a solver.
Edit them in one place (`CHARTS`) if you disagree.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from functools import cache

from poker_table.cards import Card, Rank, Suit

RANKS = "AKQJT98765432"  # high to low


def hand_class(hole: Sequence[Card]) -> str:
    """``[As, Kd]`` -> ``"AKo"``; ``[7c, 7h]`` -> ``"77"``; ``[Td, 9d]`` -> ``"T9s"``."""
    a, b = sorted(hole, key=lambda c: c.rank, reverse=True)
    if a.rank == b.rank:
        return f"{a.rank.char}{b.rank.char}"
    return f"{a.rank.char}{b.rank.char}{'s' if a.suit == b.suit else 'o'}"


def _rank_index(char: str) -> int:
    return RANKS.index(char)


def expand(notation: str) -> frozenset[str]:
    """Expand range notation into hand classes.

    Supported: ``"77"``, ``"22+"``, ``"55-99"``, ``"AKs"``, ``"A2s+"`` (kicker and up),
    ``"T9s-65s"`` (connectors down), ``"AJo+"``, comma separated. ``"*"`` means every hand.
    """
    classes: set[str] = set()
    for raw in notation.replace(" ", "").split(","):
        if not raw:
            continue
        if raw == "*":
            return frozenset(ALL_CLASSES)
        if "-" in raw:
            lo, hi = raw.split("-")
            classes |= _span(lo, hi)
        elif raw.endswith("+"):
            classes |= _plus(raw[:-1])
        else:
            _check(raw)
            classes.add(raw)
    return frozenset(classes)


def _check(cls: str) -> None:
    if cls not in ALL_CLASSES:
        raise ValueError(f"not a hand class: {cls!r}")


def _plus(cls: str) -> set[str]:
    _check(cls)
    if len(cls) == 2:  # pairs up to AA
        return {f"{r}{r}" for r in RANKS[: _rank_index(cls[0]) + 1]}
    high, low, suit = cls[0], cls[1], cls[2]
    # kicker up to one below the high card
    return {f"{high}{k}{suit}" for k in RANKS[_rank_index(high) + 1 : _rank_index(low) + 1]}


def _span(lo: str, hi: str) -> set[str]:
    _check(lo)
    _check(hi)
    if len(lo) == 2 and len(hi) == 2:
        a, b = sorted((_rank_index(lo[0]), _rank_index(hi[0])))
        return {f"{r}{r}" for r in RANKS[a : b + 1]}
    if len(lo) == 3 and len(hi) == 3 and lo[2] == hi[2]:
        gap_lo = _rank_index(lo[1]) - _rank_index(lo[0])
        gap_hi = _rank_index(hi[1]) - _rank_index(hi[0])
        if gap_lo == gap_hi:  # same gap: walk the high card down, e.g. T9s-65s
            a, b = sorted((_rank_index(lo[0]), _rank_index(hi[0])))
            out = set()
            for i in range(a, b + 1):
                if i + gap_lo < len(RANKS):
                    out.add(f"{RANKS[i]}{RANKS[i + gap_lo]}{lo[2]}")
            return out
        if lo[0] == hi[0]:  # same high card: kicker span, e.g. A9s-A5s
            a, b = sorted((_rank_index(lo[1]), _rank_index(hi[1])))
            return {f"{lo[0]}{RANKS[i]}{lo[2]}" for i in range(a, b + 1)}
    raise ValueError(f"cannot span {lo}-{hi}")


ALL_CLASSES: frozenset[str] = frozenset(
    {f"{a}{a}" for a in RANKS}
    | {f"{a}{b}s" for i, a in enumerate(RANKS) for b in RANKS[i + 1 :]}
    | {f"{a}{b}o" for i, a in enumerate(RANKS) for b in RANKS[i + 1 :]}
)


def combos(cls: str) -> int:
    if len(cls) == 2:
        return 6
    return 4 if cls.endswith("s") else 12


def range_fraction(classes: Iterable[str]) -> float:
    """Share of all 1326 combos covered by these classes."""
    return sum(combos(c) for c in set(classes)) / 1326


# ----- the reference charts -------------------------------------------------------------

# Raise-first-in by position (6-max; 9-max early seats fold to "UTG").
CHARTS: dict[str, dict[str, str]] = {
    "open": {
        "UTG": "22+, A2s+, K9s+, Q9s+, J9s+, T9s, 98s, 87s, ATo+, KQo",
        "HJ": "22+, A2s+, K8s+, Q9s+, J9s+, T8s+, 97s+, 87s, 76s, A9o+, KJo+, QJo",
        "CO": "22+, A2s+, K5s+, Q8s+, J8s+, T8s+, 97s+, 86s+, 75s+, 65s, A7o+, K9o+, QTo+, JTo",
        "BTN": (
            "22+, A2s+, K2s+, Q4s+, J6s+, T6s+, 96s+, 85s+, 74s+, 64s+, 53s+, "
            "A2o+, K7o+, Q9o+, J9o+, T9o"
        ),
        "SB": "22+, A2s+, K5s+, Q7s+, J8s+, T8s+, 97s+, 86s+, 75s+, 65s, A7o+, K9o+, QTo+, JTo",
    },
    # Facing one open raise: 3-bet for value (+ a few suited-ace bluffs), or call.
    "three_bet": {
        "*": "JJ+, AQs+, AKo, A5s, A4s",
    },
    "call": {
        # In position (CO, BTN vs an earlier open) you can call wider than from the blinds.
        "IP": "22-JJ, ATs+, KTs+, QTs+, JTs, T9s, 98s, 87s, AJo+, KQo",
        "SB": "66-JJ, AQs+, KQs, AQo+",
        "BB": (
            "22-JJ, A2s+, K5s+, Q8s+, J8s+, T8s+, 97s+, 86s+, 75s+, 65s, 54s, ATo+, KJo+, QJo, JTo"
        ),
    },
}

_POSITION_ALIAS = {
    "UTG": "UTG",
    "UTG+1": "UTG",
    "UTG+2": "UTG",
    "MP": "UTG",
    "LJ": "HJ",
    "HJ": "HJ",
    "CO": "CO",
    "BTN": "BTN",
    "BTN/SB": "BTN",
    "SB": "SB",
    "BB": "BB",
}


@cache
def chart(kind: str, key: str) -> frozenset[str]:
    table = CHARTS[kind]
    if key in table:
        return expand(table[key])
    if "*" in table:
        return expand(table["*"])
    raise KeyError(f"no {kind} chart for {key!r}")


def open_range(position: str) -> frozenset[str]:
    """RFI chart for a position label (BB has no open range; treat it as SB's)."""
    key = _POSITION_ALIAS.get(position, "UTG")
    return chart("open", "SB" if key == "BB" else key)


def three_bet_range(position: str = "*") -> frozenset[str]:
    return chart("three_bet", "*")


def call_range(position: str) -> frozenset[str]:
    key = _POSITION_ALIAS.get(position, "UTG")
    if key in ("SB", "BB"):
        return chart("call", key)
    return chart("call", "IP")


def defend_range(position: str) -> frozenset[str]:
    """Everything a seat should continue with facing an open: 3-bet plus call."""
    return three_bet_range() | call_range(position)


# ----- sampling hands from a range (for equity vs. a range) -------------------------------


def class_combos(cls: str) -> list[tuple[Card, Card]]:
    """Every specific two-card combination of a hand class."""
    a, b = Rank.from_char(cls[0]), Rank.from_char(cls[1])
    suits = list(Suit)
    if len(cls) == 2:
        return [(Card(a, s), Card(a, t)) for i, s in enumerate(suits) for t in suits[i + 1 :]]
    if cls[2] == "s":
        return [(Card(a, s), Card(b, s)) for s in suits]
    return [(Card(a, s), Card(b, t)) for s in suits for t in suits if s != t]


@cache
def _range_combos(classes: frozenset[str]) -> tuple[tuple[Card, Card], ...]:
    return tuple(combo for cls in sorted(classes) for combo in class_combos(cls))


def sample_from_range(
    classes: frozenset[str], dead: set[Card], rng: random.Random
) -> tuple[Card, Card] | None:
    """A random combo from the range that avoids ``dead`` cards, or None if impossible."""
    pool = _range_combos(classes)
    for _ in range(50):
        a, b = pool[rng.randrange(len(pool))]
        if a not in dead and b not in dead:
            return a, b
    live = [c for c in pool if c[0] not in dead and c[1] not in dead]
    return live[rng.randrange(len(live))] if live else None
