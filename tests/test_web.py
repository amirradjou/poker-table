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
    session = client.get("/api/session").json()
    assert {k: v for k, v in session.items() if k != "weeks"} == {
        "file": "hands.jsonl",
        "hands": 12,
        "players": ["tag", "maniac", "station"],
        "hero": None,
    }
    assert len(session["weeks"]) == 1 and session["weeks"][0]["hands"] == "12"


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
    assert client.get("/api/session").json() == {
        "file": "nope.jsonl",
        "hands": 0,
        "players": [],
        "hero": None,
        "weeks": [],
    }
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


def test_coach_can_be_scoped_to_a_period_and_hands_listed_by_id(hands_file: Path) -> None:
    from datetime import UTC, datetime, timedelta

    client = TestClient(create_app(hands_file))
    weeks = client.get("/api/session").json()["weeks"]
    assert len(weeks) == 1 and weeks[0]["hands"] == "12"
    assert {"label", "since", "until"} <= set(weeks[0])
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    scoped = client.get(f"/api/coach?player=maniac&samples=30&since={tomorrow}").json()
    assert scoped["hands"] == 0 and scoped["leaks"] == [] and scoped["since"] == tomorrow
    week = client.get(
        f"/api/coach?player=maniac&samples=30&since={weeks[0]['since']}&until={weeks[0]['until']}"
    ).json()
    assert week["hands"] == 12 and week["leaks"]
    assert client.get("/api/coach?player=maniac&since=not-a-date").status_code == 400
    subset = client.get("/api/hands?ids=3,1,999").json()
    assert [h["hand_id"] for h in subset["hands"]] == ["3", "1"] and subset["total"] == 2


