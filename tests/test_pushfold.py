"""nash:pushfold: the game it solves is the table's game, the chart is an equilibrium, and the
seat says when something else played."""

from __future__ import annotations

from fractions import Fraction

import pytest

from poker_table.agents.base import Decision, TableShape
from poker_table.agents.handclass import class_combos, class_name, class_of_name
from poker_table.agents.pushfold import (
    PAPER,
    PushFoldAgent,
    ante_key,
    best_response_gain_mbb,
    call_probability,
    chart,
    charts,
    off_chart,
    push_probability,
    stack_index,
)
from poker_table.agents.registry import check_seats_ready, make_agent, make_agents
from poker_table.agents.scripted import CallingStation, Maniac, TightAggressive
from poker_table.cards import Deck, parse_cards
from poker_table.cli import main
from poker_table.engine import Action, Hand, Player, Street
from poker_table.experiments import run_experiment
from poker_table.league import (
    DEFAULT_SCHEDULE,
    LeagueConfig,
    TournamentConfig,
    parse_levels,
    run_league,
    run_tournament,
    tournament_shape,
)
from poker_table.table import play_hand


def test_the_engine_deals_heads_up_the_way_the_game_was_solved() -> None:
    hand = Hand([Player("a", 20), Player("b", 20)], button=0, small_blind=1, big_blind=2, seed=1)
    # The button posts the small blind and acts first before the flop ...
    assert (hand.sb_index, hand.bb_index) == (0, 1)
    assert [s.street_bet for s in hand.seats] == [1, 2]
    assert hand.actor is not None and hand.actor.index == 0
    hand.apply(Action.call())
    hand.apply(Action.check())
    # ... and the big blind acts first after it.
    assert hand.street is Street.FLOP and hand.actor is not None and hand.actor.index == 1


def test_the_chart_covers_every_hand_at_every_half_big_blind() -> None:
    data = chart()
    assert data["stacks_bb"][0] == 1.0 and data["stacks_bb"][-1] == 20.0
    assert len(data["push_per_mille"]) == len(data["call_per_mille"]) == 169
    assert all(len(row) == len(data["stacks_bb"]) for row in data["push_per_mille"].values())
    assert push_probability("AA", 20) == call_probability("AA", 20) == 1.0
    assert push_probability("72o", 10) == 0.0
    assert stack_index(0.4) == 0 and stack_index(7.3) == stack_index(7.5) == 13
    assert stack_index(40) == len(data["stacks_bb"]) - 1


@pytest.mark.parametrize("index", [0, 9, 18, 28, 38])
def test_an_exact_best_response_gains_almost_nothing(index: int) -> None:
    gain = best_response_gain_mbb(index)
    assert -1e-9 <= gain < 0.05  # thousandths of a big blind per hand
    assert gain == pytest.approx(chart()["best_response_gain_mbb"][index], abs=1e-3)


@pytest.mark.parametrize(
    ("hand", "action", "inside", "outside"),
    [
        # HoldemResources, no-ante tables (fetched 2026-11-26): the largest effective stack at
        # which the hand is pushed or called. Ours is read on the half-big-blind grid.
        ("K2o", "push", 11.0, 12.5),  # published 11.6
        ("Q2o", "push", 6.5, 7.5),  # 7.0
        ("T3s", "push", 7.5, 8.5),  # 7.7
        ("J7o", "push", 8.0, 9.0),  # 8.5
        ("A2o", "call", 15.5, 16.5),  # 15.8
        ("22", "call", 14.5, 15.5),  # 15.0
        ("Q9o", "call", 11.5, 12.5),  # 11.7
        ("K2o", "call", 7.5, 8.5),  # 8.1
    ],
)
def test_cells_agree_with_a_published_chart(hand, action, inside, outside) -> None:
    read = push_probability if action == "push" else call_probability
    assert read(hand, inside) >= 0.5
    assert read(hand, outside) < 0.5


