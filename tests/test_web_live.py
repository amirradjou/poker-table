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
    assert kinds.count("hand") == 5 and kinds.count("hand_start") == 5 and kinds[-1] == "done"
    assert kinds.index("hand_start") < kinds.index("hand")


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
    assert client.post("/api/live/pace", json={"pace": 1}).status_code == 404


def drain(events) -> list[dict]:
    out = []
    while not events.empty():
        out.append(events.get_nowait())
    return out


def test_spectator_session_streams_every_step_with_cards(tmp_path: Path) -> None:
    from poker_table.web.app import steps_for

    path = tmp_path / "live.jsonl"
    session = LiveSession(
        [TightAggressive("tag"), CallingStation("s")], LeagueConfig(hands=1, seed=4), path
    )
    events = session.subscribe()
    session.start()
    session.join(5)
    assert session.error == "" and session.spectator
    seq = drain(events)
    types = [e["type"] for e in seq]
    assert types[0] == "hand_start" and types[-2:] == ["hand", "done"]
    assert "acting" in types and "step" in types
    start = seq[0]["hand"]
    assert [p["kind"] for p in start["players"]] == ["tag", "station"]
    assert all(len(p["hole"]) == 2 and None not in p["hole"] for p in start["players"])
    assert [s["kind"] for s in start["steps"]] == ["post_blind", "post_blind"]
    # every engine event of the finished hand was streamed, in order, as a step
    history = next(read_jsonl(path))
    streamed = start["steps"] + [e["step"] for e in seq if e["type"] == "step"]
    assert [s["kind"] for s in streamed] == [s["kind"] for s in steps_for(history)]
    actions = [e["step"] for e in seq if e["type"] == "step" and e["step"]["kind"] == "action"]
    assert actions and all("reasoning" in a and "latency_ms" in a for a in actions)
    acting = [e for e in seq if e["type"] == "acting"]
    assert len(acting) == len(actions) and acting[0]["kind"] in ("tag", "station")
    assert session.current is None and session.status()["current"] is None


def test_session_with_a_human_hides_cards_and_reasoning(tmp_path: Path) -> None:
    path = tmp_path / "live.jsonl"
    session = LiveSession(
        [WebHumanAgent("me"), CallingStation("s")], LeagueConfig(hands=1, seed=2, buy_in=100), path
    )
    client = TestClient(create_app(path, session))
    events = session.subscribe()
    session.start()
    wait_until(lambda: client.get("/api/live").json()["turn"] is not None)
    status = client.get("/api/live").json()
    assert status["spectator"] is False and status["kinds"] == ["human", "station"]
    current = status["current"]
    assert current["hand_id"] == "1"
    assert all(p["hole"] == [None, None] for p in current["players"])  # cards only via the turn
    assert [s["kind"] for s in current["steps"]] == ["post_blind", "post_blind"]
    assert client.post("/api/live/pace", json={"pace": 9}).json() == {"pace": 5.0}
    assert session.pace == 5.0
    session.pace = 0
    client.post("/api/act", json={"action": "raise 100"})
    session.join(5)
    seq = drain(events)
    actions = [e["step"] for e in seq if e["type"] == "step" and e["step"]["kind"] == "action"]
    assert actions and all("reasoning" not in a and "meta" not in a for a in actions)
    assert actions[0]["action"] == "raise 100" and actions[0]["latency_ms"] >= 0
