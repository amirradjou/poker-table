import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from poker_table.agents import CallingStation, TightAggressive, make_view
from poker_table.engine import Hand, Player
from poker_table.history import read_jsonl
from poker_table.league import LeagueConfig
from poker_table.web.app import create_app
from poker_table.web.live import LiveSession, WebHumanAgent, live_view_payload


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.01)


def test_live_session_plays_in_the_background_and_publishes(tmp_path: Path) -> None:
    path = tmp_path / "live.jsonl"
    session = LiveSession(
        [TightAggressive("tag"), CallingStation("s")], LeagueConfig(hands=5), path
    )
    events = session.subscribe()
    session.start()
    session.join(5)
    assert session.finished and not session.running and session.error == ""
    assert session.hands_played == 5
    assert len(list(read_jsonl(path))) == 5
    kinds = []
    while not events.empty():
        kinds.append(events.get_nowait()["type"])
    assert kinds == ["hand"] * 5 + ["done"]


def test_live_view_payload_hides_other_cards_and_reconstructs_stacks() -> None:
    hand = Hand(
        [Player("me", 100), Player("bot", 100)], button=0, small_blind=1, big_blind=2, seed=3
    )
    payload = live_view_payload(make_view(hand, 0))
    me, bot = payload["players"]
    assert me["hole"] == [str(c) for c in hand.seats[0].hole]
    assert bot["hole"] == [None, None]
    assert me["stack"] == 100 and bot["stack"] == 100  # starting stacks, so the replay math works
    assert [s["kind"] for s in payload["steps"]] == ["post_blind", "post_blind"]
    assert payload["legal"]["describe"] == "fold, call 1, raise to 4..100"
    assert payload["small_blind"] == 1 and payload["big_blind"] == 2
    assert json.dumps(payload)  # JSON-safe


def test_browser_human_plays_a_hand_through_the_api(tmp_path: Path) -> None:
    path = tmp_path / "live.jsonl"
    session = LiveSession(
        [WebHumanAgent("me"), CallingStation("s")], LeagueConfig(hands=1, seed=2, buy_in=100), path
    )
    client = TestClient(create_app(path, session))
    assert client.post("/api/act", json={"action": "call"}).status_code == 409  # not started
    session.start()
    wait_until(lambda: client.get("/api/live").json()["turn"] is not None)
    status = client.get("/api/live").json()
    assert status["human"] == "me" and status["running"]
    turn = status["turn"]
    assert turn["seat"] == 0 and turn["players"][1]["hole"] == [None, None]
    assert client.post("/api/act", json={"action": "shove"}).status_code == 400
    r = client.post("/api/act", json={"action": "raise 100", "table_talk": "all of it"})
    assert r.json() == {"ok": True, "action": "raise 100"}
    session.join(5)
    assert session.finished and session.hands_played == 1
    history = next(read_jsonl(path))
    assert history.decisions[0].table_talk == "all of it"
    assert history.decisions[0].applied == "raise 100"
    assert client.get("/api/live").json()["turn"] is None
    assert client.post("/api/act", json={"action": "call"}).status_code == 409


def test_sse_stream_starts_with_hello(tmp_path: Path) -> None:
    session = LiveSession(
        [TightAggressive("tag"), CallingStation("s")], LeagueConfig(hands=2), (tmp_path / "x.jsonl")
    )
    client = TestClient(create_app(tmp_path / "x.jsonl", session))
    session.start()
    session.join(5)
    response = client.get("/api/events")  # the session is over, so the stream ends after hello
    assert response.headers["content-type"].startswith("text/event-stream")
    first = response.text.splitlines()[0]
    assert first.startswith("data: ")
    hello = json.loads(first[6:])
    assert hello["type"] == "hello" and hello["live"] and hello["hands_played"] == 2
    assert hello["finished"]


def test_non_live_app_reports_it(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "none.jsonl"))
    assert client.get("/api/live").json() == {"live": False}
    assert client.get("/api/events").status_code == 404
    assert client.post("/api/act", json={"action": "call"}).status_code == 404