def test_coach_narrate_without_credentials_is_a_clean_503(hands_file: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    client = TestClient(create_app(hands_file))
    r = client.post("/api/coach/narrate", json={"player": "maniac"})
    assert r.status_code == 503 and "Error" in r.json()["detail"]
    assert client.post("/api/coach/narrate", json={"player": "nobody"}).status_code == 404


def test_an_ante_hand_reaches_the_viewer_as_its_own_step(tmp_path: Path) -> None:
    out = tmp_path / "antes.jsonl"
    assert (
        main(["play", "-n", "4", "--seats", "tag,station", "--ante", "1", "-o", str(out), "-q"])
        == 0
    )
    client = TestClient(create_app(out))
    hand = client.get("/api/hands/1").json()
    assert hand["ante"] == 1
    antes = [s for s in hand["steps"] if s["kind"] == "post_ante"]
    # posted starting left of the button, which is seat 0 in the first hand
    assert [(s["seat"], s["amount"], s["name"]) for s in antes] == [
        (1, 1, "station"),
        (0, 1, "tag"),
    ]
    assert [s["kind"] for s in hand["steps"]][:4] == [
        "post_ante",
        "post_ante",
        "post_blind",
        "post_blind",
    ]


def test_export_writes_a_static_site_answering_the_same_json(tmp_path: Path, hands_file: Path):
    import json

    from poker_table.web.export import FLAG, export_site

    site = tmp_path / "site"
    written = export_site(hands_file, site, players=["tag"])
    assert written["hands"] == 12 and written["coached"] == ["tag"]

    page = (site / "index.html").read_text()
    assert FLAG in page and 'src="static/app.js"' in page
    assert (site / "static" / "app.js").exists() and (site / "static" / "app.css").exists()
    assert not (site / "static" / "index.html").exists()  # the page lives at the root

    client = TestClient(create_app(hands_file))
    for route, file in [
        ("/api/session", "session.json"),
        ("/api/bankroll", "bankroll.json"),
        ("/api/stats", "stats.json"),
        ("/api/hands/7", "hands/7.json"),
        ("/api/coach?player=tag", "coach/tag.json"),
    ]:
        served = client.get(route).json()
        assert json.loads((site / "data" / file).read_text()) == served, route

    # the whole list, newest first: the page does the paging the server used to do
    listed = json.loads((site / "data" / "hands.json").read_text())
    assert listed["total"] == 12
    assert [h["hand_id"] for h in listed["hands"]] == [str(i) for i in range(12, 0, -1)]
    assert listed["hands"][:5] == client.get("/api/hands?limit=5").json()["hands"]
    assert len(list((site / "data" / "hands").glob("*.json"))) == 12


def test_export_refuses_what_it_cannot_write(tmp_path: Path, hands_file: Path) -> None:
    from poker_table.web.export import export_site

    with pytest.raises(ValueError, match="no seat named 'nobody'"):
        export_site(hands_file, tmp_path / "a", players=["nobody"])
    empty = tmp_path / "empty.jsonl"
    empty.touch()
    with pytest.raises(ValueError, match="no hands"):
        export_site(empty, tmp_path / "b")


def test_export_cli(tmp_path: Path, hands_file: Path) -> None:
    import io

    site = tmp_path / "site"
    buf = io.StringIO()
    code = main(["serve", str(hands_file), "--export", str(site), "--export-coach", "tag"], out=buf)
    assert code == 0 and (site / "data" / "session.json").exists()
    assert "12 hands written to" in buf.getvalue()
    assert "coach reports for tag" in buf.getvalue()
    assert "python3 -m http.server" in buf.getvalue()


def test_odds_for_a_hand_answer_with_and_without_the_other_cards(hands_file: Path) -> None:
    client = TestClient(create_app(hands_file))
    data = client.get("/api/odds/3").json()
    hand = client.get("/api/hands/3").json()
    assert data["hand_id"] == "3" and data["spots"]
    actions = [i for i, s in enumerate(hand["steps"]) if s["kind"] == "action"]
    assert [s["step"] for s in data["spots"]] == actions[: len(data["spots"])]
    for spot in data["spots"]:
        assert hand["steps"][spot["step"]]["seat"] == spot["seat"]
        assert 0.0 <= spot["blind"]["equity"] <= 1.0
        assert spot["blind"]["opponents"] >= 1
        assert spot["blind"]["outs"] is None  # blind: the other hands are not known
        if spot["known"] is not None:  # a league hand knows every seat's cards
            assert 0.0 <= spot["known"]["equity"] <= 1.0
        if spot["to_call"]:
            assert spot["required"] == pytest.approx(
                spot["to_call"] / (spot["pot"] + spot["to_call"]), abs=1e-4
            )
        else:
            assert spot["ev_call"] == 0.0
    assert client.get("/api/odds/999").status_code == 404


def test_odds_are_exported_only_when_asked_for(tmp_path: Path, hands_file: Path) -> None:
    from poker_table.web.export import export_site

    plain = export_site(hands_file, tmp_path / "plain", players=[])
    assert plain["odds"] is False and not (tmp_path / "plain" / "data" / "odds").exists()
    with_odds = export_site(hands_file, tmp_path / "odds", players=[], odds=True)
    assert with_odds["odds"] is True
    files = sorted((tmp_path / "odds" / "data" / "odds").glob("*.json"))
    assert len(files) == 12
    import json

    client = TestClient(create_app(hands_file))
    assert json.loads((tmp_path / "odds" / "data" / "odds" / "5.json").read_text()) == (
        client.get("/api/odds/5").json()
    )


def test_the_table_equities_follow_folds_and_new_cards(hands_file: Path) -> None:
    client = TestClient(create_app(hands_file))
    for hand_id in ("1", "5", "9"):
        data = client.get(f"/api/odds/{hand_id}").json()
        hand = client.get(f"/api/hands/{hand_id}").json()
        table = data["table"]
        assert table and table[0]["step"] == 0
        assert len(table[0]["equity"]) == len(hand["players"])  # everyone is dealt in
        for row in table:
            assert sum(row["equity"].values()) == pytest.approx(1.0, abs=1e-3)
        # each later entry starts right after a fold or a street card
        for row in table[1:]:
            before = hand["steps"][row["step"] - 1]
            assert before["kind"] == "street" or before["action"] == "fold"
