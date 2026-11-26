"""Friends at the table: several people and bots at one live table, each person on a private link.

Most of this file is one rule. While a hand is played, a request carrying seat A's link, or no
link, never gets seat B's hole cards or reasoning, anyone's win chances, or a coach line about B.
Once the hand is over, A gets the public view of it (the board, the actions, the cards turned
over at showdown) plus A's own seat. The scan below asks every route the app has, so a new route
that leaks fails it, and it is shown to find each kind of leak where one is allowed to exist.
"""

from __future__ import annotations

import io
import json
import re
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from itertools import chain
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount

from poker_table.agents.base import Decision, make_view
from poker_table.agents.scripted import CallingStation, TightAggressive
from poker_table.cli import main
from poker_table.engine import Hand, Player
from poker_table.history import HandHistory, public_view, read_jsonl
from poker_table.league import LeagueConfig
from poker_table.web.app import create_app
from poker_table.web.live import LiveSession, WebHumanAgent, review_of

PEOPLE = ("ann", "bob")
SECRET = "SECRET-REASON"
CARD = re.compile(r"[2-9TJQKA][cdhs]")


def wait_until(check: Callable[[], Any], seconds: float = 10.0) -> None:
    end = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.005)


class Whispers:
    """A bot whose every reason and note carries a marker, so a leaked one is easy to find."""

    def act(self, view):
        decision = super().act(view)
        return Decision(
            decision.action,
            reasoning=f"{SECRET} of {self.name}",
            table_talk=decision.table_talk,
            meta={"note": SECRET},
        )


class Station(Whispers, CallingStation):
    """Never folds, so its cards are turned over at the showdowns."""


class Tag(Whispers, TightAggressive):
    """Folds most hands, so its cards stay hidden."""


def shared_table(path: Path, hands: int = 3, **kwargs: Any) -> LiveSession:
    seats = [*(WebHumanAgent(name) for name in PEOPLE), Station("station"), Tag("tag")]
    path.touch()
    return LiveSession(seats, LeagueConfig(hands=hands, seed=5, buy_in=100), path, **kwargs)


def answer(human: WebHumanAgent, turn: dict[str, Any]) -> str:
    """ann checks or calls everything; bob folds to a bet in the odd hands."""
    legal = turn["legal"]
    if human.name == "bob" and int(turn["hand_id"]) % 2 == 1 and not legal["can_check"]:
        return "fold"
    return "check" if legal["can_check"] else "call"


def play_out(session: LiveSession, on_turn: Callable[[WebHumanAgent, dict], None]) -> None:
    """Answer for each person until the session is over, calling ``on_turn`` at the first
    decision each person faces in each hand, while the table waits for them."""
    asked: set[tuple[str, str]] = set()
    while not session.finished:
        for human in session.humans:
            turn = human.turn()
            if turn is None:
                continue
            if (turn["hand_id"], human.name) not in asked:
                asked.add((turn["hand_id"], human.name))
                on_turn(human, turn)
            human.submit(answer(human, turn))
        time.sleep(0.002)


# ----- every route the app has ------------------------------------------------------------

STREAMS = {"/api/events"}  # read from before the first deal to the end instead
BODIES: dict[str, Callable[[str], dict[str, Any]]] = {
    # A body each route refuses, or one that changes nothing, so the scan never plays a move.
    "/api/act": lambda player: {"action": "shove"},
    "/api/live/pace": lambda player: {"pace": 0},
    "/api/coach/narrate": lambda player: {"player": player},
    "/api/drill/answer": lambda player: {"player": player, "key": "no such spot", "action": "f"},
}


def _params(dependant: Any, kind: str) -> list[Any]:
    found = list(getattr(dependant, kind))
    for sub in dependant.dependencies:
        found += _params(sub, kind)
    return found


