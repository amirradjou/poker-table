"""nash:pushfold: the game it solves is the table's game, the chart is an equilibrium, and the
seat says when something else played."""

from __future__ import annotations

import pytest

from poker_table.agents.base import Decision, TableShape
from poker_table.agents.handclass import class_of_name
from poker_table.agents.pushfold import (
    PAPER,
    PushFoldAgent,
    best_response_gain_mbb,
    call_probability,
    chart,
    push_probability,
    stack_index,
)
from poker_table.agents.registry import check_seats_ready, make_agent, make_agents
from poker_table.agents.scripted import CallingStation, Maniac, TightAggressive
from poker_table.cards import Deck, parse_cards
from poker_table.engine import Action, Hand, Player, Street
from poker_table.league import LeagueConfig, run_league
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


def test_it_parses_as_a_kind_and_lists_itself() -> None:
    assert make_agent("nash:pushfold").kind == "nash:pushfold"
    me = make_agent("me:nash:pushfold")
    assert (me.name, me.kind) == ("me", "nash:pushfold")
    agents = make_agents(["nash:pushfold", "tag"])
    assert [a.name for a in agents] == ["pushfold", "tag"]


def test_it_refuses_a_table_of_three_and_a_table_with_antes() -> None:
    with pytest.raises(ValueError, match="heads-up only"):
        check_seats_ready(make_agents(["nash:pushfold", "tag", "rock"]))
    with pytest.raises(ValueError, match="heads-up only"):
        run_league(make_agents(["nash:pushfold", "tag", "rock"]), LeagueConfig(hands=1))
    with pytest.raises(ValueError, match="ante"):
        run_league(make_agents(["nash:pushfold", "tag"]), LeagueConfig(hands=1, ante=1))
    note = PushFoldAgent().table_note(TableShape(2, 1, 2, 0, 200))
    assert note and "100 big blinds is deeper" in note


def play(hole_sb: str, hole_bb: str, sb, bb, stack: int = 20):
    cards = parse_cards(hole_bb)[:1] + parse_cards(hole_sb)[:1]
    cards += parse_cards(hole_bb)[1:] + parse_cards(hole_sb)[1:]
    deck = Deck.stacked(cards)  # dealt one at a time from the big blind (left of the button)
    hand = Hand(
        [Player(sb.name, stack), Player(bb.name, stack)],
        button=0,
        small_blind=1,
        big_blind=2,
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
