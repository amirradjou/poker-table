import pytest

from poker_table.agents import make_view
from poker_table.agents.reading import read_hand
from poker_table.cards import Deck, parse_cards
from poker_table.coach.equity import equity
from poker_table.coach.facts import read_ranges
from poker_table.coach.ranges import open_range, range_fraction
from poker_table.engine import Action, Hand, Player
from poker_table.odds import chances


def six_max(hole: dict[int, str], board: str = "") -> Hand:
    """Six seats, button on seat 0: UTG is seat 3, HJ 4, CO 5, then BTN, SB 1, BB 2."""
    order = [(1 + i) % 6 for i in range(6)]
    first = [parse_cards(hole[i])[0] for i in order]
    second = [parse_cards(hole[i])[1] for i in order]
    deck = Deck.stacked(first + second + parse_cards(board))
    players = [Player(f"p{i}", 200) for i in range(6)]
    return Hand(players, button=0, small_blind=1, big_blind=2, seed=0, deck=deck)


HOLES = {0: "2c 3d", 1: "4c 5d", 2: "Kh Jd", 3: "9c 9d", 4: "6c 6d", 5: "Ac Qs"}


def co_opens_to_the_big_blind(holes=HOLES, board="8s 7d 2h 3s Th") -> Hand:
    hand = six_max(holes, board)
    hand.apply(Action.fold())  # UTG
    hand.apply(Action.fold())  # HJ
    hand.apply(Action.raise_to(6))  # CO opens
    hand.apply(Action.fold())  # BTN
    hand.apply(Action.fold())  # SB
    return hand  # the big blind (seat 2) to act


def test_an_open_puts_the_opener_on_the_chart_for_their_seat() -> None:
    view = make_view(co_opens_to_the_big_blind(), 2)
    ranges = read_ranges(view)
    assert ranges == {5: open_range("CO")}  # folded seats are gone
    reading = read_hand(view, samples=400)
    (co,) = reading.ranges
    assert co.name == "p5" and co.story == "opened from the CO"
    assert co.share == pytest.approx(range_fraction(open_range("CO")))
    assert reading.informative


def test_a_bet_on_the_flop_narrows_the_range_further() -> None:
    hand = co_opens_to_the_big_blind()
    hand.apply(Action.call())  # BB defends
    hand.apply(Action.check())  # flop: BB checks
    hand.apply(Action.bet(8))  # CO c-bets
    view = make_view(hand, 2)
    (co,) = read_hand(view, samples=300).ranges
    assert co.story == "opened from the CO, bet the flop"
    assert co.share < range_fraction(open_range("CO"))


def test_the_range_costs_equity_that_random_hands_do_not() -> None:
    hand = six_max(HOLES)
    hand.apply(Action.raise_to(6))  # UTG opens: a tight range
    for _ in range(4):
        hand.apply(Action.fold())
    view = make_view(hand, 2)  # KJo in the big blind
    reading = read_hand(view, samples=3000, seed=1)
    against_random = chances(view.hole, (), 1, samples=3000, seed=1).equity
    assert reading.equity < against_random - 0.1  # KJo is fine against any two, not vs UTG
    same = equity(view.hole, (), list(read_ranges(view).values()), samples=3000, seed=1)
    assert reading.equity == pytest.approx(same)
    assert "Your equity against those ranges" in reading.lines()[-1]


def test_it_reads_the_action_not_the_cards() -> None:
    weak = co_opens_to_the_big_blind()
    strong = co_opens_to_the_big_blind({**HOLES, 5: "7c 2s"})  # same action, other cards
    a = read_hand(make_view(weak, 2), samples=500, seed=3)
    b = read_hand(make_view(strong, 2), samples=500, seed=3)
    assert a == b


def test_before_anyone_acts_there_is_nothing_to_read() -> None:
    view = make_view(six_max(HOLES), 3)  # UTG, first to act
    reading = read_hand(view, samples=200)
    assert not reading.informative
    assert {r.story for r in reading.ranges} == {"has not acted yet"}
