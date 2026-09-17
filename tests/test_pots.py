import pytest

from poker_table.pots import Pot, build_pots, split_amount, uncalled_amount


def test_single_pot_when_everyone_matched() -> None:
    assert build_pots({0: 50, 1: 50, 2: 50}, in_hand=[0, 1, 2]) == [Pot(150, (0, 1, 2))]


def test_folded_seat_pays_into_the_pot_but_cannot_win_it() -> None:
    assert build_pots({0: 50, 1: 50, 2: 10}, in_hand=[0, 1]) == [Pot(110, (0, 1))]


def test_one_short_all_in_creates_a_side_pot() -> None:
    pots = build_pots({0: 20, 1: 100, 2: 100}, in_hand=[0, 1, 2])
    assert pots == [Pot(60, (0, 1, 2)), Pot(160, (1, 2))]


def test_two_short_all_ins_create_two_side_pots() -> None:
    pots = build_pots({0: 10, 1: 30, 2: 100, 3: 100}, in_hand=[0, 1, 2, 3])
    assert pots == [Pot(40, (0, 1, 2, 3)), Pot(60, (1, 2, 3)), Pot(140, (2, 3))]


def test_fold_between_all_in_levels_merges_into_the_side_pot() -> None:
    # Seat 3 folded after putting in 50: those chips join the pot the others contest.
    pots = build_pots({0: 20, 1: 100, 2: 100, 3: 50}, in_hand=[0, 1, 2])
    assert pots == [Pot(80, (0, 1, 2)), Pot(190, (1, 2))]


def test_zero_contributions_are_ignored() -> None:
    assert build_pots({0: 0, 1: 5, 2: 5}, in_hand=[1, 2]) == [Pot(10, (1, 2))]


def test_uncalled_bet_is_detected() -> None:
    assert uncalled_amount({0: 150, 1: 50, 2: 50}) == (0, 100)
    assert uncalled_amount({0: 100, 1: 100, 2: 20}) is None
    assert uncalled_amount({0: 3}) is None


def test_split_even() -> None:
    assert split_amount(100, [1, 3], first_after=0, num_seats=6) == {1: 50, 3: 50}


def test_odd_chips_go_clockwise_from_the_button() -> None:
    # Button on seat 4: seat 5 is first after it, then 0, 1, ...
    payout = split_amount(101, [1, 5], first_after=5, num_seats=6)
    assert payout == {1: 50, 5: 51}
    payout = split_amount(11, [0, 2, 4], first_after=3, num_seats=6)
    assert payout == {4: 4, 0: 4, 2: 3}


def test_split_requires_a_winner() -> None:
    with pytest.raises(ValueError):
        split_amount(10, [], first_after=0, num_seats=2)
