import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from poker_table.agents import CallingStation, TightAggressive
from poker_table.agents.registry import available_kinds, make_agent, make_agents
from poker_table.league import LeagueConfig, TournamentConfig, run_league, run_tournament


def fresh_agents():
    rock = TightAggressive("rock", tightness=2)
    return [TightAggressive("tag"), CallingStation("station"), rock]


def fingerprint(result):
    return [
        (h.board, [p.hole for p in h.players], [e.action for e in h.actions()])
        for h in result.histories
    ]


def test_league_is_reproducible_and_rotates_the_button() -> None:
    config = LeagueConfig(hands=12, seed=3)
    a = run_league(fresh_agents(), config)
    b = run_league(fresh_agents(), config)
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(run_league(fresh_agents(), LeagueConfig(hands=12, seed=4)))
    assert [h.button for h in a.histories] == [i % 3 for i in range(12)]
    assert [h.hand_id for h in a.histories] == [str(i + 1) for i in range(12)]
    assert sum(a.bankrolls.values()) == 0
    assert a.bankrolls == {s.name: s.net for s in a.stats.values()}


def test_top_up_resets_stacks_every_hand() -> None:
    agents = [TightAggressive("tag"), CallingStation("station")]
    result = run_league(agents, LeagueConfig(hands=20, seed=1, buy_in=50))
    assert all(p.stack == 50 for h in result.histories for p in h.players)
    assert result.rebuys == {"tag": 0, "station": 0}


def test_carry_over_stacks_and_rebuys() -> None:
    agents = [TightAggressive("tag"), CallingStation("station")]
    result = run_league(agents, LeagueConfig(hands=200, seed=1, buy_in=20, top_up=False))
    stacks = [tuple(p.stack for p in h.players) for h in result.histories]
    assert any(s != (20, 20) for s in stacks)  # stacks moved between hands
    assert sum(result.rebuys.values()) > 0
    assert sum(result.bankrolls.values()) == 0


def test_on_hand_callback_and_streaming_without_keeping_histories() -> None:
    seen = []
    result = run_league(
        [TightAggressive("tag"), CallingStation("station")],
        LeagueConfig(hands=5, seed=2),
        on_hand=seen.append,
        keep_histories=False,
    )
    assert len(seen) == 5 and result.histories == []


def test_league_validation() -> None:
    with pytest.raises(ValueError, match="at least two"):
        run_league([CallingStation("s")], LeagueConfig(hands=1))
    with pytest.raises(ValueError, match="unique"):
        run_league([CallingStation("s"), CallingStation("s")], LeagueConfig(hands=1))


def test_agent_kinds_reach_the_hand_history() -> None:
    agents = make_agents(["tag", "rock", "alice:maniac", "station", "random", "llm:nerd"], seed=1)
    assert [a.kind for a in agents] == ["tag", "rock", "maniac", "station", "random", "llm:nerd"]
    result = run_league(agents[:5], LeagueConfig(hands=2, seed=1))
    assert [p.kind for p in result.histories[0].players] == [
        "tag",
        "rock",
        "maniac",
        "station",
        "random",
    ]
    from poker_table.history import HandHistory

    again = HandHistory.from_json(result.histories[0].to_json())
    assert [p.kind for p in again.players] == ["tag", "rock", "maniac", "station", "random"]
    assert again.hero == ""  # no human at this table


def test_a_lone_human_seat_is_the_hero_of_league_hands() -> None:
    from poker_table.cli import resolve_player
    from poker_table.web.live import WebHumanAgent

    class Folder(WebHumanAgent):  # a browser seat that answers instantly
        def act(self, view):
            from poker_table.agents import Decision
            from poker_table.engine import Action

            return Decision(Action.fold())

    agents = [Folder("me"), *make_agents(["tag", "station"], seed=2)]
    result = run_league(agents, LeagueConfig(hands=3, seed=2))
    assert all(h.hero == "me" for h in result.histories)
    assert resolve_player(result.histories, None, __import__("pathlib").Path("x")) == "me"


def test_registry_builds_named_and_numbered_agents() -> None:
    agents = make_agents(["tag", "alice:maniac", "tag", "station"], seed=1)
    assert [a.name for a in agents] == ["tag", "alice", "tag2", "station"]
    assert type(agents[1]).__name__ == "Maniac"
    assert "rock" in available_kinds()
    with pytest.raises(ValueError, match="unknown agent kind"):
        make_agent("wizard")
    with pytest.raises(ValueError, match="duplicate"):
        make_agent("tag:tag", taken=["tag"])


