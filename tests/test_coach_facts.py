from poker_table.agents import CallingStation, Maniac, TightAggressive
from poker_table.cards import Deck, parse_cards
from poker_table.coach.facts import replay, tag_hand, tag_hands
from poker_table.engine import Action, Hand, Player
from poker_table.history import HandHistory
from poker_table.league import LeagueConfig, run_league
from poker_table.table import PlayedHand


def stacked(hole: dict[int, str], board: str, *, button: int) -> Deck:
    n = len(hole)
    order = [(button + 1 + i) % n for i in range(n)]
    first = [parse_cards(hole[i])[0] for i in order]
    second = [parse_cards(hole[i])[1] for i in order]
    return Deck.stacked(first + second + parse_cards(board))


def scripted(hole: dict[int, str], board: str, actions: list[Action], *, names=None) -> HandHistory:
    n = len(hole)
    names = names or [f"p{i}" for i in range(n)]
    hand = Hand(
        [Player(nm, 200) for nm in names],
        button=0,
        small_blind=1,
        big_blind=2,
        seed=0,
        deck=stacked(hole, board, button=0),
    )
    for a in actions:
        hand.apply(a)
    assert hand.finished
    return HandHistory.from_played(PlayedHand(hand))


def by_tag(facts, tag):
    return [f for f in facts if f.tag == tag]


def test_replay_reproduces_the_hand_exactly() -> None:
    # 6-max: UTG opens, BTN calls, blinds fold; flop bet/call; turn check/check; river bet/fold
    holes = {0: "Ad Kd", 1: "2c 7d", 2: "3c 8d", 3: "Qh Qd", 4: "9s 4h", 5: "Ts 6c"}
    history = scripted(
        holes,
        "Qs 7h 2d 5c Jc",
        [
            Action.raise_to(6),  # UTG (seat 3)
            Action.fold(),
            Action.fold(),
            Action.call(),  # BTN (seat 0)
            Action.fold(),
            Action.fold(),
            Action.bet(8),  # flop: UTG c-bets
            Action.call(),
            Action.check(),
            Action.check(),
            Action.bet(20),  # river
            Action.fold(),
        ],
    )
    steps = list(replay(history))
    assert [(v.seat, str(a)) for v, a in steps] == [
        (3, "raise 6"),
        (4, "fold"),
        (5, "fold"),
        (0, "call"),
        (1, "fold"),
        (2, "fold"),
        (3, "bet 8"),
        (0, "call"),
        (3, "check"),
        (0, "check"),
        (3, "bet 20"),
        (0, "fold"),
    ]
    flop_view = steps[6][0]
    assert flop_view.pot == 15 and [str(c) for c in flop_view.board] == ["Qs", "7h", "2d"]
    river_call_view = steps[11][0]
    assert river_call_view.to_call == 20 and river_call_view.pot == 51


def test_preflop_facts() -> None:
    holes = {0: "Kh 8c", 1: "7c 2d", 2: "As Ks", 3: "9h 4d", 4: "Ad Kd", 5: "Qs Qc"}
    history = scripted(
        holes,
        "2h 5d 9c Ts Jh",
        [
            Action.raise_to(6),  # UTG 94o opens
            Action.call(),  # HJ AKd calls
            Action.fold(),  # CO QQ folds
            Action.call(),  # BTN K8o calls
            Action.fold(),  # SB 72o folds
            Action.fold(),  # BB AKs folds
        ]
        + [Action.check()] * 9,
    )
    f = tag_hand(history, "p3")[0]
    assert f.tag == "open" and f.ok is False and "outside the chart" in f.detail
    f = tag_hand(history, "p4")[0]
    assert f.tag == "call_open" and f.ok is True
    f = tag_hand(history, "p5")[0]
    assert f.tag == "fold_vs_open" and f.ok is False and "should continue" in f.detail
    f = tag_hand(history, "p0")[0]
    assert f.tag == "call_open" and f.ok is False and "outside the calling range" in f.detail
    f = tag_hand(history, "p1")[0]
    assert f.tag == "fold_vs_open" and f.ok is True
    f = tag_hand(history, "p2")[0]
    assert f.tag == "fold_vs_open" and f.ok is False and f.position == "BB"


def test_limp_and_option_check() -> None:
    history = scripted(
        {0: "Ad Kd", 1: "7c 2d", 2: "3c 8d"},
        "2h 5d 9c Ts Jh",
        [Action.call(), Action.call(), Action.check()] + [Action.check()] * 9,
    )
    assert tag_hand(history, "p0")[0].tag == "limp"
    assert tag_hand(history, "p0")[0].ok is False
    assert tag_hand(history, "p2")[0].tag == "check_option"


