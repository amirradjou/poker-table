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


# ----- the push/fold game with an ante ------------------------------------------------------

# Two kinds of hand, dealt independently: a strong one beats a weak one every time, and two of
# a kind split. Strong hands shove and call; the weak ones mix, and the mix has a closed form.
# The big blind's weak hand is indifferent when S / (1 + p) = 1 + a (p: how often a weak small
# blind shoves); the small blind's weak hand when S / 2 = (1 - q)(1 + a) / 2 + 1/2 + a (q: how
# often a weak big blind calls). So p = S / (1 + a) - 1 and q = (2 + 3a - S) / (1 + a).
TOY_EQUITY = [[0.5, 1.0], [0.0, 0.5]]


@pytest.mark.parametrize(("stack", "ante"), [(1.75, 0.0), (1.75, 0.125), (2.0, 0.1)])
def test_push_fold_with_an_ante_has_the_toy_games_closed_form(stack: float, ante: float) -> None:
    from poker_table.solvers.pushfold import game

    toy = game(stack, np.array(TOY_EQUITY), np.full((2, 2), 0.25), ante=ante)
    _, avg = solved(toy, 20000)
    weak = 1
    assert avg[0][0, 1] == pytest.approx(1.0, abs=1e-3)  # a strong small blind shoves
    assert avg[1][0, 1] == pytest.approx(1.0, abs=1e-3)  # a strong big blind calls
    assert avg[0][weak, 1] == pytest.approx(stack / (1 + ante) - 1, abs=0.01)
    assert avg[1][weak, 1] == pytest.approx((2 + 3 * ante - stack) / (1 + ante), abs=0.01)


@pytest.mark.parametrize("ante", [0.0, 0.1, 0.125])
def test_the_ante_is_dead_money_in_the_push_fold_game(ante: float) -> None:
    from poker_table.solvers.pushfold import game

    toy = game(10.0, np.array(TOY_EQUITY), np.full((2, 2), 0.25), ante=ante)
    folds, shoves = toy.uniform(), toy.uniform()
    folds[0][:] = [1.0, 0.0]  # the small blind always folds: it loses its ante and blind
    assert expected_value(toy, folds) == pytest.approx(-(0.5 + ante))
    shoves[0][:] = [0.0, 1.0]  # it always shoves and the big blind always folds
    shoves[1][:] = [1.0, 0.0]
    assert expected_value(toy, shoves) == pytest.approx(1.0 + ante)
    shoves[1][:] = [0.0, 1.0]  # called every time: the whole stacks, antes in them, at showdown
    assert expected_value(toy, shoves) == pytest.approx(0.0)


def test_a_big_blind_all_in_from_its_post_calls_everything_and_the_game_is_even() -> None:
    from poker_table.agents.pushfold import equities
    from poker_table.solvers.pushfold import game, posted

    # At 1 bb with an ante of 1/8 the big blind has nothing behind: folding loses all of it,
    # so it calls with every hand, and against that the small blind shoves every hand. Both
    # seats then go to showdown every deal, which is worth nothing by symmetry: the deals are
    # as likely either way round and the equities add to one.
    assert posted(1.0, 0.125) == (0.625, 1.0)
    full = game(1.0, np.array(equities()), ante=0.125)
    _, avg = solved(full, 300)
    assert avg[1][:, 1].min() > 0.99 and avg[0][:, 1].min() > 0.99
    assert expected_value(full, avg) == pytest.approx(0.0, abs=1e-3)
    everything = [np.tile([0.0, 1.0], (169, 1)), np.tile([0.0, 1.0], (169, 1))]
    assert expected_value(full, everything) == pytest.approx(0.0, abs=1e-12)
