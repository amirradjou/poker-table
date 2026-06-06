"""Hand histories: everything that happened in a hand, with each seat's private reasoning.

A :class:`HandHistory` is plain data (JSON-serialisable) so it can be written to JSONL,
replayed, fed to the stats module or, later, to poker-coach.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from poker_table.agents.base import position_name
from poker_table.cards import Card
from poker_table.engine import Action, ActionType, Event, EventKind, Street
from poker_table.table import PlayedHand

FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class PlayerRecord:
    seat: int
    name: str
    position: str
    stack: int  # at the start of the hand
    hole: list[str]
    net: int


@dataclass(frozen=True, slots=True)
class EventRecord:
    kind: str
    street: str
    seat: int | None = None
    action: str | None = None
    amount: int = 0
    cards: list[str] = field(default_factory=list)
    text: str = ""
    all_in: bool = False


@dataclass(frozen=True, slots=True)
class DecisionTrace:
    seat: int
    street: str
    requested: str
    applied: str
    illegal: bool
    reasoning: str
    table_talk: str
    latency_ms: float
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HandHistory:
    hand_id: str
    seed: int
    small_blind: int
    big_blind: int
    button: int
    players: list[PlayerRecord]
    board: list[str]
    events: list[EventRecord]
    decisions: list[DecisionTrace]
    pots: list[dict[str, Any]]
    payouts: dict[int, int]
    showdown: dict[int, str]
    version: int = FORMAT_VERSION
    played_at: str = ""  # ISO 8601 UTC; empty when unknown (older files)

    @property
    def played(self) -> datetime | None:
        return datetime.fromisoformat(self.played_at) if self.played_at else None

    # -- construction --

    @classmethod
    def from_played(cls, played: PlayedHand, *, played_at: datetime | None = None) -> HandHistory:
        hand = played.hand
        if not hand.finished:
            raise ValueError("hand is not finished")
        stamp = (played_at or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        net = hand.net()
        players = [
            PlayerRecord(
                seat=s.index,
                name=s.name,
                position=position_name(s.index, hand.button, hand.num_players),
                stack=hand.starting_stacks[s.index],
                hole=[str(c) for c in (s.hole or ())],
                net=net[s.index],
            )
            for s in hand.seats
        ]
        return cls(
            hand_id=hand.hand_id,
            seed=hand.seed,
            small_blind=hand.small_blind,
            big_blind=hand.big_blind,
            button=hand.button,
            players=players,
            board=[str(c) for c in hand.board],
            events=[_event_record(e) for e in hand.events],
            decisions=[
                DecisionTrace(
                    seat=d.seat,
                    street=d.street.value,
                    requested=str(d.requested),
                    applied=str(d.applied),
                    illegal=d.illegal,
                    reasoning=d.reasoning,
                    table_talk=d.table_talk,
                    latency_ms=round(d.latency_ms, 3),
                    meta=dict(d.meta),
                )
                for d in played.decisions
            ],
            pots=[{"amount": p.amount, "eligible": list(p.eligible)} for p in hand.pots],
            payouts=dict(hand.payouts),
            showdown={i: r.describe() for i, r in hand.showdown_ranks.items()},
            played_at=stamp,
        )

    # -- (de)serialisation --

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["payouts"] = {str(k): v for k, v in self.payouts.items()}
        data["showdown"] = {str(k): v for k, v in self.showdown.items()}
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HandHistory:
        return cls(
            hand_id=data["hand_id"],
            seed=data["seed"],
            small_blind=data["small_blind"],
            big_blind=data["big_blind"],
            button=data["button"],
            players=[PlayerRecord(**p) for p in data["players"]],
            board=list(data["board"]),
            events=[EventRecord(**e) for e in data["events"]],
            decisions=[DecisionTrace(**d) for d in data["decisions"]],
            pots=list(data["pots"]),
            payouts={int(k): v for k, v in data["payouts"].items()},
            showdown={int(k): v for k, v in data["showdown"].items()},
            version=data.get("version", FORMAT_VERSION),
            played_at=data.get("played_at", ""),
        )

    @classmethod
    def from_json(cls, text: str) -> HandHistory:
        return cls.from_dict(json.loads(text))

    # -- queries --

    def player(self, seat: int) -> PlayerRecord:
        return self.players[seat]

    def actions(self, street: Street | None = None) -> list[EventRecord]:
        return [
            e
            for e in self.events
            if e.kind == EventKind.ACTION.value and (street is None or e.street == street.value)
        ]

    def winners(self) -> list[int]:
        return sorted(self.payouts)

    # -- rendering --

    def render(self, *, reasoning: bool = False) -> str:
        """A text hand history in the familiar style, optionally with the private reasoning."""
        names = {p.seat: p.name for p in self.players}
        lines = [
            f"Hand #{self.hand_id} · NLHE {self.small_blind}/{self.big_blind} · seed {self.seed}",
            f"Seat {self.button} is the button",
        ]
        for p in self.players:
            lines.append(f"Seat {p.seat}: {p.name} ({p.stack} in chips) [{p.position}]")
        traces = {(d.seat, d.street, d.applied): d for d in self.decisions}
        seen: set[tuple[int, str, str]] = set()
        blinds_posted = 0
        for e in self.events:
            name = names.get(e.seat, "") if e.seat is not None else ""
            match e.kind:
                case EventKind.POST_BLIND.value:
                    which = "small" if blinds_posted == 0 else "big"  # engine posts SB first
                    blinds_posted += 1
                    suffix = " and is all-in" if e.all_in else ""
                    lines.append(f"{name}: posts {which} blind {e.amount}{suffix}")
                case EventKind.DEAL_HOLE.value:
                    lines.append(f"Dealt to {name} [{' '.join(e.cards)}]")
                case EventKind.STREET.value:
                    lines.append(f"*** {e.street.upper()} *** [{e.text}]")
                case EventKind.ACTION.value:
                    suffix = " and is all-in" if e.all_in else ""
                    lines.append(f"{name}: {e.action}{suffix}")
                    key = (e.seat or 0, e.street, e.action or "")
                    trace = traces.get(key)
                    if trace is not None and key not in seen:
                        seen.add(key)
                        if trace.table_talk:
                            lines.append(f'    {name} says: "{trace.table_talk}"')
                        if reasoning and trace.reasoning:
                            flag = (
                                " [ILLEGAL: wanted " + trace.requested + "]"
                                if trace.illegal
                                else ""
                            )
                            lines.append(f"    ({name} thinks: {trace.reasoning}){flag}")
                case EventKind.RETURN_UNCALLED.value:
                    lines.append(f"Uncalled bet ({e.amount}) returned to {name}")
                case EventKind.SHOWDOWN.value:
                    lines.append(f"{name}: shows [{' '.join(e.cards)}] ({e.text})")
                case EventKind.WIN.value:
                    lines.append(f"{name} collected {e.amount} from {e.text}")
        lines.append("*** SUMMARY ***")
        total = sum(p["amount"] for p in self.pots)
        lines.append(f"Total pot {total} · Board [{' '.join(self.board)}]")
        for p in self.players:
            sign = "+" if p.net > 0 else ""
            lines.append(f"Seat {p.seat}: {p.name} {sign}{p.net}")
        return "\n".join(lines)


def _event_record(e: Event) -> EventRecord:
    return EventRecord(
        kind=e.kind.value,
        street=e.street.value,
        seat=e.seat,
        action=str(e.action) if e.action is not None else None,
        amount=e.amount,
        cards=[str(c) for c in e.cards],
        text=e.text,
        all_in=e.all_in,
    )


def parse_action(text: str) -> Action:
    """Inverse of ``str(Action)``: ``"raise 12"`` -> ``Action.raise_to(12)``."""
    verb, _, amount = text.partition(" ")
    kind = ActionType(verb)
    if kind in (ActionType.BET, ActionType.RAISE):
        return Action(kind, int(amount))
    return Action(kind)


def hole_cards(history: HandHistory, seat: int) -> tuple[Card, Card]:
    a, b = (Card.parse(c) for c in history.players[seat].hole)
    return a, b


def board_cards(history: HandHistory) -> list[Card]:
    return [Card.parse(c) for c in history.board]


def write_jsonl(path: Path | str, histories: Iterable[HandHistory], *, append: bool = False) -> int:
    count = 0
    with open(path, "a" if append else "w", encoding="utf-8") as fh:
        for history in histories:
            fh.write(history.to_json() + "\n")
            count += 1
    return count


def read_jsonl(path: Path | str) -> Iterator[HandHistory]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield HandHistory.from_json(line)


__all__ = [
    "DecisionTrace",
    "EventRecord",
    "HandHistory",
    "PlayerRecord",
    "board_cards",
    "hole_cards",
    "parse_action",
    "read_jsonl",
    "write_jsonl",
]
