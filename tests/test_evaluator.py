import itertools
import random

import pytest

from poker_table.cards import FULL_DECK, parse_cards
from poker_table.evaluator import HandCategory, best_hands, evaluate


def ev(text: str):
    return evaluate(parse_cards(text))


@pytest.mark.parametrize(
    ("cards", "category", "description"),
    [
        ("As Ks Qs Js Ts", HandCategory.STRAIGHT_FLUSH, "royal flush"),
        ("9h 8h 7h 6h 5h 2c 2d", HandCategory.STRAIGHT_FLUSH, "straight flush, nine high"),
        ("Ah 2h 3h 4h 5h Kd Kc", HandCategory.STRAIGHT_FLUSH, "straight flush, five high"),
        ("Kc Kd Kh Ks 2c 3d 4h", HandCategory.FOUR_OF_A_KIND, "four of a kind, kings"),
        ("Kc Kd Kh 5s 5c 3d 4h", HandCategory.FULL_HOUSE, "full house, kings full of fives"),
        ("Kc Kd Kh 5s 5c 5d 4h", HandCategory.FULL_HOUSE, "full house, kings full of fives"),
        ("Ac 9c 7c 4c 2c Kd Kh", HandCategory.FLUSH, "flush, ace high"),
        ("9c 8d 7h 6s 5c Ad Kh", HandCategory.STRAIGHT, "straight, nine high"),
        ("Ac 2d 3h 4s 5c Kd Qh", HandCategory.STRAIGHT, "straight, five high"),
        ("7c 7d 7h 2s 3c Kd 9h", HandCategory.THREE_OF_A_KIND, "three of a kind, sevens"),
        ("Kc Kd 5h 5s 3c 8d 9h", HandCategory.TWO_PAIR, "two pair, kings and fives"),
        ("Jc Jd 5h 2s 3c 8d 9h", HandCategory.PAIR, "a pair of jacks"),
        ("Ac Jd 5h 2s 3c 8d 9h", HandCategory.HIGH_CARD, "ace high"),
    ],
)
def test_categories_and_descriptions(cards: str, category: HandCategory, description: str) -> None:
    rank = ev(cards)
    assert rank.category is category
    assert rank.describe() == description
    assert len(rank.best) == 5
    assert set(rank.best) <= set(parse_cards(cards))


def test_category_order() -> None:
    ladder = [
        "Ac Jd 5h 2s 3c",
        "Jc Jd 5h 2s 3c",
        "Kc Kd 5h 5s 3c",
        "7c 7d 7h 2s 3c",
        "9c 8d 7h 6s 5c",
        "Ac 9c 7c 4c 2c",
        "Kc Kd Kh 5s 5c",
        "Kc Kd Kh Ks 2c",
        "9h 8h 7h 6h 5h",
    ]
    ranks = [ev(hand) for hand in ladder]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)


def test_kickers_break_ties() -> None:
    assert ev("Ac Ad Kh 9s 3c") > ev("As Ah Qh 9d 3d")
    assert ev("Kc Kd 5h 5s Ac") > ev("Kh Ks 5c 5d Qc")
    assert ev("Kc Kd Kh 5s 5c") > ev("Qc Qd Qh As Ac")
    assert ev("Ac 9c 7c 4c 3c") > ev("Ad 9d 7d 4d 2d")
    assert ev("Ac Jd 9h 5s 3c") > ev("Ad Jh 9c 5d 2c")


def test_equal_hands_compare_equal_regardless_of_suits() -> None:
    assert ev("Ac Kd Qh Js 9c") == ev("Ad Kh Qs Jc 9d")
    assert ev("5c 5d 9h 9s Ac 2d 3h") == ev("5h 5s 9c 9d Ah 2c 3s")


def test_wheel_is_the_lowest_straight() -> None:
    assert ev("Ac 2d 3h 4s 5c") < ev("2c 3d 4h 5s 6c")
    assert ev("Ac 2d 3h 4s 5c").kickers == (5,)


def test_flush_uses_best_five_of_six_suited() -> None:
    rank = ev("Ac Kc 9c 7c 4c 2c 3d")
    assert rank.category is HandCategory.FLUSH
    assert [c.rank.char for c in rank.best] == ["A", "K", "9", "7", "4"]


def test_three_pairs_pick_the_best_kicker() -> None:
    rank = ev("Kc Kd 9h 9s 5c 5d Ah")
    assert rank.category is HandCategory.TWO_PAIR
    assert rank.kickers == (13, 9, 14)


def test_two_pair_with_a_paired_kicker_below() -> None:
    # Three pairs where the best kicker is one of the third pair's cards.
    rank = ev("Kc Kd 9h 9s 5c 5d 2h")
    assert rank.kickers == (13, 9, 5)


def test_straight_flush_beats_quads_on_the_same_board() -> None:
    assert ev("9h 8h 7h 6h 5h 5c 5d") > ev("5s 5c 5d 5h Ah 2c 3d")


def test_rejects_wrong_sizes_and_duplicates() -> None:
    with pytest.raises(ValueError):
        ev("Ac Kd Qh Js")
    with pytest.raises(ValueError):
        ev("Ac Kd Qh Js 9c 8d 7h 6s")
    with pytest.raises(ValueError):
        ev("Ac Ac Qh Js 9c")


def test_seven_card_matches_best_of_all_five_card_subsets() -> None:
    rng = random.Random(2024)
    for _ in range(300):
        cards = rng.sample(FULL_DECK, 7)
        brute = max(evaluate(list(combo)) for combo in itertools.combinations(cards, 5))
        assert evaluate(cards) == brute


def test_best_hands_returns_every_winner_on_a_chop() -> None:
    board = parse_cards("Ac Kd Qh Js Tc")
    hands = {
        "a": board + parse_cards("2c 3d"),
        "b": board + parse_cards("4c 5d"),
        "c": board + parse_cards("9c 9d"),
    }
    assert best_hands(hands) == ["a", "b", "c"]
    hands["c"] = board + parse_cards("Kc Kh")
    assert best_hands(hands) == ["a", "b", "c"]  # still just a broadway straight for everyone
    hands["c"] = board + parse_cards("Ad Ah")
    assert best_hands(hands) == ["a", "b", "c"]  # trips lose to the straight; still a chop


def test_best_hands_single_winner() -> None:
    board = parse_cards("Ac Kd 7h 2s 9c")
    hands = {"a": board + parse_cards("Ad 3d"), "b": board + parse_cards("Kc 3c")}
    assert best_hands(hands) == ["a"]
