import json

import pytest

from poker_table.agents import LLMAgent, make_view
from poker_table.agents.llm import estimate_cost
from poker_table.agents.ollama import OllamaClient, default_host
from poker_table.cards import Deck, parse_cards
from poker_table.engine import Action, Hand, Player


class FakeOllama:
    """Records what the seat sent and answers like /api/chat and /api/tags."""

    def __init__(self, *replies, pulled=("qwen2.5:7b",)):
        self.replies = list(replies)
        self.pulled = list(pulled)
        self.sent = []

    def __call__(self, path, body):
        self.sent.append((path, body))
        if path == "/api/tags":
            return {"models": [{"name": n} for n in self.pulled]}
        reply = self.replies.pop(0) if self.replies else {"action": "fold", "amount": 0}
        return {
            "message": {"role": "assistant", "content": json.dumps(reply)},
            "done_reason": "stop",
            "prompt_eval_count": 812,
            "eval_count": 64,
        }


def a_hand() -> Hand:
    order = [1, 2, 0]
    hole = {0: "Ac Ad", 1: "2c 7d", 2: "3c 8d"}
    first = [parse_cards(hole[i])[0] for i in order]
    second = [parse_cards(hole[i])[1] for i in order]
    players = [Player(f"p{i}", 200) for i in range(3)]
    deck = Deck.stacked(first + second)
    return Hand(players, button=0, small_blind=1, big_blind=2, seed=0, deck=deck)


def local_seat(fake: FakeOllama, **kwargs) -> LLMAgent:
    client = OllamaClient("http://ollama.test:11434", transport=fake)
    return LLMAgent("alice", "nerd", model="ollama/qwen2.5:7b", client=client, **kwargs)


def test_a_local_model_gets_the_same_question_in_ollamas_shape() -> None:
    fake = FakeOllama({"action": "raise", "amount": 6, "table_talk": "", "reasoning": "AA"})
    seat = local_seat(fake)
    decision = seat.act(make_view(a_hand(), 0))
    assert decision.action == Action.raise_to(6) and decision.reasoning == "AA"
    path, body = fake.sent[0]
    assert path == "/api/chat" and body["model"] == "qwen2.5:7b" and body["stream"] is False
    system, user = body["messages"]
    assert system["role"] == "system" and "THE MATH NERD" in system["content"]
    assert user["role"] == "user" and user["content"].endswith("Decide now.")
    assert body["format"] == seat.schema  # structured output, the same schema as Claude's
    assert body["options"]["num_ctx"] >= 8192 and body["options"]["num_predict"] == 8000


def test_a_local_model_is_free_and_says_which_model_played() -> None:
    fake = FakeOllama({"action": "call", "amount": 0, "table_talk": "", "reasoning": ""})
    seat = local_seat(fake)
    decision = seat.act(make_view(a_hand(), 0))
    assert decision.meta["model"] == "ollama/qwen2.5:7b"
    assert decision.meta["cost_usd"] == 0.0 and decision.meta["input_tokens"] == 812
    assert seat.usage.calls == 1 and seat.usage.cost_usd == 0.0
    assert estimate_cost("ollama/anything", object()) == 0.0


def test_a_truncated_or_broken_local_reply_degrades_to_a_safe_action() -> None:
    def truncated(path, body):
        return {"message": {"content": '{"action": "ra'}, "done_reason": "length"}

    seat = LLMAgent(
        "alice",
        "nerd",
        model="ollama/qwen2.5:7b",
        client=OllamaClient(transport=truncated),
    )
    decision = seat.act(make_view(a_hand(), 0))
    assert decision.action == Action.fold() and "max_tokens" in decision.reasoning

    def down(path, body):
        raise ConnectionRefusedError("connection refused")

    seat = LLMAgent("bob", "nerd", model="ollama/x", client=OllamaClient(transport=down))
    decision = seat.act(make_view(a_hand(), 0))
    assert decision.action == Action.fold() and "ConnectionRefusedError" in decision.reasoning
    assert seat.usage.errors == 1


def test_checking_a_local_model_before_the_first_hand() -> None:
    client = OllamaClient(transport=FakeOllama(pulled=["qwen2.5:7b", "llama3:latest"]))
    client.check("ollama/qwen2.5:7b")
    client.check("ollama/llama3")  # ":latest" is implied, as with `ollama run`
    with pytest.raises(ValueError, match="run `ollama pull qwen2.5:3b`"):
        client.check("ollama/qwen2.5:3b")

    def down(path, body):
        raise ConnectionRefusedError("refused")

    with pytest.raises(ValueError, match="start it with `ollama serve`"):
        OllamaClient(transport=down).check("ollama/qwen2.5:7b")


def test_the_host_comes_from_the_environment_like_ollama_itself(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert default_host() == "http://localhost:11434"
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11500")
    assert default_host() == "http://0.0.0.0:11500"
    monkeypatch.setenv("OLLAMA_HOST", "https://gpu.box/")
    assert default_host() == "https://gpu.box"


def test_the_registry_builds_a_local_seat_from_a_spec() -> None:
    from poker_table.agents.registry import make_agent

    seat = make_agent("llm:rock@ollama/qwen2.5:7b")
    assert isinstance(seat, LLMAgent) and seat.model == "ollama/qwen2.5:7b"
    assert seat.name == "rock" and seat.kind == "llm:rock"
    named = make_agent("bob:llm:nerd@ollama/qwen2.5:7b")
    assert named.name == "bob" and named.model == "ollama/qwen2.5:7b"
    assert isinstance(named.client, OllamaClient)  # built lazily, no network yet
