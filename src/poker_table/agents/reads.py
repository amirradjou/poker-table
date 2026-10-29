"""What a seat has learned about the others: a HUD built from the hands it was shown.

Everything here comes from ``history.public_view`` — the actions, the cards turned over at
showdown, the table talk — so a read is exactly what a sharp player at the table could have
written down, and nothing a player could not.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # imported for types only: history imports the agents package
    from poker_table.history import HandHistory
    from poker_table.stats import PlayerStats

MIN_HANDS = 12  # fewer hands than this and a style label is noise: show the counts only
LOOSE, TIGHT = 0.35, 0.20  # share of hands played (VPIP)
AGGRESSIVE, PASSIVE = 0.55, 0.30  # share of the hands played that were raised first (PFR/VPIP)


@dataclass(frozen=True, slots=True)
class Shown:
    """A hand an opponent turned over, and how they played it after the flop."""

    hand_id: str
    cards: str
    made: str
    line: str
    won: bool

    def describe(self) -> str:
        how = f"{self.line}; " if self.line else ""
        result = "won" if self.won else "lost"
        return f"{self.cards} for {self.made} ({how}{result}, hand {self.hand_id})"


class Reads:
    """Per-opponent stats and shown hands, over the last ``remember`` hands a seat saw."""

    def __init__(self, me: str, *, remember: int = 300, shown: int = 2) -> None:
        self.me = me
        self.hands: deque[HandHistory] = deque(maxlen=remember)
        self.shown: dict[str, deque[Shown]] = {}
        self._keep_shown = shown
        self._stats: dict[str, PlayerStats] | None = None

    def observe(self, history: HandHistory) -> None:
        self.hands.append(history)
        self._stats = None
        for seat, made in history.showdown.items():
            player = history.players[seat]
            if player.name == self.me or len(player.hole) != 2:
                continue
            shown = Shown(
                hand_id=history.hand_id,
                cards=" ".join(player.hole),
                made=made,
                line=_postflop_line(history, seat),
                won=history.payouts.get(seat, 0) > 0,
            )
            self.shown.setdefault(player.name, deque(maxlen=self._keep_shown)).append(shown)

    @property
    def stats(self) -> dict[str, PlayerStats]:
        if self._stats is None:
            from poker_table.stats import compute_stats

            self._stats = compute_stats(self.hands)
        return self._stats

    def about(self, name: str) -> str:
        """A few short sentences on ``name``: sample, style, the numbers behind it, shown hands."""
        s = self.stats.get(name)
        if s is None or s.hands == 0:
            return f"{name}: no hands seen yet."
        style = style_of(s)
        head = f"{name} ({s.hands} hand{'' if s.hands == 1 else 's'})" + (
            f": {style}." if style else "."
        )
        preflop = [f"plays {s.vpip:.0%}", f"raises first {s.pfr:.0%}"]
        if s.three_bet_opps >= 5:
            preflop.append(f"3-bets {s.three_bet:.0%}")
        sentences = [head, _sentence(preflop)]
        postflop = []
        if s.postflop_bets_raises + s.postflop_calls >= 5:
            af = s.aggression_factor
            postflop.append("never calls" if af == float("inf") else f"aggression {af:.1f}")
        faced = s.postflop_folds + s.postflop_calls
        if faced >= 5:
            postflop.append(f"folds to a bet {s.postflop_folds / faced:.0%}")
        if s.saw_flop >= 5:
            postflop.append(f"goes to showdown {s.wtsd:.0%}")
        if s.bluffs:
            postflop.append(f"bet with air {_times(s.bluffs)} in hands it showed")
        if postflop:
            sentences.append("After the flop: " + ", ".join(postflop) + ".")
        shown = self.shown.get(name)
        if shown:
            sentences.append("Showed " + "; ".join(h.describe() for h in shown) + ".")
        return " ".join(sentences)

    def describe(self, names: Iterable[str]) -> list[str]:
        return [self.about(name) for name in names if name != self.me]


def style_of(s: PlayerStats) -> str:
    """The two-word label players use (tight-aggressive, loose-passive ...), or ""."""
    if s.hands < MIN_HANDS or s.vpip is None or s.pfr is None:
        return ""
    width = "loose" if s.vpip >= LOOSE else "tight" if s.vpip <= TIGHT else "medium"
    share = s.pfr / s.vpip if s.vpip else 0.0
    temper = "aggressive" if share >= AGGRESSIVE else "passive" if share <= PASSIVE else "balanced"
    label = f"{width}-{temper}"
    if s.saw_flop >= MIN_HANDS // 2 and (s.wtsd or 0) >= 0.45 and temper == "passive":
        label += ", calls down"
    return label


def _postflop_line(history: HandHistory, seat: int) -> str:
    """``"flop bet, turn call, river check"`` — what one seat did after the flop."""
    steps = []
    for e in history.events:
        if e.kind != "action" or e.seat != seat or e.street == "preflop" or e.action is None:
            continue
        verb = e.action.split(" ")[0]
        steps.append(f"{e.street} {verb}")
    return ", ".join(steps)


def _sentence(parts: list[str]) -> str:
    text = ", ".join(parts)
    return text[0].upper() + text[1:] + "."


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")
