"""888poker text hand histories -> HandHistory.

A different dialect from PokerStars: ``***** 888poker Hand History for Game N *****``,
``$0.01/$0.02 Blinds No Limit Holdem - *** 10 09 2026 20:11:22`` (DD MM YYYY, local time),
seats as ``Seat 1: name ( $2.34 )``, ``name posts small blind [$0.01]``,
``** Dealing down cards **`` / ``** Dealing flop ** [ 2c, 7d, Kh ]``, actions like
``name raises [$0.20]`` where the bracketed amount is what the player *adds* (not a
"raise to"), and ``name collected [ $0.87 ]`` with no "uncalled bet returned" line — the
builder infers the return.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime

from poker_table.coach.importers.builder import (
    HandBuilder,
    ImportError_,
    amount,
    cards,
    money_scale,
)
from poker_table.coach.importers.pokerstars import ImportResult
from poker_table.engine import Street

_MONEY = r"\$?([\d,]+(?:\.\d+)?)"
_RE_GAME = re.compile(r"^\*+ 888poker Hand History for Game (?P<id>\d+) \*+")
_RE_BLINDS = re.compile(
    r"^"
    + _MONEY.replace("(", "(?P<sb>", 1)
    + r"/"
    + _MONEY.replace("(", "(?P<bb>", 1)
    + r" Blinds No Limit Holdem - \*\*\* (?P<date>\d{2} \d{2} \d{4} \d{1,2}:\d{2}:\d{2})"
)
_RE_BUTTON = re.compile(r"^Seat (?P<button>\d+) is the button")
_RE_SEAT = re.compile(r"^Seat (?P<seat>\d+): (?P<name>.+?) \( " + _MONEY + r" \)")
_RE_POST = re.compile(
    r"^(?P<name>.+?) posts (?P<kind>small blind|big blind|ante|dead blind|big blind \+ dead)"
    r" \[" + _MONEY + r"\]"
)
_RE_DEALT = re.compile(r"^Dealt to (?P<name>.+?) \[ (?P<cards>[^\]]+) \]")
_RE_ACTION = re.compile(
    r"^(?P<name>.+?) (?P<verb>folds|checks|calls|bets|raises)(?: \[" + _MONEY + r"\])?"
    r"(?P<allin>,? and is all[- ]in)?$"
)
_RE_STREET = re.compile(r"^\*\* Dealing (?P<name>flop|turn|river) \*\* \[ (?P<cards>[^\]]+) \]")
_RE_COLLECT = re.compile(r"^(?P<name>.+?) collected \[ " + _MONEY + r" \]")
_RE_SHOWS = re.compile(r"^(?P<name>.+?) shows \[ (?P<cards>[^\]]+) \]")


def split_hands(text: str) -> Iterator[str]:
    chunk: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        line = line.strip("﻿ \t")
        if _RE_GAME.match(line) and chunk:
            yield "\n".join(chunk)
            chunk = []
        if line:
            chunk.append(line)
    if chunk:
        yield "\n".join(chunk)


def parse_888(text: str) -> ImportResult:
    result = ImportResult()
    for raw in split_hands(text):
        head = _RE_GAME.match(raw)
        if head is None:
            continue
        hand_id = head.group("id")
        try:
            history, hero = _parse_hand(raw, hand_id)
        except ImportError_ as exc:
            result.skipped.append((hand_id, str(exc)))
            continue
        result.hands.append(history)
        if hero and result.hero is None:
            result.hero = hero
    return result


def _played_at(text: str) -> str:
    """``10 09 2026 20:11:22`` (DD MM YYYY, the client's clock) -> ISO 8601, taken as UTC."""
    d, mo, y, clock = text.split(" ")
    h, mi, s = (int(x) for x in clock.split(":"))
    return datetime(int(y), int(mo), int(d), h, mi, s, tzinfo=UTC).isoformat(timespec="seconds")


def _parse_hand(raw: str, hand_id: str) -> tuple:
    lines = raw.split("\n")
    blinds = next((m for line in lines[:4] if (m := _RE_BLINDS.match(line))), None)
    if blinds is None:
        raise ImportError_("no blinds line")
    scale = money_scale(blinds.group("bb"))
    button = next((m for line in lines[:8] if (m := _RE_BUTTON.match(line))), None)
    if button is None:
        raise ImportError_("no button line")
    seats: list[tuple[int, str, int]] = []
    for line in lines:
        if m := _RE_SEAT.match(line):
            seats.append((int(m.group("seat")), m.group("name"), amount(m.group(3), scale)))
        elif line.startswith("** Dealing down cards **"):
            break
    hand = HandBuilder(
        hand_id,
        small_blind=amount(blinds.group("sb"), scale),
        big_blind=amount(blinds.group("bb"), scale),
        seats=seats,
        button_seat=int(button.group("button")),
        played_at=_played_at(blinds.group("date")),
    )
    hero: str | None = None
    for line in lines:
        if _RE_SEAT.match(line):
            continue
        if m := _RE_POST.match(line):
            hand.post_blind(m.group("name"), m.group("kind"), amount(m.group(3), scale))
        elif m := _RE_DEALT.match(line):
            if hand.knows(m.group("name")):
                hero = hero or m.group("name")
                hand.deal(m.group("name"), cards(m.group("cards")))
        elif m := _RE_STREET.match(line):
            hand.new_street(Street(m.group("name")), cards(m.group("cards")))
        elif m := _RE_ACTION.match(line):
            if not hand.knows(m.group("name")):
                continue
            hand.action(
                m.group("name"),
                m.group("verb"),
                chips=amount(m.group(3), scale),  # 888 prints what was added, never "to"
                all_in=bool(m.group("allin")),
            )
        elif m := _RE_SHOWS.match(line):
            hand.shows(m.group("name"), cards(m.group("cards")))
        elif m := _RE_COLLECT.match(line):
            hand.collect(m.group("name"), amount(m.group(2), scale))
    return hand.finish(hero, infer_uncalled=True), hero


__all__ = ["parse_888", "split_hands"]
