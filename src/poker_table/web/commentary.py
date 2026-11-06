"""A broadcast's commentary: a line when something happens at the table, silence otherwise.

No model is involved, so it costs nothing and runs in a static export. It reads the same win
chances the seats' nameplates show, and it says what everyone's cards mean, so it follows the same
rule as those numbers: never at a table with a human seated.
"""

from __future__ import annotations

from typing import Any

from poker_table.cards import Card

FOLD_WORTH_A_LINE = 0.40  # folding the best hand of five at 25% is routine; at 40% it is news
SWING_WORTH_A_LINE = 0.30  # a card has to move a hand this far to have turned the hand around
LONG_SHOT = 0.25
NUMBERS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def _card(card: Card | str) -> str:
    """A card as the table draws it: the ten is 10, not T."""
    card = Card.parse(card) if isinstance(card, str) else card
    return card.pretty.replace("T", "10")


def _article(percent: str) -> str:
    """ "An 11% shot", "a 12% shot", "an 8% shot"."""
    spoken_with_a_vowel = percent.startswith("8") or percent in ("11%", "18%")
    return "An" if spoken_with_a_vowel else "A"


def _percent(share: float) -> str:
    # A hand that can still win never reads as 0%, and one that can still lose never as 100%.
    pct = round(share * 100)
    if 0 < share < 1:
        pct = min(max(pct, 1), 99)
    return f"{pct}%"


class Commentator:
    """Turns one hand's steps into lines. ``take`` is called once per step, in order."""

    def __init__(self, holes: dict[int, tuple[Card, Card]], names: dict[int, str]) -> None:
        self.holes = holes
        self.names = names
        self.bet_made = False  # a fold only says something once there was a bet to fold to

    def take(
        self,
        position: int,
        step: dict[str, Any],
        before: dict[str, float],
        after: dict[str, float],
    ) -> dict[str, Any] | None:
        """The line for the step that brings the viewer to ``position``, if it earns one.

        ``before`` and ``after`` are everyone's share of the pot either side of the step.
        """
        text = None
        if step["kind"] == "action":
            text = self._action(step, before)
        elif step["kind"] == "street":
            text = self._cards(step, before, after)
        return {"step": position, "text": text} if text else None

    def _hole(self, seat: int) -> str:
        high_first = sorted(self.holes[seat], key=lambda c: c.rank, reverse=True)
        return " ".join(_card(c) for c in high_first)

    def _action(self, step: dict[str, Any], before: dict[str, float]) -> str | None:
        seat = step["seat"]
        share = before.get(str(seat))
        if share is None or len(before) < 2:
            return None
        verb = step["action"].split()[0]
        name = self.names[seat]
        if verb == "fold":
            return self._fold(seat, share, before) if self.bet_made else None
        self.bet_made = self.bet_made or verb in ("bet", "raise")
        if not step["all_in"]:
            return None
        chance = self._chance(share, len(before) - 1)
        if verb in ("bet", "raise"):
            return f"{name} moves all in with {self._hole(seat)}, {chance}."
        if verb == "call":
            return f"{name} calls all in with {self._hole(seat)}, {chance}."
        return None

    def _chance(self, share: float, others: int) -> str:
        if share == 0:
            return "drawing dead"
        if others == 1:
            return f"{_percent(share)} to win"
        return f"{_percent(share)} against the other {NUMBERS.get(others, others)}"

    def _fold(self, seat: int, share: float, before: dict[str, float]) -> str | None:
        if share < FOLD_WORTH_A_LINE or share < max(before.values()):
            return None
        text = f"{self.names[seat]} folds the best hand: {self._hole(seat)} was {_percent(share)}"
        rest = [int(s) for s in before if int(s) != seat]
        if len(rest) == 1:
            return f"{text}, and {self.names[rest[0]]} takes it with {self._hole(rest[0])}."
        return f"{text}."

    def _cards(
        self, step: dict[str, Any], before: dict[str, float], after: dict[str, float]
    ) -> str | None:
        cards = " ".join(_card(c) for c in step["cards"])
        verb = "s" if len(step["cards"]) == 1 else ""  # the turn turns it; the flop's cards turn it
        river = step["street"] == "river"
        top = max(after.values())
        leaders = [s for s, v in after.items() if v == top]
        if len(leaders) == 1 and before[leaders[0]] < max(before.values()):
            rise = top - before[leaders[0]]
            seat = int(leaders[0])
            if rise >= SWING_WORTH_A_LINE:
                was = _percent(before[leaders[0]])
                if river and before[leaders[0]] <= LONG_SHOT:
                    shot = f"{_article(was)} {was} shot"
                    return f"{shot} comes in: the {cards} gives {self.names[seat]} the pot."
                if river:
                    return f"The {cards} on the river gives {self.names[seat]} the pot, from {was}."
                move = f"{self.names[seat]} goes from {was} to {_percent(top)}"
                return f"The {cards} turn{verb} it around: {move}."
        if river:
            return None
        dead = [s for s, v in after.items() if v == 0 and before[s] >= 0.2]
        if len(dead) == 1:
            return f"The {cards} leave{verb} {self.names[int(dead[0])]} drawing dead."
        return None
