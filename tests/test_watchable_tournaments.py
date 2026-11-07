"""A freezeout you can watch: what each hand records, the standings, and the live table."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from poker_table.agents.registry import make_agents
from poker_table.cli import main
from poker_table.history import HandHistory, read_jsonl
from poker_table.league import LeagueConfig, TournamentConfig, run_league, run_tournament
from poker_table.web.app import create_app, summarize, tournament_standing
from poker_table.web.live import LiveSession

SEATS = ["tag", "station", "maniac", "rock", "random"]


def freezeout(seed: int = 3, hands_per_level: int = 10):
    agents = make_agents(SEATS, seed=seed)
    return run_tournament(agents, TournamentConfig(seed=seed, hands_per_level=hands_per_level))


def test_each_hand_says_its_level_who_was_left_and_who_went_out() -> None:
    result = freezeout()
    places = {}
    for history in result.histories:
        t = history.tournament
        assert t["level"] == result.config.level_number(int(history.hand_id) - 1)
        assert t["left"] == len(history.players)
        places |= t["out"]
    assert places == {name: place for name, place in result.finishes.items() if place > 1}

    # the hand where two went out says both, the bigger stack finishing higher (the same
    # freezeout as test_league's double bust-out)
    agents = make_agents(["tag", "rock", "maniac", "station", "random"], seed=1)
    other = run_tournament(agents, TournamentConfig(seed=3, hands_per_level=10))
    both = next(h for h in other.histories if len(h.tournament["out"]) == 2)
    assert both.tournament["out"] == {
        "maniac": other.finishes["maniac"],
        "random": other.finishes["random"],
    }
    assert both.tournament["out"]["maniac"] < both.tournament["out"]["random"]


def test_a_tournament_hand_reads_back_and_a_cash_hand_is_written_as_before() -> None:
    hand = freezeout().histories[0]
    assert HandHistory.from_json(hand.to_json()) == hand
    cash = run_league(make_agents(["tag", "station"]), LeagueConfig(hands=1, seed=1)).histories[0]
    assert "tournament" not in json.loads(cash.to_json())
    assert HandHistory.from_json(cash.to_json()).tournament == {}


def test_the_standings_part_way_and_at_the_end() -> None:
    result = freezeout()
    first_out = next(i for i, h in enumerate(result.histories) if h.tournament["out"])
    early = tournament_standing(result.histories[: first_out + 1])
    assert early is not None and not early["over"] and early["left"] == 4
    assert sum(row["chips"] for row in early["standings"]) == 5 * 200
    gone = [row for row in early["standings"] if row["out_in"]]
    assert gone == [{"name": gone[0]["name"], "place": 5, "chips": 0, "out_in": str(first_out + 1)}]

    final = tournament_standing(result.histories)
    assert final["over"] and final["left"] == 1
    assert [row["name"] for row in final["standings"]] == result.standings()
    assert [row["place"] for row in final["standings"]] == [1, 2, 3, 4, 5]


def test_cash_hands_have_no_standings_and_list_rows_mark_who_went_out() -> None:
    cash = run_league(make_agents(["tag", "station"]), LeagueConfig(hands=3, seed=1)).histories
    assert tournament_standing(cash) is None and "out" not in summarize(cash[0])
    result = freezeout()
    rows = [summarize(h) for h in result.histories]
    assert all("out" in row for row in rows)
    assert sum(len(row["out"]) for row in rows) == 4


def test_the_viewer_serves_a_tournament_file(tmp_path: Path) -> None:
    path = tmp_path / "freezeout.jsonl"
    code = main(
        ["play", "--tournament", "--seats", ",".join(SEATS), "--seed", "3", "-o", str(path), "-q"]
    )
    assert code == 0
    assert all(h.tournament for h in read_jsonl(path))
    session = TestClient(create_app(path)).get("/api/session").json()
    assert session["tournament"]["over"] and session["tournament"]["left"] == 1


def test_a_live_table_plays_a_freezeout_to_the_end(tmp_path: Path) -> None:
    path = tmp_path / "live.jsonl"
    session = LiveSession(
        make_agents(SEATS, seed=3),
        LeagueConfig(hands=1, seed=3),
        path,
        tournament=TournamentConfig(seed=3, hands_per_level=10),
    )
    client = TestClient(create_app(path, session))
    session.start()
    end = time.time() + 60
    while not session.finished:
        assert time.time() < end, "the freezeout did not finish"
        time.sleep(0.05)
    status = client.get("/api/live").json()
    assert status["error"] == "" and status["hands_total"] is None
    t = status["tournament"]
    assert t["left"] == 1 and sorted(t["out"].values()) == [2, 3, 4, 5]
    assert session.hands_played == sum(1 for _ in read_jsonl(path))
    assert client.get("/api/session").json()["tournament"]["over"]


def test_watch_and_serve_take_the_tournament_options(monkeypatch, tmp_path: Path) -> None:
    seen = {}

    def fake_serve(args, out):
        seen.update(vars(args))
        return 0

    monkeypatch.setattr("poker_table.cli.cmd_serve", fake_serve)
    assert main(["watch", "--tournament", "--levels", "1/2,2/4", "--level-hands", "5"]) == 0
    assert seen["tournament"] and seen["levels"] == "1/2,2/4" and seen["level_hands"] == 5
    assert seen["hands"] is None  # the tournament's own limit, not 100000 cash hands
    assert main(["watch"]) == 0
    assert seen["tournament"] is False and seen["hands"] == 100_000
