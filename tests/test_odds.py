import pytest

from poker_table.agents import make_view
from poker_table.cards import Deck, parse_cards
from poker_table.engine import Action, Hand, Player
from poker_table.odds import (
    calculate,
    chances,
    for_view,
    hit_probability,
    improving_cards,
    rule_of_two_and_four,
    winning_outs,
)


def cards(text: str):
    return parse_cards(text)


def hand(text: str) -> tuple:
    a, b = parse_cards(text)
    return (a, b)


# ----- with the other hands: enumerated, so the answer is a fraction, not an estimate -----


def test_known_hands_on_the_turn_count_every_river_card() -> None:
    # 98 needs a six or a jack against aces: 8 of the 44 unseen cards, and nothing else.
    result = chances(cards("9h 8d"), cards("Ts 7c 2d Kh"), [hand("As Ac")])
    assert result.exact and result.trials == 44
    assert result.win == pytest.approx(8 / 44) and result.lose == pytest.approx(36 / 44)
    assert result.tie == 0.0 and result.equity == pytest.approx(8 / 44)
    assert result.error == 0.0


def test_a_hand_that_cannot_win_is_zero_and_a_hand_that_cannot_lose_is_one() -> None:
    drawing_dead = chances(cards("Ah Kd"), cards("8s 7d 2c"), [hand("Qs Js"), hand("7h 7c")])
    assert drawing_dead.equity == 0.0 and drawing_dead.exact
    nuts = chances(cards("Ah Kh"), cards("Qh Jh Th"), [hand("As Ac")])
    assert nuts.equity == 1.0 and nuts.win == 1.0


def test_a_chopped_pot_is_half_an_equity_not_a_win() -> None:
    # both play the board: a five-card straight nobody can beat with their own cards
    split = chances(cards("2c 3d"), cards("Ah Kh Qs Js Tc"), [hand("2h 3s")])
    assert split.tie == 1.0 and split.win == 0.0
    assert split.equity == pytest.approx(0.5)
    three_ways = chances(cards("2c 3d"), cards("Ah Kh Qs Js Tc"), [hand("2h 3s"), hand("2s 4d")])
    assert three_ways.equity == pytest.approx(1 / 3)


def test_the_river_against_one_unknown_hand_is_enumerated_too() -> None:
    result = chances(cards("Ah Kh"), cards("Qh 7h 2c 3s 9d"), 1)
    assert result.exact and result.trials == 990  # every hand they could hold
    assert result.win + result.tie + result.lose == pytest.approx(1.0)


# ----- without the other hands: sampled, and it says so ----------------------------------


def test_sampling_is_seeded_and_reports_its_own_error() -> None:
    kwargs = {"samples": 2000, "seed": 4}
    first = chances(cards("Ah Ad"), (), 1, **kwargs)
    assert first == chances(cards("Ah Ad"), (), 1, **kwargs)
    assert not first.exact and first.trials == 2000
    assert first.equity != chances(cards("Ah Ad"), (), 1, samples=2000, seed=5).equity
    # aces against one random hand are about 85%: the published number, within the error bar
    assert abs(first.equity - 0.85) < 4 * first.error
    assert 0.005 < first.error < 0.02
    assert chances(cards("Ah Ad"), (), 1, samples=8000, seed=4).error < first.error


def test_some_hands_are_known_and_some_are_not() -> None:
    mixed = chances(cards("Ah Ad"), cards("Kc 7d 2s"), [hand("Kh Ks"), None], samples=800, seed=1)
    assert not mixed.exact  # one unknown hand, so it is sampled
    assert mixed.equity < 0.2  # set of kings, and someone else still to beat


# ----- outs -------------------------------------------------------------------------------


def test_outs_against_a_known_hand_are_the_cards_that_put_you_ahead() -> None:
    outs = winning_outs(cards("9h 8d"), cards("Ts 7c 2d Kh"), [hand("As Ac")])
    assert len(outs) == 8 and {c.rank.char for c in outs} == {"6", "J"}
    ahead = winning_outs(cards("As Ac"), cards("Ts 7c 2d Kh"), [hand("9h 8d")])
    assert ahead == ()  # already the best hand: no outs, just the pot


