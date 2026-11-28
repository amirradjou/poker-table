"""The table in the published demo: the engine runs in the visitor's browser, against the bots.

The demo is a static site (GitHub Pages), so there is no server to run the table. The page loads
Python in the browser (Pyodide) and this module in it. A thread cannot wait there for a click,
and Pages cannot send the headers that would let a worker block on one, so the page drives the
table one step at a time:

- ``step()`` plays one thing: it deals the next hand, or lets the bot whose turn it is decide.
  It stops at the visitor's turn and at the end of each hand, so what ``/api/live`` says always
  matches what the page has been shown.
- ``act(action)`` plays the visitor's decision, then stops where ``step`` would.
- ``get(path)`` and ``post(path, body)`` answer the routes of ``web/app.py`` from the hands
  played so far, with the same payloads (``web/payloads.py``), so the viewer works unchanged.

Seats are built from the same spec list as ``poker-table play --seats you:human,...``, so the
same seed deals the same hands, and the same decisions play them out the same way.

Nothing here may import the web stack or a model's SDK: neither exists in the browser.
"""

from __future__ import annotations

import json
from collections.abc import Generator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from poker_table.agents.base import Decision, SeatView
from poker_table.agents.human import HumanAgent
from poker_table.agents.registry import make_agents
from poker_table.history import parse_action
from poker_table.league import LeagueConfig, LeagueResult, league_turns
from poker_table.table import ask
from poker_table.web.live import MAX_PACE, LiveSession, WebHumanAgent
from poker_table.web.payloads import (
    HandStore,
    bankroll_payload,
    coach_payload,
    hand_payload,
    hands_payload,
    highlights_payload,
    margins_payload,
    session_payload,
    stats_payload,
)

# The order the bots sit down in. Seats are seeded by position, so a new bot only ever goes on
# the end: the first N must stay the same for every N, or old seeds would deal other hands.
BOTS = (
    "tag",
    "maniac",
    "station",
    "rock",
    "random",
    "exploiter",
    "fish",
    "lag",
    "calculator",
    "trapper",
    "tilter",
)
DEFAULT_BOTS = 3
MOST_BOTS = 8  # you and at most eight bots: nine seats in all
HANDS = 100  # what `poker-table play` deals when not told
YOU = "you"
PACE = 0.8  # seconds a bot "thinks" before it acts, as `serve --live` paces a table
PATH = Path("/home/pyodide/your-table.jsonl")  # Pyodide's in-memory home directory


class HttpError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def seats_for(bots: int | Sequence[str] = DEFAULT_BOTS) -> list[str]:
    """The visitor in the first seat and the bots after them: the first ``bots`` of ``BOTS``,
    or the ones named. Named bots sit in ``BOTS`` order, whatever order they were picked in, so
    picking the first three seats the table ``bots=3`` does."""
    if isinstance(bots, int):
        if not 1 <= bots <= MOST_BOTS:
            raise ValueError(f"the demo seats 1 to {MOST_BOTS} bots, not {bots}")
        return [f"{YOU}:human", *BOTS[:bots]]
    names = list(bots)
    unknown = [name for name in names if name not in BOTS]
    if unknown:
        raise ValueError(f"the demo has no bot {unknown[0]!r}; it has {', '.join(BOTS)}")
    if not names or len(set(names)) != len(names):
        raise ValueError("pick each bot at most once, and at least one")
    if len(names) > MOST_BOTS:
        raise ValueError(f"the demo seats at most {MOST_BOTS} bots, not {len(names)}")
    return [f"{YOU}:human", *(bot for bot in BOTS if bot in names)]


def command(seats: Sequence[str], seed: int, hands: int = HANDS) -> str:
    """The `poker-table play` command that deals the same hands."""
    count = "" if hands == HANDS else f" -n {hands}"
    return f"poker-table play --seed {seed} --seats {','.join(seats)}{count}"


