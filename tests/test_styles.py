"""fish, lag, calculator, trapper and tilter: legal, repeatable, and each doing its one thing."""

from __future__ import annotations

import random

import pytest

from poker_table.agents import TightAggressive, make_view
from poker_table.agents.registry import BUILT_IN, SCRIPTED, make_agents
from poker_table.agents.styles import (
    TILT_BB,
    TILT_HANDS,
    Calculator,
    Fish,
    LooseAggressive,
    Tilter,
    Trapper,
    equity_of,
)
from poker_table.cards import Deck, parse_cards
from poker_table.engine import Action, ActionType, Hand, Player
from poker_table.history import HandHistory, PlayerRecord
from poker_table.league import LeagueConfig, run_league
from poker_table.table import new_hand, play_hand

KINDS = ("fish", "lag", "calculator", "trapper", "tilter")
OTHERS = ("tag", "maniac", "station", "rock", "fish", "lag", "trapper", "tilter")


def hand_with(hole: dict[int, str], board: str = "", *, button: int = 0) -> Hand:
    n = len(hole)
    order = [(button + 1 + i) % n for i in range(n)]
    first = [parse_cards(hole[i])[0] for i in order]
    second = [parse_cards(hole[i])[1] for i in order]
    deck = Deck.stacked(first + second + parse_cards(board))
    players = [Player(f"p{i}", 200) for i in range(n)]
    return Hand(players, button=button, small_blind=1, big_blind=2, seed=0, deck=deck)


def to_flop(hand: Hand) -> None:
    """Everyone limps and the big blind checks."""
    while hand.street.value == "preflop":
        hand.apply(Action.check() if hand.legal_actions().can_check else Action.call())


def test_every_new_kind_is_registered_and_listed() -> None:
    for kind in KINDS:
        assert kind in SCRIPTED and kind in BUILT_IN
        assert make_agents([kind])[0].kind == kind


@pytest.mark.parametrize("kind", KINDS)
def test_plays_only_legal_actions_at_two_to_nine_seats(kind: str) -> None:
    rng = random.Random(7)
    hands = 15 if kind == "calculator" else 40
    for seats in range(2, 10):
        specs = [kind, *(OTHERS[(seats + i) % len(OTHERS)] for i in range(seats - 1))]
        agents = make_agents(specs, seed=seats)
        for i in range(hands):
            stacks = [rng.randint(20, 400) for _ in agents]
            hand, seated = new_hand(
                agents, stacks, button=i % seats, small_blind=1, big_blind=2, seed=i
            )
            played = play_hand(hand, seated)
            assert hand.finished
            for d in played.decisions:
                assert not d.illegal, d
                assert not d.reasoning.startswith("agent error"), d.reasoning
            assert sum(s.stack for s in hand.seats) == sum(stacks)


@pytest.mark.parametrize("kind", KINDS)
def test_the_same_seed_plays_the_same_hands(kind: str) -> None:
    def run() -> list[tuple[str, str, str]]:
        agents = make_agents([kind, "maniac", "tag"], seed=5)
        result = run_league(agents, LeagueConfig(hands=60, seed=5))
        return [(h.hand_id, d.applied, d.table_talk) for h in result.histories for d in h.decisions]

    assert run() == run()


def test_fish_folds_air_to_a_bet_and_calls_with_a_pair() -> None:
    for cards, expected in (("7c 8d", Action.fold()), ("9c 8d", Action.call())):
        hand = hand_with({0: "Ah Kd", 1: cards, 2: "2h 3h"}, "Ac 9s 4d")
        to_flop(hand)
        hand.apply(Action.check())  # the fish, first to act, checks
        hand.apply(Action.check())  # BB
        hand.apply(Action.bet(4))  # the button bets
        assert Fish().act(make_view(hand, 1)).action == expected


def test_fish_limps_a_hand_tag_folds_and_bets_only_with_top_pair_or_better() -> None:
    hand = hand_with({0: "Jc 6d", 1: "3c 8d", 2: "7s 2h", 3: "Kd 5c"})  # UTG: J6o
    assert TightAggressive().act(make_view(hand, 3)).action == Action.fold()
    assert Fish().act(make_view(hand, 3)).action == Action.call()
    for seed in range(20):  # with no pair it never bets, whatever it draws
        flop = hand_with({0: "Ah Kd", 1: "7c 8d", 2: "2h 3h"}, "Ac 9s 4d")
        to_flop(flop)
        assert Fish(seed=seed).act(make_view(flop, 1)).action == Action.check()


def test_lag_opens_a_hand_tag_folds() -> None:
    hand = hand_with({0: "3c 8d", 1: "4c 9d", 2: "7s 2h", 3: "Qc Jd", 4: "2c 5d", 5: "6h 2d"})
    utg = make_view(hand, 3)  # Q-J offsuit under the gun, six-handed: Chen 7
    assert TightAggressive().act(utg).action == Action.fold()
    assert LooseAggressive().act(utg).action == Action.raise_to(6)


def test_lag_gives_up_to_a_raise_without_a_real_hand() -> None:
    hand = hand_with({0: "Kc Qd", 1: "7h 7s", 2: "2c 3d"}, "Jc 8s 4d")
    hand.apply(Action.raise_to(6))  # the lag opens on the button
    hand.apply(Action.call())
    hand.apply(Action.fold())
    hand.apply(Action.check())  # SB
    hand.apply(Action.bet(6))  # the lag c-bets
    hand.apply(Action.raise_to(18))  # and is raised
    decision = LooseAggressive().act(make_view(hand, 0))
    assert decision.action == Action.fold()
    assert "give up" in decision.reasoning


