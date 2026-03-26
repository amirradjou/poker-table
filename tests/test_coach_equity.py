import pytest

from poker_table.cards import parse_cards
from poker_table.coach.equity import board_texture, equity, pot_odds
from poker_table.coach.ranges import expand


def c(text: str):
    return parse_cards(text)


def test_preflop_equities_match_the_well_known_numbers() -> None:
    assert equity(c("As Ah"), [], 1, samples=3000) == pytest.approx(0.85, abs=0.03)
    assert equity(c("As Ks"), [], 1, samples=3000) == pytest.approx(0.67, abs=0.03)
    assert equity(c("7s 2h"), [], 1, samples=3000) == pytest.approx(0.35, abs=0.04)
    assert equity(c("As Ah"), [], 4, samples=3000) == pytest.approx(0.56, abs=0.04)


def test_river_heads_up_is_exact_and_cached() -> None:
    nuts = equity(c("As Ks"), c("Qs Js Ts 2d 3c"), 1)
    assert nuts == 1.0
    # Overpair of kings on 2-5-8-T-4: loses to AA, sets, two pairs and three straights.
    kings = equity(c("Kc Kd"), c("2h 5d 8c Ts 4s"), 1)
    assert 0.80 < kings < 0.88
    assert equity(c("Kc Kd"), c("2h 5d 8c Ts 4s"), 1) == kings


def test_equity_against_a_range() -> None:
    tight = expand("QQ+, AKs, AKo")
    vs_range = equity(c("Jh Jd"), [], [tight], samples=2000)
    vs_random = equity(c("Jh Jd"), [], 1, samples=2000)
    assert vs_range < 0.45 < vs_random  # jacks are a favourite against anyone, an underdog vs QQ+
    assert equity(c("Ac Ad"), c("2h 7d 9s"), [expand("*")], samples=1000) > 0.8


def test_equity_edge_cases() -> None:
    assert equity(c("As Ah"), [], 0) == 1.0
    # A range that cannot exist given the dead cards yields no samples
    assert equity(c("As Ah"), c("Ad Ac 2c"), [expand("AA")], samples=100) == 0.0


def test_pot_odds() -> None:
    assert pot_odds(0, 100) == 0.0
    assert pot_odds(50, 100) == pytest.approx(1 / 3)
    assert pot_odds(100, 100) == 0.5


@pytest.mark.parametrize(
    ("board", "texture"),
    [
        ("", "preflop"),
        ("Ah 7d 2c", "dry"),
        ("Kh 8d 3s", "dry"),
        ("Ah 7h 2c", "wet"),  # two hearts
        ("9c 8d 5s", "wet"),  # connected
        ("Ah 2d 4c", "wet"),  # wheel draws
        ("7c 7d Kh", "paired"),
        ("Ah 7d 2c 9h", "dry"),  # two hearts on the turn is not a big draw board
        ("Ah 7h 2c 9h", "wet"),
        ("Ah 7d 2c 9s Ks", "dry"),
        ("Ah 7d 2c 9s 5c", "wet"),  # 5-7-9 leaves double-gutter straights
    ],
)
def test_board_texture(board: str, texture: str) -> None:
    assert board_texture(c(board)) == texture
