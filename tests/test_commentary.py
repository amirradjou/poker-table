"""What the commentator says, on hands whose cards are stacked so the answer is known."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poker_table.agents.base import Decision
from poker_table.cards import Card, Deck, parse_cards
from poker_table.engine import Action, Hand, Player
from poker_table.history import HandHistory, read_jsonl, write_jsonl
from poker_table.table import play_hand
from poker_table.web.app import _broadcast, create_app, odds_payload
from poker_table.web.commentary import Commentator
from poker_table.web.export import export_site


class Script:
    """Plays the given actions in order, then checks or calls."""

    kind = "script"

    def __init__(self, name: str, *actions: str) -> None:
        self.name = name
        self.actions = list(actions)

    def act(self, view) -> Decision:
        legal = view.legal
        next_up = self.actions.pop(0) if self.actions else "passive"
        if next_up == "shove":
            return Decision(
                Action.raise_to(legal.max_raise_to) if legal.can_raise else Action.call()
            )
        if next_up == "fold":
            return Decision(Action.fold())
        return Decision(Action.check() if legal.can_check else Action.call())


def dealt(cards: str, *agents: Script) -> HandHistory:
    """Cards go out one at a time from the seat left of the button, which is seat 0, so ``cards``
    starts with seat 1's first card."""
    hand = Hand(
        [Player(a.name, 200) for a in agents],
        button=0,
        small_blind=1,
        big_blind=2,
        seed=7,
        deck=Deck.stacked(parse_cards(cards)),
    )
    return HandHistory.from_played(play_hand(hand, dict(enumerate(agents))))


def lines_of(history: HandHistory) -> list[str]:
    broadcast = _broadcast(history)
    assert broadcast is not None
    return [line["text"] for line in broadcast.lines]


def test_a_shove_and_a_call_say_who_has_what_and_their_chances() -> None:
    # Seat 0 is the button and acts first heads-up; high cards are named first.
    history = dealt(
        "2d Ah 7c As 3s 4s 8h 9h Jd", Script("aces", "shove"), Script("sevens", "shove")
    )
    first, second, *_ = lines_of(history)
    assert first.startswith("aces moves all in with A♥ A♠, 8") and first.endswith("% to win.")
    assert second.startswith("sevens calls all in with 7♣ 2♦, 1") and second.endswith("% to win.")


def test_several_hands_in_are_counted() -> None:
    history = dealt(
        "Qh Ah Kh Qd Ad Kd 2c 3c 4d 8s 9s",
        Script("kings", "shove"),
        Script("queens", "fold"),
        Script("aces", "fold"),
    )
    assert " against the other two." in lines_of(history)[0]


def test_folding_the_best_hand_is_called_and_names_who_takes_it() -> None:
    history = dealt("7c Ah 2d As 7h 7s 2c 3d 9h", Script("aces", "shove"), Script("sevens", "fold"))
    assert lines_of(history)[-1].startswith("aces moves")  # the worse hand folding is no news
    history = dealt("Ah 7c As 2d 7h 7s 2c 3d 9h", Script("sevens", "shove"), Script("aces", "fold"))
    assert lines_of(history)[-1].startswith("aces folds the best hand: A♥ A♠ was 8")
    assert lines_of(history)[-1].endswith(", and sevens takes it with 7♣ 2♦.")


def test_folding_to_no_bet_is_no_news_however_good_the_hand() -> None:
    # Everyone folds to the big blind, and the small blind's aces were ahead of it.
    history = dealt(
        "Ah 7c 3d As 2d 4h 7h 7s 2c 3h 9h", Script("a", "fold"), Script("sb", "fold"), Script("bb")
    )
    assert not any("folds the best" in line for line in lines_of(history))


def test_a_small_lead_among_many_is_no_news_either() -> None:
    commentator = Commentator(
        {seat: tuple(parse_cards(hole)) for seat, hole in enumerate(["Kd Qc", "9h 8h", "7c 2d"])},
        {0: "a", 1: "b", 2: "c"},
    )
    raise_ = {"kind": "action", "seat": 2, "action": "raise 8", "all_in": False}
    fold = {"kind": "action", "seat": 0, "action": "fold", "all_in": False}
    shares = {"0": 0.39, "1": 0.33, "2": 0.28}
    assert commentator.take(1, raise_, shares, shares) is None
    assert commentator.take(2, fold, shares, shares) is None
    assert commentator.take(2, fold, {"0": 0.41, "1": 0.31, "2": 0.28}, shares) is not None


