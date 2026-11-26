"""A league running in a background thread, streamed to the browser; people's seats in it."""

from __future__ import annotations

import copy
import json
import queue
import secrets
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poker_table.agents.base import Agent, Decision, SeatView, position_name
from poker_table.cards import Card
from poker_table.engine import Action, Event, EventKind, Hand
from poker_table.history import HandHistory, parse_action, public_view, write_jsonl
from poker_table.league import LeagueConfig, TournamentConfig, run_league, run_tournament
from poker_table.odds import chance_row, for_view, table_equities
from poker_table.table import DecisionRecord
from poker_table.web.commentary import Commentator

MAX_PACE = 5.0
SHARED_TIME_LIMIT = 60.0  # seconds per decision at a table with two or more people, by default


ODDS_SAMPLES = 800  # a seat's own odds: the same count live and in a replay, so they match
REVIEW_SAMPLES = 200


class EquityTrack:
    """Everyone's share of the pot through a hand, re-computed whenever it can change.

    A fold or a new card adds a row, and each row holds from viewer position ``step`` on. A live
    table and the replay of the same hand build this from the same steps with the same seeds,
    so the numbers do not jump when a finished hand opens as a replay.
    """

    def __init__(self, holes: dict[int, tuple[Card, Card]], *, samples: int = 600) -> None:
        self.holes = holes
        self.samples = samples
        self.live = set(holes)
        self.board: list[Card] = []
        self.rows: list[dict[str, Any]] = []
        self._snapshot(0)

    def take(self, position: int, step: dict[str, Any]) -> dict[str, Any] | None:
        """Note the step that brings the viewer to ``position``; the new row, if it adds one."""
        if step["kind"] == "action" and step["action"] == "fold" and step["seat"] in self.live:
            self.live.discard(step["seat"])
        elif step["kind"] == "street":
            self.board += [Card.parse(c) for c in step["cards"]]
        else:
            return None
        return self._snapshot(position)

    def _snapshot(self, position: int) -> dict[str, Any]:
        shares, exact = table_equities(
            {s: self.holes[s] for s in sorted(self.live)},
            self.board,
            samples=self.samples,
            seed=position,
        )
        row = {
            "step": position,
            "exact": exact,
            "equity": {str(s): round(v, 4) for s, v in shares.items()},
        }
        self.rows.append(row)
        return row