def test_registry_builds_llm_seats_without_touching_the_network() -> None:
    from poker_table.agents import LLMAgent

    agents = make_agents(["llm:nerd", "bob:llm:maniac@claude-sonnet-5", "llm:nerd"])
    assert [a.name for a in agents] == ["nerd", "bob", "nerd2"]
    assert all(isinstance(a, LLMAgent) for a in agents)
    assert agents[0].model == "claude-opus-5-5" and agents[1].model == "claude-sonnet-5"
    assert agents[1].personality.key == "maniac"
    with pytest.raises(ValueError, match="unknown personality"):
        make_agent("llm:wizard")
    assert "llm:storyteller" in available_kinds()


def tournament_agents():
    return make_agents(["tag", "rock", "maniac", "station", "random"], seed=1)


def test_a_tournament_ends_with_one_seat_holding_every_chip() -> None:
    config = TournamentConfig(seed=7, hands_per_level=15)
    result = run_tournament(tournament_agents(), config)
    assert result.complete and result.winner is not None
    assert sum(result.stacks.values()) == 5 * config.starting_stack
    assert result.stacks[result.winner] == 5 * config.starting_stack
    assert sorted(result.finishes.values()) == [1, 2, 3, 4, 5]
    assert len(result.histories) == result.hands
    # nobody buys back in: each hand starts every seat where the last one left it
    seen: dict[str, int] = {}
    for history in result.histories:
        for player in history.players:
            if player.name in seen:
                assert player.stack == seen[player.name]
            seen[player.name] = player.stack + player.net
    # and the seats that busted are exactly the ones missing from the last hands
    assert set(result.out_at) == set(result.finishes) - {result.winner}
    assert f"1  {result.winner}" in result.render()


def test_the_blinds_and_antes_rise_on_the_schedule() -> None:
    config = TournamentConfig(seed=7, hands_per_level=10)
    result = run_tournament(tournament_agents(), config)
    stakes = [(h.small_blind, h.big_blind, h.ante) for h in result.histories]
    assert stakes[0] == (1, 2, 0) and stakes[9] == (1, 2, 0)
    assert stakes[10] == (2, 4, 0)  # level 2 starts at the eleventh hand
    assert stakes[30] == (5, 10, 1)  # level 4 brings in the ante
    assert result.level == config.level_number(result.hands - 1)
    assert str(config.level(0)) == "1/2" and str(config.level(30)) == "5/10+1"


def test_two_seats_busting_in_one_hand_are_ranked_by_the_chips_they_had() -> None:
    result = run_tournament(tournament_agents(), TournamentConfig(seed=3, hands_per_level=10))
    hand = next(h for h in result.histories if sum(p.net == -p.stack for p in h.players) >= 2)
    out = sorted((p for p in hand.players if p.net == -p.stack), key=lambda p: -p.stack)
    assert [p.name for p in out] == ["maniac", "random"]  # 105 chips and 1 chip
    assert result.finishes["maniac"] < result.finishes["random"]  # the bigger stack outlasted it
    assert result.out_at["maniac"] == result.out_at["random"]


def test_a_tournament_is_reproducible_and_the_button_keeps_moving() -> None:
    config = TournamentConfig(seed=11, hands_per_level=12)
    a = run_tournament(tournament_agents(), config)
    b = run_tournament(tournament_agents(), config)
    assert fingerprint(a) == fingerprint(b) and a.finishes == b.finishes
    other = run_tournament(tournament_agents(), TournamentConfig(seed=12, hands_per_level=12))
    assert fingerprint(a) != fingerprint(other)
    # the button belongs to a different seat each hand, even as seats disappear
    for first, second in zip(a.histories, a.histories[1:], strict=False):
        if len(first.players) == len(second.players):
            assert first.players[first.button].name != second.players[second.button].name


