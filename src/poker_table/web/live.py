"""A league running in a background thread, streamed to the browser; a human seat in it."""

from __future__ import annotations

import copy
import json
import queue
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poker_table.agents.base import Agent, Decision, SeatView, position_name
from poker_table.engine import Action, Event, EventKind, Hand
from poker_table.history import HandHistory, parse_action, write_jsonl
from poker_table.league import LeagueConfig, run_league
from poker_table.table import DecisionRecord

MAX_PACE = 5.0


@dataclass(slots=True)
class LiveSession:
    """Plays ``config.hands`` between ``agents`` on a thread, appending to ``path``.

    Everything that happens is published to subscribers as it happens: ``hand_start`` (the
    seats, and everyone's cards when nobody human is watching their own), ``acting`` (who is
    thinking), one ``step`` per engine event (posts, actions with their decision details,
    streets, showdown, wins), ``hand`` once it is on disk, ``turn`` when the browser seat must
    act and ``done`` at the end. ``pace`` slows a bot-only table down to watching speed.
    """

    agents: Sequence[Agent]
    config: LeagueConfig
    path: Path
    pace: float = 0.0
    hands_played: int = 0
    running: bool = False
    finished: bool = False
    error: str = ""
    current: dict[str, Any] | None = None  # the hand in progress, in the viewer's shape
    _subscribers: list[queue.Queue[dict[str, Any]]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _thread: threading.Thread | None = None
    _published: int = 0  # engine events of the current hand already turned into steps

    @property
    def human(self) -> WebHumanAgent | None:
        return next((a for a in self.agents if isinstance(a, WebHumanAgent)), None)

    @property
    def spectator(self) -> bool:
        """No human at the table, so every seat's cards may be shown while the hand is played."""
        return self.human is None

    def start(self) -> None:
        if self._thread is not None:
            return
        for agent in self.agents:
            if isinstance(agent, WebHumanAgent):
                agent.session = self
        self.running = True
        self._thread = threading.Thread(target=self._run, name="poker-table-live", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        try:
            run_league(
                self.agents,
                self.config,
                on_hand=self._on_hand,
                on_hand_start=self._on_hand_start,
                on_view=self._on_view,
                on_decision=self._on_decision,
                keep_histories=False,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the browser, not swallowed
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.running = False
            self.finished = True
            self.current = None
            self.publish({"type": "done", "hands": self.hands_played, "error": self.error})

    # -- the hand as it is played --

    def _on_hand_start(self, hand: Hand) -> None:
        reveal = self.spectator
        self.current = {
            "hand_id": hand.hand_id,
            "button": hand.button,
            "small_blind": hand.small_blind,
            "big_blind": hand.big_blind,
            "ante": hand.ante,
            "players": [
                {
                    "seat": s.index,
                    "name": s.name,
                    "position": position_name(s.index, hand.button, hand.num_players),
                    "kind": s.kind,
                    "stack": hand.starting_stacks[s.index],
                    "hole": [str(c) for c in s.hole] if reveal and s.hole else [None, None],
                    "net": 0,
                }
                for s in hand.seats
            ],
            "board": [],
            "steps": [],
            "showdown": {},
            "pots": [],
            "decisions": [],
        }
        self._published = 0
        self._flush(hand, publish=False)  # the blind posts travel inside hand_start
        self.publish({"type": "hand_start", "hand": copy.deepcopy(self.current)})  # a snapshot
        self._sleep()

    def _on_view(self, view: SeatView) -> None:
        kind = getattr(self.agents[view.seat], "kind", "")
        self.publish({"type": "acting", "hand_id": view.hand_id, "seat": view.seat, "kind": kind})
        if not kind.startswith("llm:") and kind != "human":
            self._sleep()  # a bot decides instantly; the pace is where it visibly "thinks"

    def _on_decision(self, record: DecisionRecord, hand: Hand) -> None:
        details: dict[str, Any] = {
            "table_talk": record.table_talk,
            "illegal": record.illegal,
            "requested": str(record.requested),
            "latency_ms": round(record.latency_ms, 1),
        }
        if self.spectator:  # private reasoning is only broadcast when nobody is playing
            details["reasoning"] = record.reasoning
            details["meta"] = dict(record.meta)
        self._flush(hand, details)
        if hand.finished:
            self._sleep()  # let the payout land before the next deal

    def _flush(
        self, hand: Hand, decision: dict[str, Any] | None = None, *, publish: bool = True
    ) -> None:
        """Turn engine events not yet published into steps; the first action gets the decision."""
        if self.current is None:
            return
        names = {s.index: s.name for s in hand.seats}
        for event in hand.events[self._published :]:
            self._published += 1
            if event.kind in (EventKind.HAND_START, EventKind.DEAL_HOLE):
                continue
            step = event_to_step(event, names)
            if event.kind is EventKind.ACTION and decision is not None:
                step |= decision
                decision = None
            if event.kind is EventKind.STREET:
                self.current["board"] = [str(c) for c in hand.board]
            self.current["steps"].append(step)
            if publish:
                self.publish({"type": "step", "hand_id": hand.hand_id, "step": step})

    def _sleep(self) -> None:
        if self.pace > 0:
            time.sleep(min(self.pace, MAX_PACE))

    def _on_hand(self, history: HandHistory) -> None:
        write_jsonl(self.path, [history], append=True)
        self.hands_played += 1
        self.current = None
        self.publish({"type": "hand", "hand_id": history.hand_id, "hands": self.hands_played})

    # -- pub/sub for server-sent events --

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            for q in self._subscribers:
                q.put(event)

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def status(self) -> dict[str, Any]:
        human = self.human
        return {
            "live": True,
            "running": self.running,
            "finished": self.finished,
            "error": self.error,
            "hands_played": self.hands_played,
            "hands_total": self.config.hands,
            "seats": [a.name for a in self.agents],
            "kinds": [getattr(a, "kind", "") for a in self.agents],
            "spectator": self.spectator,
            "pace": self.pace,
            "human": human.name if human else None,
            "turn": human.pending if human else None,
            "current": self.current,
        }

    def sse(self, keepalive: float = 15.0) -> Iterator[str]:
        """Server-sent events: a hello with the status, then every published event."""
        q = self.subscribe()
        try:
            yield f"data: {json.dumps({'type': 'hello', **self.status()})}\n\n"
            while not (self.finished and q.empty()):
                try:
                    event = q.get(timeout=keepalive)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            self.unsubscribe(q)


class WebHumanAgent:
    """A seat whose decisions come from the browser: act() blocks until submit() is called."""

    kind = "human"

    def __init__(self, name: str = "you") -> None:
        self.name = name
        self.session: LiveSession | None = None
        self.pending: dict[str, Any] | None = None
        self._answer: queue.Queue[Decision] = queue.Queue()

    def act(self, view: SeatView) -> Decision:
        self.pending = live_view_payload(view)
        if self.session is not None:
            self.session.publish({"type": "turn", "seat": view.seat, "hand_id": view.hand_id})
        try:
            return self._answer.get()
        finally:
            self.pending = None

    def submit(self, action_text: str, table_talk: str = "") -> Action:
        """Called from the request thread; the table's sanitizer handles off-by-a-bit sizes."""
        if self.pending is None:
            raise LookupError("it is not your turn")
        action = parse_action(action_text)
        self._answer.put(Decision(action, reasoning="human (browser)", table_talk=table_talk))
        return action


def event_to_step(event: Event, names: dict[int, str]) -> dict[str, Any]:
    """An engine event in the shape the viewer steps through (see ``app.steps_for``)."""
    return {
        "kind": event.kind.value,
        "street": event.street.value,
        "seat": event.seat,
        "name": names.get(event.seat) if event.seat is not None else None,
        "action": str(event.action) if event.action is not None else None,
        "amount": event.amount,
        "cards": [str(c) for c in event.cards],
        "text": event.text,
        "all_in": event.all_in,
    }


def live_view_payload(view: SeatView) -> dict[str, Any]:
    """A SeatView as the viewer's hand shape: own cards only, public events as steps."""
    contributed = {p.seat: 0 for p in view.players}
    steps: list[dict[str, Any]] = []
    names = {p.seat: p.name for p in view.players}
    for e in view.events:
        if e.kind in (EventKind.HAND_START, EventKind.DEAL_HOLE):
            continue
        if e.seat is not None and e.kind in (
            EventKind.POST_ANTE,
            EventKind.POST_BLIND,
            EventKind.ACTION,
        ):
            contributed[e.seat] += e.amount
        steps.append(event_to_step(e, names))
    legal = view.legal
    return {
        "hand_id": view.hand_id,
        "seat": view.seat,
        "street": view.street.value,
        "button": view.button,
        "small_blind": view.small_blind,
        "big_blind": view.big_blind,
        "ante": view.ante,
        "players": [
            {
                "seat": p.seat,
                "name": p.name,
                "position": p.position,
                "stack": p.stack + contributed[p.seat],
                "hole": [str(c) for c in view.hole] if p.seat == view.seat else [None, None],
                "net": 0,
            }
            for p in view.players
        ],
        "board": [str(c) for c in view.board],
        "steps": steps,
        "talk": [list(t) for t in view.talk],
        "pot": view.pot,
        "to_call": view.to_call,
        "current_bet": view.current_bet,
        "street_bet": view.street_bet,
        "legal": {
            "can_check": legal.can_check,
            "call_amount": legal.call_amount,
            "min_raise_to": legal.min_raise_to,
            "max_raise_to": legal.max_raise_to,
            "is_bet": legal.is_bet,
            "describe": legal.describe(),
        },
        "showdown": {},
        "pots": [],
        "decisions": [],
    }
