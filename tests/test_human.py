from poker_table.agents import CallingStation, HumanAgent, make_view
from poker_table.agents.human import parse_command
from poker_table.engine import Action, Hand, Player
from poker_table.table import new_hand, play_hand


def preflop_view(seat=0):
    hand = Hand(
        [Player("a", 100), Player("b", 100), Player("c", 100)],
        button=0,
        small_blind=1,
        big_blind=2,
        seed=1,
    )
    return hand, make_view(hand, seat)


def test_parse_commands_against_legal_actions() -> None:
    _, view = preflop_view()
    assert parse_command("f", view) == (Action.fold(), "")
    assert parse_command("call", view) == (Action.call(), "")
    assert parse_command("r 10", view) == (Action.raise_to(10), "")
    assert parse_command("a", view) == (Action.raise_to(100), "")
    assert parse_command("k", view)[0] is None
    assert "amount" in parse_command("r", view)[1]
    assert "4..100" in parse_command("r 3", view)[1]
    assert "unknown" in parse_command("zzz", view)[1]


def test_bet_and_check_when_nobody_has_bet() -> None:
    hand, _ = preflop_view()
    hand.apply(Action.call())
    hand.apply(Action.call())
    view = make_view(hand, 2)  # big blind option
    assert parse_command("c", view) == (Action.check(), "")  # "call" with nothing to call = check
    hand.apply(Action.check())
    view = make_view(hand, 1)  # flop, first to act
    assert parse_command("b 5", view) == (Action.bet(5), "")
    assert parse_command("x", view) == (Action.check(), "")


def test_human_agent_reads_until_a_valid_command_and_carries_table_talk() -> None:
    lines = iter(["?", "say nice hand", "k", "r 2", "r 12"])
    shown: list[str] = []
    human = HumanAgent("me", input_fn=lambda prompt: next(lines), output_fn=shown.append)
    _, view = preflop_view()
    decision = human.act(view)
    assert decision.action == Action.raise_to(12)
    assert decision.table_talk == "nice hand"
    assert any("commands:" in line for line in shown)
    assert any("cannot check" in line for line in shown)
    assert any("4..100" in line for line in shown)
    assert any("Your cards:" in line for line in shown)


def test_eof_folds_or_checks_safely() -> None:
    def closed(prompt: str) -> str:
        raise EOFError

    human = HumanAgent("me", input_fn=closed, output_fn=lambda s: None)
    _, view = preflop_view()
    assert human.act(view).action == Action.fold()


def test_human_can_play_a_whole_hand() -> None:
    lines = iter(["c", "c", "c", "c"])
    human = HumanAgent("me", input_fn=lambda p: next(lines), output_fn=lambda s: None)
    hand, agents = new_hand(
        [human, CallingStation("s")], [50, 50], button=0, small_blind=1, big_blind=2, seed=2
    )
    played = play_hand(hand, agents)
    assert hand.finished
    assert all(d.applied.type.value in ("call", "check") for d in played.decisions)
