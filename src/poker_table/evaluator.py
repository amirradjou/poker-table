"""Hand evaluation: the best five-card hand out of five to seven cards."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum

from poker_table.cards import Card, Rank, Suit


class HandCategory(IntEnum):
    HIGH_CARD = 0
    PAIR = 1
    TWO_PAIR = 2
    THREE_OF_A_KIND = 3
    STRAIGHT = 4
    FLUSH = 5
    FULL_HOUSE = 6
    FOUR_OF_A_KIND = 7
    STRAIGHT_FLUSH = 8


_PLURAL = {
    Rank.TWO: "twos",
    Rank.THREE: "threes",
    Rank.FOUR: "fours",
    Rank.FIVE: "fives",
    Rank.SIX: "sixes",
    Rank.SEVEN: "sevens",
    Rank.EIGHT: "eights",
    Rank.NINE: "nines",
    Rank.TEN: "tens",
    Rank.JACK: "jacks",
    Rank.QUEEN: "queens",
    Rank.KING: "kings",
    Rank.ACE: "aces",
}
_SINGULAR = {rank: name[:-1] if name != "sixes" else "six" for rank, name in _PLURAL.items()}


@dataclass(frozen=True, order=True, slots=True)
class HandRank:
    """A comparable hand strength. Higher compares greater; equal means a chopped pot."""

    category: HandCategory
    kickers: tuple[int, ...]
    best: tuple[Card, ...] = field(compare=False)

    def describe(self) -> str:
        k = [Rank(v) for v in self.kickers]
        match self.category:
            case HandCategory.STRAIGHT_FLUSH:
                if k[0] is Rank.ACE:
                    return "royal flush"
                return f"straight flush, {_SINGULAR[k[0]]} high"
            case HandCategory.FOUR_OF_A_KIND:
                return f"four of a kind, {_PLURAL[k[0]]}"
            case HandCategory.FULL_HOUSE:
                return f"full house, {_PLURAL[k[0]]} full of {_PLURAL[k[1]]}"
            case HandCategory.FLUSH:
                return f"flush, {_SINGULAR[k[0]]} high"
            case HandCategory.STRAIGHT:
                return f"straight, {_SINGULAR[k[0]]} high"
            case HandCategory.THREE_OF_A_KIND:
                return f"three of a kind, {_PLURAL[k[0]]}"
            case HandCategory.TWO_PAIR:
                return f"two pair, {_PLURAL[k[0]]} and {_PLURAL[k[1]]}"
            case HandCategory.PAIR:
                return f"a pair of {_PLURAL[k[0]]}"
            case HandCategory.HIGH_CARD:
                return f"{_SINGULAR[k[0]]} high"
        raise AssertionError(self.category)


def _straight_high(ranks: Iterable[int]) -> int | None:
    """Highest top rank of a five-long run among ``ranks`` (ace also plays low), or None."""
    present = set(ranks)
    if Rank.ACE in present:
        present.add(1)
    for high in range(Rank.ACE, 4, -1):
        if all(r in present for r in range(high - 4, high + 1)):
            return high
    return None


def _straight_cards(cards: Sequence[Card], high: int) -> tuple[Card, ...]:
    wanted = [r if r > 1 else int(Rank.ACE) for r in range(high, high - 5, -1)]
    by_rank = {}
    for card in cards:
        by_rank.setdefault(int(card.rank), card)
    return tuple(by_rank[r] for r in wanted)


def evaluate(cards: Sequence[Card]) -> HandRank:
    """Rank the best five-card hand available in ``cards`` (5 to 7 cards)."""
    if not 5 <= len(cards) <= 7:
        raise ValueError(f"evaluate needs 5-7 cards, got {len(cards)}")
    if len(set(cards)) != len(cards):
        raise ValueError("duplicate cards")

    desc = sorted(cards, key=lambda c: c.rank, reverse=True)
    by_suit: dict[Suit, list[Card]] = defaultdict(list)
    for card in desc:
        by_suit[card.suit].append(card)
    flush_cards = next((cs for cs in by_suit.values() if len(cs) >= 5), None)

    if flush_cards is not None:
        high = _straight_high(c.rank for c in flush_cards)
        if high is not None:
            return HandRank(
                HandCategory.STRAIGHT_FLUSH, (high,), _straight_cards(flush_cards, high)
            )

    counts = Counter(c.rank for c in desc)
    # Groups ordered by count, then rank, both descending: quads, trips, pairs, singles.
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    of_rank = lambda rank: [c for c in desc if c.rank == rank]  # noqa: E731
    others = lambda *used: [c for c in desc if c.rank not in used]  # noqa: E731

    if groups[0][1] == 4:
        quad = groups[0][0]
        kicker = others(quad)[0]
        return HandRank(
            HandCategory.FOUR_OF_A_KIND,
            (quad, kicker.rank),
            (*of_rank(quad), kicker),
        )

    if groups[0][1] == 3 and groups[1][1] >= 2:
        trips, pair = groups[0][0], groups[1][0]
        return HandRank(
            HandCategory.FULL_HOUSE,
            (trips, pair),
            (*of_rank(trips), *of_rank(pair)[:2]),
        )

    if flush_cards is not None:
        top = tuple(flush_cards[:5])
        return HandRank(HandCategory.FLUSH, tuple(c.rank for c in top), top)

    high = _straight_high(c.rank for c in desc)
    if high is not None:
        return HandRank(HandCategory.STRAIGHT, (high,), _straight_cards(desc, high))

    if groups[0][1] == 3:
        trips = groups[0][0]
        kickers = others(trips)[:2]
        return HandRank(
            HandCategory.THREE_OF_A_KIND,
            (trips, *(c.rank for c in kickers)),
            (*of_rank(trips), *kickers),
        )

    if groups[0][1] == 2 and groups[1][1] == 2:
        hi, lo = groups[0][0], groups[1][0]
        kicker = others(hi, lo)[0]
        return HandRank(
            HandCategory.TWO_PAIR,
            (hi, lo, kicker.rank),
            (*of_rank(hi), *of_rank(lo), kicker),
        )

    if groups[0][1] == 2:
        pair = groups[0][0]
        kickers = others(pair)[:3]
        return HandRank(
            HandCategory.PAIR,
            (pair, *(c.rank for c in kickers)),
            (*of_rank(pair), *kickers),
        )

    top = tuple(desc[:5])
    return HandRank(HandCategory.HIGH_CARD, tuple(c.rank for c in top), top)


def best_hands(hands: dict[str, Sequence[Card]]) -> list[str]:
    """Return the key(s) holding the strongest hand — several keys means a chop."""
    ranked = {key: evaluate(cards) for key, cards in hands.items()}
    top = max(ranked.values())
    return [key for key, rank in ranked.items() if rank == top]