def test_counting_outs_blind_matches_the_table_counts() -> None:
    assert len(improving_cards(cards("Ah Kd"), cards("8s 7d 2c"))) == 6  # two overcards
    assert len(improving_cards(cards("Ah Kh"), cards("Qh 7h 2c"))) == 15  # and a flush draw
    assert len(improving_cards(cards("9h 8d"), cards("Ts 7c 2d"))) == 14  # open-ender plus pairs
    # a card that only pairs the board hands everyone the same pair, so it is not an out
    assert not any(c.rank.char == "8" for c in improving_cards(cards("Ah Kd"), cards("8s 7d 2c")))
    assert improving_cards(cards("Ah Kd"), ()) == ()  # preflop everything "improves"


def test_outs_to_a_probability() -> None:
    assert hit_probability(9, 47, 1) == pytest.approx(9 / 47)  # 19.1% on the turn
    assert hit_probability(9, 47, 2) == pytest.approx(1 - (38 * 37) / (47 * 46))  # 35.0%
    assert hit_probability(0, 47, 2) == 0.0 and hit_probability(9, 47, 0) == 0.0
    assert hit_probability(50, 47, 2) == 1.0  # more outs than cards: it cannot miss
    assert rule_of_two_and_four(9, 2) == pytest.approx(0.36)  # the estimate runs high
    assert rule_of_two_and_four(9, 1) == pytest.approx(0.18)


# ----- the price --------------------------------------------------------------------------


def test_pot_odds_and_the_value_of_a_call() -> None:
    spot = calculate(cards("9h 8d"), cards("Ts 7c 2d Kh"), [hand("As Ac")], pot=34, to_call=12)
    assert spot.pot_odds == pytest.approx(12 / 46)  # you buy 46 for 12
    assert spot.required_equity == spot.pot_odds and spot.ratio == "2.8:1"
    assert spot.equity == pytest.approx(8 / 44)
    assert spot.ev_call == pytest.approx(8 / 44 * 46 - 12)
    assert spot.ev_call < 0 and "less equity than the price asks" in spot.verdict
    cheap = calculate(cards("9h 8d"), cards("Ts 7c 2d Kh"), [hand("As Ac")], pot=200, to_call=12)
    assert cheap.ev_call > 0 and "more equity" in cheap.verdict
    free = calculate(cards("9h 8d"), cards("Ts 7c 2d Kh"), [hand("As Ac")], pot=34)
    assert free.pot_odds == 0.0 and free.ratio == "-" and free.verdict == "nothing to call"


def test_the_lines_say_what_was_counted() -> None:
    exact = calculate(cards("9h 8d"), cards("Ts 7c 2d Kh"), [hand("As Ac")], pot=34, to_call=12)
    text = "\n".join(exact.lines())
    assert "exact, 44 run-outs" in text and "Outs to the best hand: 8" in text
    assert "18.2% by the river, 16% by the rule of 2 and 4" in text
    assert "pot odds 2.8:1" in text and "break-even equity 26.1%" in text
    blind = calculate(cards("9h 8d"), cards("Ts 7c 2d"), 2, pot=34, to_call=12, samples=500)
    text = "\n".join(blind.lines())
    assert "500 samples, ±" in text and "Cards that better your hand: 14" in text
    assert "2 unknown hands" in blind.summary() and "pot odds" in blind.summary()


# ----- what a seat is allowed to ask ------------------------------------------------------


def stacked(hole: dict[int, str], board: str = "", *, button: int = 0) -> Deck:
    n = len(hole)
    order = [(button + 1 + i) % n for i in range(n)]
    first = [parse_cards(hole[i])[0] for i in order]
    second = [parse_cards(hole[i])[1] for i in order]
    return Deck.stacked(first + second + parse_cards(board))


