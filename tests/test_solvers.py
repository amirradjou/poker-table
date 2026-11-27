"""The CFR+ code is checked on games whose answers are known before any seat trusts it."""

from __future__ import annotations

import random

import pytest

np = pytest.importorskip("numpy")

from poker_table.agents.handclass import card_of  # noqa: E402
from poker_table.evaluator import evaluate  # noqa: E402
from poker_table.solvers.cfr import (  # noqa: E402
    CFRPlus,
    best_response_value,
    expected_value,
    exploitability,
)
from poker_table.solvers.fasteval import encode, evaluate_many, strengths  # noqa: E402
from poker_table.solvers.toy_games import KUHN_VALUE, kuhn, leduc  # noqa: E402


def solved(game, iterations: int) -> tuple[CFRPlus, list]:
    solver = CFRPlus(game)
    solver.run(iterations)
    return solver, solver.average()


def test_cfr_plus_reaches_the_value_of_kuhn_poker() -> None:
    game = kuhn()
    solver = CFRPlus(game)
    gaps = []
    for checkpoint in (10, 100, 1000):
        solver.run(checkpoint - solver.iterations)
        gaps.append(exploitability(game, solver.average()))
    value = expected_value(game, solver.average())
    assert value == pytest.approx(KUHN_VALUE, abs=1e-3)  # -1/18 for the first player
    assert gaps[0] > gaps[1] > gaps[2]
    assert gaps[2] < 5e-4


def test_kuhn_strategy_is_one_of_the_known_equilibria() -> None:
    game = kuhn()
    _, avg = solved(game, 2000)
    jack, queen, king = 0, 1, 2
    root = game.root  # player 0: check or bet
    bet = {card: avg[root.index][card, 1] for card in (jack, queen, king)}
    assert bet[queen] < 0.01  # the queen always checks
    assert bet[king] == pytest.approx(3 * bet[jack], abs=0.02)  # a king bets three times as often
    assert 0 <= bet[jack] <= 1 / 3 + 0.01
    check_bet = root.children[0].children[1]  # player 0 after check, bet
    assert avg[check_bet.index][queen, 1] == pytest.approx(bet[jack] + 1 / 3, abs=0.02)
    assert avg[check_bet.index][king, 1] > 0.99 and avg[check_bet.index][jack, 1] < 0.01


def test_cfr_plus_drives_leduc_exploitability_towards_zero() -> None:
    game = leduc()
    early = exploitability(game, solved(game, 30)[1])
    late = exploitability(game, solved(game, 300)[1])
    assert late < early / 5
    assert late < 0.005


def test_a_best_response_wins_against_a_uniform_player_and_not_against_equilibrium() -> None:
    game = kuhn()
    uniform = game.uniform()
    assert best_response_value(game, uniform, 0) > 0.3
    assert best_response_value(game, uniform, 1) > 0.3
    _, avg = solved(game, 2000)
    # Against equilibrium a best response gets the game's value from either seat, no more.
    assert best_response_value(game, avg, 0) == pytest.approx(KUHN_VALUE, abs=1e-3)
    assert best_response_value(game, avg, 1) == pytest.approx(-KUHN_VALUE, abs=1e-3)


@pytest.mark.parametrize("size", [5, 6, 7])
def test_the_fast_evaluator_agrees_with_evaluate(size: int) -> None:
    rng = random.Random(size)
    hands = [rng.sample(range(52), size) for _ in range(3000)]
    fast = evaluate_many(np.array(hands))
    slow = [encode(evaluate([card_of(c) for c in hand])) for hand in hands]
    assert fast.tolist() == slow


def test_hand_strength_counts_every_other_hand_once() -> None:
    board = [0, 17, 30, 44, 51]
    combos, _, wins2, totals = strengths(board)
    assert len(combos) == 1081  # 47 cards left, two each
    assert set(totals.tolist()) == {990}  # 45 cards left for the other hand
    assert wins2.sum() == 1081 * 990  # every pair counted from both sides adds to two
