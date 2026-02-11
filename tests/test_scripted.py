import random

from poker_table.agents import CallingStation, Maniac, RandomAgent, TightAggressive, make_view
from poker_table.cards import Deck, parse_cards
from poker_table.engine import Action, ActionType, Hand, Player
from poker_table.table import new_hand, play_hand


def stacked(hole: dict[int, str], board: str, *, button: int) -> Deck:
    n = len(hole)
    order = [(button + 1 + i) % n for i in range(n)]
    first = [parse_cards(hole[i])[0] for i in order]
    second = [parse_cards(hole[i])[1] for i in order]
    return Deck.stacked(first + second + parse_cards(board))


def hand_with(hole: dict[int, str], board: str = "", *, button: int = 0, stacks=None) -> Hand:
    n = len(hole)
    players = [Player(f"p{i}", (stacks or [200] * n)[i]) for i in range(n)]
    deck = stacked(hole, board, button=button)
    return Hand(players, button=button, small_blind=1, big_blind=2, seed=0, deck=deck)


def test_tag_opens_premiums_and_folds_junk_from_early_position() -> None:
    hand = hand_with({0: "2c 7d", 1: "3c 8d", 2: "4c 9d", 3: "As Ah", 4: "2h 7h", 5: "3h 8s"})
    tag = TightAggressive()
    decision = tag.act(make_view(hand, 3))  # UTG with aces
    assert decision.action == Action.raise_to(6)
    assert "open" in decision.reasoning
    hand.apply(decision.action)
    hand.apply(Action.fold())
    hand.apply(Action.fold())
    hand.apply(Action.fold())
    junk = hand_with({0: "2c 7d", 1: "3c 8d", 2: "4c 9d", 3: "7s 2s", 4: "2h 7h", 5: "3h 8s"})
    assert tag.act(make_view(junk, 3)).action == Action.fold()


def test_tag_three_bets_with_a_premium_and_folds_junk_to_a_raise() -> None:
    hand = hand_with({0: "Kc Kd", 1: "3c 8d", 2: "7s 2h"})
    hand.apply(Action.raise_to(6))  # button opens
    tag = TightAggressive()
    sb = tag.act(make_view(hand, 1))
    assert sb.action == Action.fold()
    hand.apply(sb.action)
    hand2 = hand_with({0: "3c 8d", 1: "Kc Kd", 2: "7s 2h"})
    hand2.apply(Action.raise_to(6))
    assert tag.act(make_view(hand2, 1)).action == Action.raise_to(18)


def test_tag_checks_the_big_blind_option_with_junk() -> None:
    hand = hand_with({0: "3c 8d", 1: "4c 9d", 2: "7s 2h"})
    hand.apply(Action.call())
    hand.apply(Action.call())
    assert TightAggressive().act(make_view(hand, 2)).action == Action.check()


def test_tag_shoves_short_with_a_playable_hand() -> None:
    hand = hand_with({0: "3c 8d", 1: "4c 9d", 2: "Ac Kd"}, stacks=[200, 200, 20])
    hand.apply(Action.raise_to(6))
    hand.apply(Action.fold())
    decision = TightAggressive().act(make_view(hand, 2))
    assert decision.action == Action.raise_to(20)
    assert "shove" in decision.reasoning


def test_tag_value_bets_top_pair_and_folds_air_to_a_bet() -> None:
    hand = hand_with({0: "Ah Kd", 1: "7c 8d", 2: "2h 3h"}, "Ac 9s 4d")
    for _ in range(3):
        hand.apply(Action.call() if not hand.legal_actions().can_check else Action.check())
    assert hand.street.value == "flop"
    tag = TightAggressive()
    sb = tag.act(make_view(hand, 1))  # 7-8 with nothing, first to act
    assert sb.action == Action.check()
    hand.apply(sb.action)
    hand.apply(Action.check())  # BB
    btn = tag.act(make_view(hand, 0))  # top pair, top kicker
    assert btn.action == Action.bet(3)
    assert "bet" in btn.reasoning
    hand.apply(btn.action)
    assert tag.act(make_view(hand, 1)).action == Action.fold()


def test_tag_calls_a_draw_when_priced_in() -> None:
    hand = hand_with({0: "Ah 5h", 1: "Kc Kd", 2: "2c 3d"}, "Qh 9h 2s")
    for _ in range(3):
        hand.apply(Action.call() if not hand.legal_actions().can_check else Action.check())
    hand.apply(Action.bet(2))  # SB bets small into 6
    hand.apply(Action.fold())
    decision = TightAggressive().act(make_view(hand, 0))
    assert decision.action == Action.call()
    assert "priced in" in decision.reasoning


def test_rock_variant_is_tighter_and_more_passive() -> None:
    rock = TightAggressive("rock", tightness=2, aggression=0.0)
    hand = hand_with({0: "3c 8d", 1: "Kc Jd", 2: "7s 2h"})  # KJo scores 7: TAG opens from SB
    hand.apply(Action.fold())
    assert TightAggressive().act(make_view(hand, 1)).action.type is ActionType.RAISE
    assert rock.act(make_view(hand, 1)).action == Action.fold()


def test_maniac_mostly_raises() -> None:
    maniac = Maniac(seed=1, raise_rate=0.9)
    raises = 0
    for i in range(50):
        players = [Player("a", 200), Player("b", 200)]
        hand = Hand(players, button=0, small_blind=1, big_blind=2, seed=i)
        d = maniac.act(make_view(hand, 0))
        raises += d.action.type is ActionType.RAISE
    assert raises >= 40


def test_all_scripted_agents_finish_a_long_session_legally() -> None:
    agents = [
        TightAggressive("tag", seed=1),
        TightAggressive("rock", tightness=2, aggression=0.3, seed=2),
        Maniac("maniac", seed=3),
        CallingStation("station"),
        RandomAgent("random", seed=4),
    ]
    rng = random.Random(0)
    for i in range(300):
        stacks = [rng.randint(20, 400) for _ in agents]
        hand, seated = new_hand(agents, stacks, button=i % 5, small_blind=1, big_blind=2, seed=i)
        played = play_hand(hand, seated)
        assert hand.finished
        assert not any(d.illegal for d in played.decisions), played.decisions
        assert sum(s.stack for s in hand.seats) == sum(stacks)
