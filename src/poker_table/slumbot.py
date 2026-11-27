"""Slumbot's public API, as a benchmark for our own seats. It is not a seat.

Slumbot (https://www.slumbot.com/) is a strong heads-up no-limit bot. Its site offers an API "so
that researchers and hobbyists can test their bots against Slumbot". Its server deals the cards
and plays its own format: blinds of 50 and 100, stacks of 20,000 chips (200 big blinds), reset
after every hand. So Slumbot never sits at our table. One of our seats goes and plays there.

Our side of each hand runs on our own engine, fed the cards the API shows as it shows them. So
our seat is asked through the same :class:`SeatView` as at our table, and the hand is written as
an ordinary :class:`HandHistory`. Three things have to agree at every step: the API's answers,
the API's rules (read by :func:`parse_line`, written here from its protocol) and our engine. A
hand where they do not is refused and recorded, never counted. Slumbot's hole cards reach our
engine only with the answer that ends the hand, after our seat's last decision.

Politeness: one request at a time, at most four a second, a back-off on HTTP 429 and 5xx, no
login, no account and Python's own User-Agent.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Generator
from dataclasses import dataclass, field, replace
from typing import Any

from poker_table.agents.base import Agent, Decision, SeatView, TableShape
from poker_table.cards import Card
from poker_table.engine import Action, ActionType, EventKind, Hand, LegalActions, Player, Street
from poker_table.history import HandHistory
from poker_table.table import DecisionRecord, PlayedHand, ask, fallback_action, play_turns, sanitize

BASE_URL = "https://slumbot.com/slumbot/api/"
SMALL_BLIND = 50
BIG_BLIND = 100
STACK = 20_000
SHAPE = TableShape(seats=2, small_blind=SMALL_BLIND, big_blind=BIG_BLIND, ante=0, stack=STACK)

NAME = "slumbot"  # its seat in our hand histories
KIND = "slumbot-api"  # not a kind --seats takes: Slumbot cannot sit at our table
OURS, THEIRS = 0, 1  # seat numbers in our engine
BIG, SMALL = 0, 1  # positions as the API numbers them: client_pos 0 is the big blind

_STREETS = (Street.PREFLOP, Street.FLOP, Street.TURN, Street.RIVER)
_BOARD_AT = (0, 3, 4, 5)


class Refused(Exception):
    """A hand that does not fit. It is recorded with the reason and never counted.

    ``stop`` asks the run to stop as well: chips that do not add up, or a seat that crashed,
    mean something is wrong beyond this hand.
    """

    def __init__(self, reason: str, *, stop: bool = False) -> None:
        super().__init__(reason)
        self.stop = stop


class Unavailable(Exception):
    """The API kept failing after the back-off, so the run stops."""


# ----- the API's rules --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Move:
    """One action in an API action string."""

    street: int  # 0 preflop, 1 flop, 2 turn, 3 river
    position: int  # BIG or SMALL
    code: str  # "k" check, "c" call, "f" fold, "b" bet or raise
    to: int = 0  # for "b": the chips the bettor has in on this street after it

    def __str__(self) -> str:
        return f"b{self.to}" if self.code == "b" else self.code


@dataclass(frozen=True, slots=True)
class Line:
    """Where a hand stands after an action string, by the API's rules."""

    moves: tuple[Move, ...]
    street: int
    to_act: int | None  # the position due to act, None once the hand is over
    ended: str  # "" while the hand is on, then "fold" or "showdown"
    put_in: tuple[int, int]  # chips each position has in the pot, by position

    @property
    def board_size(self) -> int:
        """How many board cards the API should be showing."""
        return 5 if self.ended == "showdown" else _BOARD_AT[self.street]


_STREET_TEXT = re.compile(r"(?:[kcf]|b\d+)*")
_TOKEN = re.compile(r"[kcf]|b(\d+)")


