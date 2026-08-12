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
from decimal import Decimal

from poker_table.agents.base import position_name
from poker_table.cards import Card
from poker_table.engine import Action, Street
from poker_table.evaluator import evaluate
from poker_table.history import DecisionTrace, EventRecord, HandHistory, PlayerRecord


class ImportError_(ValueError):
    """A hand (or file) that cannot be turned into a HandHistory."""


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
_RE_TOTAL = re.compile(r"^Total pot " + _MONEY + r"(?: Main pot .*?)?(?: \| Rake " + _MONEY + r")?")


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
            history, hero = _parse_hand(raw, _money_scale(head.group("bb")))
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


def _money_scale(big_blind: str) -> int:
    """100 when the blinds are in dollars and cents, else 1 (tournament chips, play money)."""
    return 100 if "." in big_blind else 1


def _amount(token: str | None, scale: int) -> int:
    if token is None:
        return 0
    return int((Decimal(token.replace(",", "")) * scale).to_integral_value())


def _cards(text: str) -> list[str]:
    return [str(Card.parse(c)) for c in text.split()]


def _parse_hand(raw: str, scale: int) -> tuple[HandHistory, str | None]:
    lines = raw.split("\n")
    head = _RE_HEADER.match(lines[0])
    assert head is not None
    hand_id = head.group("id")
    small_blind, big_blind = _amount(head.group("sb"), scale), _amount(head.group("bb"), scale)
    table = next((m for line in lines[1:3] if (m := _RE_TABLE.match(line))), None)
    if table is None:
        raise ImportError_("no table line")
    button_seat = int(table.group("button"))

    # seats -> engine indexes in seat order, skipping empty seats and players sitting out
    seats: list[tuple[int, str, int]] = []
    for line in lines:
        m = _RE_SEAT.match(line)
        if m and not m.group("out"):
            seats.append((int(m.group("seat")), m.group("name"), _amount(m.group(3), scale)))
        if line.startswith("*** HOLE CARDS ***"):
            break
    if len(seats) < 2:
        raise ImportError_("fewer than two players dealt in")
    index_of = {seat: i for i, (seat, _, _) in enumerate(seats)}
    names = [name for _, name, _ in seats]
    stacks = [stack for _, _, stack in seats]
    seat_of_name = {name: i for i, name in enumerate(names)}
    if button_seat not in index_of:
        raise ImportError_("dead button")
    button = index_of[button_seat]
    n = len(seats)

    # The engine derives the blinds from the button; the file must agree.
    expected_sb = button if n == 2 else (button + 1) % n
    expected_bb = (expected_sb + 1) % n
    posts: list[tuple[int, int, str]] = []
    for line in lines:
        m = _RE_POST.match(line)
        if m:
            kind = m.group("kind")
            if kind in ("the ante", "small & big blinds"):
                raise ImportError_(f"unsupported post: {kind}")
            who = seat_of_name.get(m.group("name"))
            if who is None:
                raise ImportError_("blind posted by an unknown player")
            posts.append((who, _amount(m.group(3), scale), kind))
    if [(w, k) for w, _, k in posts] != [(expected_sb, "small blind"), (expected_bb, "big blind")]:
        raise ImportError_("blinds do not match the button (missed or dead blinds)")

    events: list[EventRecord] = []
    street = Street.PREFLOP
    street_bets = dict.fromkeys(range(n), 0)
    total_bets = dict.fromkeys(range(n), 0)
    stack_left = dict(enumerate(stacks))
    holes: dict[int, list[str]] = {i: [] for i in range(n)}
    board: list[str] = []
    payouts: dict[int, int] = {}
    showdown: dict[int, str] = {}
    hero: str | None = None
    folded: set[int] = set()

    def put(seat: int, amount: int) -> None:
        street_bets[seat] += amount
        total_bets[seat] += amount
        stack_left[seat] -= amount

    for who, amount, _kind in posts:
        put(who, amount)
        events.append(
            EventRecord("post_blind", "preflop", who, None, amount, [], "", stack_left[who] <= 0)
        )

    in_summary = False
    for line in lines[1:]:
        if line.startswith("*** SUMMARY ***"):
            in_summary = True
        if in_summary:
            m = _RE_SUMMARY_SHOW.match(line)
            if m and m.group("name") in seat_of_name:
                holes[seat_of_name[m.group("name")]] = _cards(m.group("cards"))
            continue
        if _RE_SEAT.match(line) or _RE_POST.match(line) or line.startswith("*** HOLE CARDS"):
            continue
        if _RE_RUN_TWICE.match(line):
            raise ImportError_("run it twice")
        m = _RE_DEALT.match(line)
        if m:
            who = seat_of_name.get(m.group("name"))
            if who is not None:
                if hero is None or m.group("name") == "Hero":
                    hero = m.group("name")
                holes[who] = _cards(m.group("cards"))
                events.append(EventRecord("deal_hole", "preflop", who, None, 0, holes[who], ""))
            continue
        m = _RE_STREET.match(line)
        if m:
            street = Street(m.group("name").lower())
            groups = re.findall(r"\[([^\]]+)\]", m.group("cards"))
            new_cards = _cards(groups[-1])
            board = _cards(" ".join(groups))
            for seat in street_bets:
                street_bets[seat] = 0
            events.append(
                EventRecord("street", street.value, None, None, 0, new_cards, " ".join(board))
            )
            continue
        m = _RE_ACTION.match(line)
        if m and m.group("name") in seat_of_name:
            who = seat_of_name[m.group("name")]
            verb = m.group("verb")
            a1, a2 = _amount(m.group(3), scale), _amount(m.group(4), scale)
            if verb == "folds":
                action, paid = Action.fold(), 0
                folded.add(who)
            elif verb == "checks":
                action, paid = Action.check(), 0
            elif verb == "calls":
                action, paid = Action.call(), a1
            elif verb == "bets":
                action, paid = Action.bet(a1), a1
            else:  # raises X to Y
                action, paid = Action.raise_to(a2), a2 - street_bets[who]
            put(who, paid)
            all_in = bool(m.group("allin")) or stack_left[who] <= 0
            events.append(
                EventRecord("action", street.value, who, str(action), paid, [], "", all_in)
            )
            continue
        m = _RE_UNCALLED.match(line)
        if m and m.group("name") in seat_of_name:
            who = seat_of_name[m.group("name")]
            amount = _amount(m.group(1), scale)
            street_bets[who] -= amount
            total_bets[who] -= amount
            stack_left[who] += amount
            events.append(EventRecord("return_uncalled", street.value, who, None, amount))
            continue
        m = _RE_SHOWS.match(line)
        if m and m.group("name") in seat_of_name:
            who = seat_of_name[m.group("name")]
            holes[who] = _cards(m.group("cards"))
            continue
        m = _RE_COLLECTED.match(line)
        if m and m.group("name") in seat_of_name:
            who = seat_of_name[m.group("name")]
            amount = _amount(m.group(2), scale)
            payouts[who] = payouts.get(who, 0) + amount
            stack_left[who] += amount
            events.append(EventRecord("win", street.value, who, None, amount, [], m.group("pot")))
            continue

    # Showdown descriptions for everyone whose cards we know and who did not fold, if 5 cards.
    if len(board) == 5:
        for seat, cards in holes.items():
            if len(cards) == 2 and seat not in folded:
                rank = evaluate([Card.parse(c) for c in [*cards, *board]])
                showdown[seat] = rank.describe()
                events.append(
                    EventRecord("showdown", "showdown", seat, None, 0, cards, rank.describe())
                )
    events.append(EventRecord("hand_end", street.value if len(board) < 5 else "showdown"))

    players = [
        PlayerRecord(
            seat=i,
            name=names[i],
            position=position_name(i, button, n),
            stack=stacks[i],
            hole=holes[i],
            net=payouts.get(i, 0) - total_bets[i],
        )
        for i in range(n)
    ]
    history = HandHistory(
        hand_id=hand_id,
        seed=0,
        small_blind=small_blind,
        big_blind=big_blind,
        button=button,
        players=players,
        board=board,
        events=events,
        decisions=[
            DecisionTrace(e.seat or 0, e.street, e.action or "", e.action or "", False, "", "", 0.0)
            for e in events
            if e.kind == "action"
        ],
        pots=[{"amount": sum(payouts.values()), "eligible": sorted(payouts)}],
        payouts=payouts,
        showdown=showdown,
        played_at=_played_at(lines[0]),
        hero=hero or "",
    )
    return history, hero


__all__ = ["ImportError_", "ImportResult", "parse_pokerstars", "split_hands"]
