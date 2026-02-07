import pytest

from poker_table.agents import CallingStation, TightAggressive
from poker_table.agents.registry import available_kinds, make_agent, make_agents
from poker_table.league import LeagueConfig, run_league


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


def test_registry_builds_named_and_numbered_agents() -> None:
    agents = make_agents(["tag", "alice:maniac", "tag", "station"], seed=1)
    assert [a.name for a in agents] == ["tag", "alice", "tag2", "station"]
    assert type(agents[1]).__name__ == "Maniac"
    assert "rock" in available_kinds()
    with pytest.raises(ValueError, match="unknown agent kind"):
        make_agent("wizard")
    with pytest.raises(ValueError, match="duplicate"):
        make_agent("tag:tag", taken=["tag"])
