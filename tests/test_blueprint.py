"""cfr:blueprint: the seat plays the tree it was trained on, keeps it in step with the real hand,
and says when something else played."""

from __future__ import annotations

import hashlib
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from poker_table.agents.abstraction import (
    CHANCE,
    DECISION,
    FLOP,
    FOLD,
    PREFLOP,
    RIVER,
    Betting,
    Cards,
    bucket,
    build_tree,
    grouped_state,
    number,
    pseudo_harmonic,
    quantize,
    read_strategy,
    write_strategy,
)
from poker_table.agents.base import Decision, TableShape
from poker_table.agents.blueprint import DATA, BlueprintAgent, shipped
from poker_table.agents.handclass import card_of, class_index
from poker_table.agents.registry import check_seats_ready, make_agent, make_agents
from poker_table.agents.scripted import CallingStation, Maniac, RandomAgent, TightAggressive
from poker_table.cli import main
from poker_table.engine import Action, Hand, Player
from poker_table.league import LeagueConfig, run_league

ROOT = Path(__file__).resolve().parent.parent
CUTS = Cards(
    flop=(0.3, 0.55, 0.75, 0.9),
    turn=(0.3, 0.55, 0.75, 0.9),
    river=(0.15, 0.3, 0.45, 0.58, 0.7, 0.8, 0.87, 0.93, 0.97),
)


@pytest.fixture(scope="module")
def uniform(tmp_path_factory):
    """A blueprint that plays every action of the tree equally often: the widest test of the
    seat's bookkeeping, since it goes everywhere in the tree."""
    betting = Betting()
    nodes = number(build_tree(betting))
    rows = [
        quantize([1 / len(n.labels)] * len(n.labels))
        for n in nodes
        for _ in range(CUTS.buckets(n.street))
    ]
    path = tmp_path_factory.mktemp("blueprint") / "uniform.bin"
    write_strategy(path, betting, CUTS, rows, {"about": "uniform, for tests"})
    return read_strategy(path.read_bytes())


GROUPS = [i % 3 for i in range(169)]


@pytest.fixture(scope="module")
def grouped_file(tmp_path_factory) -> Path:
    """A format-2 blueprint, whose states after the flop keep one of three preflop groups. Each
    row is a different random mix, so a wrong row would show."""
    betting = Betting()
    rng = random.Random(11)
    rows = []
    for n in number(build_tree(betting)):
        states = CUTS.buckets(n.street) * (3 if n.street else 1)
        for _ in range(states):
            weights = [rng.random() + 0.01 for _ in n.labels]
            rows.append(quantize([w / sum(weights) for w in weights]))
    path = tmp_path_factory.mktemp("blueprint") / "grouped.bin"
    write_strategy(path, betting, CUTS, rows, {"about": "format 2, for tests"}, groups=GROUPS)
    return path


def decisions_of(result, name: str):
    return [d for h in result.histories for d in h.decisions if h.players[d.seat].name == name]


def test_pseudo_harmonic_mapping() -> None:
    # Ganzfried & Sandholm (IJCAI 2013): f(x) = (B - x)(1 + A) / ((B - A)(1 + x))
    assert pseudo_harmonic(0.5, 1.0, 0.5) == 1.0
    assert pseudo_harmonic(0.5, 1.0, 1.0) == 0.0
    assert pseudo_harmonic(0.0, 1.0, 1 / 3) == pytest.approx(0.5)  # its median for 0 and pot
    assert pseudo_harmonic(0.0, 0.25, 0.2) == pytest.approx(1 / 6)  # 100 into 500, sizes 0, 1/4
    sizes = [0.5 + 0.05 * i for i in range(11)]
    chances = [pseudo_harmonic(0.5, 1.0, x) for x in sizes]
    assert chances == sorted(chances, reverse=True)
    with pytest.raises(ValueError):
        pseudo_harmonic(0.5, 1.0, 1.5)