def table(hole: dict[int, str], board: str = "") -> Hand:
    players = [Player(f"p{i}", 200) for i in range(len(hole))]
    return Hand(players, button=0, small_blind=1, big_blind=2, seed=0, deck=stacked(hole, board))


def to_the_flop(hand_: Hand) -> Hand:
    hand_.apply(Action.call())
    hand_.apply(Action.call())
    hand_.apply(Action.check())
    return hand_


def test_a_seat_calculates_blind_because_it_cannot_see_the_other_cards() -> None:
    mine = {0: "Ah Kd", 1: "7c 2d", 2: "Qs Js"}
    theirs = {0: "Ah Kd", 1: "As Ac", 2: "Ks Kc"}  # same seat, far better opponents
    a = for_view(make_view(to_the_flop(table(mine, "8s 7d 2c")), 0), samples=300)
    b = for_view(make_view(to_the_flop(table(theirs, "8s 7d 2c")), 0), samples=300)
    assert a.chances == b.chances  # the cards it cannot see change nothing
    assert not a.known_hands and a.outs is None
    assert a.opponents == 2 and a.cards_to_come == 2


def test_a_seat_sees_the_price_it_is_being_offered() -> None:
    hand_ = table({0: "Ah Kd", 1: "7c 2d", 2: "Qs Js"})
    hand_.apply(Action.raise_to(6))  # seat 0 opens, seat 1 folds, seat 2 faces it
    hand_.apply(Action.fold())
    spot = for_view(make_view(hand_, 2), samples=200)
    assert spot.pot == 9 and spot.to_call == 4  # the big blind already has 2 of the 6 in
    assert spot.opponents == 1  # the folded seat is not in the way any more
    assert spot.required_equity == pytest.approx(4 / 13)


# ----- refusals ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"hole": "Ah Kd Qs", "board": "", "opponents": 1}, "two cards"),
        ({"hole": "Ah Kd", "board": "8s 7d 2c 3h 4h 5h", "opponents": 1}, "at most five"),
        ({"hole": "Ah Kd", "board": "", "opponents": 0}, "nobody"),
        ({"hole": "Ah Kd", "board": "Ah 7d 2c", "opponents": 1}, "two places"),
    ],
)
def test_refuses_an_impossible_spot(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        chances(cards(kwargs["hole"]), cards(kwargs["board"]), kwargs["opponents"])


# ----- the broadcast numbers: every hand at once ------------------------------------------


def test_table_equities_agree_with_the_calculator_and_add_up_to_one() -> None:
    from poker_table.odds import table_equities

    holes = {0: hand("Ah Ad"), 1: hand("Kh Kd"), 2: hand("9s 8s")}
    shares, exact = table_equities(holes, cards("2c 7d Js"))
    assert exact and sum(shares.values()) == pytest.approx(1.0)
    # the same spot from seat 0's side, as the calculator counts it
    alone = chances(cards("Ah Ad"), cards("2c 7d Js"), [hand("Kh Kd"), hand("9s 8s")])
    assert shares[0] == pytest.approx(alone.equity)


def test_table_equities_settle_on_the_river_and_split_a_chop() -> None:
    from poker_table.odds import table_equities

    shares, exact = table_equities({0: hand("Ah Ad"), 1: hand("Kh Kd")}, cards("2c 7d Js 3h 4c"))
    assert exact and shares == {0: 1.0, 1: 0.0}
    chop, _ = table_equities({0: hand("2c 3d"), 1: hand("2h 3s")}, cards("Ah Kh Qs Js Tc"))
    assert chop == {0: 0.5, 1: 0.5}
    alone, _ = table_equities({4: hand("7c 2d")})
    assert alone == {4: 1.0}  # everyone else folded


def test_preflop_is_sampled_and_seeded() -> None:
    from poker_table.odds import table_equities

    holes = {0: hand("Ah Ad"), 1: hand("Kh Kd")}
    first, exact = table_equities(holes, samples=3000, seed=2)
    assert not exact and first == table_equities(holes, samples=3000, seed=2)[0]
    assert abs(first[0] - 0.82) < 0.03  # aces over kings is about 82%
