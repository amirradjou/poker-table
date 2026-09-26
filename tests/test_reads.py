from poker_table.agents import CallingStation, Maniac, TightAggressive
from poker_table.agents.reads import Reads, style_of
from poker_table.league import LeagueConfig, run_league


class Reader(TightAggressive):
    def __init__(self, name: str = "me") -> None:
        super().__init__(name, seed=9)
        self.reads = Reads(name)

    def observe(self, history) -> None:
        self.reads.observe(history)


def session(hands: int = 80):
    me = Reader()
    others = [Maniac("maniac", seed=2), CallingStation("station"), TightAggressive("tag", seed=3)]
    result = run_league([me, *others], LeagueConfig(hands=hands, seed=11))
    return me.reads, result


def test_reads_label_the_bots_the_way_a_player_would() -> None:
    reads, _ = session()
    assert style_of(reads.stats["maniac"]).startswith("loose-aggressive")
    assert style_of(reads.stats["station"]).startswith("loose-passive")
    assert "calls down" in style_of(reads.stats["station"])
    assert style_of(reads.stats["tag"]).startswith(("tight", "medium"))


def test_a_read_is_one_line_with_the_numbers_behind_the_label() -> None:
    reads, _ = session()
    line = reads.about("maniac")
    assert line.startswith("maniac (80 hands): loose-aggressive. Plays ")
    assert "raises first" in line and "After the flop: aggression" in line
    assert "\n" not in line
    assert reads.describe(["me", "tag"]) == [reads.about("tag")]  # never a read on yourself


def test_shown_hands_are_only_the_ones_turned_over() -> None:
    reads, result = session(40)
    turned_over = {
        (h.hand_id, h.players[seat].name) for h in result.histories for seat in h.showdown
    }
    for name, shown in reads.shown.items():
        assert name != "me"
        for s in shown:
            assert (s.hand_id, name) in turned_over
            assert len(s.cards.split()) == 2
    assert any(reads.shown.values())


def test_a_stranger_and_a_short_sample_get_no_label() -> None:
    reads = Reads("me")
    assert reads.about("nobody") == "nobody: no hands seen yet."
    short, _ = session(5)
    assert style_of(short.stats["maniac"]) == ""  # 5 hands say nothing about a style
    assert short.about("maniac").startswith("maniac (5 hands). Plays ")


def test_memory_is_bounded() -> None:
    me = Reader()
    me.reads = Reads("me", remember=10)
    run_league([me, CallingStation("station")], LeagueConfig(hands=30, seed=1))
    assert len(me.reads.hands) == 10 and me.reads.stats["station"].hands == 10
