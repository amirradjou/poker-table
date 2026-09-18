import pytest

from poker_table.cards import FULL_DECK, Card, Deck, Rank, Suit, cards_str, parse_cards


def test_full_deck_has_52_unique_cards() -> None:
    assert len(FULL_DECK) == 52
    assert len(set(FULL_DECK)) == 52


def test_card_round_trips_through_text() -> None:
    for card in FULL_DECK:
        assert Card.parse(str(card)) == card


def test_parse_is_case_insensitive_and_accepts_spacing() -> None:
    assert parse_cards("as KD") == [Card(Rank.ACE, Suit.SPADES), Card(Rank.KING, Suit.DIAMONDS)]
    assert parse_cards("AsKd, Tc") == parse_cards("As Kd Tc")
    assert cards_str(parse_cards("AsKd")) == "As Kd"


@pytest.mark.parametrize("bad", ["1s", "Ax", "A", "Ass"])
def test_parse_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        Card.parse(bad)


def test_ranks_order_and_ace_is_high() -> None:
    assert Rank.TWO < Rank.TEN < Rank.ACE
    assert Rank.from_char("t") is Rank.TEN
    assert Rank.ACE.char == "A"


def test_seeded_deck_is_reproducible() -> None:
    first = Deck(seed=7).deal(52)
    second = Deck(seed=7).deal(52)
    assert first == second
    assert len(set(first)) == 52


def test_different_seeds_give_different_orders() -> None:
    assert Deck(seed=1).deal(10) != Deck(seed=2).deal(10)


def test_deck_runs_out() -> None:
    deck = Deck(seed=0)
    deck.deal(52)
    assert len(deck) == 0
    with pytest.raises(RuntimeError):
        deck.draw()


def test_deck_does_not_touch_global_random(monkeypatch: pytest.MonkeyPatch) -> None:
    import random

    random.seed(123)
    before = random.random()
    random.seed(123)
    Deck(seed=99).deal(52)
    assert random.random() == before


def test_stacked_deck_deals_the_given_cards_first_then_the_rest() -> None:
    top = parse_cards("As Kd 2c")
    deck = Deck.stacked(top)
    assert deck.deal(3) == top
    remaining = deck.deal(49)
    assert len(set(remaining) | set(top)) == 52


def test_stacked_deck_rejects_duplicates() -> None:
    with pytest.raises(ValueError):
        Deck.stacked(parse_cards("As As"))