def every_request(
    app: Any, hand_ids: Iterable[str], players: Iterable[str]
) -> list[tuple[str, str, dict[str, Any], dict[str, Any] | None]]:
    """A request for every route of ``app``, with every value its parameters can take here.
    A route this cannot fill fails the test: a new route must say what it is asked with."""
    hand_ids, players = [*hand_ids, "no-such-hand"], list(players)
    out = []
    for route in app.routes:
        if isinstance(route, Mount):
            assert route.path == "/static", route.path  # files from disk, the same for everyone
            continue
        if not isinstance(route, APIRoute):
            out.append(("GET", route.path, {}, None))  # /openapi.json: the routes, no hands
            continue
        if route.path in STREAMS:
            continue
        paths = {f.name for f in _params(route.dependant, "path_params")}
        required = {
            f.name for f in _params(route.dependant, "query_params") if f.field_info.is_required()
        }
        unknown = (paths - {"hand_id"}) | (required - {"player"})
        assert not unknown, f"{route.path} needs {sorted(unknown)}: give this test values for it"
        for method in sorted(route.methods):
            posting = method == "POST"
            assert not posting or route.path in BODIES, f"POST {route.path}: give it a body here"
            for hand_id in hand_ids if "hand_id" in paths else [None]:
                for player in players if "player" in required or posting else [None]:
                    url = route.path.replace("{hand_id}", hand_id) if hand_id else route.path
                    query: dict[str, Any] = {"player": player} if "player" in required else {}
                    if route.path == "/api/coach":
                        query["samples"] = 20
                    body = BODIES[route.path](player) if posting else None
                    out.append((method, url, query, body))
    return out


Answer = tuple[str, int, str, str | None]  # what was asked, status, body, the seat it names


def ask_everything(
    app: Any, token: str | None, hand_ids: Iterable[str], players: Iterable[str]
) -> list[Answer]:
    client = TestClient(app)
    seat = {"seat": token} if token else {}
    answers = []
    for method, url, query, body in every_request(app, hand_ids, players):
        response = client.request(method, url, params=query | seat, json=body)
        about = query.get("player") or (body or {}).get("player")
        answers.append((f"{method} {url} {query}", response.status_code, response.text, about))
    return answers


def check_answers(oracle: Oracle, answers: list[Answer], viewer: str | None, **when) -> None:
    for asked, status, text, about in answers:
        assert oracle.leaks(text, viewer, **when) == [], (who(viewer), asked, status)
        # A coach report or drill about another seat is about their play, whatever it shows.
        assert about in (None, viewer) or status >= 400, (who(viewer), asked, status)


def read_stream(app: Any, token: str | None, into: dict[Any, str], key: Any) -> threading.Thread:
    """Read the live stream, as a page with ``token`` would, until the session ends."""

    def read() -> None:
        into[key] = TestClient(app).get("/api/events", params={"seat": token} if token else {}).text

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    return reader


# ----- what each reader may be sent ---------------------------------------------------------


def decode(text: str) -> list[Any]:
    """A body as JSON values: one, or one per server-sent event; none for the page itself."""
    if text.startswith("data: ") or text.startswith(": "):
        return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    try:
        return [json.loads(text)]
    except ValueError:
        return []


