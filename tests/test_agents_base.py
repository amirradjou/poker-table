import pytest

from poker_table.agents import Decision, SeatView, make_view, position_name
from poker_table.engine import Action, EventKind, Hand, Player


def players(*stacks: int) -> list[Player]:
    return [Player(f"p{i}", stack) for i, stack in enumerate(stacks)]


@pytest.mark.parametrize(
    ("n", "button", "expected"),
    [
        (2, 0, ["BTN/SB", "BB"]),
        (3, 0, ["BTN", "SB", "BB"]),
        (4, 3, ["SB", "BB", "UTG", "BTN"]),
        (6, 0, ["BTN", "SB", "BB", "UTG", "HJ", "CO"]),
        (9, 8, ["SB", "BB", "UTG", "UTG+1", "MP", "LJ", "HJ", "CO", "BTN"]),
        (10, 0, ["BTN", "SB", "BB", "UTG", "UTG+1", "UTG+2", "MP", "LJ", "HJ", "CO"]),
    ],
)
def test_position_names(n: int, button: int, expected: list[str]) -> None:
    assert [position_name(i, button, n) for i in range(n)] == expected


def test_view_never_leaks_other_hole_cards() -> None:
    hand = Hand(players(100, 100, 100), button=0, small_blind=1, big_blind=2, seed=3)
    view = make_view(hand, 0)
    assert view.hole == hand.seats[0].hole
    others = {c for s in hand.seats[1:] for c in (s.hole or ())}
    mentioned = {c for e in view.events for c in e.cards}
    assert not (mentioned & others)
    assert others.isdisjoint(view.hole)
    text = view.describe()
    for card in others:
        assert str(card) not in text
    for card in view.hole:
        assert str(card) in text


def test_view_reflects_the_acting_seat() -> None:
    hand = Hand(players(100, 100, 100), button=0, small_blind=1, big_blind=2, seed=3)
    view = make_view(hand, 0)
    assert view.position == "BTN"
    assert view.to_call == 2
    assert view.pot == 3
    assert view.pot_odds == pytest.approx(2 / 5)
    assert view.active_players == 3
    assert view.legal.describe() == "fold, call 2, raise to 4..100"
    assert [p.position for p in view.players] == ["BTN", "SB", "BB"]
    assert "Legal: fold, call 2, raise to 4..100" in view.describe()


def test_view_of_a_seat_not_on_the_move_has_no_legal_actions() -> None:
    hand = Hand(players(100, 100, 100), button=0, small_blind=1, big_blind=2, seed=3)
    view = make_view(hand, 2)
    assert not view.legal.can_raise and not view.legal.can_call and not view.legal.can_check


def test_preflop_aggressor_and_street_actions() -> None:
    hand = Hand(players(100, 100, 100), button=0, small_blind=1, big_blind=2, seed=3)
    hand.apply(Action.raise_to(6))
    hand.apply(Action.call())
    hand.apply(Action.call())
    view = make_view(hand, 1)
    assert view.preflop_aggressor() == 0
    assert view.street_actions == ()
    assert any(e.kind is EventKind.STREET for e in view.events)
    assert "--- flop:" in view.describe()


def test_decision_defaults() -> None:
    d = Decision(Action.fold())
    assert d.reasoning == "" and d.table_talk == ""
    assert isinstance(d, Decision) and isinstance(make_view, object)
    assert SeatView.__dataclass_params__.frozen


def test_a_seat_can_ask_for_the_numbers_without_seeing_another_seat_s_cards() -> None:
    from poker_table.cards import Deck, parse_cards

    hole = {0: "Ah Kh", 1: "7c 2d", 2: "Qs Js"}
    order = [(1 + i) % 3 for i in range(3)]
    deck = Deck.stacked(
        [parse_cards(hole[i])[0] for i in order]
        + [parse_cards(hole[i])[1] for i in order]
        + parse_cards("Qh 7h 2c")
    )
    hand = Hand(players(200, 200, 200), button=0, small_blind=1, big_blind=2, seed=0, deck=deck)
    for action in (Action.call(), Action.call(), Action.check()):
        hand.apply(action)
    view = make_view(hand, 0)
    spot = view.odds(samples=300)
    assert spot.opponents == 2 and not spot.known_hands and spot.outs is None
    assert 0.0 < spot.equity < 1.0
    text = view.describe(numbers=True)
    assert "The numbers" in text and "Equity" in text
    assert "7c" not in text and "Qs" not in text  # still nobody else's cards
    assert "The numbers" not in view.describe()  # off unless asked for