def test_three_bet_and_facing_it() -> None:
    history = scripted(
        {0: "Ad Ac", 1: "7c 2d", 2: "Ts 9s"},
        "",
        [
            Action.raise_to(6),
            Action.fold(),
            Action.raise_to(20),
            Action.raise_to(60),
            Action.fold(),
        ],
    )
    bb = tag_hand(history, "p2")
    assert [(f.tag, f.ok) for f in bb] == [("three_bet", False), ("vs_three_bet", True)]
    btn = tag_hand(history, "p0")
    assert [(f.tag, f.ok) for f in btn] == [("open", True), ("vs_three_bet", True)]
    assert "too loose" not in btn[1].detail


def test_postflop_price_facts() -> None:
    # BTN calls with a bare gutshot facing a pot-sized bet: not the price.
    history = scripted(
        {0: "Jh Td", 1: "7c 2d", 2: "Ac Kc"},
        "As 8d 3c 2h 5s",
        [
            Action.raise_to(6),  # BTN opens JTo
            Action.fold(),
            Action.call(),  # BB calls with AKs (calling is in the BB defend range)
            Action.bet(13),  # flop: BB bets pot
            Action.call(),  # BTN calls with nothing — no price
            Action.bet(39),  # turn: pot again
            Action.fold(),  # BTN folds
        ],
    )
    btn = tag_hand(history, "p0", samples=400)
    tags = [(f.tag, f.ok) for f in btn]
    assert tags[0] == ("open", True)
    assert tags[1] == ("call_vs_bet", False)
    assert btn[1].equity is not None and btn[1].price is not None
    assert btn[1].equity < btn[1].price
    assert "not enough" in btn[1].detail
    assert tags[2] == ("fold_vs_bet", True)
    bb = tag_hand(history, "p2", samples=400)
    assert [(f.tag, f.ok) for f in bb] == [("call_open", True), ("bet", None), ("bet", None)]
    assert "value" in bb[1].detail


def test_folding_a_big_draw_to_a_small_bet_is_flagged() -> None:
    history = scripted(
        {0: "Kh Qh", 1: "7c 2d", 2: "Ac 8s"},
        "Jh 9h 2c",
        [
            Action.raise_to(6),
            Action.fold(),
            Action.call(),
            Action.bet(2),  # BB min-bets into 13
            Action.fold(),  # BTN folds a flush draw + gutshot getting 7.5:1
        ],
    )
    fold = tag_hand(history, "p0", samples=400)[1]
    assert fold.tag == "fold_vs_bet" and fold.ok is False
    assert "profitable call" in fold.detail


def test_cbet_and_missed_river_value() -> None:
    history = scripted(
        {0: "As Ad", 1: "7c 2d", 2: "Kc Qc"},
        "Ah 7d 2s 9s 4c",
        [
            Action.raise_to(6),  # BTN opens AA
            Action.fold(),
            Action.call(),  # BB
            Action.check(),
            Action.check(),  # BTN checks the flop: a missed c-bet (observation)
            Action.check(),
            Action.bet(6),  # BTN bets the turn
            Action.call(),
            Action.check(),
            Action.check(),  # BTN checks back the river with the nuts: missed value
        ],
    )
    btn = tag_hand(history, "p0", samples=300)
    tags = [f.tag for f in btn]
    assert tags == ["open", "cbet", "bet", "check_strong_river"]
    assert "checks instead of c-betting" in btn[1].detail and btn[1].texture == "dry"
    assert btn[3].ok is False


def test_tag_hands_over_a_bot_session_runs_and_is_consistent() -> None:
    agents = [TightAggressive("tag"), Maniac("maniac", seed=1), CallingStation("station")]
    result = run_league(agents, LeagueConfig(hands=40, seed=4))
    facts = tag_hands(result.histories, "station", samples=100)
    assert facts
    assert all(
        f.tag
        in (
            "limp",
            "call_open",
            "call_vs_bet",
            "check",
            "check_option",
            "vs_three_bet",
            "fold_first_in",
            "check_strong_river",
            "cbet",
            "bet",
            "fold_vs_open",
            "fold_vs_bet",
            "open",
            "three_bet",
            "raise_vs_bet",
        )
        for f in facts
    )
    assert not any(f.tag == "fold_vs_bet" for f in facts)  # a station never folds
    assert tag_hands(result.histories, "nobody") == []