@pytest.mark.parametrize(
    ("hand", "action", "inside", "outside"),
    [
        # HoldemResources, ante 10% tab (fetched 2026-11-26), cells it agrees on to 0.1 bb; the
        # no-ante chart does not play any of them at the inside stack.
        ("K2o", "push", 13.0, 13.5),  # published 13.3; 11.6 with no ante
        ("Q2o", "push", 8.0, 8.5),  # 8.1; 7.0
        ("J7o", "push", 9.5, 10.0),  # 9.7; 8.5
        ("A2o", "call", 18.0, 18.5),  # 18.1; 15.8
        ("22", "call", 16.5, 17.0),  # 16.9; 15.0
        ("K2o", "call", 9.0, 9.5),  # 9.4; 8.1
    ],
)
def test_ante_cells_agree_with_a_published_chart(hand, action, inside, outside) -> None:
    read = push_probability if action == "push" else call_probability
    assert read(hand, inside, "1/10") >= 0.5
    assert read(hand, outside, "1/10") < 0.5
    assert read(hand, inside) < 0.5


def test_it_parses_as_a_kind_and_lists_itself() -> None:
    assert make_agent("nash:pushfold").kind == "nash:pushfold"
    me = make_agent("me:nash:pushfold")
    assert (me.name, me.kind) == ("me", "nash:pushfold")
    agents = make_agents(["nash:pushfold", "tag"])
    assert [a.name for a in agents] == ["pushfold", "tag"]


def test_it_refuses_a_table_of_three_and_a_cash_ante_it_has_no_chart_for() -> None:
    with pytest.raises(ValueError, match="heads-up only"):
        check_seats_ready(make_agents(["nash:pushfold", "tag", "rock"]))
    with pytest.raises(ValueError, match="heads-up only"):
        run_league(make_agents(["nash:pushfold", "tag", "rock"]), LeagueConfig(hands=1))
    with pytest.raises(ValueError, match="ante"):
        run_league(make_agents(["nash:pushfold", "tag"]), LeagueConfig(hands=1, ante=1))
    note = PushFoldAgent().table_note(TableShape(2, 1, 2, 0, 200))
    assert note and "100 big blinds is deeper" in note


def play(hole_sb: str, hole_bb: str, sb, bb, stack: int = 20, blinds=(1, 2), ante: int = 0):
    cards = parse_cards(hole_bb)[:1] + parse_cards(hole_sb)[:1]
    cards += parse_cards(hole_bb)[1:] + parse_cards(hole_sb)[1:]
    deck = Deck.stacked(cards)  # dealt one at a time from the big blind (left of the button)
    hand = Hand(
        [Player(sb.name, stack), Player(bb.name, stack)],
        button=0,
        small_blind=blinds[0],
        big_blind=blinds[1],
        ante=ante,
        seed=1,
        deck=deck,
    )
    return play_hand(hand, {0: sb, 1: bb})


def test_the_small_blind_shoves_or_folds_from_its_chart() -> None:
    seat = PushFoldAgent("pf")
    played = play("As Ah", "7c 2d", seat, TightAggressive("tag"))
    assert played.decisions[0].applied == Action.raise_to(20)
    assert played.decisions[0].meta == {"paper": PAPER, "effective_bb": 10.0, "method": "own"}
    played = play("7c 2d", "As Ah", PushFoldAgent("pf"), TightAggressive("tag"))
    assert played.decisions[0].applied == Action.fold()


def test_a_big_blind_facing_any_raise_shoves_its_calling_range_or_folds() -> None:
    class MinRaiser:
        name = "minraiser"

        def act(self, view):
            return Decision(Action.raise_to(view.legal.min_raise_to))

    seat = PushFoldAgent("pf")
    strong = play("7c 2d", "Ks Kh", MinRaiser(), seat)
    mine = [d for d in strong.decisions if d.seat == 1]
    assert mine[0].applied == Action.raise_to(20) and mine[0].meta["method"] == "translated"
    seat = PushFoldAgent("pf")
    weak = play("Ks Kh", "7c 2d", MinRaiser(), seat)
    assert [d.applied for d in weak.decisions if d.seat == 1] == [Action.fold()]
    assert seat.usage.translated == 1


def test_a_big_blind_facing_a_limp_shoves_its_calling_range_or_checks() -> None:
    seat = PushFoldAgent("pf")
    played = play("7c 2d", "Ks Kh", CallingStation("station"), seat)
    assert [d for d in played.decisions if d.seat == 1][0].applied == Action.raise_to(20)
    seat = PushFoldAgent("pf")
    played = play("Ks Kh", "7c 3d", CallingStation("station"), seat)
    mine = [d for d in played.decisions if d.seat == 1]
    assert mine[0].applied == Action.check()
    # after the flop a limped pot is past the chart: the fallback plays, and is counted
    assert all(d.meta["method"] == "fallback" for d in mine[1:])
    assert seat.usage.fallback == len(mine) - 1 > 0