class DemoTable:
    """A cash game of ``hands`` hands at blinds 1/2, every hand from 200 chips: what `poker-table
    play` deals by default. A ``human`` seat is played from the page; the others are bots."""

    def __init__(
        self, seats: Sequence[str], *, seed: int, hands: int = HANDS, path: Path = PATH
    ) -> None:
        agents = [
            WebHumanAgent(a.name) if isinstance(a, HumanAgent) else a
            for a in make_agents(list(seats), seed=seed)
        ]
        if sum(isinstance(a, WebHumanAgent) for a in agents) > 1:
            raise ValueError("the demo's table has room for one person")
        self.seats = list(seats)
        self.seed = seed
        self.config = LeagueConfig(hands=hands, seed=seed)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        self.session = LiveSession(agents, self.config, path)
        for person in self.session.humans:
            person.session = self.session  # so that opening a turn tells the page
        self.session.running = True
        self.pace = PACE  # only reported: the page does the waiting, Python never sleeps
        self.store = HandStore(path)
        self._events = self.session.subscribe(None)  # the one page: the person's, or a watcher's
        self._turns: Generator[SeatView, Decision, LeagueResult] | None = None
        self._view: SeatView | None = None  # the decision the table is waiting for
        self._dealt = 0

    @property
    def command(self) -> str:
        return command(self.seats, self.seed, self.config.hands)

    # -- playing --

    def step(self) -> dict[str, Any]:
        """Play one thing; what the page is to show (``events``) and what it waits for next
        (``next``): "bot" (a bot is thinking: step again after the pace), "you" (the visitor's
        turn: answer with ``act``), "deal" (a hand is over: step again to deal the next one) or
        "done"."""
        if self.session.finished:
            return self._out("done")
        view = self._view
        if view is not None and self._person(view) is not None:
            return self._out("you")
        if self._turns is None:
            if self._dealt >= self.config.hands:
                self.session.close()
                return self._out("done")
            # One hand at a time, dealt exactly as hand n of the whole league: its deck is the
            # league's n-th, and every hand starts from the buy-in, as `play` deals them.
            one = replace(self.config, hands=self._dealt + 1, first_hand=self._dealt)
            self._turns = league_turns(self.session.agents, one, **self.session.hooks())
            self._dealt += 1
            return self._play(None)
        assert view is not None
        return self._play(ask(self.session.agents[view.seat], view))

    def act(self, action: str, table_talk: str = "") -> dict[str, Any]:
        """Play the visitor's decision ("fold", "call", "raise 12"...), then go on as ``step``.
        LookupError when it is not their turn, ValueError for an action that does not parse."""
        person = self._person(self._view) if self._view is not None else None
        if person is None:
            raise LookupError("it is not your turn")
        return self._play(person.answer(action, table_talk))

    def run_out(self) -> None:
        """Play every hand to the end. Only a table of bots can: the visitor's turn stops it."""
        while (out := self.step())["next"] != "done":
            if out["next"] == "you":
                raise LookupError("a person is at this table: answer with act()")

    def _play(self, decision: Decision | None) -> dict[str, Any]:
        assert self._turns is not None
        try:
            view = next(self._turns) if decision is None else self._turns.send(decision)
        except StopIteration:
            self._turns = self._view = None
            if self._dealt >= self.config.hands:
                self.session.close()
                return self._out("done")
            return self._out("deal")
        except Exception as exc:  # noqa: BLE001 - surfaced to the page, as a live table does
            self._turns = self._view = None
            self.session.error = f"{type(exc).__name__}: {exc}"
            self.session.close()
            return self._out("done")
        self._view = view
        person = self._person(view)
        if person is None:
            return self._out("bot")
        person.open_turn(view)
        return self._out("you")

    def _person(self, view: SeatView) -> WebHumanAgent | None:
        seat = self.session.agents[view.seat]
        return seat if isinstance(seat, WebHumanAgent) else None

    def _out(self, waiting: str) -> dict[str, Any]:
        events = []
        while not self._events.empty():
            events.append(self._events.get_nowait())
        return {"events": events, "next": waiting}

    def hello(self) -> dict[str, Any]:
        """What a page's event stream opens with, as ``LiveSession.sse`` sends it."""
        return {"type": "hello", **self.status()}

    def status(self) -> dict[str, Any]:
        return self.session.status() | {"pace": self.pace}

    # -- the routes --

    def get(self, path: str) -> Any:
        """The GET routes of ``web/app.py``, for a table with one person (or nobody) at it."""
        route, query = _split(path)
        store = self.store
        if route == "/api/session":
            store.reload()
            return session_payload(store) | {"file": "your session"}
        if route == "/api/hands":
            return hands_payload(
                store, query.number("offset", 0), query.number("limit", 100), query.text("ids")
            )
        if route.startswith("/api/hands/") or route.startswith("/api/odds/"):
            hand_id = unquote(route.split("/", 3)[3])
            history = store.by_id.get(hand_id)
            if history is None:
                raise HttpError(404, f"no hand {hand_id!r}")
            return hand_payload(history) if route.startswith("/api/hands/") else store.odds(hand_id)
        if route == "/api/live":
            return self.status()
        if route == "/api/coach":
            player = query.text("player")
            if player not in store.players():
                raise HttpError(404, f"no seat named {player!r}")
            try:
                return coach_payload(
                    store,
                    player,
                    query.number("samples", 200),
                    query.text("since"),
                    query.text("until"),
                )
            except ValueError as exc:  # a malformed date
                raise HttpError(400, str(exc)) from None
        if route == "/api/highlights":
            offset = query.number("offset", 0)
            if offset == 0:
                store.reload()
            return highlights_payload(store, offset, query.number("limit", 10))
        if route == "/api/bankroll":
            return bankroll_payload(store)
        if route == "/api/stats":
            return stats_payload(store)
        if route == "/api/margins":
            return margins_payload(store)
        raise HttpError(404, f"{route} is not part of the demo's table")

    def post(self, path: str, body: dict[str, Any]) -> tuple[Any, dict[str, Any] | None]:
        """The POST routes the live table uses; the answer, and for an action what it played."""
        route, _ = _split(path)
        if route == "/api/act":
            text = str(body.get("action", ""))
            try:
                played = self.act(text, str(body.get("table_talk", "")))
            except LookupError as exc:
                raise HttpError(409, str(exc)) from None
            except ValueError as exc:
                raise HttpError(400, f"bad action: {exc}") from None
            return {"ok": True, "action": str(parse_action(text))}, played
        if route == "/api/live/pace":
            self.pace = min(max(float(body.get("pace", PACE)), 0.0), MAX_PACE)
            return {"pace": self.pace}, None
        raise HttpError(404, f"{route} is not part of the demo's table")

    def page(self, request: str) -> str:
        """One request from the page, JSON in and JSON out: ``{"op": "step"}``, ``{"op": "get",
        "path": ...}`` or ``{"op": "post", "path": ..., "body": {...}}``. The answer has the
        HTTP ``status`` and ``body``, and for a step or an action, ``events`` and ``next``."""
        ask_for = json.loads(request)
        op = ask_for["op"]
        try:
            if op == "step":
                return json.dumps({"status": 200} | self.step())
            if op == "hello":
                return json.dumps({"status": 200, "body": self.hello()})
            if op == "about":
                about = {"seats": self.seats, "seed": self.seed, "command": self.command}
                return json.dumps({"status": 200, "body": about})
            if op == "get":
                return json.dumps({"status": 200, "body": self.get(ask_for["path"])})
            if op == "post":
                body, played = self.post(ask_for["path"], ask_for.get("body") or {})
                return json.dumps({"status": 200, "body": body} | (played or {}))
        except HttpError as exc:
            return json.dumps({"status": exc.status, "body": {"detail": exc.detail}})
        raise ValueError(f"unknown request {op!r}")


def open_table(request: str, path: Path = PATH) -> DemoTable:
    """The table the page asks for, in JSON: ``{"bots": 3, "seed": 42}`` seats the visitor with
    that many bots, ``{"bots": ["tag", "rock"], "seed": 42}`` with those bots;
    ``{"seats": ["tag", "maniac"], "seed": 42, "hands": 10}`` seats exactly those (a table of
    bots only plays itself out, step by step)."""
    ask_for = json.loads(request)
    bots = ask_for.get("bots", DEFAULT_BOTS)
    seats = ask_for.get("seats") or seats_for(bots if isinstance(bots, list) else int(bots))
    hands = int(ask_for.get("hands", HANDS))
    return DemoTable(seats, seed=int(ask_for["seed"]), hands=hands, path=path)


class _Query:
    def __init__(self, text: str) -> None:
        self._values = parse_qs(text)

    def text(self, name: str, default: str = "") -> str:
        return self._values.get(name, [default])[0]

    def number(self, name: str, default: int) -> int:
        try:
            return int(self.text(name, str(default)))
        except ValueError:
            raise HttpError(422, f"{name} must be a whole number") from None


def _split(path: str) -> tuple[str, _Query]:
    parts = urlsplit(path)
    return parts.path, _Query(parts.query)
