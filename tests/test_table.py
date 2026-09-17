from poker_table.agents import CallingStation, Decision, RandomAgent, SeatView
from poker_table.engine import Action, LegalActions
from poker_table.table import fallback_action, new_hand, play_hand, sanitize


def legal(**kw) -> LegalActions:
    base = dict(can_check=False, call_amount=2, min_raise_to=4, max_raise_to=100, current_bet=2)
    return LegalActions(**(base | kw))


def test_sanitize_keeps_legal_actions() -> None:
    assert sanitize(Action.fold(), legal()) == Action.fold()
    assert sanitize(Action.call(), legal()) == Action.call()
    assert sanitize(Action.raise_to(10), legal()) == Action.raise_to(10)
    assert sanitize(Action.check(), legal(can_check=True, call_amount=0)) == Action.check()


def test_sanitize_maps_slips_to_the_nearest_legal_action() -> None:
    assert sanitize(Action.bet(10), legal()) == Action.raise_to(10)  # bet while facing a bet
    assert sanitize(Action.raise_to(1), legal()) == Action.raise_to(4)  # below the minimum
    assert sanitize(Action.raise_to(999), legal()) == Action.raise_to(100)  # above the stack
    free = legal(can_check=True, call_amount=0, current_bet=0)
    assert sanitize(Action.call(), free) == Action.check()  # "call" with nothing to call
    assert sanitize(Action.raise_to(10), free) == Action.bet(10)


def test_sanitize_rejects_the_truly_illegal() -> None:
    assert sanitize(Action.check(), legal()) is None
    assert sanitize(Action.raise_to(50), legal(min_raise_to=0, max_raise_to=0)) is None
    assert sanitize(Action.call(), legal(call_amount=0)) is None


def test_fallback_checks_when_it_can_else_folds() -> None:
    assert fallback_action(legal(can_check=True, call_amount=0)) == Action.check()
    assert fallback_action(legal()) == Action.fold()


class Stubborn:
    """Always tries to check, even facing a bet."""

    name = "stubborn"

    def act(self, view: SeatView) -> Decision:
        return Decision(Action.check(), reasoning="I only ever check", table_talk="hm")


class Crashy:
    name = "crashy"

    def act(self, view: SeatView) -> Decision:
        raise RuntimeError("boom")


def test_play_hand_records_illegal_decisions_and_applies_the_fallback() -> None:
    hand, agents = new_hand(
        [Stubborn(), CallingStation("s1")], [100, 100], button=0, small_blind=1, big_blind=2, seed=1
    )
    played = play_hand(hand, agents)
    assert hand.finished
    first = played.decisions[0]
    assert first.seat == 0 and first.illegal
    assert first.requested == Action.check() and first.applied == Action.fold()
    assert first.reasoning == "I only ever check" and first.table_talk == "hm"
    assert first.latency_ms >= 0
    assert hand.net() == {0: -1, 1: 1}


def test_agent_exceptions_become_folds() -> None:
    hand, agents = new_hand(
        [Crashy(), CallingStation()], [100, 100], button=0, small_blind=1, big_blind=2, seed=1
    )
    played = play_hand(hand, agents)
    assert hand.finished
    assert played.decisions[0].applied == Action.fold()
    assert "boom" in played.decisions[0].reasoning


def test_random_vs_station_plays_out_many_hands_without_illegal_actions() -> None:
    agents = [RandomAgent("r1", seed=1), RandomAgent("r2", seed=2), CallingStation("s")]
    for i in range(200):
        hand, seated = new_hand(
            agents, [100, 100, 100], button=i % 3, small_blind=1, big_blind=2, seed=i
        )
        played = play_hand(hand, seated)
        assert hand.finished
        assert not any(d.illegal for d in played.decisions)
        assert sum(s.stack for s in hand.seats) == 300