def parse_line(text: str) -> Line:
    """Read an action string such as ``b200c/kb400c/kk/kk``, checking every move is legal.

    Streets are separated by ``/``. ``b<n>`` puts the bettor at n chips on this street. Preflop
    the small blind acts first; after it the big blind does. Once both players are all in, the
    streets left have no moves, and their slashes are either all there or all missing.
    """
    streets = text.split("/")
    if len(streets) > 4:
        raise Refused(f"{text!r} has more than four streets")
    reader = _Reader(text)
    for number, street in enumerate(streets):
        if number:
            reader.next_street()
        if not _STREET_TEXT.fullmatch(street):
            raise Refused(f"{text!r}: cannot read {street!r}")
        for token in _TOKEN.finditer(street):
            reader.move(token.group(0)[0], int(token.group(1) or 0))
    if reader.all_in_on is not None and len(streets) not in (reader.all_in_on + 1, 4):
        raise Refused(f"{text!r}: after the all-in, some of the streets left have a slash")
    if reader.closed and not reader.ended:
        reader.next_street()  # "b200c" is the flop about to start, like "b200c/"
    return Line(
        moves=tuple(reader.moves),
        street=reader.street,
        to_act=reader.to_act,
        ended=reader.ended,
        put_in=(reader.put_in[0], reader.put_in[1]),
    )


class _Reader:
    def __init__(self, text: str) -> None:
        self.text = text
        self.street = 0
        self.on_street = [BIG_BLIND, SMALL_BLIND]  # by position
        self.put_in = [BIG_BLIND, SMALL_BLIND]
        self.acted = [False, False]
        self.to_act: int | None = SMALL
        self.min_raise = BIG_BLIND  # what a raise must add at least, unless it is all in
        self.closed = False  # this street's betting is over
        self.ended = ""
        self.all_in_on: int | None = None  # the street both players went all in on
        self.moves: list[Move] = []

    def refuse(self, why: str) -> Refused:
        return Refused(f"{self.text!r}: {why}")

    def move(self, code: str, to: int) -> None:
        if self.ended or self.closed or self.to_act is None:
            raise self.refuse("a move after the betting was over")
        me, them = self.to_act, 1 - self.to_act
        high = max(self.on_street)
        owed = high - self.on_street[me]
        if code == "k" and owed:
            raise self.refuse("a check facing a bet")
        if code in "cf" and not owed:
            raise self.refuse(f"{'a call' if code == 'c' else 'a fold'} with nothing to call")
        if code == "c":
            self.pay(me, owed)
        elif code == "f":
            self.ended = "fold"
        elif code == "b":
            most = self.on_street[me] + STACK - self.put_in[me]
            raise_by = to - high
            if raise_by <= 0 or to > most:
                raise self.refuse(f"b{to} is not a raise this seat can make (it can go to {most})")
            if raise_by < self.min_raise and to != most:
                raise self.refuse(f"b{to} raises by {raise_by}, less than the {self.min_raise} due")
            self.pay(me, to - self.on_street[me])
            self.min_raise = max(self.min_raise, raise_by)
            self.acted[them] = False
        self.acted[me] = True
        self.moves.append(Move(self.street, me, code, to if code == "b" else 0))
        if self.ended:
            self.to_act = None
        elif all(self.acted) and self.on_street[BIG] == self.on_street[SMALL]:
            self.close_street()
        else:
            self.to_act = them

    def pay(self, position: int, chips: int) -> None:
        self.on_street[position] += chips
        self.put_in[position] += chips

    def close_street(self) -> None:
        self.closed = True
        if self.put_in[BIG] == STACK:  # both all in: the streets left have no betting
            self.all_in_on = self.street
            self.ended, self.to_act = "showdown", None
        elif self.street == 3:
            self.ended, self.to_act = "showdown", None

    def next_street(self) -> None:
        if self.all_in_on is not None:
            self.street += 1  # an empty street after the all-in
            return
        if self.ended:
            raise self.refuse(f"a street after the hand ended in a {self.ended}")
        if not self.closed:
            raise self.refuse("a '/' before the street's betting was over")
        self.street += 1
        self.on_street = [0, 0]
        self.acted = [False, False]
        self.min_raise = BIG_BLIND
        self.closed = False
        self.to_act = BIG


