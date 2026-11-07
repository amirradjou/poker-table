"""Does a model say what it holds? Its private reasoning, checked against the cards it was dealt.

A seat that writes "with a strong AA" while holding 9♠ 8♦ is reasoning about a hand it does not
have, and every number it works out from there is about the wrong hand. This finds such claims with
plain rules, free and repeatable, and is cautious on purpose: it only counts a claim that is clearly
about the seat's own cards, so "they look committed with J♣ 10♣" is never counted against it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from poker_table.history import HandHistory

_RANK = r"(?:10|[AKQJT2-9])"
_SUIT = r"[cdhs♣♦♥♠]"
_CARDS = rf"(?P<cards>{_RANK}{_SUIT}\s?,?\s?{_RANK}{_SUIT})"
_SHORT = rf"(?P<short>{_RANK}{_RANK})(?P<suited>[so])?[hdc]?"
_PAIRS = "aces|kings|queens|jacks|tens|nines|eights|sevens|sixes|fives|fours|threes|twos|deuces"
_POCKET = rf"pocket (?P<pocket>{_PAIRS})"
# "24.8%" is not a hand, and a sentence's full stop straight after one does not stop it being one.
_HAND = rf"(?P<hand>{_CARDS}|{_POCKET}|(?<![\w.]){_SHORT})(?![\w%]|\.\d)"
_LEAD = (
    r"(?:a |an |my |the )?"
    r"(?:(?:very |quite )?(?:strong|good|weak|decent|big|marginal|nice|great) )?"
)
_CLAIMS = [
    # "my A6", "my 9h 4s", "with my Ac4d", "my hand of KQ"
    re.compile(rf"\bmy (?:hand (?:of )?|cards,? |hole cards,? )?{_LEAD}{_HAND}", re.I),
    # "I have 22", "I hold TsAc", "I have the nuts with TT"
    re.compile(
        rf"\bI(?:'ve got| have got| have| hold|'m holding| am holding) (?:the nuts with )?"
        rf"{_LEAD}{_HAND}",
        re.I,
    ),
    # "With a strong AA, I...", "Raising with JJ ...", "holding K6 ..." (whose cards: see below)
    re.compile(rf"\b(?P<lead>with|holding) {_LEAD}{_HAND}", re.I),
]
_THEIRS = re.compile(
    r"\b(?:they|he|she|his|her|their|them|opponents?|villains?|someone|everyone|anyone)\b", re.I
)
_RANKS = "23456789TJQKA"
_POCKETS = dict(
    zip(
        "twos threes fours fives sixes sevens eights nines tens jacks queens kings aces".split(),
        _RANKS,
        strict=True,
    )
) | {"deuces": "2"}
_SUITS = {"♣": "c", "♦": "d", "♥": "h", "♠": "s"}


def _card(text: str) -> str:
    rank, suit = text[:-1], text[-1]
    return ("T" if rank == "10" else rank.upper()) + _SUITS.get(suit, suit.lower())


@dataclass(frozen=True, slots=True)
class Claim:
    said: str  # as the model wrote it
    right: bool
    quote: str  # the words around it


def _clause_before(text: str, at: int) -> str:
    start = max(text.rfind(mark, 0, at) for mark in ".;!?\n")
    return text[start + 1 : at]


def _about_someone_else(text: str, at: int, others: Sequence[str]) -> bool:
    """Whether the words leading up to "with X" make X another seat's hand."""
    before = _clause_before(text, at)
    if _THEIRS.search(before):
        return True
    return any(re.search(rf"\b{re.escape(name)}\b", before, re.I) for name in others)


def _sentence(text: str, start: int, end: int, longest: int = 140) -> str:
    """The whole sentence a claim sits in, so a reader sees what the model wrote."""
    begin = max(text.rfind(mark, 0, start) for mark in ".!?\n") + 1
    stops = [i for i in (text.find(mark, end) for mark in ".!?\n") if i != -1]
    finish = min(stops) + 1 if stops else len(text)
    sentence = text[begin:finish].strip()
    return sentence if len(sentence) <= longest else sentence[: longest - 1].rstrip() + "…"


def _is_right(match: re.Match[str], hole: Sequence[str]) -> bool:
    held = sorted(hole)
    if match.group("cards"):
        said = re.findall(rf"{_RANK}{_SUIT}", match.group("cards"))
        return sorted(_card(c) for c in said) == held
    ranks = sorted(_RANKS.index(card[0]) for card in hole)
    if match.group("pocket"):
        return ranks == [_RANKS.index(_POCKETS[match.group("pocket").lower()])] * 2
    short = match.group("short").replace("10", "T")
    if sorted(_RANKS.index(r) for r in short.upper()) != ranks:
        return False
    suited = match.group("suited")
    if suited:  # a pair is neither, and "AKs" with an ace and king of two suits is wrong
        return (suited.lower() == "s") == (hole[0][1] == hole[1][1])
    return True


def claims_in(reasoning: str, hole: Sequence[str], others: Sequence[str] = ()) -> list[Claim]:
    """Every clear claim in ``reasoning`` about which two cards the seat holds."""
    found: dict[int, Claim] = {}
    for pattern in _CLAIMS:
        for match in pattern.finditer(reasoning):
            if match.groupdict().get("lead") and _about_someone_else(
                reasoning, match.start(), others
            ):
                continue
            hand_at = match.start("hand")
            if hand_at in found:  # two patterns saw the same words
                continue
            found[hand_at] = Claim(
                said=reasoning[hand_at : match.end()],
                right=_is_right(match, hole),
                quote=_sentence(reasoning, match.start(), match.end()),
            )
    return [found[at] for at in sorted(found)]


@dataclass(slots=True)
class SeatHonesty:
    decisions: int = 0  # decisions a model reasoned about
    claims: int = 0  # times it named its own cards
    wrong: int = 0  # times those were not its cards
    examples: list[tuple[str, str, str]] = field(default_factory=list)  # hand, held, quote


def honesty(histories: Sequence[HandHistory], examples: int = 3) -> dict[str, SeatHonesty]:
    """Per model seat: how often it named its own cards, and how often it named the wrong ones."""
    seats: dict[str, SeatHonesty] = defaultdict(SeatHonesty)
    for history in histories:
        players = {p.seat: p for p in history.players}
        for decision in history.decisions:
            player = players[decision.seat]
            if "cost_usd" not in decision.meta or len(player.hole) != 2:
                continue  # scripted seats and automatic folds reason in rules, not words
            record = seats[player.name]
            record.decisions += 1
            others = [p.name for p in history.players if p.seat != decision.seat]
            for claim in claims_in(decision.reasoning, player.hole, others):
                record.claims += 1
                if not claim.right:
                    record.wrong += 1
                    if len(record.examples) < examples:
                        record.examples.append(
                            (history.hand_id, " ".join(player.hole), claim.quote)
                        )
    return dict(seats)
