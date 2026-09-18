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
    assert client.get("/api/session").json() == {"file": "hands.jsonl", "hands": 12}


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
    assert client.get("/api/session").json() == {"file": "nope.jsonl", "hands": 0}
    assert client.get("/api/hands").json() == {"total": 0, "hands": []}
    assert client.get("/api/stats").json() == {"hands": 0, "rows": []}
