"""Forgetting and remembering versions of one game: what scripts/diagnose_blueprint.py relies on.

The cfr:blueprint gets more exploitable as it trains. These tests pin the pieces of the answer
on games small enough to solve in a second: the best response knows nothing beyond its own
memory, it walks the same game as the trainer, and CFR+ goes to zero when the players remember
and stops short of it when they forget.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from poker_table.solvers.cfr import (  # noqa: E402
    CFRPlus,
    Game,
    best_response_value,
    expected_value,
    exploitability,
)
from poker_table.solvers.recall import coarsen, lift, with_recall  # noqa: E402
from poker_table.solvers.toy_games import _betting, leduc  # noqa: E402


def markov(seed: int, states=(3, 2, 2)) -> Game:
    """Three betting rounds on Leduc's betting, with private states that move at random between
    rounds (Markov in the pair, like the blueprint's chance model) and forget the earlier ones."""
    rng = np.random.default_rng(seed)
    p0 = rng.random((states[0], states[0]))
    transitions = [None]
    for before, after in zip(states, states[1:], strict=False):
        move = rng.random((before, before, after, after)) ** 3
        transitions.append(move / move.sum(axis=(2, 3), keepdims=True))
    share = {r: rng.random((n, n)) for r, n in enumerate(states)}
    root = _betting(0, len(states) - 1, (2.0, 4.0, 4.0), max_raises=2)(1.0, 1.0)
    return Game(
        states=states,
        p0=p0 / p0.sum(),
        transitions=tuple(transitions),
        share=share,
        root=root,
    )


def random_strategy(game: Game, seed: int) -> list:
    rng = np.random.default_rng(seed)
    rows = [rng.random(s.shape) + 0.05 for s in game.uniform()]
    return [r / r.sum(axis=1, keepdims=True) for r in rows]


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_the_best_response_knows_nothing_but_its_own_memory(seed: int) -> None:
    game = markov(seed)
    remembering = with_recall(game)
    assert remembering.states == (3, 6, 12)
    strategy = random_strategy(game, seed)
    for seat in (0, 1):
        forgetting = best_response_value(game, strategy, seat)
        lifted = best_response_value(remembering, lift(game, strategy), seat)
        assert forgetting == pytest.approx(lifted, abs=1e-12)
    assert expected_value(game, strategy) == pytest.approx(
        expected_value(remembering, lift(game, strategy)), abs=1e-12
    )


@pytest.mark.parametrize("make", [lambda: markov(4), leduc])
def test_the_best_response_walks_the_trainers_game(make) -> None:
    """Made to play a fixed strategy, the best response's walk values it exactly as
    ``expected_value`` and the trainer's own walk do: same tree, chances and payoffs."""
    game = make()
    strategy = random_strategy(game, 9)
    value = expected_value(game, strategy)
    trainer = CFRPlus(game)
    trainer.regrets = [rows.copy() for rows in strategy]
    walked = float((game.p0 * trainer._walk(game.root, game.p0, game.p0, 0)).sum())
    assert walked == pytest.approx(value, abs=1e-12)
    assert best_response_value(game, strategy, 0, responder=strategy) == pytest.approx(value)
    assert best_response_value(game, strategy, 1, responder=strategy) == pytest.approx(-value)


def test_cfr_plus_goes_to_zero_when_players_remember_and_stalls_when_they_forget() -> None:
    game = markov(1)
    remembering = with_recall(game)
    gaps = {}
    for name, played in (("forgets", game), ("remembers", remembering)):
        solver = CFRPlus(played)
        for checkpoint in (30, 300):
            solver.run(checkpoint - solver.iterations)
            # the same best response both times: the forgetting game's one remembers
            gaps[name, checkpoint] = exploitability(played, solver.average())
    # 0.172 then 0.007 remembering, 0.620 then 0.638 forgetting, when this was written
    assert gaps["remembers", 300] < 0.02
    assert gaps["remembers", 300] < gaps["remembers", 30] / 5
    assert gaps["forgets", 300] > 0.3
    assert gaps["forgets", 300] > gaps["forgets", 30] / 2


def test_lifting_a_game_that_already_remembers_changes_nothing() -> None:
    game = leduc()  # a Leduc state is (private card, public card): it remembers already
    strategy = CFRPlus(game)
    strategy.run(200)
    avg = strategy.average()
    lifted = with_recall(game)
    assert exploitability(game, avg) == pytest.approx(
        exploitability(lifted, lift(game, avg)), abs=1e-12
    )


def test_coarsening_merges_states_and_keeps_the_chances() -> None:
    game = markov(2, states=(4, 3, 3))
    same = coarsen(game, [np.arange(n) for n in game.states])
    strategy = random_strategy(game, 3)
    assert exploitability(same, strategy) == pytest.approx(exploitability(game, strategy))
    small = coarsen(game, [np.array([0, 0, 1, 1]), np.array([0, 1, 1]), np.array([0, 0, 0])])
    assert small.states == (2, 2, 1)
    assert small.p0.sum() == pytest.approx(1.0)
    for move in small.transitions[1:]:
        assert np.allclose(move.sum(axis=(2, 3)), 1.0)
    # one state on the last round: every showdown there is the chance-weighted average share
    pair = np.einsum(
        "ab,abcd->cd", np.einsum("ab,abcd->cd", game.p0, game.transitions[1]), game.transitions[2]
    )
    assert small.share[2][0, 0] == pytest.approx((pair * game.share[2]).sum())


def test_training_one_seat_against_a_fixed_strategy_finds_the_best_response() -> None:
    """With perfect recall, CFR+ for one seat alone, the other seat's strategy fixed, wins
    what the exact best response wins (``diagnose_blueprint.py exploiter`` relies on it)."""
    game = leduc()
    fixed = random_strategy(game, 5)
    for seat in (0, 1):
        solver = CFRPlus(game)
        for node, rows, mine in zip(game.decisions, solver.regrets, fixed, strict=True):
            if node.player != seat:
                rows[...] = mine
        solver.run(300, players=(seat,))
        found = solver.average()
        played = [found[n.index] if n.player == seat else fixed[n.index] for n in game.decisions]
        value = expected_value(game, played) * (1 if seat == 0 else -1)
        best = best_response_value(game, fixed, seat)
        assert value <= best + 1e-9
        assert value == pytest.approx(best, abs=0.01)