def test_deep_stacks_go_to_the_fallback_and_are_counted() -> None:
    seat = PushFoldAgent("pf")
    played = play("As Ah", "7c 2d", seat, TightAggressive("tag"), stack=200)
    assert played.decisions[0].meta["method"] == "fallback"
    assert seat.usage.own == 0 and seat.usage.fallback >= 1


def test_a_short_session_against_every_bot_is_all_its_own_and_legal() -> None:
    for opponent in (TightAggressive("tag"), Maniac("maniac"), CallingStation("station")):
        seat = PushFoldAgent("pf")
        result = run_league([seat, opponent], LeagueConfig(hands=60, buy_in=20, seed=3))
        decisions = [
            d for h in result.histories for d in h.decisions if h.players[d.seat].name == "pf"
        ]
        assert not any(d.illegal for d in decisions)
        assert not any(d.reasoning.startswith("agent error") for d in decisions)
        assert seat.usage.decisions == len(decisions)
        assert seat.usage.own > 0
    assert result.histories[0].players[0].kind == "nash:pushfold"
    assert class_of_name("AA") == 0


# ----- antes ----------------------------------------------------------------------------------


def test_the_data_file_keeps_the_no_ante_chart_where_it_was_and_adds_the_antes() -> None:
    data = chart()
    # What a reader of the first file read is still there, at the top level, unchanged in shape.
    for key in ("stacks_bb", "push_per_mille", "call_per_mille", "best_response_gain_mbb"):
        assert key in data
    assert data["valid_up_to_bb"] == 15.0 and data["paper"] == PAPER
    assert sorted(data["antes"]) == ["1/10", "1/8"]
    assert sorted(charts()) == ["0", "1/10", "1/8"] and charts()["0"] is data
    for key, table in data["antes"].items():
        assert table["ante_bb"] == float(Fraction(key))
        assert len(table["push_per_mille"]) == len(table["call_per_mille"]) == 169
        assert all(len(row) == len(data["stacks_bb"]) for row in table["push_per_mille"].values())
        assert len(table["best_response_gain_mbb"]) == len(data["stacks_bb"])


@pytest.mark.parametrize("ante", ["1/10", "1/8"])
@pytest.mark.parametrize("index", [0, 9, 18, 28, 38])
def test_an_exact_best_response_gains_almost_nothing_with_an_ante(ante: str, index: int) -> None:
    gain = best_response_gain_mbb(index, ante)
    assert -1e-9 <= gain < 0.05  # thousandths of a big blind per hand
    assert gain == pytest.approx(chart()["antes"][ante]["best_response_gain_mbb"][index], abs=1e-3)


@pytest.mark.parametrize(("ante", "gain"), [("1/10", 7.4), ("1/8", 11.3)])
def test_the_no_ante_chart_is_beatable_at_a_table_with_an_ante(ante: str, gain: float) -> None:
    # The check has teeth: with the antes' payoffs, the no-ante chart gives a best response this
    # much at 10 bb, where the right chart gives it under 0.001 (docs/solver-seats.md quotes it).
    data = chart()
    index = data["stacks_bb"].index(10.0)
    wrong = dict(data["antes"][ante])
    wrong.update(push_per_mille=data["push_per_mille"], call_per_mille=data["call_per_mille"])
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(data["antes"], ante, wrong)
        assert best_response_gain_mbb(index, ante) == pytest.approx(gain, abs=0.05)


def combo_share(table: dict, key: str, index: int) -> float:
    rows = table[key]
    return sum(len(class_combos()[i]) * rows[class_name(i)][index] for i in range(169)) / 1326e3


def test_more_dead_money_widens_the_ranges() -> None:
    # The big blind calls wider with each step up in the ante, at every stack. The small blind
    # shoves wider with either ante than with none; from 1/10 to 1/8 its range can narrow a
    # little, because it now meets that wider calling range (it narrows by 0.4 points at most,
    # at 5.5, 9.5, 11 and 16 bb). Its no-ante range wobbles as much from one stack to the next.
    no_ante, tenth, eighth = (charts()[k] for k in ("0", "1/10", "1/8"))
    for index in range(len(chart()["stacks_bb"])):
        calls = [combo_share(t, "call_per_mille", index) for t in (no_ante, tenth, eighth)]
        assert calls[0] <= calls[1] <= calls[2], index
        pushes = [combo_share(t, "push_per_mille", index) for t in (no_ante, tenth, eighth)]
        assert pushes[0] <= min(pushes[1:]) and pushes[2] > pushes[1] - 0.005, index