def test_a_hand_limit_stops_a_tournament_and_ranks_the_survivors_by_chips() -> None:
    result = run_tournament(
        [TightAggressive("tag"), TightAggressive("rock", tightness=2)],
        TournamentConfig(seed=5, hands_per_level=50, max_hands=8),
    )
    assert result.hands == 8 and not result.complete and result.winner is not None
    assert result.out_at == {} and sorted(result.finishes.values()) == [1, 2]
    best, worst = result.standings()
    assert result.stacks[best] >= result.stacks[worst]
    assert "no winner" in result.render() and "still in with" in result.render()


def test_tournament_validation() -> None:
    with pytest.raises(ValueError, match="at least two"):
        run_tournament([TightAggressive("only")])
    with pytest.raises(ValueError, match="unique"):
        run_tournament([TightAggressive("x"), CallingStation("x")])


def test_a_cash_game_can_have_an_ante() -> None:
    agents = [TightAggressive("tag"), CallingStation("station")]
    result = run_league(agents, LeagueConfig(hands=6, seed=1, ante=1))
    assert all(h.ante == 1 for h in result.histories)
    for history in result.histories:
        antes = [e for e in history.events if e.kind == "post_ante"]
        assert [e.amount for e in antes] == [1, 1]
        assert sum(p["amount"] for p in history.pots) >= 2  # the antes are always in the pot
    assert sum(result.bankrolls.values()) == 0


class Watcher(TightAggressive):
    """A TAG that keeps every hand it is shown."""

    def __init__(self, name: str) -> None:
        super().__init__(name, seed=5)
        self.seen = []

    def observe(self, history) -> None:
        self.seen.append(history)


def test_seats_that_learn_are_shown_each_hand_from_their_own_chair() -> None:
    watcher = Watcher("watcher")
    others = [TightAggressive("tag", seed=1), CallingStation("station")]
    result = run_league([watcher, *others], LeagueConfig(hands=12, seed=4))
    assert [h.hand_id for h in watcher.seen] == [h.hand_id for h in result.histories]
    for seen, real in zip(watcher.seen, result.histories, strict=True):
        for mine, theirs in zip(seen.players, real.players, strict=True):
            if mine.name == "watcher" or mine.seat in real.showdown:
                assert mine.hole == theirs.hole
            else:
                assert mine.hole == []  # folded cards stay face down
        assert all(d.reasoning == "" for d in seen.decisions if d.seat != 0)
    assert any(h.showdown for h in watcher.seen)  # it did get to see some shown hands


def test_a_tournament_shows_hands_only_to_the_seats_still_in_it() -> None:
    watchers = [Watcher(f"w{i}") for i in range(3)]
    result = run_tournament(watchers, TournamentConfig(starting_stack=60, seed=2))
    assert result.complete
    for w in watchers:
        assert len(w.seen) == result.hands_dealt[w.name]


def test_a_league_started_partway_deals_the_hands_it_would_have_dealt() -> None:
    def stations():
        return [CallingStation("a"), CallingStation("b"), CallingStation("c")]

    whole = run_league(stations(), LeagueConfig(hands=12, seed=3))
    rest = run_league(stations(), LeagueConfig(hands=12, seed=3, first_hand=8))
    assert rest.hands == 12
    assert [h.hand_id for h in rest.histories] == ["9", "10", "11", "12"]
    assert fingerprint(rest) == fingerprint(whole)[8:]
    assert [h.button for h in rest.histories] == [h.button for h in whole.histories[8:]]


def test_only_a_topped_up_league_can_start_partway() -> None:
    with pytest.raises(ValueError, match="topped up"):
        run_league(fresh_agents(), LeagueConfig(hands=4, first_hand=2, top_up=False))


def test_a_prefix_keeps_the_hands_of_two_tables_apart() -> None:
    result = run_league(fresh_agents(), LeagueConfig(hands=2, hand_prefix="t2-"))
    assert [h.hand_id for h in result.histories] == ["t2-1", "t2-2"]


def test_a_league_stops_when_asked_between_hands() -> None:
    played = []
    result = run_league(
        fresh_agents(),
        LeagueConfig(hands=10),
        on_hand=played.append,
        stop=lambda: len(played) == 3,
    )
    assert result.hands == 3 and len(result.histories) == 3


# ----- carrying on a half-played freezeout --------------------------------------------------


def as_written(history) -> dict:
    """A hand as the file holds it, less the clock: when it was played and how long each
    decision took are the only things a second run cannot repeat."""
    data = json.loads(history.to_json())
    del data["played_at"]
    for decision in data["decisions"]:
        del decision["latency_ms"]
    return data


