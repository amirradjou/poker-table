"""PokerStars-style text hand histories (PokerStars, GGPoker) -> HandHistory.

GGPoker exports the same dialect with a ``Poker Hand #HD…`` header, amounts like ``$0.1``, a
hero literally named ``Hero``, and a ``Dealt to`` line for every player whose cards were
revealed; all of that is handled here rather than in a second parser.

Money is stored in the smallest unit seen in the file (cents when any amount has decimals),
so $0.05/$0.10 becomes blinds 5/10. Villains' hole cards are known only when shown. Antes,
straddles, dead blinds and missed-blind posts are not supported: such hands are skipped and
reported, because the engine could not replay them faithfully.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

from poker_table.coach.importers.builder import (
    HandBuilder,
    ImportError_,
    amount,
    cards,
    money_scale,
)
from poker_table.engine import Street
from poker_table.history import HandHistory


@dataclass(slots=True)
class ImportResult:
    hands: list[HandHistory] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (hand id, reason)
    hero: str | None = None


_MONEY = r"\$?([\d,]+(?:\.\d+)?)"
_RE_HEADER = re.compile(
    r"^(?:PokerStars (?:Zoom )?Hand|Poker Hand) #(?P<id>[A-Z]*\d+):\s+(?:Tournament #\d+, .*?)?"
    r"Hold'em No Limit"
    r"(?: - Level [IVXLC\d]+)?\s*\("
    + _MONEY.replace("(", "(?P<sb>", 1)
    + r"/"
    + _MONEY.replace("(", "(?P<bb>", 1)
    + r"(?: [A-Z]{3})?\)"
)
_RE_DATE = re.compile(r"(\d{4})/(\d{2})/(\d{2}) (\d{1,2}):(\d{2}):(\d{2})(?: (?P<tz>[A-Z]{2,4}))?")
# PokerStars stamps hands in the client's zone; ET is the default and the common case.
_TZ_OFFSETS = {"ET": -5, "EST": -5, "EDT": -4, "CET": 1, "CEST": 2, "UTC": 0, "GMT": 0}
_RE_TABLE = re.compile(
    r"^Table '.*?' (?P<size>\d+)-max(?: \(.*?\))? Seat #(?P<button>\d+) is the button"
)
_RE_SEAT = re.compile(
    r"^Seat (?P<seat>\d+): (?P<name>.+?) \(" + _MONEY + r" in chips\)(?P<out> is sitting out)?$"
)
_RE_POST = re.compile(
    r"^(?P<name>.+?): posts (?P<kind>small blind|big blind|small & big blinds|the ante) " + _MONEY
)
_RE_DEALT = re.compile(r"^Dealt to (?P<name>.+?) \[(?P<cards>[^\]]+)\]")
_RE_ACTION = re.compile(
    r"^(?P<name>.+?): (?P<verb>folds|checks|calls|bets|raises)"
    r"(?: " + _MONEY + r")?(?: to " + _MONEY + r")?(?P<allin> and is all-in)?"
)
_RE_STREET = re.compile(r"^\*\*\* (?P<name>FLOP|TURN|RIVER) \*\*\* (?P<cards>.+)$")
_RE_RUN_TWICE = re.compile(r"^\*\*\* (?:FIRST|SECOND) (?:FLOP|TURN|RIVER) \*\*\*")
_RE_UNCALLED = re.compile(r"^Uncalled bet \(" + _MONEY + r"\) returned to (?P<name>.+)$")
_RE_COLLECTED = re.compile(
    r"^(?P<name>.+?) collected "
    + _MONEY
    + r" from (?:the )?(?P<pot>main pot|side pot(?:-\d+)?|pot)"
)
_RE_SHOWS = re.compile(r"^(?P<name>.+?): shows \[(?P<cards>[^\]]+)\]")
_RE_SUMMARY_SHOW = re.compile(
    r"^Seat \d+: (?P<name>.+?) (?:\(.*?\) )?(?:showed|mucked) \[(?P<cards>[^\]]+)\]"
)


def split_hands(text: str) -> Iterator[str]:
    """Yield the text of each hand in a file."""
    chunk: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        line = line.strip("﻿ \t")
        if (line.startswith("PokerStars ") or line.startswith("Poker Hand #")) and chunk:
            yield "\n".join(chunk)
            chunk = []
        if line:
            chunk.append(line)
    if chunk:
        yield "\n".join(chunk)


def parse_pokerstars(text: str) -> ImportResult:
    result = ImportResult()
    raw_hands = [h for h in split_hands(text) if _RE_HEADER.match(h)]
    for raw in raw_hands:
        head = _RE_HEADER.match(raw)
        assert head is not None
        hand_id = head.group("id")
        try:
            history, hero = _parse_hand(raw, money_scale(head.group("bb")))
        except ImportError_ as exc:
            result.skipped.append((hand_id, str(exc)))
            continue
        result.hands.append(history)
        if hero and result.hero is None:
            result.hero = hero
    return result


def _played_at(header: str) -> str:
    """The hand's date from the header, as ISO 8601 UTC (empty if absent)."""
    m = _RE_DATE.search(header)
    if m is None:
        return ""
    y, mo, d, h, mi, sec = (int(x) for x in m.groups()[:6])
    tz = m.group("tz") or "UTC"  # PokerStars says ET; GGPoker gives no zone and stamps UTC
    offset = _TZ_OFFSETS.get(tz, 0)
    if tz == "ET" and 3 <= mo <= 11:  # close enough to US daylight saving for a weekly trend
        offset = -4
    local = datetime(y, mo, d, h, mi, sec, tzinfo=timezone(timedelta(hours=offset)))
    return local.astimezone(UTC).isoformat(timespec="seconds")


