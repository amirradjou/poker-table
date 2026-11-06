"""Help while you play: your own odds on your turn, and the coach after each hand."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poker_table.agents.base import make_view
from poker_table.agents.scripted import CallingStation, TightAggressive
from poker_table.cards import Deck, parse_cards
from poker_table.engine import Hand, Player
from poker_table.history import read_jsonl
from poker_table.league import LeagueConfig
from poker_table.web.app import create_app, odds_payload
from poker_table.web.live import LiveSession, WebHumanAgent, own_odds, review_of


def wait_until(check, seconds: float = 8.0) -> None:
    end = time.time() + seconds
    while not check():
        assert time.time() < end, "timed out"
        time.sleep(0.01)


def seated(path: Path, hands: int = 1, seed: int = 2, bots=None) -> tuple[LiveSession, TestClient]:
    players = [WebHumanAgent("me"), *(bots or [CallingStation("s")])]
    session = LiveSession(players, LeagueConfig(hands=hands, seed=seed, buy_in=100), path)
    return session, TestClient(create_app(path, session))


def hand_with(cards: str) -> Hand:
    """Heads-up from a stacked deck. Seat 1 gets the first card, the button (seat 0) the second."""
    players = [Player("me", 100), Player("them", 100)]
    return Hand(
        players, button=0, small_blind=1, big_blind=2, seed=3, deck=Deck.stacked(parse_cards(cards))
    )


def test_your_turn_comes_with_your_odds(tmp_path: Path) -> None:
    session, client = seated(tmp_path / "t.jsonl")
    session.start()
    wait_until(lambda: client.get("/api/live").json()["turn"] is not None)
    turn = client.get("/api/live").json()["turn"]
    odds = turn["odds"]
    assert odds["name"] == "me" and odds["street"] == "preflop" and odds["known"] is None
    assert 0 < odds["blind"]["equity"] < 1 and odds["blind"]["opponents"] == 1
    assert odds["to_call"] == turn["to_call"] and odds["pot"] == turn["pot"]
    client.post("/api/act", json={"action": "fold"})
    session.join(5)


def test_your_odds_cannot_depend_on_anyone_elses_cards() -> None:
    # The same cards for you and the same board to come, with very different cards opposite.
    aces_opposite = hand_with("Ah 7c As 2d")
    junk_opposite = hand_with("3h 7c 4s 2d")
    assert own_odds(make_view(aces_opposite, 0)) == own_odds(make_view(junk_opposite, 0))


def test_your_odds_match_what_the_replay_will_show(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    session, client = seated(path)
    session.start()
    wait_until(lambda: client.get("/api/live").json()["turn"] is not None)
    asked = client.get("/api/live").json()["turn"]["odds"]
    client.post("/api/act", json={"action": "call"})

    # Check or call down whatever else this hand asks of the seat.
    def answer_the_rest() -> None:
        while not session.finished:
            if session.human.pending is not None:
                try:
                    legal = session.human.pending["legal"]
                    session.human.submit("check" if legal["can_check"] else "call")
                except LookupError:
                    pass
            time.sleep(0.005)

    threading.Thread(target=answer_the_rest, daemon=True).start()
    session.join(8)
    history = next(read_jsonl(path))
    replayed = next(s for s in odds_payload(history)["spots"] if s["name"] == "me")
    for key in ("blind", "pot", "to_call", "required", "ratio", "ev_call", "street"):
        assert asked[key] == replayed[key], key


def test_a_table_with_no_human_gets_no_coaching(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    session = LiveSession([TightAggressive("t"), CallingStation("s")], LeagueConfig(hands=2), path)
    events = session.subscribe()
    session.start()
    session.join(5)
    assert session.status()["review"] is None
    seen = []
    while not events.empty():
        seen.append(events.get())
    hands = [event for event in seen if event["type"] == "hand"]
    assert hands and all("review" not in event for event in hands)


def test_the_coach_speaks_after_each_hand_you_play(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    session, client = seated(path, hands=2)
    events = session.subscribe()
    session.start()
    for _ in range(2):
        wait_until(lambda: session.human.pending is not None or session.finished)
        if session.finished:
            break
        session.human.submit("fold")
        wait_until(lambda: session.human.pending is None)
    session.join(8)
    seen = []
    while not events.empty():
        seen.append(events.get())
    reviews = [event["review"] for event in seen if event["type"] == "hand"]
    assert reviews and all(r is None or r["text"].startswith("Coach, hand #") for r in reviews)
    assert any(r is not None for r in reviews)
    assert client.get("/api/live").json()["review"] == reviews[-1]


def history_where_i(action: str, cards: str):
    """A finished heads-up hand in which I act once and the other seat calls everything."""
    from poker_table.agents.base import Decision
    from poker_table.engine import Action
    from poker_table.history import HandHistory
    from poker_table.table import play_hand

    class Me:
        kind = "human"
        name = "me"

        def act(self, view):
            parsed = {"fold": Action.fold(), "call": Action.call()}[action]
            legal = view.legal
            if action == "call" and not legal.can_call:
                parsed = Action.check()
            return Decision(parsed)

    return HandHistory.from_played(
        play_hand(hand_with(cards), {0: Me(), 1: CallingStation("them")})
    )


def test_a_mistake_gets_named_and_a_fine_hand_gets_left_alone() -> None:
    folded_aces = review_of(history_where_i("fold", "Kh As Qd Ad 2c 3c 4d 8s 9s"), "me")
    assert folded_aces is not None
    assert folded_aces["text"].startswith("Coach, hand #1, preflop: ")
    assert "AA" in folded_aces["text"] and folded_aces["text"].endswith(".")

    folded_junk = review_of(history_where_i("fold", "Kh 7c Qd 2d 2c 3c 4d 8s 9s"), "me")
    assert folded_junk == {"hand_id": "1", "text": "Coach, hand #1: nothing to change."}


def test_a_broken_review_never_stops_the_table(tmp_path: Path, monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise RuntimeError("the coach is out")

    monkeypatch.setattr("poker_table.coach.facts.tag_hand", broken)
    session, _ = seated(tmp_path / "t.jsonl", hands=2)
    session.start()
    for _ in range(2):
        wait_until(lambda: session.human.pending is not None or session.finished)
        if not session.finished:
            session.human.submit("fold")
            wait_until(lambda: session.human.pending is None)
    session.join(8)
    assert session.finished and session.error == "" and session.review is None


@pytest.mark.parametrize("name", ["me", "someone-else"])
def test_a_seat_that_was_not_in_the_hand_has_no_review(name: str) -> None:
    history = history_where_i("fold", "Kh 7c Qd 2d 2c 3c 4d 8s 9s")
    review = review_of(history, name)
    assert (review is None) == (name == "someone-else")