def bots():
    # tag, maniac and random keep their own random choices, so these test the replay properly
    return make_agents(["tag", "rock", "maniac", "station", "random"], seed=6)


QUICK = TournamentConfig(seed=6, hands_per_level=5, hand_prefix="f2-", number=2)


def test_a_tournament_carried_on_from_any_hand_plays_out_as_if_never_stopped() -> None:
    whole = run_tournament(bots(), QUICK)
    assert whole.complete and whole.hands > 20
    expected = [as_written(h) for h in whole.histories]
    busts = [i for i, h in enumerate(whole.histories) if h.tournament["out"]]
    assert busts  # cuts at, just before and just after the hands where seats went out
    for cut in range(whole.hands + 1):
        new = []
        rest = run_tournament(bots(), QUICK, resume_from=whole.histories[:cut], on_hand=new.append)
        assert [as_written(h) for h in new] == expected[cut:], f"carried on after hand {cut}"
        assert rest.differed == {}
        assert (rest.finishes, rest.out_at, rest.hands_dealt, rest.stacks, rest.level) == (
            whole.finishes,
            whole.out_at,
            whole.hands_dealt,
            whole.stacks,
            whole.level,
        )
        assert [as_written(h) for h in rest.histories] == expected


def test_each_hand_of_a_numbered_tournament_says_which_one_it_is() -> None:
    result = run_tournament(bots(), QUICK)
    assert [h.hand_id for h in result.histories[:2]] == ["f2-1", "f2-2"]
    assert all(h.tournament["id"] == 2 for h in result.histories)
    assert list(result.histories[0].tournament) == ["id", "level", "left", "out"]
    plain = run_tournament(bots(), TournamentConfig(seed=6, hands_per_level=5))
    assert "id" not in plain.histories[0].tournament  # play --tournament writes what it did


class Priced(TightAggressive):
    """A seat with ``usage``, the way a model seat has: it must not be asked again."""

    def __init__(self, name: str) -> None:
        super().__init__(name, seed=3)
        self.usage = SimpleNamespace(cost_usd=0.0, calls=0, errors=0)

    def act(self, view):
        self.usage.calls += 1
        return super().act(view)


def test_a_model_seat_is_not_asked_again_and_a_bot_that_answers_otherwise_is_named() -> None:
    def seats():
        return [Priced("model"), TightAggressive("tag", seed=1), CallingStation("station")]

    config = TournamentConfig(seed=4, hands_per_level=5)
    whole = run_tournament(seats(), config)
    again, new = seats(), []
    rest = run_tournament(again, config, resume_from=whole.histories[:10], on_hand=new.append)
    asked = sum(h.players[d.seat].name == "model" for h in new for d in h.decisions)
    assert asked and again[0].usage.calls == asked  # only its new hands, not the ten before
    assert rest.differed == {}

    other = [Priced("model"), TightAggressive("tag", seed=2), CallingStation("station")]
    moved = run_tournament(other, config, resume_from=whole.histories[:10])
    assert set(moved.differed) == {"tag"}  # another seed: tag's own choices no longer match


def test_carrying_on_from_hands_this_tournament_did_not_deal_is_refused() -> None:
    whole = run_tournament(bots(), QUICK)
    with pytest.raises(ValueError, match="not what this tournament deals"):
        run_tournament(bots(), replace(QUICK, seed=7), resume_from=whole.histories[:3])
    edited = replace(whole.histories[2], players=list(reversed(whole.histories[2].players)))
    with pytest.raises(ValueError, match="hand f2-3 is not what"):
        run_tournament(bots(), QUICK, resume_from=[*whole.histories[:2], edited])
    with pytest.raises(ValueError, match="more were given"):
        run_tournament(bots(), QUICK, resume_from=[*whole.histories, whole.histories[-1]])


def test_a_tournament_stops_when_asked_and_carries_on_from_there() -> None:
    whole = run_tournament(bots(), QUICK)
    played = []
    cut = run_tournament(bots(), QUICK, on_hand=played.append, stop=lambda: len(played) == 7)
    assert cut.hands == 7 and not cut.complete
    rest = run_tournament(bots(), QUICK, resume_from=played)
    assert rest.finishes == whole.finishes and rest.hands == whole.hands