def _parse_hand(raw: str, scale: int) -> tuple[HandHistory, str | None]:
    lines = raw.split("\n")
    head = _RE_HEADER.match(lines[0])
    assert head is not None
    table = next((m for line in lines[1:3] if (m := _RE_TABLE.match(line))), None)
    if table is None:
        raise ImportError_("no table line")

    # seats in seat order, skipping empty seats and players sitting out
    seats: list[tuple[int, str, int]] = []
    for line in lines:
        m = _RE_SEAT.match(line)
        if m and not m.group("out"):
            seats.append((int(m.group("seat")), m.group("name"), amount(m.group(3), scale)))
        if line.startswith("*** HOLE CARDS ***"):
            break
    hand = HandBuilder(
        head.group("id"),
        small_blind=amount(head.group("sb"), scale),
        big_blind=amount(head.group("bb"), scale),
        seats=seats,
        button_seat=int(table.group("button")),
        played_at=_played_at(lines[0]),
    )
    hero: str | None = None
    in_summary = False
    for line in lines[1:]:
        if line.startswith("*** SUMMARY ***"):
            in_summary = True
        if in_summary:
            m = _RE_SUMMARY_SHOW.match(line)
            if m:
                hand.shows(m.group("name"), cards(m.group("cards")))
            continue
        if _RE_SEAT.match(line) or line.startswith("*** HOLE CARDS"):
            continue
        if _RE_RUN_TWICE.match(line):
            raise ImportError_("run it twice")
        if m := _RE_POST.match(line):
            hand.post_blind(m.group("name"), m.group("kind"), amount(m.group(3), scale))
        elif m := _RE_DEALT.match(line):
            if hand.knows(m.group("name")) and (hero is None or m.group("name") == "Hero"):
                hero = m.group("name")
            hand.deal(m.group("name"), cards(m.group("cards")))
        elif m := _RE_STREET.match(line):
            groups = re.findall(r"\[([^\]]+)\]", m.group("cards"))
            hand.new_street(Street(m.group("name").lower()), cards(groups[-1]))
        elif m := _RE_ACTION.match(line):
            a1, a2 = amount(m.group(3), scale), amount(m.group(4), scale)
            verb = m.group("verb")
            hand.action(
                m.group("name"),
                verb,
                chips=a1,
                to=a2 if verb == "raises" else None,
                all_in=bool(m.group("allin")),
            )
        elif m := _RE_UNCALLED.match(line):
            hand.uncalled(m.group("name"), amount(m.group(1), scale))
        elif m := _RE_SHOWS.match(line):
            hand.shows(m.group("name"), cards(m.group("cards")))
        elif m := _RE_COLLECTED.match(line):
            hand.collect(m.group("name"), amount(m.group(2), scale), m.group("pot"))
    return hand.finish(hero), hero


__all__ = ["ImportError_", "ImportResult", "parse_pokerstars", "split_hands"]
