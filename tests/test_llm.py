import json
from types import SimpleNamespace

import pytest

from poker_table.agents import LLMAgent, make_view
from poker_table.agents.llm import ACTION_SCHEMA, estimate_cost
from poker_table.cards import Deck, parse_cards
from poker_table.engine import Action, Hand, Player
from poker_table.history import HandHistory
from poker_table.stats import compute_stats
from poker_table.table import new_hand, play_hand


class FakeMessages:
    """Records requests and replays canned responses (a JSON dict, or an exception)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0) if self.replies else {"action": "fold", "amount": 0}
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, SimpleNamespace):
            return reply
        return response(json.dumps(reply))


def response(text, *, stop="end_turn", input_tokens=1000, output_tokens=50, cache_read=0):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=0,
        ),
    )


class FakeClient:
    def __init__(self, *replies):
        self.messages = FakeMessages(replies)


def stacked(hole: dict[int, str], board: str = "", *, button: int = 0) -> Deck:
    n = len(hole)
    order = [(button + 1 + i) % n for i in range(n)]
    first = [parse_cards(hole[i])[0] for i in order]
    second = [parse_cards(hole[i])[1] for i in order]
    return Deck.stacked(first + second + parse_cards(board))


def hand_with(hole: dict[int, str], board: str = "") -> Hand:
    players = [Player(f"p{i}", 200) for i in range(len(hole))]
    return Hand(players, button=0, small_blind=1, big_blind=2, seed=0, deck=stacked(hole, board))


def test_request_shape_uses_structured_output_and_a_cached_system_prompt() -> None:
    client = FakeClient({"action": "raise", "amount": 6, "table_talk": "", "reasoning": "AA"})
    agent = LLMAgent("alice", "nerd", client=client)
    hand = hand_with({0: "Ac Ad", 1: "2c 7d", 2: "3c 8d"})
    decision = agent.act(make_view(hand, 0))
    assert decision.action == Action.raise_to(6)
    assert decision.reasoning == "AA" and decision.table_talk == ""
    request = client.messages.requests[0]
    assert request["model"] == "claude-opus-5"
    assert request["output_config"]["format"] == {"type": "json_schema", "schema": ACTION_SCHEMA}
    assert request["output_config"]["effort"] == "high"  # the nerd thinks hard
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "THE MATH NERD" in request["system"][0]["text"]
    assert "You are alice." in request["system"][0]["text"]
    body = request["messages"][0]["content"]
    assert "Your cards: Ac Ad" in body and "Legal: fold, call 2, raise to 4..200" in body
    assert body.endswith("Decide now.")
    assert "2c" not in body and "8d" not in body  # never another seat's cards


def test_usage_is_accounted_per_decision_and_in_stats() -> None:
    client = FakeClient(
        response(json.dumps({"action": "call", "amount": 0, "table_talk": "hi", "reasoning": "x"}))
    )
    agent = LLMAgent("alice", "storyteller", client=client)
    hand = hand_with({0: "Ac Kd", 1: "2c 7d", 2: "3c 8d"})
    decision = agent.act(make_view(hand, 0))
    assert decision.meta["input_tokens"] == 1000 and decision.meta["output_tokens"] == 50
    assert agent.usage.calls == 1 and agent.usage.cost_usd > 0
    assert decision.meta["cost_usd"] == pytest.approx((1000 * 5 + 50 * 25) / 1e6)


def test_estimate_cost_prices_cache_reads_at_ten_percent() -> None:
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=10,
        cache_read_input_tokens=900,
        cache_creation_input_tokens=0,
    )
    assert estimate_cost("claude-haiku-4-5", usage) == pytest.approx(
        (100 * 1 + 900 * 0.1 + 10 * 5) / 1e6
    )
    assert estimate_cost("unknown-model", usage) == estimate_cost("claude-opus-5", usage)


def test_junk_facing_a_raise_is_folded_without_a_model_call() -> None:
    client = FakeClient()
    agent = LLMAgent("alice", "nerd", client=client)  # auto_fold_below=4
    hand = hand_with({0: "7c 2d", 1: "Ac Ad", 2: "3c 8d"})
    hand.apply(Action.raise_to(6))  # button opens
    hand.apply(Action.raise_to(20))  # sb 3-bets
    view = make_view(hand, 2)  # bb holds 83o
    decision = agent.act(view)
    assert decision.action == Action.fold()
    assert decision.reasoning.startswith("auto-fold")
    assert client.messages.requests == [] and agent.usage.gated == 1


def test_gate_does_not_fire_without_a_raise_or_for_the_maniac() -> None:
    hand = hand_with({0: "7c 2d", 1: "Ac Ad", 2: "3c 8d"})
    client = FakeClient({"action": "call", "amount": 0, "table_talk": "", "reasoning": "limp"})
    LLMAgent("alice", "nerd", client=client).act(make_view(hand, 0))  # 72o, only the blinds
    assert len(client.messages.requests) == 1
    hand.apply(Action.raise_to(6))
    client = FakeClient({"action": "call", "amount": 0, "table_talk": "", "reasoning": "yolo"})
    LLMAgent("m", "maniac", client=client).act(make_view(hand, 1))
    assert len(client.messages.requests) == 1


def test_api_errors_and_bad_replies_degrade_to_check_or_fold() -> None:
    hand = hand_with({0: "Ac Kd", 1: "2c 7d", 2: "3c 8d"})
    view = make_view(hand, 0)
    agent = LLMAgent("alice", "rock", client=FakeClient(RuntimeError("boom")))
    decision = agent.act(view)
    assert decision.action == Action.fold() and "api error" in decision.reasoning
    assert agent.usage.errors == 1

    agent = LLMAgent("alice", "rock", client=FakeClient(response("not json")))
    assert agent.act(view).action == Action.fold()

    agent = LLMAgent("alice", "rock", client=FakeClient(response("{}", stop="max_tokens")))
    assert "max_tokens" in agent.act(view).reasoning

    agent = LLMAgent("alice", "rock", client=FakeClient({"action": "shove", "amount": 1}))
    assert "unknown action" in agent.act(view).reasoning

    hand.apply(Action.call())
    hand.apply(Action.call())
    view = make_view(hand, 2)  # BB can check
    agent = LLMAgent("alice", "rock", client=FakeClient(RuntimeError("boom")))
    assert agent.act(view).action == Action.check()


def test_full_hand_with_llm_seats_records_talk_cost_and_slips(tmp_path) -> None:
    replies = [
        {"action": "raise", "amount": 6, "table_talk": "raising it up", "reasoning": "AK"},
        {"action": "bet", "amount": 12, "table_talk": "", "reasoning": "oops, bet vs raise"},
        {"action": "call", "amount": 0, "table_talk": "fine", "reasoning": "call"},
    ]
    alice = LLMAgent("alice", "storyteller", client=FakeClient(*replies))
    bob = LLMAgent("bob", "maniac", client=FakeClient({"action": "fold", "amount": 0}))
    hand, seated = new_hand([alice, bob], [200, 200], button=0, small_blind=1, big_blind=2, seed=1)
    played = play_hand(hand, seated)
    assert hand.finished
    history = HandHistory.from_played(played)
    first, second = history.decisions[0], history.decisions[1]
    assert first.table_talk == "raising it up" and first.meta["model"] == "claude-opus-5"
    assert second.seat == 1
    # bob saw alice's table talk in his prompt
    prompt = bob.client.messages.requests[0]["messages"][0]["content"]
    assert 'alice: "raising it up"' in prompt
    stats = compute_stats([history])
    assert stats["alice"].model_calls == 1 and stats["alice"].cost_usd > 0
    assert stats["alice"].cost_per_hand == pytest.approx(stats["alice"].cost_usd)
    assert stats["alice"].talks == 1
    text = history.render(reasoning=True)
    assert 'alice says: "raising it up"' in text


def test_bet_slip_is_sanitized_not_punished() -> None:
    replies = [{"action": "bet", "amount": 6, "table_talk": "", "reasoning": "meant raise"}]
    alice = LLMAgent("alice", "nerd", client=FakeClient(*replies))
    hand = hand_with({0: "Ac Kd", 1: "2c 7d", 2: "3c 8d"})
    played = play_hand(hand, {0: alice, 1: _Folder(), 2: _Folder()})
    assert played.decisions[0].requested == Action.bet(6)
    assert played.decisions[0].applied == Action.raise_to(6)
    assert not played.decisions[0].illegal


class _Folder:
    name = "folder"

    def act(self, view):
        from poker_table.agents import Decision

        return Decision(Action.fold())
