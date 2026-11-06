"""Finding the good hands in a file, from what the commentary actually said."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poker_table.cards import Deck, parse_cards
from poker_table.cli import main
from poker_table.coach.importers import import_text
from poker_table.engine import Hand, Player
from poker_table.history import HandHistory, write_jsonl
from poker_table.table import play_hand
from poker_table.web.app import (
    NEWEST_TO_SCAN,
    STORY,
    HandStore,
    create_app,
    highlight_of,
    highlights_payload,
    summarize,
)
from poker_table.web.export import export_site

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def hands_file(tmp_path: Path) -> Path:
    path = tmp_path / "hands.jsonl"
    seats = "maniac,station,tag,random,rock"
    assert main(["play", "-n", "16", "--seats", seats, "--seed", "5", "-o", str(path), "-q"]) == 0
    return path


def everything(client: TestClient) -> tuple[list[dict], list[dict]]:
    """Every row, following the pages the way the page does, and the pages themselves."""
    rows, pages, offset = [], [], 0
    while offset is not None:
        page = client.get(f"/api/highlights?offset={offset}&limit=7").json()
        pages.append(page)
        rows += page["hands"]
        offset = page["next"]
    return rows, pages


def test_the_hands_come_in_pages_newest_first(hands_file: Path) -> None:
    rows, pages = everything(TestClient(create_app(hands_file)))
    assert len(pages) == 3 and pages[-1]["next"] is None and pages[0]["total"] == 16
    assert [r["hand_id"] for r in rows] == [str(n) for n in range(16, 0, -1)]


def test_a_tag_always_matches_a_line_the_replay_says(hands_file: Path) -> None:
    client = TestClient(create_app(hands_file))
    rows, _ = everything(client)
    assert any(r["kinds"] for r in rows)
    for row in rows:
        said = client.get(f"/api/odds/{row['hand_id']}").json()["commentary"]
        assert row["kinds"] == sorted({line["kind"] for line in said})
        if said:
            assert row["line"] in [line["text"] for line in said]
        else:
            assert row["line"] == "" and row["score"] == 0


def test_the_headline_is_the_biggest_moment_and_the_score_ranks_hands() -> None:
    history = stacked_hand()
    lines = [
        {"step": 1, "kind": "shove", "text": "a shove"},
        {"step": 2, "kind": "long_shot", "text": "a long shot"},
        {"step": 3, "kind": "all_in_call", "text": "a call"},
    ]
    row = highlight_of(history, lines)
    assert row["line"] == "a long shot"
    assert row["kinds"] == ["all_in_call", "long_shot", "shove"]
    assert row["score"] == STORY["long_shot"] + STORY["shove"] + STORY["all_in_call"]
    assert highlight_of(history, [])["score"] == 0


def test_the_rarer_long_shot_ranks_first_whatever_else_happened() -> None:
    history = stacked_hand()
    long_shot = lambda share: {"step": 9, "kind": "long_shot", "text": "x", "share": share}  # noqa: E731
    busy = {"step": 1, "kind": "folded_best", "text": "y"}
    five_percent = highlight_of(history, [long_shot(0.05)])["score"]
    twenty_percent_and_more = highlight_of(history, [long_shot(0.21), busy])["score"]
    assert five_percent > twenty_percent_and_more
    # And by enough that the rest of a busy hand cannot lift a 12% shot past a 5% one.
    everything_else = [
        {"step": n, "kind": kind, "text": kind}
        for n, kind in enumerate(["folded_best", "turnaround", "shove", "all_in_call", "dead"])
    ]
    busy_twelve = highlight_of(history, [long_shot(0.12), *everything_else])["score"]
    assert five_percent > busy_twelve


def stacked_hand() -> HandHistory:
    class Shover:
        kind = "shover"

        def __init__(self, name):
            self.name = name

        def act(self, view):
            from poker_table.agents.base import Decision
            from poker_table.engine import Action

            legal = view.legal
            return Decision(
                Action.raise_to(legal.max_raise_to) if legal.can_raise else Action.call()
            )

    agents = [Shover("aces"), Shover("sevens")]
    hand = Hand(
        [Player(a.name, 200) for a in agents],
        button=0,
        small_blind=1,
        big_blind=2,
        seed=7,
        deck=Deck.stacked(parse_cards("7c Ah 2d As 7h 7s 2c 3d 9h")),
    )
    return HandHistory.from_played(play_hand(hand, dict(enumerate(agents))))


def test_a_summary_knows_an_all_in_without_any_slow_work(tmp_path: Path) -> None:
    history = stacked_hand()
    assert summarize(history)["all_in"] is True
    path = tmp_path / "one.jsonl"
    write_jsonl(path, [history])
    row = TestClient(create_app(path)).get("/api/hands").json()["hands"][0]
    assert row["all_in"] is True


def test_each_hand_is_only_tagged_once(hands_file: Path, monkeypatch) -> None:
    import poker_table.web.app as app

    calls = []
    real = app._broadcast
    monkeypatch.setattr(app, "_broadcast", lambda h: calls.append(h.hand_id) or real(h))
    store = HandStore(hands_file)
    highlights_payload(store, 0, 5)
    highlights_payload(store, 0, 5)
    assert len(calls) == 5


def test_one_request_cannot_ask_for_a_whole_big_file(hands_file: Path, monkeypatch) -> None:
    monkeypatch.setattr("poker_table.web.app.HIGHLIGHT_BATCH", 3)
    client = TestClient(create_app(hands_file))
    page = client.get("/api/highlights?limit=100000").json()
    assert len(page["hands"]) == 3 and page["next"] == 3
    assert len(client.get("/api/highlights?limit=0").json()["hands"]) == 1


def test_a_very_long_file_only_looks_at_its_newest_hands(hands_file: Path, monkeypatch) -> None:
    monkeypatch.setattr("poker_table.web.app.NEWEST_TO_SCAN", 10)
    page = highlights_payload(HandStore(hands_file), 0, 50)
    assert page["total"] == 10 and page["next"] is None
    assert page["hands"][0]["hand_id"] == "16" and page["hands"][-1]["hand_id"] == "7"
    assert NEWEST_TO_SCAN >= 1000


def test_new_hands_are_seen_when_a_scan_starts_over(hands_file: Path) -> None:
    client = TestClient(create_app(hands_file))
    assert client.get("/api/highlights").json()["total"] == 16
    more = ["play", "-n", "3", "--seats", "maniac,station,tag,random,rock", "--seed", "6", "-q"]
    assert main([*more, "-o", str(hands_file)]) == 0
    assert client.get("/api/highlights").json()["total"] == 19


def test_hands_from_a_site_get_no_story_but_keep_what_is_free() -> None:
    imported = import_text((FIXTURES / "pokerstars_cash.txt").read_text()).hands
    for hand in imported:
        assert isinstance(summarize(hand)["all_in"], bool)


def test_cards_nobody_showed_make_no_story(tmp_path: Path) -> None:
    history = stacked_hand()
    history.players[0].hole.clear()
    path = tmp_path / "one.jsonl"
    write_jsonl(path, [history])
    row = TestClient(create_app(path)).get("/api/highlights").json()["hands"][0]
    assert row["kinds"] == [] and row["line"] == "" and row["all_in"] is True


def test_the_session_says_highlights_are_on_offer(hands_file: Path) -> None:
    assert TestClient(create_app(hands_file)).get("/api/session").json()["highlights"] is True


def test_an_export_with_odds_carries_them_whole(hands_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    export_site(hands_file, out, players=[], odds=True)
    written = json.loads((out / "data" / "highlights.json").read_text())
    assert written["next"] is None and written["total"] == 16
    assert json.loads((out / "data" / "session.json").read_text())["highlights"] is True
    served, _ = everything(TestClient(create_app(hands_file)))
    assert written["hands"] == served


def test_an_export_without_odds_does_not_pretend(hands_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    export_site(hands_file, out, players=[])
    assert not (out / "data" / "highlights.json").exists()
    assert json.loads((out / "data" / "session.json").read_text())["highlights"] is False
