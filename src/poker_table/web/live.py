"""A league running in a background thread, streamed to the browser; a human seat in it."""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poker_table.agents.base import Agent, Decision, SeatView
from poker_table.engine import Action, EventKind
from poker_table.history import HandHistory, parse_action, write_jsonl
from poker_table.league import LeagueConfig, run_league


@dataclass(slots=True)
class LiveSession:
    """Plays ``config.hands`` between ``agents`` on a thread, appending to ``path``."""

    agents: Sequence[Agent]
    config: LeagueConfig
    path: Path
    hands_played: int = 0
    running: bool = False
    finished: bool = False
    error: str = ""
    _subscribers: list[queue.Queue[dict[str, Any]]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _thread: threading.Thread | None = None

    @property
    def human(self) -> WebHumanAgent | None:
        return next((a for a in self.agents if isinstance(a, WebHumanAgent)), None)

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
            run_league(self.agents, self.config, on_hand=self._on_hand, keep_histories=False)
        except Exception as exc:  # noqa: BLE001 - surfaced to the browser, not swallowed
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.running = False
            self.finished = True
            self.publish({"type": "done", "hands": self.hands_played, "error": self.error})

    def _on_hand(self, history: HandHistory) -> None:
        write_jsonl(self.path, [history], append=True)
        self.hands_played += 1
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
            "human": human.name if human else None,
            "turn": human.pending if human else None,
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


def live_view_payload(view: SeatView) -> dict[str, Any]:
    """A SeatView as the viewer's hand shape: own cards only, public events as steps."""
    contributed = {p.seat: 0 for p in view.players}
    steps: list[dict[str, Any]] = []
    names = {p.seat: p.name for p in view.players}
    for e in view.events:
        if e.kind in (EventKind.HAND_START, EventKind.DEAL_HOLE):
            continue
        if e.seat is not None and e.kind in (EventKind.POST_BLIND, EventKind.ACTION):
            contributed[e.seat] += e.amount
        steps.append(
            {
                "kind": e.kind.value,
                "street": e.street.value,
                "seat": e.seat,
                "name": names.get(e.seat) if e.seat is not None else None,
                "action": str(e.action) if e.action is not None else None,
                "amount": e.amount,
                "cards": [str(c) for c in e.cards],
                "text": e.text,
                "all_in": e.all_in,
            }
        )
    legal = view.legal
    return {
        "hand_id": view.hand_id,
        "seat": view.seat,
        "street": view.street.value,
        "button": view.button,
        "small_blind": view.small_blind,
        "big_blind": view.big_blind,
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
