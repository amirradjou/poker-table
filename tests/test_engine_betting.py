import random

import pytest

from poker_table.cards import Deck, parse_cards
from poker_table.engine import Action, EventKind, Hand, IllegalAction, Player, Street
from poker_table.pots import Pot


def players(*stacks: int) -> list[Player]:
    return [Player(f"p{i}", stack) for i, stack in enumerate(stacks)]


def stacked(hole: dict[int, str], board: str, *, button: int) -> Deck:
    """Build a deck that deals ``hole[seat]`` to each seat and then ``board``."""
    n = len(hole)
    order = [(button + 1 + i) % n for i in range(n)]
    first = [parse_cards(hole[i])[0] for i in order]
    second = [parse_cards(hole[i])[1] for i in order]
    return Deck.stacked(first + second + parse_cards(board))


def make(stacks, *, button=0, sb=1, bb=2, deck=None) -> Hand:
    return Hand(players(*stacks), button=button, small_blind=sb, big_blind=bb, seed=1, deck=deck)


def kinds(hand: Hand, kind: EventKind) -> list:
    return [e for e in hand.events if e.kind is kind]


def test_fold_around_to_the_big_blind() -> None:
    hand = make([100, 100, 100])
    hand.apply(Action.fold())  # UTG
    hand.apply(Action.fold())  # SB
    assert hand.finished
    assert hand.net() == {0: 0, 1: -1, 2: 1}
    assert [(e.seat, e.amount) for e in kinds(hand, EventKind.RETURN_UNCALLED)] == [(2, 1)]
    assert [(e.seat, e.amount) for e in kinds(hand, EventKind.WIN)] == [(2, 2)]
    assert hand.actor is None


def test_limp_around_then_the_flop_is_dealt() -> None:
    hand = make([100, 100, 100])
    hand.apply(Action.call())
    hand.apply(Action.call())
    assert hand.legal_actions().can_check  # big blind option
    hand.apply(Action.check())
    assert hand.street is Street.FLOP
    assert len(hand.board) == 3
    assert hand.pot == 6
    assert hand.actor is not None and hand.actor.index == 1  # first after the button
    assert hand.current_bet == 0
    assert hand.legal_actions().describe() == "fold, check, bet 2..98"


def test_big_blind_option_raise_reopens_the_action() -> None:
    hand = make([100, 100, 100])
    hand.apply(Action.call())
    hand.apply(Action.call())
    hand.apply(Action.raise_to(6))
    assert hand.actor is not None and hand.actor.index == 0
    legal = hand.legal_actions()
    assert legal.call_amount == 4
    assert legal.min_raise_to == 10


def test_min_raise_tracks_the_last_full_raise() -> None:
    hand = make([200, 200, 200])
    hand.apply(Action.raise_to(6))  # raise size 4
    assert hand.legal_actions().min_raise_to == 10
    hand.apply(Action.raise_to(15))  # raise size 9
    assert hand.legal_actions().min_raise_to == 24
    hand.apply(Action.call())  # BB calls 15
    legal = hand.legal_actions()  # back to UTG
    assert legal.call_amount == 9
    assert legal.min_raise_to == 24


def test_bet_then_fold_returns_the_uncalled_bet() -> None:
    hand = make([100, 100])  # heads-up: seat 0 is the button/SB
    hand.apply(Action.call())
    hand.apply(Action.check())
    assert hand.street is Street.FLOP
    assert hand.actor is not None and hand.actor.index == 1  # BB acts first postflop
    hand.apply(Action.bet(10))
    hand.apply(Action.fold())
    assert hand.finished
    assert hand.net() == {0: -2, 1: 2}
    assert [(e.seat, e.amount) for e in kinds(hand, EventKind.RETURN_UNCALLED)] == [(1, 10)]


def test_illegal_actions_are_rejected_without_changing_state() -> None:
    hand = make([100, 100, 100])
    with pytest.raises(IllegalAction, match="cannot check"):
        hand.apply(Action.check())
    with pytest.raises(IllegalAction, match="use raise"):
        hand.apply(Action.bet(10))
    with pytest.raises(IllegalAction, match="outside 4..100"):
        hand.apply(Action.raise_to(3))
    with pytest.raises(IllegalAction, match="outside 4..100"):
        hand.apply(Action.raise_to(101))
    assert hand.actor is not None and hand.actor.index == 0
    hand.apply(Action.call())
    hand.apply(Action.call())
    with pytest.raises(IllegalAction, match="nothing to call"):
        hand.apply(Action.call())
    hand.apply(Action.check())
    with pytest.raises(IllegalAction, match="use bet"):
        hand.apply(Action.raise_to(10))
    hand.apply(Action.fold())
    hand.apply(Action.fold())
    assert hand.finished
    with pytest.raises(IllegalAction, match="over"):
        hand.apply(Action.fold())


def test_folding_is_legal_even_when_a_check_is_available() -> None:
    hand = make([100, 100])
    hand.apply(Action.call())
    hand.apply(Action.fold())
    assert hand.finished and hand.net() == {0: 2, 1: -2}


def test_heads_up_all_in_preflop_runs_out_the_board() -> None:
    deck = stacked({0: "Ac Ad", 1: "Kc Kd"}, "2h 5s 8h 9s Jh", button=0)
    hand = make([100, 100], deck=deck)
    hand.apply(Action.raise_to(100))
    assert hand.legal_actions().describe() == "fold, call 98"
    hand.apply(Action.call())
    assert hand.finished
    assert hand.street is Street.SHOWDOWN
    assert len(hand.board) == 5
    assert len(kinds(hand, EventKind.STREET)) == 3
    assert hand.net() == {0: 100, 1: -100}
    assert hand.pots == [Pot(200, (0, 1))]
    showdown = kinds(hand, EventKind.SHOWDOWN)
    assert [e.text for e in showdown] == ["a pair of aces", "a pair of kings"]
    assert kinds(hand, EventKind.WIN)[0].text == "main pot with a pair of aces"