def walk(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def card_pairs(values: Iterable[Any]) -> set[frozenset[str]]:
    """Every list of exactly two cards, which is how hole cards travel in every payload."""
    return {
        frozenset(v)
        for v in chain.from_iterable(map(walk, values))
        if isinstance(v, list)
        and len(v) == 2
        and all(isinstance(c, str) and CARD.fullmatch(c) for c in v)
    }


class Oracle:
    """What a reader must not be sent, worked out from the finished hands in the file."""

    def __init__(self, hands: list[HandHistory]) -> None:
        self.hands = hands
        self.seats = {p.name: p.seat for p in hands[0].players}  # a cash table never reseats
        self.reviews = {
            name: {h.hand_id: review_of(public_view(h, name), name) for h in hands}
            for name in PEOPLE
        }

    def hidden(self, viewer: str | None, during: str | None) -> set[frozenset[str]]:
        """Pairs of hole cards ``viewer`` must not see: while hand ``during`` is played, every
        other seat's in it and the folded ones before it; afterwards, every hand's folded ones."""
        mine = self.seats.get(viewer)
        hidden: set[frozenset[str]] = set()
        allowed: set[frozenset[str]] = set()
        for history in self.hands:
            for p in history.players:
                pair = frozenset(p.hole)
                if p.seat == mine:
                    allowed.add(pair)
                elif history.hand_id == during or p.seat not in history.showdown:
                    hidden.add(pair)
                else:
                    allowed.add(pair)
            if history.hand_id == during:
                break
        return hidden - allowed

    def leaks(self, text: str, viewer: str | None, during: str | None = None) -> list[str]:
        """Each kind of thing in ``text`` that ``viewer`` should not have been sent."""
        found = set()
        if SECRET in text:
            found.add("a bot's reasoning")
        if any(str(h.seed) in text for h in self.hands):
            found.add("a deck's seed")
        values = decode(text)
        if card_pairs(values) & self.hidden(viewer, during):
            found.add("someone else's hole cards")
        mine = self.seats.get(viewer)
        for d in chain.from_iterable(map(walk, values)):
            if not isinstance(d, dict):
                continue
            if isinstance(d.get("equity"), dict | list):
                found.add("win chances")
            if d.get("commentary") or d.get("table") or d.get("line"):
                found.add("the broadcast")
            if "seed" in d:
                found.add("a deck's seed")
            seat = d.get("seat")
            if isinstance(seat, int) and seat != mine and (d.get("reasoning") or d.get("meta")):
                found.add("someone else's reasoning")
            you = d.get("you")
            if isinstance(you, dict) and you.get("name") != viewer:
                found.add("someone else's seat")
            said = d.get("text")
            if isinstance(said, str) and said.startswith("Coach, hand #"):
                if viewer is None or d != self.reviews[viewer].get(d.get("hand_id")):
                    found.add("a coach line that is not theirs")
            if "spots" in d and "hand_id" in d:
                hand = next((h for h in self.hands if h.hand_id == d["hand_id"]), None)
                shown = set(hand.showdown) if hand else set()
                if any(s["seat"] not in shown | {mine} for s in d["spots"]):
                    found.add("someone else's odds")
        return sorted(found)

    def stream_leaks(self, text: str, viewer: str | None) -> list[str]:
        """The same, for a live stream: each event is read as of the hand it belongs to."""
        found: set[str] = set()
        for event in decode(text):
            hand_id = event.get("hand_id") or (event.get("hand") or event.get("current") or {}).get(
                "hand_id"
            )
            step = event.get("step") or {}
            if step.get("kind") == "showdown":  # turned over for everyone to see
                continue
            found |= set(self.leaks(json.dumps(event), viewer, during=hand_id))
        return sorted(found)


def who(viewer: str | None) -> str:
    return viewer or "nobody"


@pytest.fixture
def paid_call(monkeypatch) -> list:
    """Stands in for the model call on the host's key, and records whether it was reached."""
    from poker_table.coach.narrate import Narration

    called: list = []

    def fake(report, *, model, **_):
        called.append(model)
        return Narration((), "", 0, model, 0.0)

    monkeypatch.setattr("poker_table.coach.narrate.narrate", fake)
    return called


# ----- the privacy rule -----------------------------------------------------------------------


def test_no_page_is_sent_what_its_seat_could_not_see(tmp_path: Path, paid_call: list) -> None:
    path = tmp_path / "friends.jsonl"
    session = shared_table(path)
    app = create_app(path, session)
    tokens: dict[str | None, str | None] = {h.name: h.token for h in session.humans}
    tokens[None] = None  # the watching link: no seat at all
    names = [a.name for a in session.agents]

    streams: dict[str | None, str] = {}
    readers = [read_stream(app, token, streams, viewer) for viewer, token in tokens.items()]
    wait_until(lambda: len(session._subscribers) == len(tokens))  # from before the first deal

    during: list[tuple[str, str | None, list[Answer]]] = []

    def scan(human: WebHumanAgent, turn: dict[str, Any]) -> None:
        finished = [h.hand_id for h in read_jsonl(path)]
        for viewer, token in tokens.items():
            during.append((turn["hand_id"], viewer, ask_everything(app, token, finished, names)))

    session.start()
    play_out(session, scan)
    session.join(10)
    for reader in readers:
        reader.join(10)
    assert session.error == "" and session.hands_played == 3

    hands = list(read_jsonl(path))
    oracle = Oracle(hands)
    bob = oracle.seats["bob"]
    assert any(bob in h.showdown for h in hands), "bob should reach a showdown"
    assert any(bob not in h.showdown for h in hands), "bob should fold a hand"

    # While each hand was played: every route, for each of ann, bob and nobody.
    assert {(hand_id, viewer) for hand_id, viewer, _ in during} >= {
        (h.hand_id, v) for h in hands for v in tokens
    }
    for hand_id, viewer, answers in during:
        check_answers(oracle, answers, viewer, during=hand_id)
    # The live stream, from before the first deal to the end.
    for viewer in tokens:
        assert oracle.stream_leaks(streams[viewer], viewer) == [], who(viewer)
    # Once the hands are over: every hand's replay, odds, and everything else.
    finished = [h.hand_id for h in hands]
    for viewer, token in tokens.items():
        check_answers(oracle, ask_everything(app, token, finished, names), viewer)
    assert paid_call == []  # and no link could spend the host's key

    # The scan finds each kind of leak where one may exist. bob's own pages hold his cards and
    # his coach lines, which ann's reading of them must catch.
    hand_id, _, bobs = next(entry for entry in during if entry[1] == "bob")
    bobs_status = next(text for asked, _, text, _ in bobs if asked.startswith("GET /api/live "))
    assert "someone else's hole cards" in oracle.leaks(bobs_status, "ann", during=hand_id)
    assert "a coach line that is not theirs" in oracle.stream_leaks(streams["bob"], "ann")
    # And what a table of bots broadcasts, read as a shared table's page: everything.
    assert set(bot_table_broadcast(tmp_path)) >= {
        "a bot's reasoning",
        "a deck's seed",
        "someone else's hole cards",
        "someone else's reasoning",
        "the broadcast",
        "win chances",
    }


def bot_table_broadcast(tmp_path: Path) -> list[str]:
    """What the oracle finds in a bot table's stream, replay and odds, read by nobody."""
    path = tmp_path / "bots.jsonl"
    bots = [Station("station"), Tag("tag"), Station("fish")]
    session = LiveSession(bots, LeagueConfig(hands=2, seed=5), path)
    app = create_app(path, session)
    streams: dict[str, str] = {}
    reader = read_stream(app, None, streams, "nobody")
    wait_until(lambda: len(session._subscribers) == 1)
    session.start()
    session.join(10)
    reader.join(10)
    oracle = Oracle(list(read_jsonl(path)))
    client = TestClient(app)
    client.get("/api/session")  # what the page asks first, which reads the file again
    found = set(oracle.stream_leaks(streams["nobody"], None))
    for url in ("/api/hands/1", "/api/odds/1"):
        response = client.get(url)
        assert response.status_code == 200, url
        found |= set(oracle.leaks(response.text, None))
    return sorted(found)


def test_every_api_route_reads_through_the_seat_link(tmp_path: Path) -> None:
    """A route that skipped the link would answer from the whole file, to anyone."""
    app = create_app(tmp_path / "x.jsonl", shared_table(tmp_path / "x.jsonl"))
    routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/")]
    assert routes
    for route in routes:
        assert "seat" in {f.name for f in _params(route.dependant, "query_params")}, route.path


def test_a_link_this_table_did_not_make_is_refused_everywhere(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.touch()
    app = create_app(path, shared_table(path))
    client = TestClient(app)
    for method, url, query, body in every_request(app, [], PEOPLE):
        if url.startswith("/api/"):
            response = client.request(method, url, params=query | {"seat": "guess"}, json=body)
            assert response.status_code == 403, url
    assert client.get("/api/events", params={"seat": "guess"}).status_code == 403


def test_two_people_need_two_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="their own name"):
        LiveSession(
            [WebHumanAgent("ann"), WebHumanAgent("ann"), CallingStation("s")],
            LeagueConfig(hands=1),
            tmp_path / "x.jsonl",
        )


def test_each_link_is_its_own_and_unguessable() -> None:
    tokens = {WebHumanAgent("ann").token for _ in range(50)}
    assert len(tokens) == 50 and all(len(t) >= 22 for t in tokens)  # 128 random bits each


# ----- playing together ---------------------------------------------------------------------


def test_each_person_has_their_cards_from_the_deal_and_acts_only_for_their_seat(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.jsonl"
    session = shared_table(path, hands=2)
    client = TestClient(create_app(path, session))
    ann, bob = session.humans
    checked = []

    def check(human: WebHumanAgent, turn: dict[str, Any]) -> None:
        other = bob if human is ann else ann
        mine = client.get("/api/live", params={"seat": human.token}).json()
        theirs = client.get("/api/live", params={"seat": other.token}).json()
        nobody = client.get("/api/live").json()
        assert mine["turn"]["seat"] == turn["seat"] and mine["turn"]["odds"]["name"] == human.name
        assert theirs["turn"] is None and nobody["turn"] is None
        # the other person sees their own cards while waiting, not only on their turn
        assert theirs["you"]["hole"] == session._holes[other.name]
        assert nobody["you"] is None and nobody["human"] is None
        for status in (mine, theirs, nobody):
            assert all(p["hole"] == [None, None] for p in status["current"]["players"])
        # nobody else can answer for this seat
        assert (
            client.post(
                "/api/act", params={"seat": other.token}, json={"action": "fold"}
            ).status_code
            == 409
        )
        assert client.post("/api/act", json={"action": "fold"}).status_code == 403
        checked.append(human.name)

    session.start()
    play_out(session, check)
    session.join(10)
    assert session.error == "" and set(checked) == set(PEOPLE)


def test_a_person_who_comes_back_picks_up_their_seat(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    session = shared_table(path, hands=1)
    session.start()
    ann, bob = session.humans
    wait_until(lambda: ann.pending is not None or bob.pending is not None)
    waiting = ann if ann.pending is not None else bob
    other = bob if waiting is ann else ann
    for person in (waiting, other):  # a new page, as after a reload or a dropped connection
        stream = session.sse(person.name)
        hello = json.loads(next(stream)[6:])
        stream.close()
        assert hello["you"]["hole"] == session._holes[person.name]
        assert (hello["turn"] is not None) == (person is waiting)
    assert hello["current"]["hand_id"] == "1"
    for human in session.humans:
        human.time_limit = 0.01  # let the table finish on its own
    waiting.submit("fold")
    session.join(10)
    assert session.finished


def test_each_person_gets_their_own_odds_and_their_own_coach_line(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    session = shared_table(path, hands=3)
    client = TestClient(create_app(path, session))
    events = {name: session.subscribe(name) for name in PEOPLE}
    odds = []
    session.start()
    play_out(session, lambda human, turn: odds.append((human.name, turn["odds"]["name"])))
    session.join(10)
    assert odds and all(mine == said for mine, said in odds)
    hands = list(read_jsonl(path))
    for name in PEOPLE:
        expected = {h.hand_id: review_of(public_view(h, name), name) for h in hands}
        got = []
        while not events[name].empty():
            event = events[name].get()
            if event["type"] == "hand":
                got.append((event["hand_id"], event["review"]))
        assert got == list(expected.items())
        assert any(review is not None for _, review in got)
        token = next(h.token for h in session.humans if h.name == name)
        last = client.get("/api/live", params={"seat": token}).json()["review"]
        assert last == expected[hands[-1].hand_id]
    assert client.get("/api/live").json()["review"] is None  # nobody's line for nobody


def test_the_coach_and_the_drills_read_only_your_own_seat(tmp_path: Path, paid_call) -> None:
    path = tmp_path / "t.jsonl"
    session = shared_table(path, hands=2)
    client = TestClient(create_app(path, session))
    session.start()
    play_out(session, lambda human, turn: None)
    session.join(10)
    ann = {"seat": session.humans[0].token}
    assert client.get("/api/coach", params=ann | {"player": "ann"}).status_code == 200
    assert client.get("/api/drill/next", params=ann | {"player": "ann"}).status_code == 200
    for player in ("bob", "tag", "station"):
        assert client.get("/api/coach", params=ann | {"player": player}).status_code == 403
        assert client.get("/api/drill/next", params=ann | {"player": player}).status_code == 403
    assert client.get("/api/coach", params={"player": "ann"}).status_code == 403
    narrate = client.post("/api/coach/narrate", params=ann, json={"player": "ann"})
    assert narrate.status_code == 403 and "host's key" in narrate.json()["detail"]
    assert paid_call == []
    session_info = client.get("/api/session", params=ann).json()
    assert session_info["shared"] and session_info["you"] == "ann" and session_info["hero"] == "ann"
    assert client.get("/api/session").json()["you"] is None


def test_after_a_hand_each_person_sees_its_public_view_and_their_own_seat(tmp_path) -> None:
    path = tmp_path / "t.jsonl"
    session = shared_table(path, hands=3)
    client = TestClient(create_app(path, session))
    session.start()
    play_out(session, lambda human, turn: None)
    session.join(10)
    ann = {"seat": session.humans[0].token}
    for history in read_jsonl(path):
        seen = client.get(f"/api/hands/{history.hand_id}", params=ann).json()
        watched = client.get(f"/api/hands/{history.hand_id}").json()
        assert "seed" not in seen and "seed" not in watched
        for real, mine, theirs in zip(
            history.players, seen["players"], watched["players"], strict=True
        ):
            shown = real.seat in history.showdown
            assert mine["hole"] == (real.hole if shown or real.name == "ann" else [])
            assert theirs["hole"] == (real.hole if shown else [])
        for step in seen["steps"]:
            if step["kind"] == "action":
                assert bool(step["reasoning"]) == (step["name"] == "ann")
                assert step["requested"] == step["action"] or step["name"] == "ann"
    board = client.get("/api/stats", params=ann).json()
    assert {row["name"] for row in board["rows"]} == {"ann", "bob", "station", "tag"}


def test_a_shared_table_streams_only_what_was_played_not_what_was_asked(tmp_path) -> None:
    session = shared_table(tmp_path / "t.jsonl", hands=2)
    events = session.subscribe()
    session.start()
    play_out(session, lambda human, turn: None)
    session.join(10)
    steps = []
    while not events.empty():
        event = events.get()
        if event["type"] == "step" and event["step"]["kind"] == "action":
            steps.append(event["step"])
    assert steps and all(s["requested"] == s["action"] for s in steps)


# ----- the clock ----------------------------------------------------------------------------


def test_someone_who_does_not_answer_is_checked_or_folded_in_time(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    session = shared_table(path, hands=2, time_limit=0.1)
    events = session.subscribe()
    session.start()
    ann = session.humans[0]
    while not session.finished:  # ann plays; bob has walked away
        turn = ann.turn()
        if turn is not None:
            assert 0 <= turn["seconds_left"] <= 0.1 and turn["time_limit"] == 0.1
            try:
                ann.submit(answer(ann, turn))
            except LookupError:  # the clock got there first
                pass
        time.sleep(0.005)
    session.join(10)
    assert session.error == "" and session.hands_played == 2
    bobs = [d for h in read_jsonl(path) for d in h.decisions if d.seat == 1]
    assert bobs and all(d.meta == {"timed_out": True} for d in bobs)
    assert all(d.applied in ("check", "fold") for d in bobs)
    seen = []
    while not events.empty():
        event = events.get()
        if event["type"] == "step" and event["step"]["seat"] == 1 and event["step"]["action"]:
            seen.append(event["step"].get("timed_out"))
    assert seen and all(seen)


def test_an_answer_racing_the_clock_counts_once_or_not_at_all() -> None:
    """Either the answer plays this turn or it is refused: it never waits for the next one."""
    human = WebHumanAgent("me", time_limit=0.01)
    players = [Player("me", 100), Player("bot", 100)]
    hand = Hand(players, button=0, small_blind=1, big_blind=2, seed=3)
    view = make_view(hand, 0)
    for attempt in range(30):
        got: list[Decision] = []
        table = threading.Thread(target=lambda got=got: got.append(human.act(view)))
        table.start()
        wait_until(lambda got=got: human.pending is not None or got)
        time.sleep(0.004 * (attempt % 5))  # before, around and after the deadline
        try:
            human.submit("call")
            answered = True
        except LookupError:
            answered = False
        table.join(5)
        assert len(got) == 1 and human._answer.empty()
        assert got[0].meta.get("timed_out", False) is not answered


def test_a_table_built_as_before_has_no_clock(tmp_path: Path) -> None:
    session = shared_table(tmp_path / "t.jsonl", hands=1)
    session.start()
    wait_until(lambda: any(h.pending for h in session.humans))
    turn = next(h.turn() for h in session.humans if h.pending)
    assert turn["time_limit"] is None and "seconds_left" not in turn
    for human in session.humans:
        human.time_limit = 0.01
    next(h for h in session.humans if h.pending).submit("fold")
    session.join(10)


def test_a_table_for_one_is_as_it_was(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    session = LiveSession(
        [WebHumanAgent("me"), CallingStation("s")], LeagueConfig(hands=1, seed=2), path
    )
    events = session.subscribe()
    client = TestClient(create_app(path, session))
    session.start()
    wait_until(lambda: session.human.pending is not None)
    status = client.get("/api/live").json()
    assert status["turn"] is not None and status["human"] == "me"
    assert "shared" not in status and "you" not in status
    me = {"seat": session.human.token}  # the seat's own link works too
    assert client.get("/api/live", params=me).json()["turn"] == status["turn"]
    session.human.submit("fold")
    session.join(5)
    sent = [events.get_nowait() for _ in range(events.qsize())]
    start = next(e for e in sent if e["type"] == "hand_start")
    assert "you" not in start
    assert "shared" not in client.get("/api/session").json()
    assert "seed" in client.get("/api/hands/1").json()  # the replay is the whole hand, as before


# ----- the command line ---------------------------------------------------------------------


@pytest.fixture
def no_server(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: calls.update(kw, app=app))
    monkeypatch.setattr("webbrowser.open", lambda url: calls.update(opened=url))
    return calls


def run(argv: list[str], no_server: dict) -> tuple[str, LiveSession]:
    out = io.StringIO()
    assert main(argv, out=out) == 0
    live = no_server["app"].state.live
    end = time.monotonic() + 10
    while not live.finished and time.monotonic() < end:  # nobody is there to play: fold for them
        for human in live.humans:
            try:
                human.submit("fold")
            except LookupError:
                pass
        time.sleep(0.005)
    return out.getvalue(), live


def test_serve_prints_a_private_link_per_person_and_one_to_watch(tmp_path, no_server) -> None:
    seats = "ann:human,bob:human,tag,maniac"
    printed, live = run(
        ["serve", str(tmp_path / "f.jsonl"), "--live", "-n", "1", "--seats", seats], no_server
    )
    for human in live.humans:
        link = re.escape(f"http://127.0.0.1:8765/?seat={human.token}")
        assert re.search(rf"^  {human.name} +{link}$", printed, re.M)
    assert re.search(r"^  watch +http://127\.0\.0\.1:8765/  \(no seat, no cards\)$", printed, re.M)
    assert "60 s a decision" in printed and live.time_limit == 60
    assert "--host 0.0.0.0" in printed  # bound to this computer: how friends elsewhere get in
    assert live.config.seed != 0  # a secret deck, not the repo's default seed


def test_watch_reaches_other_machines_when_asked(tmp_path, no_server) -> None:
    argv = ["watch", "-n", "1", "--seats", "ann:human,bob:human,tag", "--host", "0.0.0.0"]
    printed, live = run([*argv, "--time-limit", "20", "--no-open"], no_server)
    assert no_server["host"] == "0.0.0.0" and live.time_limit == 20
    assert "http://0.0.0.0" not in printed.split("Seat links")[1]  # a link someone can open
    printed, live = run(
        ["watch", "-n", "1", "--seats", "ann:human,bob:human", "--allow-host", "t.example"],
        no_server,
    )
    assert "https://t.example/?seat=" in printed
    assert no_server["opened"].endswith(f":{no_server['port']}/")  # the watching page


def test_one_person_gets_no_links_and_no_clock(tmp_path, no_server) -> None:
    printed, live = run(
        ["serve", str(tmp_path / "f.jsonl"), "--live", "-n", "1", "--seats", "me:human,tag"],
        no_server,
    )
    assert "Seat links" not in printed and live.time_limit == 0 and live.config.seed == 0
