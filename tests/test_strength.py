import pytest

from poker_table.agents.strength import (
    MadeHand,
    chen_score,
    classify,
    has_flush_draw,
    has_open_ender,
    outs_equity,
)
from poker_table.cards import parse_cards


@pytest.mark.parametrize(
    ("hole", "score"),
    [
        ("As Ah", 20),
        ("Ks Kh", 16),
        ("As Ks", 12),
        ("As Kh", 10),
        ("Ts Th", 10),
        ("2s 2h", 5),
        ("7s 2h", -1),
        ("Js Ts", 9),
        ("5s 4s", 6),
        ("As 2h", 5),  # A-2 offsuit: 10 - 5 (4+ gap)
    ],
)
def test_chen_scores(hole: str, score: float) -> None:
    assert chen_score(parse_cards(hole)) == score


def c(hole: str, board: str) -> MadeHand:
    return classify(parse_cards(hole), parse_cards(board))


def test_classify_pairs() -> None:
    assert c("Ah Kd", "Ac 7s 2d") is MadeHand.TOP_PAIR
    assert c("Kh Qd", "Ac Ks 2d") is MadeHand.WEAK_PAIR
    assert c("Qh Qd", "Jc 7s 2d") is MadeHand.TOP_PAIR  # overpair
    assert c("7h 7d", "Jc 9s 2d") is MadeHand.WEAK_PAIR
    assert c("Qh 3d", "7c 7s 2d") is MadeHand.NOTHING  # board pair only
    assert c("Qh 3d", "7c 9s 2d") is MadeHand.NOTHING


def test_classify_strong_hands_need_our_cards() -> None:
    assert c("Ah Kd", "Ac Ks 2d") is MadeHand.STRONG
    assert c("7h 7d", "7c Ks 2d") is MadeHand.STRONG
    assert c("Qh 3d", "7c 7s 2d 2h") is MadeHand.NOTHING  # two pair on the board, not ours
    assert c("Qh 3d", "7c 7s 2d 2h 9c") is MadeHand.NOTHING
    assert c("Th 9d", "8c 7s 6d") is MadeHand.STRONG  # straight
    assert c("Ah 3d", "8c 7s 6d 5h 4c") is MadeHand.NOTHING  # straight on the board
    assert c("9h 3d", "8c 7s 6d 5h 4c") is MadeHand.STRONG  # we improve the board straight
    assert c("Ah Qd", "Ac 7s 7d") is MadeHand.STRONG  # top pair on a paired board = two pair


def test_draws() -> None:
    assert has_flush_draw(parse_cards("Ah 2h"), parse_cards("Kh 7h 2d"))
    assert has_flush_draw(parse_cards("Ah 2d"), parse_cards("Kh 7h 2h"))  # one-card nut draw
    assert not has_flush_draw(parse_cards("Ac 2d"), parse_cards("Kh 7h 2h"))  # none of ours
    assert not has_flush_draw(parse_cards("Ah 2h"), parse_cards("Kh 7h 2d 3c 4s"))  # river
    assert has_open_ender(parse_cards("Th 9d"), parse_cards("8c 7s 2d"))
    assert not has_open_ender(parse_cards("Th 9d"), parse_cards("8c 7s 6d"))  # already made
    assert not has_open_ender(parse_cards("Ah Kd"), parse_cards("Qc Js 2d"))  # one-ended
    assert not has_open_ender(parse_cards("2h 3d"), parse_cards("Tc 9s 8d"))  # not our cards


def test_outs_equity() -> None:
    assert outs_equity(9, 2) == pytest.approx(0.36)
    assert outs_equity(8, 1) == pytest.approx(0.16)
    assert outs_equity(40, 2) == 0.95


def test_quick_made_hand_agrees_with_classify() -> None:
    import random

    from poker_table.agents.strength import quick_made_hand
    from poker_table.cards import FULL_DECK

    rng = random.Random(9)
    agree = total = 0
    for _ in range(3000):
        n = rng.choice([3, 4, 5])
        cards = rng.sample(FULL_DECK, 2 + n)
        hole, board = cards[:2], cards[2:]
        total += 1
        agree += quick_made_hand(hole, board) == classify(hole, board)
    assert agree / total > 0.99
    assert quick_made_hand(parse_cards("Ah Kd"), parse_cards("Ac 7s 2d")) is MadeHand.TOP_PAIR
    assert quick_made_hand(parse_cards("7h 7d"), parse_cards("Jc 9s 2d")) is MadeHand.WEAK_PAIR
    assert quick_made_hand(parse_cards("Th 9d"), parse_cards("8c 7s 6d")) is MadeHand.STRONG
    assert quick_made_hand(parse_cards("Ah 2h"), parse_cards("Kh 7h 3h")) is MadeHand.STRONG
    assert quick_made_hand(parse_cards("Qh 3d"), parse_cards("7c 7s 2d")) is MadeHand.NOTHING