class Broadcast:
    """What a hand's viewers are told about everyone's cards: the win chances and the commentary.

    One object for a live table and for a replay, so the two can never tell a hand differently.
    """

    def __init__(self, holes: dict[int, tuple[Card, Card]], names: dict[int, str]) -> None:
        self.track = EquityTrack(holes)
        self.commentator = Commentator(holes, names)
        self.lines: list[dict[str, Any]] = []

    @property
    def rows(self) -> list[dict[str, Any]]:
        return self.track.rows

    def take(
        self, position: int, step: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Note the step that brings the viewer to ``position``; any new row, and any new line."""
        before = self.track.rows[-1]["equity"]
        row = self.track.take(position, step)
        line = self.commentator.take(position, step, before, row["equity"] if row else before)
        if line:
            self.lines.append(line)
        return row, line


@dataclass(slots=True)
class LiveSession:
    """Plays ``config.hands`` between ``agents`` on a thread, appending to ``path``.

    Everything that happens is published to subscribers as it happens: ``hand_start`` (the
    seats, and everyone's cards when nobody human is watching their own), ``acting`` (who is
    thinking), one ``step`` per engine event (posts, actions with their decision details,
    streets, showdown, wins), ``hand`` once it is on disk, ``turn`` when the browser seat must
    act and ``done`` at the end. ``pace`` slows a bot-only table down to watching speed.

    With ``tournament`` it plays that freezeout instead of ``config.hands`` cash hands, and the
    status says the level, how many are left and who went out in what place.

    With nobody human at the table the steps also carry everyone's win chances (``equity``),
    like a broadcast. Those numbers are worked out from every seat's cards, so a table with a
    player at it must never send them: they would tell the player how strong the others are.

    A table with two or more people is *shared*. Each person's page names its seat by the seat's
    token, and only that page is sent the seat's cards, turn, odds and coach line. A subscriber
    is someone (a person's name) or nobody, and what is published goes to everyone unless it is
    addressed to someone. A page with no token is the one person at a table for one, as it
    always was, and nobody at a shared table. ``time_limit`` gives each person that many seconds
    a decision before the table checks or folds for them; 0 waits for ever.
    """

    agents: Sequence[Agent]
    config: LeagueConfig
    path: Path
    pace: float = 0.0
    tournament: TournamentConfig | None = None
    time_limit: float = 0.0
    hands_played: int = 0
    running: bool = False
    finished: bool = False
    error: str = ""
    current: dict[str, Any] | None = None  # the hand in progress, in the viewer's shape: public
    _subscribers: list[tuple[queue.Queue[dict[str, Any]], str | None]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _thread: threading.Thread | None = None
    _published: int = 0  # engine events of the current hand already turned into steps
    _broadcast: Broadcast | None = None  # the current hand's win chances, for spectators only
    _holes: dict[str, list[str]] = field(default_factory=dict)  # this hand's cards, by name
    _seats: dict[str, int] = field(default_factory=dict)  # this hand's seat for each name
    _kinds: dict[int, str] = field(default_factory=dict)  # this hand's kind for each seat
    reviews: dict[str, dict[str, Any] | None] = field(default_factory=dict)  # by person
    _standing: dict[str, Any] = field(default_factory=dict)  # a freezeout's level, left, out

    def __post_init__(self) -> None:
        names = [a.name for a in self.agents]
        twice = sorted({name for name in names if names.count(name) > 1})
        if self.shared and twice:
            raise ValueError(f"two seats are called {twice[0]!r}: give each person their own name")

    @property
    def humans(self) -> list[WebHumanAgent]:
        return [a for a in self.agents if isinstance(a, WebHumanAgent)]

    @property
    def human(self) -> WebHumanAgent | None:
        return next(iter(self.humans), None)

    @property
    def shared(self) -> bool:
        """Two or more people: each sees only their own seat, through its own link."""
        return len(self.humans) > 1

    @property
    def spectator(self) -> bool:
        """No human at the table, so every seat's cards may be shown while the hand is played."""
        return self.human is None

    @property
    def review(self) -> dict[str, Any] | None:
        """The coach on the last hand, for the one person at a table for one."""
        person = self.person(None)
        return self.reviews.get(person.name) if person is not None else None

    def seat_for(self, token: str) -> WebHumanAgent | None:
        """The person whose link carries ``token``, or None."""
        found = None
        for agent in self.humans:
            if secrets.compare_digest(agent.token.encode(), token.encode()):
                found = agent
        return found

    def person(self, viewer: str | None) -> WebHumanAgent | None:
        """Whose page this is: the named person, else the one person at a table for one, else
        nobody (a bot table, or a page with no seat link at a shared table)."""
        if viewer is not None:
            return next((a for a in self.humans if a.name == viewer), None)
        return None if self.shared else self.human

    def start(self) -> None:
        if self._thread is not None:
            return
        for agent in self.humans:
            agent.session = self
            if self.time_limit:
                agent.time_limit = self.time_limit
        self.running = True
        self._thread = threading.Thread(target=self._run, name="poker-table-live", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        hooks = {
            "on_hand": self._on_hand,
            "on_hand_start": self._on_hand_start,
            "on_view": self._on_view,
            "on_decision": self._on_decision,
            "keep_histories": False,
        }
        try:
            if self.tournament is not None:
                run_tournament(self.agents, self.tournament, **hooks)
            else:
                run_league(self.agents, self.config, **hooks)
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
        current: dict[str, Any] = {
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
        if self.tournament is not None:
            level = self.tournament.level_number(self.hands_played)
            current["tournament"] = {"level": level, "left": hand.num_players, "out": {}}
            self._standing = {
                "level": level,
                "blinds": str(self.tournament.schedule[level - 1]),
                "left": hand.num_players,
                "out": self._standing.get("out", {}),
            }
        self._published = 0
        self._broadcast = None
        # A freezeout reseats the table as seats go out, so who sits where is read per hand.
        self._seats = {s.name: s.index for s in hand.seats}
        self._kinds = {s.index: s.kind for s in hand.seats}
        self._holes = {s.name: [str(c) for c in s.hole] for s in hand.seats if s.hole}
        # The maths costs up to 40 ms a row: nothing at watching speed, but real on a full-speed
        # session nobody has opened, so that one skips it.
        if reveal and (self.pace > 0 or self._subscribers):
            self._broadcast = Broadcast(
                {s.index: tuple(s.hole) for s in hand.seats if s.hole},
                {s.index: s.name for s in hand.seats},
            )
            current["equity"] = self._broadcast.rows
            current["commentary"] = self._broadcast.lines
        self.current = current  # a request thread may serialize it from here on
        self._flush(hand, publish=False)  # the blind posts travel inside hand_start
        # At a shared table each person is sent their own cards with the deal: waiting for
        # their first turn would leave them looking at card backs while others think.
        mine = {a.name: {"you": self._you(a.name)} for a in self.humans} if self.shared else None
        self.publish({"type": "hand_start", "hand": copy.deepcopy(self.current)}, mine=mine)
        self._sleep()

    def _you(self, name: str) -> dict[str, Any]:
        """A person's own seat in the hand being played: where they sit, and their two cards."""
        return {"name": name, "seat": self._seats.get(name), "hole": self._holes.get(name)}

    def _on_view(self, view: SeatView) -> None:
        kind = self._kinds.get(view.seat, "")
        self.publish({"type": "acting", "hand_id": view.hand_id, "seat": view.seat, "kind": kind})
        if not kind.startswith("llm:") and kind != "human":
            self._sleep()  # a bot decides instantly; the pace is where it visibly "thinks"

    def _on_decision(self, record: DecisionRecord, hand: Hand) -> None:
        # What a seat asked for before the table corrected it is private at a shared table, as
        # it is in the hand's public view afterwards: the step says only what was played.
        requested = record.applied if self.shared else record.requested
        details: dict[str, Any] = {
            "table_talk": record.table_talk,
            "illegal": record.illegal,
            "requested": str(requested),
            "latency_ms": round(record.latency_ms, 1),
        }
        if record.meta.get("timed_out"):
            details["timed_out"] = True  # the whole table sees the clock run out
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
            at = len(self.current["steps"])
            row, line = self._broadcast.take(at + 1, step) if self._broadcast else (None, None)
            self.current["steps"].append(step)
            if publish:
                message = {"type": "step", "hand_id": hand.hand_id, "at": at, "step": step}
                if row:
                    message["equity"] = row
                if line:
                    message["line"] = line
                self.publish(message)

    def _sleep(self) -> None:
        if self.pace > 0:
            time.sleep(min(self.pace, MAX_PACE))

    def _on_hand(self, history: HandHistory) -> None:
        write_jsonl(self.path, [history], append=True)
        self.hands_played += 1
        if history.tournament:
            out = history.tournament["out"]
            self._standing = self._standing | {
                "left": history.tournament["left"] - len(out),
                "out": self._standing.get("out", {}) | out,
            }
        self.current = None
        self._holes = {}
        event = {"type": "hand", "hand_id": history.hand_id, "hands": self.hands_played}
        for human in self.humans:
            # At a shared table the coach reads the hand as that person saw it, so their line
            # cannot depend on anyone else's folded cards.
            seen = public_view(history, human.name) if self.shared else history
            self.reviews[human.name] = review_of(seen, human.name)
        mine = {name: {"review": review} for name, review in self.reviews.items()}
        self.publish(event, mine=mine)

    # -- pub/sub for server-sent events --

    def publish(
        self,
        event: dict[str, Any],
        *,
        to: str | None = None,
        mine: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Send ``event`` to every page, or only to the pages of ``to``. ``mine`` adds what each
        person, by name, is sent of their own; a page of nobody gets the event alone."""
        with self._lock:
            for q, who in self._subscribers:
                if to is not None and who != to:
                    continue
                extra = mine.get(who) if mine is not None and who is not None else None
                q.put(event | extra if extra is not None else event)

    def subscribe(self, viewer: str | None = None) -> queue.Queue[dict[str, Any]]:
        """A queue of what ``viewer``'s page is sent (see ``person`` for a page with no name)."""
        person = self.person(viewer)
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._subscribers.append((q, person.name if person is not None else None))
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers[:] = [(s, who) for s, who in self._subscribers if s is not q]

    def status(self, viewer: str | None = None) -> dict[str, Any]:
        """The table as ``viewer``'s page may see it: the public hand, plus that person's own
        turn and coach line, and at a shared table their own cards from the deal on."""
        person = self.person(viewer)
        status = {
            "live": True,
            "running": self.running,
            "finished": self.finished,
            "error": self.error,
            "hands_played": self.hands_played,
            "hands_total": None if self.tournament is not None else self.config.hands,
            "tournament": dict(self._standing) if self.tournament is not None else None,
            "seats": [a.name for a in self.agents],
            "kinds": [getattr(a, "kind", "") for a in self.agents],
            "spectator": self.spectator,
            "pace": self.pace,
            "human": person.name if person else None,
            "turn": person.turn() if person else None,
            "review": self.reviews.get(person.name) if person else None,
            "current": self.current,
        }
        if self.shared:
            status["shared"] = True
            status["time_limit"] = self.time_limit or None
            dealt = person is not None and self.current is not None
            status["you"] = self._you(person.name) if dealt else None
        return status

    def sse(self, viewer: str | None = None, keepalive: float = 15.0) -> Iterator[str]:
        """Server-sent events for ``viewer``'s page: a hello with the status, then every event
        published to that page.

        The queue exists before the status is read, so nothing falls between the two. A step may
        arrive twice, in the hello's hand and as an event; its ``at`` says which one it is.
        """
        q = self.subscribe(viewer)
        try:
            yield f"data: {json.dumps({'type': 'hello', **self.status(viewer)})}\n\n"
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
    """A seat whose decisions come from the browser: act() blocks until submit() is called.

    ``token`` is the secret in the seat's link. With a ``time_limit`` and no answer in that many
    seconds, the seat checks if it can and folds if it cannot.
    """

    kind = "human"

    def __init__(self, name: str = "you", *, time_limit: float = 0.0) -> None:
        self.name = name
        self.token = secrets.token_urlsafe(16)  # 128 random bits: the link is the seat's only key
        self.time_limit = time_limit
        self.session: LiveSession | None = None
        self.pending: dict[str, Any] | None = None
        self._deadline: float | None = None
        self._answer: queue.Queue[Decision] = queue.Queue()
        self._submitting = threading.Lock()

    def act(self, view: SeatView) -> Decision:
        limit = self.time_limit if self.time_limit > 0 else None
        self._deadline = time.monotonic() + limit if limit else None
        self.pending = live_view_payload(view) | {"odds": own_odds(view), "time_limit": limit}
        if self.session is not None:
            event = {"type": "turn", "seat": view.seat, "hand_id": view.hand_id}
            self.session.publish(event, to=self.name)
        try:
            return self._answer.get(timeout=limit)
        except queue.Empty:
            pass
        # Time is up, unless an answer is on its way. This and submit() decide under one lock,
        # so a late click is never left waiting to play the seat's next turn.
        with self._submitting:
            if self.pending is not None:
                self.pending = None
                action = Action.check() if view.legal.can_check else Action.fold()
                return Decision(action, reasoning="out of time", meta={"timed_out": True})
        return self._answer.get()

    def turn(self) -> dict[str, Any] | None:
        """The decision waiting for this seat, with the seconds it has left; None if none is."""
        pending, deadline = self.pending, self._deadline
        if pending is None or deadline is None:
            return pending
        return pending | {"seconds_left": round(max(0.0, deadline - time.monotonic()), 1)}

    def submit(self, action_text: str, table_talk: str = "") -> Action:
        """Called from the request thread; the table's sanitizer handles off-by-a-bit sizes.

        The turn closes here rather than when the table wakes up, so a double click cannot
        leave a second answer waiting to play the seat's next turn unseen.
        """
        with self._submitting:
            if self.pending is None:
                raise LookupError("it is not your turn")
            action = parse_action(action_text)
            self.pending = None
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


def own_odds(view: SeatView) -> dict[str, Any]:
    """What a player could work out at the table: their cards against hands they cannot see. Only
    the view goes in, and a view never holds another seat's cards, so this cannot give them away.
    It has the shape of a spot in the replay's odds panel, and the same numbers."""
    spot = for_view(view, samples=ODDS_SAMPLES)
    return {
        "seat": view.seat,
        "name": view.name,
        "street": view.street.value,
        "pot": view.pot,
        "to_call": view.to_call,
        "pot_odds": round(spot.pot_odds, 4),
        "required": round(spot.required_equity, 4),
        "ratio": spot.ratio,
        "ev_call": round(spot.ev_call, 2),
        "blind": chance_row(spot),
        "known": None,
    }


def review_of(history: HandHistory, player: str) -> dict[str, Any] | None:
    """One line from the coach on how ``player`` played a finished hand, or None if there is
    nothing to say. The hand is over, so this may read everyone's cards."""
    from poker_table.coach.facts import tag_hand  # the coach imports the seats

    try:
        facts = tag_hand(history, player, samples=REVIEW_SAMPLES)
    except Exception:  # noqa: BLE001 - a review is a nicety, and must never stop the table
        return None
    graded = [fact for fact in facts if fact.ok is not None]
    if not graded:
        return None
    leaks = [fact for fact in graded if fact.ok is False]
    if not leaks:
        text = f"Coach, hand #{history.hand_id}: nothing to change."
    else:
        more = f" (and {len(leaks) - 1} more)" if len(leaks) > 1 else ""
        text = f"Coach, hand #{history.hand_id}, {leaks[0].street}: {leaks[0].detail}{more}."
    return {"hand_id": history.hand_id, "text": text}


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
