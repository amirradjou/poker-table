from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poker_table.cli import main
from poker_table.web.app import create_app


@pytest.fixture
def hands_file(tmp_path: Path) -> Path:
    out = tmp_path / "hands.jsonl"
    assert main(["play", "-n", "12", "--seats", "tag,maniac,station", "-o", str(out), "-q"]) == 0
    return out


def test_index_and_session(hands_file: Path) -> None:
    client = TestClient(create_app(hands_file))
    page = client.get("/")
    assert page.status_code == 200 and "<title>poker-table</title>" in page.text
    assert 'href="static/app.css"' in page.text and 'src="static/app.js"' in page.text
    css = client.get("/static/app.css")
    assert css.status_code == 200 and "--felt:" in css.text
    js = client.get("/static/app.js")
    assert js.status_code == 200 and "function stateAt(" in js.text
    assert client.get("/api/session").json() == {
        "file": "hands.jsonl",
        "hands": 12,
        "players": ["tag", "maniac", "station"],
    }


def test_hand_list_is_newest_first_and_paged(hands_file: Path) -> None:
    client = TestClient(create_app(hands_file))
    data = client.get("/api/hands?limit=5").json()
    assert data["total"] == 12
    assert [h["hand_id"] for h in data["hands"]] == ["12", "11", "10", "9", "8"]
    assert {"hand_id", "board", "pot", "winners", "showdown", "players"} <= set(data["hands"][0])
    rest = client.get("/api/hands?offset=10&limit=5").json()
    assert [h["hand_id"] for h in rest["hands"]] == ["2", "1"]


def test_hand_detail_joins_decisions_onto_action_steps(hands_file: Path) -> None:
    client = TestClient(create_app(hands_file))
    hand = client.get("/api/hands/3").json()
    assert hand["hand_id"] == "3"
    kinds = [s["kind"] for s in hand["steps"]]
    assert kinds[0] == "post_blind" and kinds[-1] == "hand_end"
    assert "deal_hole" not in kinds and "hand_start" not in kinds
    actions = [s for s in hand["steps"] if s["kind"] == "action"]
    assert len(actions) == len(hand["decisions"])
    for step, trace in zip(actions, hand["decisions"], strict=True):
        assert step["seat"] == trace["seat"] and step["action"] == trace["applied"]
        assert step["reasoning"] == trace["reasoning"]
    assert client.get("/api/hands/999").status_code == 404


def test_stats_endpoint_is_json_safe(hands_file: Path) -> None:
    client = TestClient(create_app(hands_file))
    data = client.get("/api/stats").json()
    assert data["hands"] == 12
    names = [r["name"] for r in data["rows"]]
    assert sorted(names) == ["maniac", "station", "tag"]
    assert all(r["af"] is None or isinstance(r["af"], int | float) for r in data["rows"])


def test_session_reload_sees_appended_hands(hands_file: Path) -> None:
    client = TestClient(create_app(hands_file))
    assert client.get("/api/session").json()["hands"] == 12
    main(["play", "-n", "3", "--seats", "tag,station", "-o", str(hands_file), "-q"])
    assert client.get("/api/session").json()["hands"] == 15


def test_missing_file_serves_an_empty_session(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "nope.jsonl"))
    assert client.get("/api/session").json() == {"file": "nope.jsonl", "hands": 0, "players": []}
    assert client.get("/api/hands").json() == {"total": 0, "hands": []}
    assert client.get("/api/stats").json() == {"hands": 0, "rows": []}


def test_bankroll_series_are_cumulative_and_aligned(hands_file: Path) -> None:
    client = TestClient(create_app(hands_file))
    data = client.get("/api/bankroll").json()
    assert data["hands"] == [str(i) for i in range(1, 13)]
    assert data["big_blind"] == 2
    assert sorted(s["name"] for s in data["series"]) == ["maniac", "station", "tag"]
    for s in data["series"]:
        assert len(s["values"]) == 12
    # zero-sum at every hand
    for i in range(12):
        assert sum(s["values"][i] for s in data["series"]) == 0
    finals = {s["name"]: s["values"][-1] for s in data["series"]}
    stats = {r["name"]: r["net"] for r in client.get("/api/stats").json()["rows"]}
    assert finals == stats
    empty = TestClient(create_app(hands_file.parent / "none.jsonl")).get("/api/bankroll").json()
    assert empty == {"hands": [], "big_blind": 0, "series": []}


def test_coach_endpoint_reports_a_seat_with_replay_steps(hands_file: Path) -> None:
    client = TestClient(create_app(hands_file))
    assert client.get("/api/session").json()["players"] == ["tag", "maniac", "station"]
    data = client.get("/api/coach?player=maniac&samples=30").json()
    assert data["player"] == "maniac" and data["hands"] == 12
    assert "facts" not in data
    assert data["leaks"], "the maniac always has leaks"
    example = data["leaks"][0]["examples"][0]
    assert isinstance(example["step"], int)
    hand = client.get(f"/api/hands/{example['hand_id']}").json()
    step = hand["steps"][example["step"]]
    assert step["kind"] == "action" and step["seat"] == example["seat"]
    assert step["street"] == example["street"] and step["action"] == example["action"]
    assert client.get("/api/coach?player=nobody").status_code == 404
    # cached: the same object comes back until hands change
    assert client.get("/api/coach?player=maniac&samples=30").json() == data


def test_coach_narrate_without_credentials_is_a_clean_503(hands_file: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    client = TestClient(create_app(hands_file))
    r = client.post("/api/coach/narrate", json={"player": "maniac"})
    assert r.status_code == 503 and "Error" in r.json()["detail"]
    assert client.post("/api/coach/narrate", json={"player": "nobody"}).status_code == 404