def test_the_tree_is_the_engine_game(uniform) -> None:
    """Walk random lines through the tree and play the same moves on the engine: every move is
    legal there and the chips in the pot agree at every node."""
    rng = random.Random(7)
    for line in range(300):
        node = uniform.root
        hand = Hand([Player("sb", 200), Player("bb", 200)], button=0, small_blind=1,
                    big_blind=2, seed=line)  # fmt: skip
        while True:
            assert tuple(s.total_bet for s in hand.seats) == node.put_in
            if node.kind == CHANCE:
                node = node.children[0]
                continue
            if node.kind != DECISION:
                assert hand.finished or node.kind != FOLD
                break
            assert hand.actor is not None and hand.actor.index == node.actor
            label = rng.choice(node.labels)
            legal = hand.legal_actions()
            amount = node.amounts[node.labels.index(label)]
            if label == "f":
                action = Action.fold()
            elif label == "c":
                action = Action.check() if legal.can_check else Action.call()
            else:
                assert legal.min_raise_to <= amount <= legal.max_raise_to
                action = Action.bet(amount) if legal.is_bet else Action.raise_to(amount)
            hand.apply(action)
            node = node.child(label)
            if node.kind == FOLD:
                assert hand.finished
                break


def test_against_itself_every_decision_is_its_own(uniform) -> None:
    a = BlueprintAgent("a", strategy=uniform, seed=1)
    b = BlueprintAgent("b", strategy=uniform, seed=2)
    result = run_league([a, b], LeagueConfig(hands=40, seed=4))
    for seat in (a, b):
        assert seat.usage.translated == seat.usage.fallback == 0
        made = decisions_of(result, seat.name)
        assert seat.usage.own == len(made) > 0
        assert all(d.requested == d.applied and not d.illegal for d in made)


@pytest.mark.parametrize("opponent", [Maniac("maniac", seed=3), RandomAgent("random", seed=3)])
def test_wild_opponents_never_break_it_and_every_desync_is_counted(uniform, opponent) -> None:
    seat = BlueprintAgent("bp", strategy=uniform)
    result = run_league([seat, opponent], LeagueConfig(hands=60, seed=9))
    made = decisions_of(result, "bp")
    assert not any(d.illegal or d.reasoning.startswith("agent error") for d in made)
    assert all(d.requested == d.applied for d in made)
    assert seat.usage.decisions == len(made)
    assert [d.meta["method"] for d in made].count("fallback") == seat.usage.fallback
    assert seat.usage.translated > 0  # wild bet sizes are read as the tree's sizes


def test_an_off_tree_bet_is_translated(uniform) -> None:
    class Overbettor:
        name = "over"

        def act(self, view):
            legal = view.legal
            if legal.can_raise:
                target = view.current_bet + int(0.7 * (view.pot + view.to_call))
                amount = min(max(target, legal.min_raise_to), legal.max_raise_to)
                return Decision(Action.bet(amount) if legal.is_bet else Action.raise_to(amount))
            return Decision(Action.check() if legal.can_check else Action.call())

    seat = BlueprintAgent("bp", strategy=uniform)
    result = run_league([seat, Overbettor()], LeagueConfig(hands=30, seed=2))
    methods = [d.meta["method"] for d in decisions_of(result, "bp")]
    assert "translated" in methods


