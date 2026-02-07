from poker_table.agents import CallingStation, Maniac, TightAggressive
from poker_table.cards import Deck, parse_cards
from poker_table.engine import Action, Hand, Player
from poker_table.history import HandHistory
from poker_table.stats import compute_stats, format_table, leaderboard
from poker_table.table import PlayedHand, new_hand, play_hand


def stacked(hole: dict[int, str], board: str, *, button: int) -> Deck:
    n = len(hole)
    order = [(button + 1 + i) % n for i in range(n)]
    first = [parse_cards(hole[i])[0] for i in order]
    second = [parse_cards(hole[i])[1] for i in order]
    return Deck.stacked(first + second + parse_cards(board))


def scripted(hole: dict[int, str], board: str, actions: list[Action]) -> HandHistory:
    names = ["btn", "sb", "bb"]
    hand = Hand(
        [Player(n, 200) for n in names],
        button=0,
        small_blind=1,
        big_blind=2,
        seed=0,
        deck=stacked(hole, board, button=0),
    )
    for action in actions:
        hand.apply(action)
    assert hand.finished
    return HandHistory.from_played(PlayedHand(hand))


def test_preflop_stats_open_three_bet_and_fold() -> None:
    history = scripted(
        {0: "Ac Kd", 1: "Qh Qd", 2: "7s 2h"},
        "",
        [Action.raise_to(6), Action.raise_to(20), Action.fold(), Action.fold()],
    )
    stats = compute_stats([history])
    btn, sb, bb = stats["btn"], stats["sb"], stats["bb"]
    assert btn.vpip == 1 and btn.pfr == 1
    assert btn.fold_to_three_bet == 1 and btn.fold_to_three_bet_opps == 1
    assert btn.three_bet_opps == 0
    assert sb.three_bet == 1 and sb.three_bet_opps == 1
    assert sb.vpip == 1 and sb.pfr == 1
    assert bb.vpip == 0 and bb.pfr == 0
    assert bb.three_bet_opps == 0  # by the time BB acted it was a 4-bet spot, not a 3-bet one
    assert bb.three_bet is None
    assert sb.net == 8 and btn.net == -6 and bb.net == -2


def test_big_blind_check_is_not_vpip_but_a_call_is() -> None:
    history = scripted(
        {0: "Ac Kd", 1: "Qh Qd", 2: "7s 2h"},
        "6c 7d 8h 9s Tc",
        [Action.call(), Action.call(), Action.check()] + [Action.check()] * 9,
    )
    stats = compute_stats([history])
    assert stats["bb"].vpip == 0
    assert stats["sb"].vpip == 1 and stats["btn"].vpip == 1
    assert all(s.pfr == 0 for s in stats.values())
    assert all(s.saw_flop == 1 and s.showdowns == 1 for s in stats.values())
    assert all(s.won_at_showdown == 1 for s in stats.values())  # everyone plays the board


def test_postflop_aggression_and_bluffs() -> None:
    history = scripted(
        {0: "7s 2h", 1: "Qh Qd", 2: "Ac Kd"},
        "Qc 9d 4h Ts 3c",
        [
            Action.call(),
            Action.call(),
            Action.check(),
            Action.check(),  # sb
            Action.check(),  # bb
            Action.bet(4),  # btn bluffs with 72o on Q94
            Action.raise_to(12),  # sb value-raises a set
            Action.fold(),  # bb
            Action.call(),  # btn calls
            Action.check(),
            Action.check(),
            Action.check(),
            Action.check(),
        ],
    )
    stats = compute_stats([history])
    btn, sb, bb = stats["btn"], stats["sb"], stats["bb"]
    assert btn.postflop_bets_raises == 1 and btn.bluffs == 1 and btn.bluff_rate == 1
    assert btn.postflop_calls == 1 and btn.aggression_factor == 1
    assert sb.postflop_bets_raises == 1 and sb.bluffs == 0 and sb.bluff_rate == 0
    assert sb.aggression_factor == float("inf")
    assert bb.postflop_folds == 1 and bb.aggression_factor is None
    assert bb.wtsd == 0 and btn.wtsd == 1
    assert sb.won_at_showdown == 1 and btn.won_at_showdown == 0


def test_bb_per_100_and_leaderboard_order() -> None:
    history = scripted(
        {0: "Ac Kd", 1: "Qh Qd", 2: "7s 2h"},
        "",
        [Action.raise_to(6), Action.raise_to(20), Action.fold(), Action.fold()],
    )
    stats = compute_stats([history, history])
    assert stats["sb"].hands == 2 and stats["sb"].net == 16
    assert stats["sb"].bb_per_100 == 400
    assert [s.name for s in leaderboard(stats)] == ["sb", "bb", "btn"]


def test_decision_stats_come_from_the_traces() -> None:
    class Stubborn:
        name = "stubborn"

        def act(self, view):
            from poker_table.agents import Decision

            return Decision(Action.check(), table_talk="hey")

    hand, agents = new_hand(
        [Stubborn(), CallingStation("s")], [100, 100], button=0, small_blind=1, big_blind=2, seed=1
    )
    stats = compute_stats([HandHistory.from_played(play_hand(hand, agents))])
    assert stats["stubborn"].decisions == 1 and stats["stubborn"].illegal == 1
    assert stats["stubborn"].illegal_rate == 1 and stats["stubborn"].talks == 1
    assert stats["stubborn"].avg_latency_ms is not None


def test_format_table_over_a_session() -> None:
    agents = [TightAggressive("tag"), Maniac("maniac", seed=1), CallingStation("station")]
    histories = []
    for i in range(60):
        hand, seated = new_hand(agents, [200] * 3, button=i % 3, small_blind=1, big_blind=2, seed=i)
        histories.append(HandHistory.from_played(play_hand(hand, seated)))
    stats = compute_stats(histories)
    assert sum(s.net for s in stats.values()) == 0
    assert stats["station"].pfr == 0
    assert stats["maniac"].vpip is not None and stats["maniac"].vpip > 0.8
    text = format_table(stats)
    assert text.splitlines()[0].startswith("name")
    assert "maniac" in text and "%" in text
    assert len(text.splitlines()) == 4