def test_short_stack_all_in_creates_a_side_pot_the_others_keep_playing_for() -> None:
    deck = stacked({0: "Ac Ad", 1: "2c 2d", 2: "Kc Kd"}, "3h 5s 8h 9s Jh", button=0)
    hand = make([20, 100, 100], deck=deck)
    hand.apply(Action.raise_to(20))  # button shoves
    hand.apply(Action.call())
    hand.apply(Action.call())
    assert hand.street is Street.FLOP
    assert hand.actor is not None and hand.actor.index == 1
    hand.apply(Action.bet(30))
    hand.apply(Action.call())
    hand.apply(Action.check())
    hand.apply(Action.check())
    hand.apply(Action.check())
    hand.apply(Action.check())
    assert hand.finished
    assert hand.pots == [Pot(60, (0, 1, 2)), Pot(60, (1, 2))]
    assert hand.payouts == {0: 60, 2: 60}
    assert hand.net() == {0: 40, 1: -50, 2: 10}


def test_incomplete_all_in_raise_does_not_reopen_the_action() -> None:
    hand = make([200, 15, 200])
    hand.apply(Action.raise_to(10))  # UTG, raise size 8
    legal = hand.legal_actions()  # SB has 14 behind + 1 posted
    assert legal.min_raise_to == legal.max_raise_to == 15
    hand.apply(Action.raise_to(15))  # all-in for less than a full raise
    legal = hand.legal_actions()  # BB has not acted yet: may still raise
    assert legal.can_raise and legal.min_raise_to == 23
    hand.apply(Action.call())
    legal = hand.legal_actions()  # back on UTG, who already acted
    assert legal.call_amount == 5
    assert not legal.can_raise
    assert legal.describe() == "fold, call 5"


def test_full_all_in_raise_reopens_the_action() -> None:
    hand = make([200, 20, 200])
    hand.apply(Action.raise_to(10))
    hand.apply(Action.raise_to(20))  # raise size 10 >= 8: a full raise
    hand.apply(Action.call())
    legal = hand.legal_actions()
    assert legal.can_raise and legal.min_raise_to == 30


def test_odd_chip_goes_to_the_first_winner_after_the_button() -> None:
    deck = stacked({0: "2c 3d", 1: "7c 8d", 2: "2h 3s"}, "Ah Kh Qh Jh Th", button=0)
    hand = make([10, 10, 10], deck=deck)
    hand.apply(Action.raise_to(5))
    hand.apply(Action.fold())
    hand.apply(Action.call())
    for _ in range(6):
        hand.apply(Action.check())
    assert hand.finished
    assert hand.payouts == {0: 5, 2: 6}


def test_everyone_all_in_from_the_blinds_needs_no_decisions() -> None:
    hand = make([1, 2])
    assert hand.finished
    assert hand.actor is None
    assert kinds(hand, EventKind.ACTION) == []
    assert len(hand.board) == 5
    assert sum(s.stack for s in hand.seats) == 3


def test_all_in_below_the_big_blind_is_only_a_call() -> None:
    hand = make([100, 100, 100, 1])
    legal = hand.legal_actions()
    assert legal.call_amount == 1 and not legal.can_raise
    hand.apply(Action.call())
    assert hand.seats[3].all_in


def random_action(hand: Hand, rng: random.Random) -> Action:
    legal = hand.legal_actions()
    choices: list[Action] = []
    if legal.can_check:
        choices += [Action.check()] * 3
    else:
        choices += [Action.fold()]
    if legal.can_call:
        choices += [Action.call()] * 3
    if legal.can_raise:
        if rng.random() < 0.5:
            amount = rng.choice([legal.min_raise_to, legal.max_raise_to])
        else:
            amount = rng.randint(legal.min_raise_to, legal.max_raise_to)
        choices += [Action.bet(amount) if legal.is_bet else Action.raise_to(amount)] * 2
    return rng.choice(choices)


@pytest.mark.parametrize("seed", range(40))
def test_random_play_always_finishes_and_conserves_chips(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(10):
        n = rng.randint(2, 6)
        stacks = [rng.randint(1, 300) for _ in range(n)]
        ante = rng.choice([0, 0, 1, 5])  # antes are dead money, so they stress the pot maths
        hand = Hand(
            players(*stacks),
            button=rng.randrange(n),
            small_blind=1,
            big_blind=2,
            ante=ante,
            seed=rng.randrange(10**6),
        )
        steps = 0
        while not hand.finished:
            hand.apply(random_action(hand, rng))
            steps += 1
            assert steps < 500
        assert sum(s.stack for s in hand.seats) == sum(stacks)
        assert sum(hand.payouts.values()) == sum(s.total_bet for s in hand.seats)
        posted = [e.amount for e in hand.events if e.kind is EventKind.POST_ANTE]
        assert sum(posted) == sum(min(ante, stack) for stack in stacks)  # short stacks ante partly
        assert hand.events[-1].kind is EventKind.HAND_END
        assert len(hand.board) in (0, 3, 4, 5)
        assert all(s.stack >= 0 for s in hand.seats)
        assert set(hand.payouts) <= {s.index for s in hand.in_hand}
