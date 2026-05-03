"""Turn a pile of facts into the recurring leaks, with the hands that prove them."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from poker_table.coach.facts import TITLES, Fact
from poker_table.history import HandHistory


@dataclass(slots=True)
class Leak:
    tag: str
    title: str
    leaks: int
    opportunities: int
    by_position: dict[str, tuple[int, int]]
    by_street: dict[str, tuple[int, int]]
    examples: list[Fact]

    @property
    def rate(self) -> float:
        return self.leaks / self.opportunities if self.opportunities else 0.0


@dataclass(slots=True)
class Report:
    player: str
    hands: int
    decisions: int
    net: int
    big_blind: int
    leaks: list[Leak]
    observations: list[str]
    trend: list[str] = field(default_factory=list)  # first half vs second half of the session
    facts: list[Fact] = field(default_factory=list)

    @property
    def bb_per_100(self) -> float:
        return (
            0.0
            if not self.hands or not self.big_blind
            else 100 * self.net / self.big_blind / self.hands
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "player": self.player,
            "hands": self.hands,
            "decisions": self.decisions,
            "net": self.net,
            "bb_per_100": round(self.bb_per_100, 2),
            "leaks": [
                {
                    "tag": leak.tag,
                    "title": leak.title,
                    "leaks": leak.leaks,
                    "opportunities": leak.opportunities,
                    "rate": round(leak.rate, 3),
                    "by_position": leak.by_position,
                    "by_street": leak.by_street,
                    "examples": [f.to_dict() for f in leak.examples],
                }
                for leak in self.leaks
            ],
            "observations": self.observations,
            "trend": self.trend,
            "facts": [f.to_dict() for f in self.facts],
        }

    def render(self, *, top: int = 5, examples: int = 4) -> str:
        lines = [
            f"Coach report for {self.player} — {self.hands} hands, "
            f"{self.decisions} decisions tagged",
            f"Bankroll: {self.net:+} chips ({self.bb_per_100:+.1f} bb/100)",
            "",
        ]
        if not self.leaks:
            lines.append("No leaks found by the charts and the math. Play more hands.")
        else:
            lines.append("Recurring leaks (most costly first)")
            for i, leak in enumerate(self.leaks[:top], 1):
                lines.append(
                    f"{i}. {leak.title} — {leak.leaks} of {leak.opportunities} ({leak.rate:.0%})"
                )
                where = ", ".join(f"{pos} {n}/{d}" for pos, (n, d) in leak.by_position.items() if n)
                if where:
                    lines.append(f"     by position: {where}")
                for f in leak.examples[:examples]:
                    lines.append(f"     #{f.hand_id} {f.street} {f.position}: {f.detail}")
        if self.observations:
            lines.append("")
            lines.append("Observations")
            lines += [f"- {o}" for o in self.observations]
        if self.trend:
            lines.append("")
            lines.append("Trend (first half of the session vs second half)")
            lines += [f"- {t}" for t in self.trend]
        return "\n".join(lines)


_MIN_OPPORTUNITIES = 3


def build_report(
    histories: Sequence[HandHistory], player: str, facts: Iterable[Fact], *, keep_facts: bool = True
) -> Report:
    facts = list(facts)
    seats = [(h, p) for h in histories for p in h.players if p.name == player]
    net = sum(p.net for _, p in seats)
    big_blind = histories[0].big_blind if histories else 0

    leaks: list[Leak] = []
    graded = [f for f in facts if f.ok is not None]
    for tag in TITLES:
        of_tag = [f for f in graded if f.tag == tag]
        if len(of_tag) < _MIN_OPPORTUNITIES:
            continue
        bad = [f for f in of_tag if f.ok is False]
        if not bad:
            continue
        leaks.append(
            Leak(
                tag=tag,
                title=TITLES[tag],
                leaks=len(bad),
                opportunities=len(of_tag),
                by_position=_split(of_tag, "position"),
                by_street=_split(of_tag, "street"),
                examples=sorted(bad, key=_severity, reverse=True),
            )
        )
    # Most costly first: how often it happens matters more than the rate on tiny samples.
    leaks.sort(key=lambda lk: (lk.leaks * lk.rate, lk.leaks), reverse=True)

    return Report(
        player=player,
        hands=len(seats),
        decisions=len(facts),
        net=net,
        big_blind=big_blind,
        leaks=leaks,
        observations=_observations(facts),
        trend=_trend(histories, facts, leaks),
        facts=facts if keep_facts else [],
    )


_MIN_HANDS_FOR_TREND = 40


def _trend(histories: Sequence[HandHistory], facts: list[Fact], leaks: list[Leak]) -> list[str]:
    """Leak rates in the first half of the session against the second — did it get better?"""
    if len(histories) < _MIN_HANDS_FOR_TREND or not leaks:
        return []
    ids = [h.hand_id for h in histories]
    first = set(ids[: len(ids) // 2])
    out: list[str] = []
    for leak in leaks:
        of_tag = [f for f in facts if f.tag == leak.tag and f.ok is not None]
        a = [f for f in of_tag if f.hand_id in first]
        b = [f for f in of_tag if f.hand_id not in first]
        if len(a) < 3 or len(b) < 3:
            continue
        ra = sum(f.ok is False for f in a) / len(a)
        rb = sum(f.ok is False for f in b) / len(b)
        verdict = "better" if rb < ra - 0.05 else "worse" if rb > ra + 0.05 else "no change"
        out.append(f"{leak.title}: {ra:.0%} → {rb:.0%} ({verdict})")
    return out


def _split(facts: list[Fact], attr: str) -> dict[str, tuple[int, int]]:
    denominators: Counter[str] = Counter(getattr(f, attr) for f in facts)
    numerators: Counter[str] = Counter(getattr(f, attr) for f in facts if f.ok is False)
    return {key: (numerators[key], denominators[key]) for key in denominators}


def _severity(fact: Fact) -> float:
    """Bigger pots and bigger equity/price gaps make better examples."""
    gap = abs((fact.equity or 0) - (fact.price or 0))
    return fact.pot + fact.to_call + 100 * gap


def _observations(facts: list[Fact]) -> list[str]:
    out: list[str] = []
    cbets = [f for f in facts if f.tag == "cbet"]
    if cbets:
        by_texture: dict[str, list[Fact]] = defaultdict(list)
        for f in cbets:
            by_texture[f.texture].append(f)
        parts = []
        for texture, fs in sorted(by_texture.items()):
            bets = sum(1 for f in fs if not f.action.startswith("check"))
            parts.append(f"{bets / len(fs):.0%} on {texture} flops ({bets}/{len(fs)})")
        out.append("c-bet frequency: " + ", ".join(parts))
    bets = [
        f
        for f in facts
        if f.tag in ("bet", "cbet", "raise_vs_bet") and not f.action.startswith("check")
    ]
    if bets:
        bluffs = sum(1 for f in bets if (f.equity or 1) < 0.3)
        out.append(
            f"bluffs: {bluffs / len(bets):.0%} of postflop bets and raises ({bluffs}/{len(bets)})"
        )
    opens = [f for f in facts if f.tag in ("open", "fold_first_in")]
    if opens:
        raised = sum(1 for f in opens if f.tag == "open")
        out.append(
            f"raise-first-in: {raised / len(opens):.0%} of unopened pots ({raised}/{len(opens)})"
        )
    calls = [f for f in facts if f.tag == "call_vs_bet"]
    if calls:
        avg = sum(f.equity or 0 for f in calls) / len(calls)
        out.append(f"average equity when calling a bet: {avg:.0%} over {len(calls)} calls")
    return out