def test_stacks_out_of_its_range_are_refused_at_seating_and_counted_when_they_drift(
    uniform,
) -> None:
    agents = [BlueprintAgent("bp", strategy=uniform), TightAggressive("tag")]
    with pytest.raises(ValueError, match="trained for 100 big blinds"):
        run_league(agents, LeagueConfig(hands=1, buy_in=100))
    with pytest.raises(ValueError, match="ante"):
        run_league(agents, LeagueConfig(hands=1, ante=1))
    with pytest.raises(ValueError, match="half the big blind"):
        run_league(agents, LeagueConfig(hands=1, small_blind=2, big_blind=2, buy_in=200))
    with pytest.raises(ValueError, match="heads-up only"):
        check_seats_ready([*agents, CallingStation("station")])
    assert agents[0].check_table(TableShape(2, 5, 10, 0, 1000)) is None  # 100 bb at 5/10 too
    # a hand that starts out of range (as a carried stack would) goes to the fallback
    seat = BlueprintAgent("bp", strategy=uniform)
    hand = Hand([Player("bp", 300), Player("x", 140)], button=0, small_blind=1, big_blind=2, seed=1)
    from poker_table.agents.base import make_view

    decision = seat.act(make_view(hand, 0))
    assert decision.meta["method"] == "fallback" and seat.usage.fallback == 1


def test_it_parses_as_a_kind() -> None:
    seat = make_agent("cfr:blueprint")
    assert (seat.name, seat.kind) == ("blueprint", "cfr:blueprint")
    me = make_agent("me:cfr:blueprint")
    assert (me.name, me.kind) == ("me", "cfr:blueprint")
    assert [a.kind for a in make_agents("cfr:blueprint,tag".split(","))] == ["cfr:blueprint", "tag"]


def test_play_seats_it_from_the_command_line(capsys) -> None:
    assert main(["play", "-n", "3", "--seats", "cfr:blueprint,tag"]) == 0
    out = capsys.readouterr().out
    assert "blueprint" in out and "decisions were the blueprint's" in out
    assert main(["play", "-n", "3", "--seats", "cfr:blueprint,tag,rock"]) == 2
    assert "plays heads-up only, and this table has 3 seats" in capsys.readouterr().err


@pytest.mark.parametrize("seat", ["cfr:blueprint", "nash:pushfold"])
def test_the_coach_quiz_refuses_a_heads_up_seat_before_building_questions(seat, capsys) -> None:
    assert main(["eval", "--seats", seat]) == 2
    assert "plays heads-up only" in capsys.readouterr().err


def test_agents_lists_the_research_seats(capsys) -> None:
    assert main(["agents"]) == 0
    out = capsys.readouterr().out
    assert "cfr:blueprint" in out and "Libratus and Pluribus" in out and "heads-up only" in out
    assert "nash:pushfold" in out and "push/fold Nash equilibrium" in out


def test_the_shipped_strategy_fits_and_matches_its_tree() -> None:
    assert DATA.stat().st_size < 500_000  # the repository's pre-commit limit
    strategy = shipped()
    assert len(strategy.nodes) == 7744
    assert strategy.betting == Betting()  # 200 chips at 1/2, no ante: the table's defaults
    assert strategy.header["method"].startswith("CFR+")
    root = strategy.probabilities(strategy.root, 0)  # aces, first to act
    assert sum(root) == pytest.approx(1.0, abs=0.01) and root[0] == 0.0  # it never folds aces


def test_buckets_match_the_training_code() -> None:
    np = pytest.importorskip("numpy")
    from poker_table.solvers.blueprint import street_buckets
    from poker_table.solvers.fasteval import combo_cards

    rng = np.random.default_rng(5)
    picks = random.Random(5)
    for _ in range(4):
        board = rng.choice(52, 5, replace=False)
        for size in (3, 4, 5):
            combos, levels, _ = street_buckets(CUTS, board[:size])
            for k in picks.sample(range(len(combos)), 3):
                hole = [card_of(int(c)) for c in combo_cards()[combos[k]]]
                cards = [card_of(int(c)) for c in board[:size]]
                assert bucket(CUTS, hole, cards) == int(levels[k])


