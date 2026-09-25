import pytest

from poker_table.engine import Action, EventKind, Hand, Player, Street


def players(*stacks: int) -> list[Player]:
    return [Player(f"p{i}", stack) for i, stack in enumerate(stacks)]


def test_six_max_blinds_and_first_actor() -> None:
    hand = Hand(players(*[200] * 6), button=0, small_blind=1, big_blind=2, seed=1)
    assert hand.sb_index == 1
    assert hand.bb_index == 2
    assert hand.seats[1].stack == 199
    assert hand.seats[2].stack == 198
    assert hand.pot == 3
    assert hand.current_bet == 2
    assert hand.street is Street.PREFLOP
    assert hand.actor is not None and hand.actor.index == 3  # UTG


def test_heads_up_button_posts_small_blind_and_acts_first() -> None:
    hand = Hand(players(100, 100), button=1, small_blind=5, big_blind=10, seed=1)
    assert hand.sb_index == 1
    assert hand.bb_index == 0
    assert hand.actor is not None and hand.actor.index == 1


def test_button_wraps_around() -> None:
    hand = Hand(players(100, 100, 100), button=2, small_blind=1, big_blind=2, seed=1)
    assert hand.sb_index == 0
    assert hand.bb_index == 1
    assert hand.actor is not None and hand.actor.index == 2


def test_everyone_gets_two_distinct_hole_cards() -> None:
    hand = Hand(players(*[100] * 9), button=4, small_blind=1, big_blind=2, seed=42)
    dealt = [card for seat in hand.seats for card in (seat.hole or ())]
    assert len(dealt) == 18
    assert len(set(dealt)) == 18
    assert len(hand.deck) == 52 - 18


def test_same_seed_deals_the_same_cards() -> None:
    a = Hand(players(100, 100, 100), button=0, small_blind=1, big_blind=2, seed=7)
    b = Hand(players(100, 100, 100), button=0, small_blind=1, big_blind=2, seed=7)
    assert [s.hole for s in a.seats] == [s.hole for s in b.seats]


def test_short_stack_posts_a_partial_blind_and_is_all_in() -> None:
    hand = Hand(players(100, 100, 1), button=0, small_blind=1, big_blind=2, seed=1)
    bb = hand.seats[2]
    assert bb.stack == 0 and bb.all_in and not bb.can_act
    assert hand.pot == 2
    assert hand.current_bet == 2  # the big blind still sets the price to play
    posts = [e for e in hand.events if e.kind is EventKind.POST_BLIND]
    assert [(e.seat, e.amount, e.all_in) for e in posts] == [(1, 1, False), (2, 1, True)]


def test_utg_legal_actions_preflop() -> None:
    hand = Hand(players(*[200] * 3), button=0, small_blind=1, big_blind=2, seed=1)
    legal = hand.legal_actions()
    assert not legal.can_check
    assert legal.call_amount == 2
    assert legal.min_raise_to == 4
    assert legal.max_raise_to == 200
    assert not legal.is_bet
    assert legal.describe() == "fold, call 2, raise to 4..200"


def test_short_stack_can_only_shove_when_below_a_min_raise() -> None:
    hand = Hand(players(200, 200, 200, 3), button=0, small_blind=1, big_blind=2, seed=1)
    legal = hand.legal_actions()  # seat 3 (UTG) has 3 chips
    assert legal.call_amount == 2
    assert legal.min_raise_to == legal.max_raise_to == 3


def test_stack_equal_to_call_cannot_raise() -> None:
    hand = Hand(players(200, 200, 200, 2), button=0, small_blind=1, big_blind=2, seed=1)
    legal = hand.legal_actions()
    assert legal.call_amount == 2
    assert not legal.can_raise
    assert legal.describe() == "fold, call 2"


@pytest.mark.parametrize(
    ("stacks", "kwargs", "message"),
    [
        ((100,), {}, "at least two"),
        ((100,) * 11, {}, "at most ten"),
        ((100, 0), {}, "needs chips"),
        ((100, 100), {"small_blind": 0}, "blinds"),
        ((100, 100), {"small_blind": 3}, "blinds"),
    ],
)
def test_rejects_bad_setups(stacks: tuple[int, ...], kwargs: dict, message: str) -> None:
    args = {"button": 0, "small_blind": 1, "big_blind": 2, "seed": 1} | kwargs
    with pytest.raises(ValueError, match=message):
        Hand(players(*stacks), **args)


def test_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="unique"):
        Hand([Player("x", 10), Player("x", 10)], button=0, small_blind=1, big_blind=2, seed=1)


def test_every_seat_antes_before_the_blinds() -> None:
    hand = Hand(players(*[200] * 4), button=0, small_blind=1, big_blind=2, ante=1, seed=1)
    antes = [e for e in hand.events if e.kind is EventKind.POST_ANTE]
    assert [(e.seat, e.amount) for e in antes] == [(1, 1), (2, 1), (3, 1), (0, 1)]  # left of button
    assert hand.pot == 4 + 3  # four antes plus the blinds
    assert [s.stack for s in hand.seats] == [199, 198, 197, 199]
    # the ante is dead money: it buys no part of the blind, so UTG still owes the whole 2
    assert hand.actor is not None and hand.actor.index == 3
    legal = hand.legal_actions()
    assert legal.call_amount == 2 and legal.min_raise_to == 4
    assert [s.street_bet for s in hand.seats] == [0, 1, 2, 0]


def test_an_ante_can_put_a_short_stack_all_in_before_the_deal() -> None:
    hand = Hand(players(200, 200, 1), button=0, small_blind=1, big_blind=2, ante=1, seed=1)
    short = hand.seats[2]
    assert short.stack == 0 and short.all_in and not short.can_act
    assert short.total_bet == 1 and short.street_bet == 0  # it never reached the big blind
    assert short.hole is not None  # still dealt in, and still in the hand
    assert hand.current_bet == 2  # the price to play is the big blind it could not post
    assert hand.pot == 3 + 1  # three antes and the small blind; the short seat posted no blind


def test_antes_are_paid_and_the_hand_still_balances() -> None:
    hand = Hand(players(*[10] * 3), button=0, small_blind=1, big_blind=2, ante=3, seed=5)
    assert [s.total_bet for s in hand.seats] == [3, 4, 5]
    while not hand.finished:
        hand.apply(Action.call() if hand.legal_actions().can_call else Action.check())
    assert sum(s.stack for s in hand.seats) == 30
    assert sum(hand.payouts.values()) == sum(s.total_bet for s in hand.seats)


def test_a_negative_ante_is_refused() -> None:
    with pytest.raises(ValueError, match="ante"):
        Hand(players(100, 100), button=0, small_blind=1, big_blind=2, ante=-1, seed=1)