def move_for(action: Action, street: int, position: int) -> Move:
    """Our engine's action as the API writes it. Both count a bet as the street total."""
    code = {ActionType.FOLD: "f", ActionType.CHECK: "k", ActionType.CALL: "c"}.get(action.type)
    if code is None:
        return Move(street, position, "b", action.amount)
    return Move(street, position, code)


def action_for(move: Move, legal: LegalActions) -> Action:
    """The API's move as our engine's action, refused unless our engine allows it as it is."""
    match move.code:
        case "k":
            action = Action.check()
        case "c":
            action = Action.call()
        case "f":
            action = Action.fold()
        case _:
            action = Action.bet(move.to) if legal.is_bet else Action.raise_to(move.to)
    if sanitize(action, legal) != action:
        raise Refused(f"our engine does not allow Slumbot's {move} here ({legal.describe()})")
    return action


# ----- the HTTP side ----------------------------------------------------------------------


class Api:
    """Slumbot's two endpoints, one request at a time and no faster than four a second.

    The token the API hands out is kept in memory and sent with every request, and it never
    goes into :attr:`log`. ``new_hand`` is retried with a back-off when the server is busy or
    cannot be reached. ``act`` is retried only on HTTP 429: a lost answer may mean the move
    was played, and sending it again could play it twice, so the hand is refused instead.
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        interval: float = 0.25,
        timeout: float = 30.0,
        retries: int = 5,
        backoff: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url
        self.interval = interval
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.sleep = sleep
        self.clock = clock
        self.requests = 0
        self.log: list[dict[str, Any]] = []  # this hand's exchanges, without the token
        self._token: str | None = None
        self._last: float | None = None

    def new_hand(self) -> dict[str, Any]:
        return self._post("new_hand", {}, resend=True)

    def act(self, incr: str) -> dict[str, Any]:
        return self._post("act", {"incr": incr}, resend=False)

    def _post(self, endpoint: str, body: dict[str, Any], *, resend: bool) -> dict[str, Any]:
        last = ""
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleep(self.backoff * 2 ** (attempt - 1))
            status, reply, error = self._send(endpoint, body)
            if status == 200 and reply is not None:
                return reply
            last = error or f"HTTP {status}"
            if status == 429:
                continue
            if status is not None and status < 500:
                raise Unavailable(f"{endpoint} answered HTTP {status}")
            if not resend:
                raise Refused(f"{endpoint} got no usable answer ({last}), so it was not resent")
        raise Unavailable(f"{endpoint} failed {self.retries + 1} times in a row; the last: {last}")

    def _send(
        self, endpoint: str, body: dict[str, Any]
    ) -> tuple[int | None, dict[str, Any] | None, str]:
        if self._last is not None:
            wait = self.interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()
        payload = {"token": self._token, **body} if self._token else dict(body)
        request = urllib.request.Request(
            self.base_url + endpoint,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        status: int | None = None
        text = b""
        error = ""
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status, text = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, text = exc.code, exc.read()
        except OSError as exc:  # no connection, a timeout, a reset
            error = f"{type(exc).__name__}: {exc}"
        took = (self.clock() - self._last) * 1000
        self.requests += 1
        reply = None
        if text:
            try:
                reply = json.loads(text)
            except ValueError:
                error = "the answer was not JSON"
        if reply is not None and not isinstance(reply, dict):
            reply, error = None, "the answer was not a JSON object"
        if reply is not None and reply.get("token"):
            self._token = reply["token"]
        entry: dict[str, Any] = {"endpoint": endpoint, "request": dict(body), "status": status}
        if reply is not None:
            entry["response"] = {k: v for k, v in reply.items() if k != "token"}
        if error:
            entry["error"] = error
        entry["ms"] = round(took, 1)
        self.log.append(entry)
        return status, reply, error


# ----- one hand ---------------------------------------------------------------------------


@dataclass(slots=True)
class Played:
    """One hand on the API: our history of it, or why it was refused."""

    hand_id: str
    position: int | None = None  # client_pos: BIG or SMALL (the button)
    history: HandHistory | None = None
    winnings: int | None = None  # our chips, as Slumbot reports them
    baseline: int | None = None  # Slumbot's "baseline_winnings" for the hand
    refused: str = ""
    stop: bool = False
    exchanges: list[dict[str, Any]] = field(default_factory=list)

    def record(self, run: int) -> dict[str, Any]:
        return {
            "hand": self.hand_id,
            "run": run,
            "client_pos": self.position,
            "refused": self.refused,
            "winnings": self.winnings,
            "baseline_winnings": self.baseline,
            "exchanges": self.exchanges,
        }


class _FedDeck:
    """Deals our engine the cards Slumbot's server dealt, as the API shows them.

    Slumbot's seat gets two stand-in cards: its real ones are not known until the hand ends.
    The board comes from the API's answers, and the engine asking for a card the API has not
    shown yet is a refusal.
    """

    def __init__(self, ours: tuple[Card, Card], button: int) -> None:
        # Any two cards that are not ours. Four deuces always leave two, whatever we hold.
        stand_in = [c for c in map(Card.parse, ("2c", "2d", "2h", "2s")) if c not in ours]
        holes = {OURS: ours, THEIRS: (stand_in[0], stand_in[1])}
        first = 1 - button  # the engine deals from the seat left of the button
        self._holes = [holes[first][0], holes[button][0], holes[first][1], holes[button][1]]
        self.board: list[Card] = []
        self._dealt = 0

    def draw(self) -> Card:
        if not self._holes:
            raise Refused("our engine asked for a card the API has not shown")
        return self._holes.pop(0)

    def deal(self, count: int) -> list[Card]:
        if self._dealt + count > len(self.board):
            raise Refused("our engine dealt a street before the API showed its cards")
        cards = self.board[self._dealt : self._dealt + count]
        self._dealt += count
        return cards

    def show(self, board: list[Card]) -> None:
        if board[: len(self.board)] != self.board:
            raise Refused("the API changed board cards it had already shown")
        self.board = board


def _cards(raw: Any, count: int, what: str) -> list[Card]:
    if not isinstance(raw, list) or len(raw) != count:
        raise Refused(f"{what}: expected {count} cards, got {raw!r}")
    try:
        return [Card.parse(str(c)) for c in raw]
    except ValueError as exc:
        raise Refused(f"{what}: {exc}") from None


@dataclass(slots=True)
class _Pending:
    move: Move
    action: Action  # what our engine must play: the decision once the table has checked it
    decision: Decision
    think_ms: float


def play_hand(agent: Agent, api: Api, hand_id: str) -> Played:
    """Play one hand on the API with ``agent`` in our seat. Never raises for a bad hand: a
    hand that does not fit comes back refused, with the exchanges so far."""
    api.log = []
    played = Played(hand_id, exchanges=api.log)
    try:
        _play(agent, api, played)
    except Refused as exc:
        played.history, played.refused, played.stop = None, str(exc), exc.stop
    except Unavailable as exc:
        played.history, played.refused, played.stop = None, f"the run stopped: {exc}", True
    except Exception as exc:  # a bug of ours: keep the hand's requests on file and stop
        played.history, played.stop = None, True
        played.refused = f"our client failed: {type(exc).__name__}: {exc}"
    return played


def _play(agent: Agent, api: Api, played: Played) -> None:
    reply = _checked(api.new_hand(), "new_hand")
    position = reply.get("client_pos")
    if position not in (BIG, SMALL):
        raise Refused(f"client_pos is {position!r}, not 0 or 1")
    played.position = position
    seat_of = {position: OURS, 1 - position: THEIRS}
    hole = _cards(reply.get("hole_cards"), 2, "our hole cards")
    button = seat_of[SMALL]
    deck = _FedDeck((hole[0], hole[1]), button)
    players = [
        Player(agent.name, STACK, kind=getattr(agent, "kind", "")),
        Player(NAME, STACK, kind=KIND),
    ]
    hand = Hand(
        players,
        button=button,
        small_blind=SMALL_BLIND,
        big_blind=BIG_BLIND,
        seed=0,  # no deck of ours: Slumbot's server shuffled
        hand_id=played.hand_id,
        deck=deck,  # type: ignore[arg-type]  # draw() and deal() are all the engine uses
    )
    records: list[DecisionRecord] = []
    turns = play_turns(hand, on_decision=lambda record, _: records.append(record))
    result: list[PlayedHand] = []
    view: SeatView | None = next(turns)
    text = ""  # the action string as the API last sent it
    pending: _Pending | None = None
    thinking: list[float] = []
    cards_known = False
    shown = True

    while True:
        if reply.get("client_pos") != position or reply.get("hole_cards") != [*map(str, hole)]:
            raise Refused("the API changed our position or our cards in the middle of the hand")
        line = parse_line(str(reply.get("action", "")))
        if line.moves[: len(records)] != tuple(_moves_so_far(records, hand, seat_of)):
            raise Refused(f"the API's {reply.get('action')!r} rewrote moves already played")
        board = _cards(reply.get("board"), line.board_size, "the board")
        deck.show(board)
        final = "winnings" in reply
        if final:
            shown = _reveal(hand, reply, line, hole, board)
            cards_known = True
        for move in line.moves[len(records) :]:
            seat = seat_of[move.position]
            if view is None:
                raise Refused(f"the API has {move} after our engine ended the hand")
            if view.seat != seat:
                raise Refused(
                    f"the API has {move} for seat {seat}, our engine asks seat {view.seat}"
                )
            if seat == THEIRS:
                decision = Decision(action_for(move, view.legal), meta={"api": str(move)})
                expected = decision.action
            else:
                if pending is None or pending.move != move:
                    raise Refused(
                        f"the API has {move} for our seat, which sent {pending and pending.move}"
                    )
                decision, expected = pending.decision, pending.action
                thinking.append(pending.think_ms)
                pending = None
            before = len(records)
            view = _send(turns, decision, result)
            # Slumbot's move must be legal as it stands. Ours may be the table's fallback for
            # an illegal one, which counts against our seat as it does at our table.
            if len(records) != before + 1 or records[-1].applied != expected:
                raise Refused(f"our engine did not play {move} as it was sent")
            if seat == THEIRS and records[-1].illegal:
                raise Refused(f"our engine does not allow Slumbot's {move}")
            if cards_known and view is not None and view.seat == OURS:
                raise Refused("our seat would be asked after Slumbot's cards were shown")
        text = str(reply.get("action", ""))
        if final:
            break
        _agree(hand, view, line, position, seat_of)
        assert view is not None
        pending = _decide(agent, view, line, text, position)
        reply = _checked(api.act(str(pending.move)), f"act {pending.move}", old=text)

    if not result or not hand.finished:
        raise Refused("the API ended the hand and our engine did not")
    net = hand.net()
    winnings = reply.get("winnings")
    played.winnings = winnings if isinstance(winnings, int) else None
    baseline = reply.get("baseline_winnings")
    played.baseline = baseline if isinstance(baseline, int) else None
    if net[OURS] != winnings or sum(net.values()) != 0:
        raise Refused(
            f"chips do not add up: our engine has {net[OURS]:+} for our seat, the API {winnings}",
            stop=True,
        )
    decisions = result[0].decisions
    ours = iter(thinking)
    for i, record in enumerate(decisions):
        if record.seat == OURS:  # the time to decide, not the round trip to the API
            decisions[i] = replace(record, latency_ms=next(ours))
    history = HandHistory.from_played(result[0])
    if not shown:  # the stand-in cards must not reach the record
        history = replace(
            history,
            players=[replace(p, hole=[]) if p.seat == THEIRS else p for p in history.players],
            events=[
                e
                for e in history.events
                if not (e.kind == EventKind.DEAL_HOLE.value and e.seat == THEIRS)
            ],
        )
    played.history = history


def _checked(reply: dict[str, Any], what: str, *, old: str = "") -> dict[str, Any]:
    if "error_msg" in reply:
        raise Refused(f"the API refused {what}: {reply['error_msg']}")
    if reply.get("old_action", "") != old:
        raise Refused(f"{what}: the API had {reply.get('old_action')!r} before it, we had {old!r}")
    return reply


def _moves_so_far(records: list[DecisionRecord], hand: Hand, seat_of: dict[int, int]) -> list[Move]:
    """The moves our engine has played, as the API writes them."""
    position_of = {seat: position for position, seat in seat_of.items()}
    streets = [e.street for e in hand.events if e.kind is EventKind.ACTION]
    return [
        move_for(r.applied, _STREETS.index(street), position_of[r.seat])
        for r, street in zip(records, streets, strict=True)
    ]


def _send(
    turns: Generator[SeatView, Decision, PlayedHand], decision: Decision, result: list[PlayedHand]
) -> SeatView | None:
    try:
        return turns.send(decision)
    except StopIteration as done:
        result.append(done.value)
        return None


def _reveal(
    hand: Hand, reply: dict[str, Any], line: Line, hole: list[Card], board: list[Card]
) -> bool:
    """Give our engine Slumbot's real cards, once the hand is over and before its showdown.

    The API has shown them at the end of every hand so far, folds included. Without them a
    showdown cannot be scored, so it is refused, and a fold keeps the stand-ins until the
    record is written without them (False)."""
    raw = reply.get("bot_hole_cards")
    if raw is None and line.ended == "showdown":
        raise Refused("a showdown without Slumbot's cards")
    theirs = _cards(raw, 2, "Slumbot's cards") if raw is not None else []
    dealt = [*hole, *theirs, *board]
    if len(set(dealt)) != len(dealt):
        raise Refused(f"a card is dealt twice: {' '.join(map(str, dealt))}")
    if not theirs:
        return False
    hand.seats[THEIRS].hole = (theirs[0], theirs[1])
    for i, event in enumerate(hand.events):
        if event.kind is EventKind.DEAL_HOLE and event.seat == THEIRS:
            hand.events[i] = replace(event, cards=(theirs[0], theirs[1]))
    return True


def _agree(
    hand: Hand, view: SeatView | None, line: Line, position: int, seat_of: dict[int, int]
) -> None:
    """Our engine and the API must agree on where the hand stands before our seat is asked."""
    if view is None or hand.finished:
        raise Refused("our engine ended the hand and the API did not")
    if line.to_act != position or view.seat != OURS:
        raise Refused(f"the API has position {line.to_act} to act, our engine seat {view.seat}")
    if hand.street is not _STREETS[line.street]:
        raise Refused(f"the API is on street {line.street}, our engine on the {hand.street}")
    for pos in (BIG, SMALL):
        if hand.seats[seat_of[pos]].total_bet != line.put_in[pos]:
            raise Refused(
                f"position {pos} has {line.put_in[pos]} in the pot for the API, "
                f"{hand.seats[seat_of[pos]].total_bet} for our engine"
            )


def _decide(agent: Agent, view: SeatView, line: Line, text: str, position: int) -> _Pending:
    """Ask our seat, then check its move against the API's rules before it is sent."""
    started = time.perf_counter()
    decision = ask(agent, view)
    think = (time.perf_counter() - started) * 1000
    if decision.reasoning.startswith("agent error"):
        raise Refused(f"our seat crashed: {decision.reasoning}", stop=True)
    action = sanitize(decision.action, view.legal) or fallback_action(view.legal)
    if action.type is ActionType.FOLD and view.legal.can_check:
        # Our table lets a seat fold when it could check. The API does not, so it checks.
        meta = {**decision.meta, "asked": str(decision.action), "sent": "k: the API refuses f here"}
        decision = replace(decision, action=Action.check(), meta=meta)
        action = Action.check()
    move = move_for(action, line.street, position)
    slash = "/" * (line.street - text.count("/"))  # a street the API closed without one
    after = parse_line(text + slash + str(move))
    if not after.moves or after.moves[-1] != move:
        raise Refused(f"{move} after {text!r} is not ours to make")
    return _Pending(move, action, decision, think)
