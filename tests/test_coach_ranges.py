import random

import pytest

from poker_table.cards import parse_cards
from poker_table.coach.ranges import (
    ALL_CLASSES,
    call_range,
    class_combos,
    combos,
    expand,
    hand_class,
    open_range,
    range_fraction,
    sample_from_range,
    three_bet_range,
)


def test_hand_classes() -> None:
    assert hand_class(parse_cards("As Kd")) == "AKo"
    assert hand_class(parse_cards("Kd As")) == "AKo"
    assert hand_class(parse_cards("Td 9d")) == "T9s"
    assert hand_class(parse_cards("7c 7h")) == "77"
    assert len(ALL_CLASSES) == 169
    assert sum(combos(c) for c in ALL_CLASSES) == 1326


def test_expand_notation() -> None:
    assert expand("22+") == frozenset(f"{r}{r}" for r in "AKQJT98765432")
    assert expand("TT+") == {"TT", "JJ", "QQ", "KK", "AA"}
    assert expand("55-77") == {"55", "66", "77"}
    assert expand("A2s+") == {f"A{k}s" for k in "KQJT98765432"}
    assert expand("KTs+") == {"KQs", "KJs", "KTs"}
    assert expand("AJo+") == {"AKo", "AQo", "AJo"}
    assert expand("T9s-65s") == {"T9s", "98s", "87s", "76s", "65s"}
    assert expand("A9s-A5s") == {"A9s", "A8s", "A7s", "A6s", "A5s"}
    assert expand("AKs, 72o") == {"AKs", "72o"}
    assert expand("*") == ALL_CLASSES
    with pytest.raises(ValueError):
        expand("AAs")
    with pytest.raises(ValueError):
        expand("T9s-A5s")


def test_charts_get_wider_toward_the_button() -> None:
    utg, hj, co, btn = (range_fraction(open_range(p)) for p in ("UTG", "HJ", "CO", "BTN"))
    assert 0.12 < utg < hj < co < btn < 0.5
    assert "AA" in open_range("UTG") and "72o" not in open_range("BTN")
    assert open_range("UTG+1") == open_range("UTG") and open_range("LJ") == open_range("HJ")
    assert "AKo" in three_bet_range() and "QQ" in three_bet_range()
    assert (
        range_fraction(call_range("BB"))
        > range_fraction(call_range("CO"))
        > range_fraction(call_range("SB"))
    )


def test_class_combos_and_sampling() -> None:
    assert len(class_combos("AA")) == 6
    assert len(class_combos("AKs")) == 4
    assert len(class_combos("AKo")) == 12
    rng = random.Random(1)
    dead = set(parse_cards("As Ah"))
    for _ in range(50):
        combo = sample_from_range(frozenset({"AA", "AKs"}), dead, rng)
        assert combo is not None
        assert not (set(combo) & dead)
    assert sample_from_range(frozenset({"AA"}), set(parse_cards("As Ah Ad")), rng) is None