def test_trapper_check_raises_a_strong_flop_where_tag_bets() -> None:
    hand = hand_with({0: "Ah Kd", 1: "9c 4c", 2: "2h 3h"}, "Ac 9s 4d")
    to_flop(hand)
    first = make_view(hand, 1)  # two pair, first to act
    assert TightAggressive().act(first).action.type is ActionType.BET
    check = Trapper().act(first)
    assert check.action == Action.check() and check.meta["method"] == "trap"
    hand.apply(check.action)
    hand.apply(Action.check())
    hand.apply(Action.bet(4))
    raised = Trapper().act(make_view(hand, 1))
    assert raised.action == Action.raise_to(12)
    assert "check-raise" in raised.reasoning and raised.meta["method"] == "trap"


def test_trapper_is_tag_without_a_strong_flop() -> None:
    hand = hand_with({0: "Ah Kd", 1: "7c 8d", 2: "2h 3h"}, "Ac 9s 4d")
    to_flop(hand)
    hand.apply(Action.check())
    hand.apply(Action.check())
    view = make_view(hand, 0)
    trapper = Trapper().act(view)
    assert trapper.action == TightAggressive().act(view).action
    assert trapper.meta == {"method": "tag"}


def history_for(name: str, *, net: int, showdown: bool) -> HandHistory:
    players = [
        PlayerRecord(0, name, "BTN/SB", 200, [], net),
        PlayerRecord(1, "villain", "BB", 200, [], -net),
    ]
    return HandHistory(
        hand_id="x",
        seed=0,
        small_blind=1,
        big_blind=2,
        button=0,
        players=players,
        board=[],
        events=[],
        decisions=[],
        pots=[],
        payouts={1: 2 * -net} if net < 0 else {},
        showdown={0: "a pair", 1: "two pair"} if showdown else {},
    )


def test_tilter_tilts_after_a_big_loss_at_showdown_and_calms_down() -> None:
    tilter = Tilter("tilter", seed=3)
    view = make_view(hand_with({0: "7s 2h", 1: "3c 8d"}), 0)
    assert tilter.act(view).meta["tilt"] is False
    tilter.observe(history_for("tilter", net=-TILT_BB * 2, showdown=False))  # folded: no tilt
    tilter.observe(history_for("tilter", net=-(TILT_BB * 2 - 2), showdown=True))  # 39 bb: no
    assert not tilter.on_tilt

    tilter.observe(history_for("tilter", net=-TILT_BB * 2, showdown=True))
    first = tilter.act(view)
    assert first.meta == {"method": "tilt", "tilt": True, "tilt_hands_left": TILT_HANDS}
    assert first.table_talk and "tilt" in first.reasoning
    for _ in range(TILT_HANDS - 1):  # a loss while on tilt does not make it longer
        tilter.observe(history_for("tilter", net=-TILT_BB * 2, showdown=True))
        assert tilter.act(view).meta["tilt"] is True
    tilter.observe(history_for("tilter", net=0, showdown=False))
    calm = tilter.act(view)
    assert calm.meta["tilt"] is False and calm.meta["method"] == "tag"
    assert "breath" in calm.table_talk
    assert tilter.act(view).table_talk == ""


def test_tilter_plays_tags_hands_until_it_tilts() -> None:
    agents = make_agents(["tilter", "station"], seed=4)
    tags = make_agents(["tilter:tag", "station"], seed=4)
    a = run_league(agents, LeagueConfig(hands=80, seed=4)).histories
    b = run_league(tags, LeagueConfig(hands=80, seed=4)).histories
    assert not any(d.meta.get("tilt") for h in a for d in h.decisions)  # it never tilted
    assert [d.applied for h in a for d in h.decisions] == [
        d.applied for h in b for d in h.decisions
    ]


BETS = (2, 3, 4, 6, 9, 12, 18, 30, 60, 120)


def facing_a_bet(bet: int):
    hand = hand_with({0: "Ah Td", 1: "Kc 4h", 2: "5h 6h"}, "Qc 9s 2d")
    to_flop(hand)
    hand.apply(Action.check())  # SB
    hand.apply(Action.fold())  # BB
    hand.apply(Action.bet(bet))  # the button bets into king high
    return make_view(hand, 1)


@pytest.mark.parametrize("bet", BETS)
def test_calculator_calls_exactly_when_its_equity_beats_the_price(bet: int) -> None:
    view = facing_a_bet(bet)
    decision = Calculator().act(view)
    equity, opponents = equity_of(view)
    assert opponents == 1
    assert decision.meta["equity"] == round(equity, 4)
    assert decision.meta["price"] == round(view.pot_odds, 4)
    assert decision.action.type is not ActionType.RAISE  # well short of a raise heads-up
    expected = Action.call() if equity > view.pot_odds else Action.fold()
    assert decision.action == expected


def test_calculator_both_calls_and_folds_as_the_bet_grows() -> None:
    actions = [Calculator().act(facing_a_bet(bet)).action for bet in BETS]
    assert actions[0] == Action.call() and actions[-1] == Action.fold()


def test_calculator_never_bets_without_the_equity() -> None:
    hand = hand_with({0: "Ah Kd", 1: "7c 3d", 2: "2h 8h"}, "Ac Ks 9d")
    to_flop(hand)
    decision = Calculator().act(make_view(hand, 1))
    assert decision.action == Action.check()
    assert decision.meta["equity"] < 0.5
