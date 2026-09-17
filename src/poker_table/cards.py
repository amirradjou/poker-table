"""Cards, ranks, suits and a seeded deck."""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum, StrEnum


class Suit(StrEnum):
    CLUBS = "c"
    DIAMONDS = "d"
    HEARTS = "h"
    SPADES = "s"

    @property
    def symbol(self) -> str:
        return {"c": "♣", "d": "♦", "h": "♥", "s": "♠"}[self.value]


class Rank(IntEnum):
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
    JACK = 11
    QUEEN = 12
    KING = 13
    ACE = 14

    @property
    def char(self) -> str:
        return "23456789TJQKA"[self.value - 2]

    @classmethod
    def from_char(cls, char: str) -> Rank:
        index = "23456789TJQKA".find(char.upper())
        if index < 0:
            raise ValueError(f"not a rank: {char!r}")
        return cls(index + 2)


@dataclass(frozen=True, order=True, slots=True)
class Card:
    rank: Rank
    suit: Suit

    def __str__(self) -> str:
        return f"{self.rank.char}{self.suit.value}"

    def __repr__(self) -> str:
        return f"Card({self})"

    @property
    def pretty(self) -> str:
        return f"{self.rank.char}{self.suit.symbol}"

    @classmethod
    def parse(cls, text: str) -> Card:
        """Parse the standard two-character notation, e.g. ``"As"`` or ``"Td"``."""
        if len(text) != 2:
            raise ValueError(f"not a card: {text!r}")
        return cls(Rank.from_char(text[0]), Suit(text[1].lower()))


def parse_cards(text: str) -> list[Card]:
    """Parse ``"As Kd"`` or ``"AsKd"`` into a list of cards."""
    compact = text.replace(" ", "").replace(",", "")
    return [Card.parse(compact[i : i + 2]) for i in range(0, len(compact), 2)]


def cards_str(cards: Iterable[Card]) -> str:
    return " ".join(str(card) for card in cards)


FULL_DECK: tuple[Card, ...] = tuple(Card(rank, suit) for suit in Suit for rank in Rank)


class Deck:
    """A 52-card deck shuffled by a private, seeded RNG.

    The same seed always yields the same order, which is what makes a hand replayable and
    lets a league log the seed instead of the whole deck.
    """

    def __init__(self, seed: int | None = None) -> None:
        self.seed = seed
        self._rng = random.Random(seed)
        self._cards: list[Card] = list(FULL_DECK)
        self._rng.shuffle(self._cards)

    def __len__(self) -> int:
        return len(self._cards)

    def draw(self) -> Card:
        if not self._cards:
            raise RuntimeError("deck is empty")
        return self._cards.pop()

    def deal(self, count: int) -> list[Card]:
        return [self.draw() for _ in range(count)]