@pytest.mark.parametrize("groups", ["0", "3"])
def test_training_gives_the_same_file_from_any_process(tmp_path, groups) -> None:
    pytest.importorskip("numpy")
    digests = []
    for n, hash_seed in enumerate(("1", "987")):
        out = tmp_path / str(n)
        env = dict(os.environ, PYTHONHASHSEED=hash_seed)
        script = ROOT / "scripts" / "train_blueprint.py"
        run = subprocess.run(
            [sys.executable, str(script), "--check", str(out), "--groups", groups],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        digests.append(run.stdout.strip())
        assert digests[-1] == hashlib.sha256((out / "blueprint.bin").read_bytes()).hexdigest()
    assert digests[0] == digests[1]
    assert read_strategy((tmp_path / "0" / "blueprint.bin").read_bytes()).version == (
        2 if groups == "3" else 1
    )


def test_format_two_keeps_a_preflop_group_after_the_flop(grouped_file) -> None:
    data = grouped_file.read_bytes()
    assert data.startswith(b"PTBLUEPRINT2\n")
    strategy = read_strategy(data)
    assert (strategy.version, strategy.groups) == (2, tuple(GROUPS))
    assert (strategy.rows(PREFLOP), strategy.rows(FLOP), strategy.rows(RIVER)) == (169, 30, 30)
    rng = random.Random(3)
    deck = [card_of(c) for c in range(52)]
    for _ in range(20):
        cards = rng.sample(deck, 7)
        hole = cards[:2]
        assert strategy.state(hole, []) == class_index(*hole)
        for board in (cards[2:5], cards[2:6], cards[2:7]):
            held = bucket(CUTS, hole, board)
            group = GROUPS[class_index(*hole)]
            assert (
                strategy.state(hole, board) == grouped_state(group, held, 10) == group * 10 + held
            )


def test_a_format_two_seat_plays_its_own_tree(grouped_file) -> None:
    strategy = read_strategy(grouped_file.read_bytes())
    a = BlueprintAgent("a", strategy=strategy, seed=1)
    b = BlueprintAgent("b", strategy=strategy, seed=2)
    result = run_league([a, b], LeagueConfig(hands=40, seed=4))
    for seat in (a, b):
        assert seat.usage.translated == seat.usage.fallback == 0
        made = decisions_of(result, seat.name)
        assert seat.usage.own == len(made) > 0
        assert all(d.requested == d.applied and not d.illegal for d in made)
    later = [d for d in decisions_of(result, "a") if d.street != "preflop"]
    assert later and all(" group " in d.reasoning for d in later)


def test_a_file_in_a_format_it_cannot_read_is_refused() -> None:
    data = DATA.read_bytes()
    assert data.startswith(b"PTBLUEPRINT1\n")
    unknown = b"PTBLUEPRINT9\n" + data[len(b"PTBLUEPRINT1\n") :]
    with pytest.raises(ValueError, match="format 9; this version of poker-table reads formats 1"):
        read_strategy(unknown)
    with pytest.raises(ValueError, match="not a blueprint strategy file"):
        read_strategy(b"PK\x03\x04 a zip file")


NO_NUMPY = """
import sys
from pathlib import Path


class NoNumpy:
    def find_spec(self, name, path=None, target=None):
        if name == "numpy" or name.startswith("numpy."):
            raise ImportError("numpy is not installed here")


sys.meta_path.insert(0, NoNumpy())
from poker_table.agents.abstraction import read_strategy
from poker_table.agents.blueprint import BlueprintAgent
from poker_table.agents.scripted import TightAggressive
from poker_table.league import LeagueConfig, run_league

for path in sys.argv[1:]:
    strategy = read_strategy(Path(path).read_bytes())
    seat = BlueprintAgent("bp", strategy=strategy)
    run_league([seat, TightAggressive("tag")], LeagueConfig(hands=20, seed=3))
    assert seat.usage.own > 0, seat.usage
assert not any(name.split(".")[0] == "numpy" for name in sys.modules)
print("played without numpy")
"""


def test_the_seat_plays_without_numpy(grouped_file) -> None:
    run = subprocess.run(
        [sys.executable, "-c", NO_NUMPY, str(DATA), str(grouped_file)],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    assert "played without numpy" in run.stdout
