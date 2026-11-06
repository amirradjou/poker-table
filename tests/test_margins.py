"""One set of error bars for the reports, the viewer and the command line."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poker_table.cli import main
from poker_table.coach.importers import import_text
from poker_table.experiments import load_experiment, render_report, seat_results
from poker_table.history import read_jsonl, write_jsonl
from poker_table.margins import SeatMargin, all_in_adjusted, all_in_early, calls, seat_margins
from poker_table.web.app import HandStore, create_app, margins_payload
from poker_table.web.export import export_site

LEAGUE = Path(__file__).parent.parent / "docs" / "results" / "local-league"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def hands_file(tmp_path: Path) -> Path:
    path = tmp_path / "hands.jsonl"
    assert main(["play", "-n", "60", "--seats", "maniac,station,tag", "-o", str(path), "-q"]) == 0
    return path


def test_the_viewer_gives_the_numbers_the_published_report_gives() -> None:
    client = TestClient(create_app(LEAGUE / "hands.jsonl"))
    data = client.get("/api/margins").json()
    rows = {row["name"]: row for row in data["rows"]}
    assert round(rows["station"]["adjusted"]) == 659 and round(rows["station"]["margin"]) == 1020
    assert round(rows["random"]["adjusted"]) == -488 and round(rows["random"]["margin"]) == 777
    assert data["calls"] == ["Too close to break-even to call: station, tag, maniac, nerd, random."]
    assert "95 of the 100 hands were all in" in data["note"]
    published = (LEAGUE / "leaderboard.md").read_text()
    assert "| station | station | +2776 | 2452 | +659 | 1020 |" in published
    assert "Too close to break-even to call: station, tag, maniac, nerd, random." in published


def test_rebuilding_the_published_report_changes_nothing() -> None:
    hands = list(read_jsonl(LEAGUE / "hands.jsonl"))
    run = json.loads((LEAGUE / "run.json").read_text())
    text = render_report(load_experiment(LEAGUE / "experiment.toml"), hands, run)
    assert text == (LEAGUE / "leaderboard.md").read_text()


def test_a_seat_row_in_a_report_comes_from_the_shared_sum(hands_file: Path) -> None:
    hands = list(read_jsonl(hands_file))
    margins = seat_margins(hands)
    for row in seat_results(hands):
        assert row.adjusted_bb_per_100 == margins[row.name].adjusted_bb_per_100
        assert row.clear_of_zero == margins[row.name].clear_of_zero


def test_a_hand_is_only_worked_out_once(hands_file: Path, monkeypatch) -> None:
    worked_out = []

    def counting(history):
        worked_out.append(history.hand_id)
        return None

    monkeypatch.setattr("poker_table.web.app.all_in_adjusted", counting)
    store = HandStore(hands_file)
    margins_payload(store)
    assert len(worked_out) == 60
    margins_payload(store)
    assert len(worked_out) == 60

    # A live table's file grows, and only the new hands cost anything.
    more = ["play", "-n", "5", "--seats", "maniac,station,tag", "--seed", "9", "-q"]
    assert main([*more, "-o", str(hands_file)]) == 0
    store.reload()
    margins_payload(store)
    assert len(worked_out) == 65


def test_an_export_answers_the_same_question_without_a_server(hands_file, tmp_path) -> None:
    out = tmp_path / "site"
    export_site(hands_file, out, players=[])
    written = json.loads((out / "data" / "margins.json").read_text())
    assert written == TestClient(create_app(hands_file)).get("/api/margins").json()


def test_an_empty_file_has_nothing_to_say(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.touch()
    data = TestClient(create_app(empty)).get("/api/margins").json()
    assert data == {"hands": 0, "rows": [], "calls": [], "note": ""}


# ----- hands from a site -----------------------------------------------------------------


@pytest.mark.parametrize("fixture", ["pokerstars_cash.txt", "ggpoker_cash.txt", "888_cash.txt"])
def test_hands_from_a_site_get_error_bars_without_being_misread(fixture: str) -> None:
    hands = import_text((FIXTURES / fixture).read_text()).hands
    assert hands
    for hand in hands:
        worth = all_in_adjusted(hand)
        if worth is None:
            continue
        # Rake leaves the table, so the seats' results add up to what the hand itself says.
        assert sum(worth.values()) == pytest.approx(sum(p.net for p in hand.players))
    assert seat_margins(hands)


def test_a_hand_decided_on_the_river_stays_as_played() -> None:
    hands = import_text((FIXTURES / "pokerstars_cash.txt").read_text()).hands
    with_river_action = [h for h in hands if any(a.street == "river" for a in h.actions())]
    assert with_river_action, "the fixture needs a hand with river betting"
    for hand in with_river_action:
        assert not all_in_early(hand) and all_in_adjusted(hand) is None


def test_cards_nobody_showed_mean_no_adjustment(hands_file: Path) -> None:
    hands = list(read_jsonl(hands_file))
    hand = next(h for h in hands if all_in_early(h))
    seat = hand.pots[0]["eligible"][0]
    next(p for p in hand.players if p.seat == seat).hole.clear()
    assert all_in_adjusted(hand) is None


# ----- the command line --------------------------------------------------------------------


def test_play_shows_the_error_bars_after_the_table(capsys) -> None:
    assert main(["play", "-n", "40", "--seats", "maniac,station,tag"]) == 0
    printed = capsys.readouterr().out
    assert printed.index("bb/100  vpip") < printed.index("All-in adjusted bb/100")
    assert "±" in printed and ("Too close" in printed or "Clear of" in printed)


def test_a_handful_of_hands_never_gets_a_verdict(capsys) -> None:
    # Seed 5 has the maniac far ahead after a few hands, on a margin that means nothing.
    assert main(["play", "-n", "4", "--seats", "maniac,station,tag", "--seed", "5"]) == 0
    printed = capsys.readouterr().out
    assert "Too few hands to call anyone: that takes at least 30." in printed
    assert "Clear of break-even" not in printed and "Too close" not in printed


def test_a_seat_with_a_real_lead_is_only_called_once_there_are_enough_hands() -> None:
    few = SeatMargin("a", 29, 500.0, 10.0, 500.0, 10.0)
    enough = SeatMargin("a", 30, 500.0, 10.0, 500.0, 10.0)
    assert not few.clear_of_zero and enough.clear_of_zero
    assert calls([enough]) == ["Clear of break-even: a."]
    # Seats short of hands in a file with others that have plenty are just too close to call.
    assert calls([enough, few]) == [
        "Clear of break-even: a.",
        "Too close to break-even to call: a.",
    ]


def test_quiet_play_stays_quiet(capsys) -> None:
    assert main(["play", "-n", "5", "--seats", "tag,station", "-q"]) == 0
    assert "All-in adjusted" not in capsys.readouterr().out


def test_stats_on_a_file_shows_them_too(hands_file: Path, capsys) -> None:
    assert main(["stats", str(hands_file)]) == 0
    assert "All-in adjusted bb/100" in capsys.readouterr().out


def test_ctrl_c_only_skips_the_error_bars(hands_file: Path, monkeypatch, capsys) -> None:
    def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr("poker_table.cli.seat_margins", interrupted)
    assert main(["stats", str(hands_file)]) == 0
    printed = capsys.readouterr().out
    assert "bb/100  vpip" in printed and "(error bars skipped)" in printed


def test_a_file_with_no_hands_prints_no_error_bars(tmp_path: Path, capsys) -> None:
    empty = tmp_path / "empty.jsonl"
    write_jsonl(empty, [])
    assert main(["stats", str(empty)]) == 0
    assert "All-in adjusted" not in capsys.readouterr().out