def test_an_ante_is_matched_exactly_by_its_share_of_the_big_blind() -> None:
    assert ante_key(2, 20) == ante_key(5, 50) == ante_key(10, 100) == "1/10"
    assert ante_key(25, 200) == ante_key(200, 1600) == "1/8"
    assert ante_key(0, 2) == "0" and ante_key(3, 20) == "3/20"
    assert off_chart(10, 20, 2) is None and off_chart(100, 200, 25) is None
    assert off_chart(1, 2, 0) is None
    assert "ante of 3" in (off_chart(10, 20, 3) or "")  # 15%: near 1/8, never rounded to it
    assert "blinds of 1/3" in (off_chart(1, 3, 0) or "")


def test_the_default_schedule_has_a_chart_at_every_level() -> None:
    shape = tournament_shape(2, DEFAULT_SCHEDULE, 200)
    assert shape.levels[3] == (5, 10, 1) and shape.levels[-1] == (800, 1600, 200)
    seat = PushFoldAgent()
    assert seat.check_table(shape) is None
    note = seat.table_note(shape)
    assert note is not None and "no chart" not in note  # only the 100 bb depth note


def test_seating_refuses_only_a_table_that_can_never_use_a_chart() -> None:
    seat = PushFoldAgent()
    # A cash table's blinds never change, so one with no chart is refused ...
    assert "no chart for this table" in (seat.check_table(TableShape(2, 1, 2, 1, 40)) or "")
    assert "blinds of 1/3" in (seat.check_table(TableShape(2, 1, 3, 0, 40)) or "")
    assert seat.check_table(TableShape(2, 10, 20, 2, 200)) is None  # a tenth: it has that one
    # ... and a freezeout only when no level of it has one.
    never = tournament_shape(2, parse_levels("2/4+1,4/8+3,10/20+3"), 200)
    assert "any level of this schedule" in (seat.check_table(never) or "")
    later = tournament_shape(2, parse_levels("2/4+1,10/20+2,20/40+3"), 200)
    assert seat.check_table(later) is None
    assert "levels 1, 3, so tag plays those" in (seat.table_note(later) or "")


def test_every_way_to_start_a_freezeout_refuses_a_schedule_with_no_chart(tmp_path) -> None:
    levels = "2/4+1,4/8+3"
    with pytest.raises(ValueError, match="no chart for any level"):
        run_tournament(
            make_agents(["nash:pushfold", "tag"]), TournamentConfig(schedule=parse_levels(levels))
        )
    seats = "nash:pushfold,tag"
    assert main(["play", "--tournament", "--seats", seats, "--levels", levels, "-q"]) != 0
    path = tmp_path / "fz.toml"
    path.write_text(
        f'name = "fz"\ncoach = false\nseats = ["nash:pushfold", "tag"]\ntournaments = 1\n'
        f'levels = "{levels}"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no chart for any level"):
        run_experiment(path, tmp_path / "out", log=lambda _: None)
    # The default schedule plays from all three.
    assert main(["play", "--tournament", "--seats", seats, "-n", "5", "-q"]) == 0


def test_a_hand_with_an_ante_reads_that_antes_chart_and_one_with_no_chart_falls_back() -> None:
    seat = PushFoldAgent("pf")
    played = play("As Ah", "7c 2d", seat, TightAggressive("tag"), 200, blinds=(10, 20), ante=2)
    first = played.decisions[0]
    assert first.applied == Action.raise_to(198) and first.meta["method"] == "own"
    assert first.meta["ante"] == "1/10" and first.meta["effective_bb"] == 10.0
    seat = PushFoldAgent("pf")
    played = play("As Ah", "7c 2d", seat, TightAggressive("tag"), 200, blinds=(10, 20), ante=3)
    first = played.decisions[0]
    assert first.meta["method"] == "fallback" and first.meta["off_chart"] == "ante"
    assert first.reasoning.startswith("an ante of 3 on a big blind of 20")
    assert seat.usage.fallback == seat.usage.decisions == 1