def test_a_card_that_turns_the_hand_around_is_called() -> None:
    # Queens lead jacks until the jack on the turn.
    history = dealt("Jh Qh Jd Qd 2c 3c 7d Js 4h", Script("queens"), Script("jacks"))
    assert any(
        line.startswith("The J♠ turns it around: jacks goes from ") for line in lines_of(history)
    )


def test_a_long_shot_on_the_river_is_called() -> None:
    # Two jacks left in the deck, with one card to come.
    history = dealt("Jh Qh Jd Qd 2c 3c 7d 4h Js", Script("queens"), Script("jacks"))
    assert lines_of(history)[-1] == "A 5% shot comes in: the J♠ gives jacks the pot."
    broadcast = _broadcast(history)
    assert broadcast is not None
    assert broadcast.lines[-1]["kind"] == "long_shot"
    assert broadcast.lines[-1]["share"] == pytest.approx(2 / 44, abs=0.001)


@pytest.mark.parametrize(
    ("share", "article"),
    [(0.08, "An"), (0.11, "An"), (0.18, "An"), (0.12, "A"), (0.21, "A"), (0.09, "A")],
)
def test_the_article_goes_with_how_the_number_sounds(share: float, article: str) -> None:
    king = tuple(parse_cards("Kd Kc"))
    ace = tuple(parse_cards("Ad Ac"))
    commentator = Commentator({0: king, 1: ace}, {0: "a", 1: "b"})
    step = {"kind": "street", "street": "river", "cards": ["Ks"]}
    line = commentator.take(9, step, {"0": share, "1": 1 - share}, {"0": 1.0, "1": 0.0})
    assert line is not None and line["text"].startswith(f"{article} {round(share * 100)}% shot")


def test_a_hand_with_nothing_in_it_stays_silent() -> None:
    # Queens lead from start to finish: nobody shoves, folds or gets turned around.
    history = dealt("Jh Qh Jd Qd 2c 3c 7d 4h 5s", Script("queens"), Script("jacks"))
    assert lines_of(history) == []


def test_drawing_dead_is_said_rather_than_zero_percent() -> None:
    # Quads against a pair on the flop, and the pair goes all in anyway.
    commentator = Commentator(
        {0: tuple(parse_cards("Ah Kh")), 1: tuple(parse_cards("7c 7d"))}, {0: "a", 1: "b"}
    )
    step = {"kind": "action", "seat": 0, "action": "call", "all_in": True}
    line = commentator.take(3, step, {"0": 0.0, "1": 1.0}, {"0": 0.0, "1": 1.0})
    assert line == {
        "step": 3,
        "kind": "all_in_call",
        "text": "a calls all in with A♥ K♥, drawing dead.",
    }


def test_a_tens_card_reads_as_the_table_draws_it() -> None:
    commentator = Commentator(
        {0: (Card.parse("Td"), Card.parse("3c")), 1: tuple(parse_cards("Ah Kh"))}, {0: "a", 1: "b"}
    )
    step = {"kind": "action", "seat": 0, "action": "raise 200", "all_in": True}
    line = commentator.take(1, step, {"0": 0.3, "1": 0.7}, {"0": 0.3, "1": 0.7})
    assert line is not None and "with 10♦ 3♣, 30% to win." in line["text"]


# ----- where it shows up, and where it must not ----------------------------------------------


def stacked_file(tmp_path: Path) -> Path:
    path = tmp_path / "hands.jsonl"
    history = dealt(
        "2d Ah 7c As 3s 4s 8h 9h Jd", Script("aces", "shove"), Script("sevens", "shove")
    )
    write_jsonl(path, [history])
    return path


def test_a_replay_and_a_static_export_carry_the_lines(tmp_path: Path) -> None:
    path = stacked_file(tmp_path)
    client = TestClient(create_app(path))
    served = client.get("/api/odds/1").json()["commentary"]
    assert served and served[0]["text"].startswith("aces moves all in")
    out = tmp_path / "site"
    export_site(path, out, odds=True)
    assert "aces moves all in" in (out / "data" / "odds" / "1.json").read_text()


def test_a_hand_with_unknown_cards_has_no_lines(tmp_path: Path) -> None:
    history = next(iter(read_jsonl(stacked_file(tmp_path))))
    history.players[0].hole.clear()  # as an imported hand, where only shown cards are known
    assert _broadcast(history) is None
    assert odds_payload(history)["commentary"] == []
