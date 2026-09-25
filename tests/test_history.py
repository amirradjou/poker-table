from pathlib import Path

from poker_table.agents import CallingStation, TightAggressive
from poker_table.cards import Deck, parse_cards
from poker_table.engine import Action, Hand, Player
from poker_table.history import (
    HandHistory,
    board_cards,
    hole_cards,
    parse_action,
    read_jsonl,
    write_jsonl,
)
from poker_table.table import PlayedHand, new_hand, play_hand


def stacked(hole: dict[int, str], board: str, *, button: int) -> Deck:
    n = len(hole)
    order = [(button + 1 + i) % n for i in range(n)]
    first = [parse_cards(hole[i])[0] for i in order]
    second = [parse_cards(hole[i])[1] for i in order]
    return Deck.stacked(first + second + parse_cards(board))


def showdown_hand() -> PlayedHand:
    deck = stacked({0: "Ac Ad", 1: "Kc Kd"}, "2h 5s 8h 9s Jh", button=0)
    hand = Hand(
        [Player("alice", 100), Player("bob", 100)],
        button=0,
        small_blind=1,
        big_blind=2,
        seed=11,
        hand_id="h7",
        deck=deck,
    )
    hand.apply(Action.raise_to(100))
    hand.apply(Action.call())
    return PlayedHand(hand)


def test_history_captures_the_hand() -> None:
    history = HandHistory.from_played(showdown_hand())
    assert history.hand_id == "h7" and history.seed == 11
    assert [p.name for p in history.players] == ["alice", "bob"]
    assert history.players[0].hole == ["Ac", "Ad"]
    assert history.players[0].position == "BTN/SB"
    assert history.board == ["2h", "5s", "8h", "9s", "Jh"]
    assert history.payouts == {0: 200}
    assert history.showdown == {0: "a pair of aces", 1: "a pair of kings"}
    assert [p.net for p in history.players] == [100, -100]
    assert history.pots == [{"amount": 200, "eligible": [0, 1]}]
    assert [a.action for a in history.actions()] == ["raise 100", "call"]
    assert history.winners() == [0]


def test_history_round_trips_through_json() -> None:
    history = HandHistory.from_played(showdown_hand())
    again = HandHistory.from_json(history.to_json())
    assert again == history


def test_history_rejects_unfinished_hands() -> None:
    hand = Hand([Player("a", 100), Player("b", 100)], button=0, small_blind=1, big_blind=2, seed=1)
    try:
        HandHistory.from_played(PlayedHand(hand))
    except ValueError as exc:
        assert "not finished" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_render_reads_like_a_hand_history() -> None:
    text = HandHistory.from_played(showdown_hand()).render()
    assert "Hand #h7 · NLHE 1/2 · seed 11" in text
    assert "alice: posts small blind 1" in text
    assert "bob: posts big blind 2" in text
    assert "Dealt to alice [Ac Ad]" in text
    assert "alice: raise 100 and is all-in" in text
    assert "bob: call and is all-in" in text
    assert "*** FLOP *** [2h 5s 8h]" in text
    assert "*** RIVER *** [2h 5s 8h 9s Jh]" in text
    assert "alice: shows [Ac Ad] (a pair of aces)" in text
    assert "alice collected 200 from main pot with a pair of aces" in text
    assert "Seat 0: alice +100" in text and "Seat 1: bob -100" in text


def test_render_can_include_private_reasoning_and_table_talk() -> None:
    hand, agents = new_hand(
        [TightAggressive("tag"), CallingStation("station")],
        [100, 100],
        button=0,
        small_blind=1,
        big_blind=2,
        seed=5,
    )
    played = play_hand(hand, agents)
    history = HandHistory.from_played(played)
    plain = history.render()
    verbose = history.render(reasoning=True)
    assert "thinks:" not in plain
    assert "tag thinks: Chen" in verbose
    assert len(history.decisions) == len(played.decisions)


def test_jsonl_round_trip(tmp_path: Path) -> None:
    histories = []
    agents = [TightAggressive("tag"), CallingStation("station")]
    for i in range(5):
        hand, seated = new_hand(
            agents, [100, 100], button=i % 2, small_blind=1, big_blind=2, seed=i
        )
        histories.append(HandHistory.from_played(play_hand(hand, seated)))
    path = tmp_path / "hands.jsonl"
    assert write_jsonl(path, histories) == 5
    assert write_jsonl(path, histories[:2], append=True) == 2
    back = list(read_jsonl(path))
    assert back == histories + histories[:2]


def test_helpers() -> None:
    history = HandHistory.from_played(showdown_hand())
    assert hole_cards(history, 1) == tuple(parse_cards("Kc Kd"))
    assert board_cards(history) == parse_cards("2h 5s 8h 9s Jh")
    assert parse_action("raise 12") == Action.raise_to(12)
    assert parse_action("bet 3") == Action.bet(3)
    assert parse_action("fold") == Action.fold()
    assert parse_action("check") == Action.check()
    assert parse_action("call") == Action.call()


def test_an_ante_survives_the_record_and_the_rendering() -> None:
    deck = stacked({0: "Ac Ad", 1: "Kc Kd"}, "2h 5s 8h 9s Jh", button=0)
    hand = Hand(
        [Player("alice", 100), Player("bob", 100)],
        button=0,
        small_blind=1,
        big_blind=2,
        ante=5,
        seed=11,
        deck=deck,
    )
    hand.apply(Action.call())
    hand.apply(Action.check())
    while not hand.finished:
        hand.apply(Action.check())
    history = HandHistory.from_played(PlayedHand(hand))
    assert history.ante == 5
    assert HandHistory.from_json(history.to_json()).ante == 5
    text = history.render()
    assert "NLHE 1/2 ante 5" in text
    assert text.count("posts the ante 5") == 2
    assert "Total pot 14" in text  # two antes and two big blinds
    # an older file without the field still loads
    assert (
        HandHistory.from_dict({k: v for k, v in history.to_dict().items() if k != "ante"}).ante == 0
    )
