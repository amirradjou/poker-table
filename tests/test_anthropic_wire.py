"""The real Anthropic SDK, over real HTTP, against a stand-in for the API.

Other tests hand a seat a fake client, which would accept a request the SDK or the API refuses.
Nobody here pays for model calls, so the first live request is made by someone on their own key,
and these tests are what stands between that person and a table that folds every hand. The rules
checked here are the API's published ones for the current models.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from poker_table.agents import LLMAgent, make_view
from poker_table.agents.llm import PRICES, explain_model_error
from poker_table.agents.personalities import PERSONALITIES
from poker_table.agents.registry import check_seats_ready, make_agents
from poker_table.cli import main
from poker_table.coach.narrate import narrate
from poker_table.coach.report import Leak, Report
from poker_table.engine import Hand, Player

KEY = "sk-ant-not-a-real-key"
CURRENT = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
ANSWER = {"action": "fold", "amount": 0, "table_talk": "", "reasoning": "not this one", "note": ""}


class StandInApi:
    """Answers like api.anthropic.com, and remembers what it was sent."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.content: list[dict] = [
            {"type": "thinking", "thinking": "", "signature": "sig"},  # comes first on Opus 5.5
            {"type": "text", "text": json.dumps(ANSWER)},
        ]
        self.stop_reason = "end_turn"
        self.status: int | None = None
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def reply(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("x-should-retry", "false")  # the SDK backs off otherwise
                self.end_headers()
                self.wfile.write(data)

            def fail(self, status: int, kind: str) -> None:
                self.reply(status, {"type": "error", "error": {"type": kind, "message": kind}})

            def authorised(self) -> bool:
                # The API takes a key in either header, and SDK 2.x sends it as a bearer token.
                bearer = self.headers.get("authorization", "").removeprefix("Bearer ")
                if KEY in (self.headers.get("x-api-key"), bearer):
                    return True
                self.fail(401, "authentication_error")
                return False

            def do_GET(self) -> None:
                api.requests.append({"method": "GET", "path": self.path})
                if not self.authorised():
                    return
                model = self.path.rsplit("/", 1)[-1]
                if not self.path.startswith("/v1/models/") or model not in PRICES:
                    self.fail(404, "not_found_error")
                    return
                self.reply(200, {"type": "model", "id": model, "display_name": model})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                api.requests.append({"method": "POST", "path": self.path, "body": body})
                if not self.authorised():
                    return
                if api.status is not None:
                    self.fail(api.status, "rate_limit_error")
                    return
                self.reply(
                    200,
                    {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": body["model"],
                        "content": api.content,
                        "stop_reason": api.stop_reason,
                        "stop_sequence": None,
                        "usage": {
                            "input_tokens": 1000,
                            "output_tokens": 300,
                            "cache_read_input_tokens": 5000,
                            "cache_creation_input_tokens": 0,
                        },
                    },
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    @property
    def calls(self) -> list[dict]:
        return [r["body"] for r in self.requests if r["method"] == "POST"]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No credentials from the environment or from a profile in the home folder."""
    for name in list(os.environ):
        if name.startswith("ANTHROPIC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def api(clean_env, monkeypatch: pytest.MonkeyPatch):
    stand_in = StandInApi()
    thread = threading.Thread(target=stand_in.server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", stand_in.url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    yield stand_in
    stand_in.server.shutdown()
    stand_in.server.server_close()


def view_for(agent_name: str = "alice"):
    players = [Player(agent_name, 200), Player("bob", 200)]
    hand = Hand(players, button=0, small_blind=1, big_blind=2, seed=3)
    assert hand.actor is not None
    return make_view(hand, hand.actor.index)


# ----- the request -------------------------------------------------------------------------


@pytest.mark.parametrize("model", CURRENT)
@pytest.mark.parametrize("personality", sorted(PERSONALITIES))
def test_a_seat_sends_what_the_current_models_accept(
    api: StandInApi, model: str, personality: str
) -> None:
    agent = LLMAgent("alice", personality, model=model)
    # Some personalities fold junk without a call, so send the request itself.
    agent._call(view_for())
    request = api.calls[0]

    assert request["model"] == model
    assert request["max_tokens"] <= 16_000  # above this the SDK wants a streaming request
    assert request["messages"][-1]["role"] == "user"  # newer models refuse a prefill
    for refused in ("thinking", "tool_choice", "temperature", "top_p", "top_k"):
        assert refused not in request  # each of these is a 400 on at least one current model
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}

    config = request["output_config"]
    assert config["effort"] in EFFORTS
    assert config["format"]["type"] == "json_schema"
    schema = config["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])


def test_a_reply_that_opens_with_thinking_is_read_and_priced(api: StandInApi) -> None:
    agent = LLMAgent("alice", "maniac", model="claude-opus-5-5")
    decision = agent.act(view_for())
    assert str(decision.action) == "fold" and decision.reasoning == "not this one"
    assert not decision.meta.get("failed")
    # 1000 fresh tokens at $4, 5000 cache reads at $0.20 and 300 out at $20, per million.
    assert decision.meta["cost_usd"] == pytest.approx(0.011)
    assert agent.usage.cost_usd == pytest.approx(0.011)


def test_a_refusal_is_a_fallback_not_a_crash(api: StandInApi) -> None:
    api.content, api.stop_reason = [], "refusal"
    agent = LLMAgent("alice", "maniac", model="claude-opus-5-5")
    decision = agent.act(view_for())
    assert decision.meta["failed"] and "refusal" in decision.reasoning
    assert agent.usage.errors == 1


def test_a_rate_limit_is_a_fallback_not_a_crash(api: StandInApi) -> None:
    api.status = 429
    agent = LLMAgent("alice", "maniac", model="claude-haiku-5-5")
    decision = agent.act(view_for())
    assert decision.meta["failed"] and agent.usage.errors == 1


def test_a_hand_file_never_holds_the_key(api: StandInApi, tmp_path: Path) -> None:
    out = tmp_path / "hands.jsonl"
    assert main(["play", "-n", "3", "--seats", "llm:maniac,tag", "-o", str(out), "-q"]) == 0
    assert api.calls  # it really did ask
    assert KEY not in out.read_text()


# ----- finding out before the first hand ---------------------------------------------------


def test_the_check_before_the_first_hand_spends_nothing(api: StandInApi) -> None:
    check_seats_ready(make_agents(["llm:nerd", "tag"], seed=0))
    assert api.requests == [{"method": "GET", "path": "/v1/models/claude-opus-5-5"}]


def test_no_key_stops_the_table_and_says_what_to_do(
    clean_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    out = tmp_path / "hands.jsonl"
    assert main(["play", "-n", "5", "--seats", "llm:nerd,tag", "-o", str(out)]) != 0
    message = capsys.readouterr().err
    assert "seat 'nerd'" in message and "your own Anthropic API key" in message
    assert "ANTHROPIC_API_KEY" in message and "ollama/" in message
    assert "TypeError" not in message
    assert not out.exists()


@pytest.mark.parametrize(
    ("key", "model", "expected"),
    [
        ("sk-wrong", "claude-opus-5-5", "refused this API key"),
        (KEY, "claude-opus-9", "no model named claude-opus-9"),
    ],
)
def test_a_bad_key_or_model_name_is_found_before_the_first_hand(
    api: StandInApi, monkeypatch: pytest.MonkeyPatch, key: str, model: str, expected: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    with pytest.raises(ValueError, match=expected):
        check_seats_ready([LLMAgent("alice", "nerd", model=model)])
    assert not api.calls


def test_no_network_is_found_before_the_first_hand(
    api: StandInApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    with pytest.raises(ValueError, match="cannot reach the Anthropic API"):
        check_seats_ready([LLMAgent("alice", "nerd")])


def test_a_table_with_no_limit_says_so(api: StandInApi, capsys: pytest.CaptureFixture) -> None:
    assert main(["play", "-n", "1", "--seats", "llm:maniac,tag", "-q"]) == 0
    assert "bill your Anthropic key with no limit" in capsys.readouterr().err
    assert main(["play", "-n", "1", "--seats", "llm:maniac,tag", "-q", "--budget", "1"]) == 0
    assert "no limit" not in capsys.readouterr().err


def test_a_free_table_says_nothing_about_money(capsys: pytest.CaptureFixture) -> None:
    assert main(["play", "-n", "1", "--seats", "tag,random", "-q"]) == 0
    assert "bill" not in capsys.readouterr().err


def test_a_trial_prices_its_hands_from_what_the_sdk_reports(
    api: StandInApi, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    path = tmp_path / "league.toml"
    path.write_text(
        'name = "t"\ncoach = false\nseats = ["llm:maniac@claude-opus-5-5", "station"]\n'
        "hands = 200\nseed = 1\nbudget_usd = 5\n"
    )
    out = tmp_path / "out"
    assert main(["experiment", "run", str(path), "-o", str(out), "--trial", "2"]) == 0
    printed = capsys.readouterr().out
    # Each decision the stand-in answers costs $0.011 at Opus 5.5's prices.
    spent = len(api.calls) * 0.011
    assert api.calls and f"cost ${spent:.4f}" in printed
    assert f"about ${spent * 100:.2f} in all" in printed


# ----- the coach's notes -------------------------------------------------------------------


def test_the_coach_notes_go_out_the_same_way(api: StandInApi) -> None:
    leak = Leak(
        tag="t",
        title="a leak",
        leaks=2,
        opportunities=4,
        by_position={},
        by_street={},
        examples=[],
    )
    report = Report(
        player="me", hands=10, decisions=4, net=0, big_blind=2, leaks=[leak], observations=[]
    )
    api.content = [{"type": "text", "text": json.dumps({"notes": [], "focus": "fold more"})}]
    narration = narrate(report, model="claude-sonnet-5-5")
    assert narration.focus == "fold more" and narration.cost_usd > 0
    request = api.calls[0]
    assert request["max_tokens"] <= 16_000 and "thinking" not in request


def test_a_failed_call_never_shows_a_person_a_bare_type_error() -> None:
    message = explain_model_error(TypeError("Could not resolve authentication method"), "m")
    assert "your own Anthropic API key" in message
    assert explain_model_error(ValueError("run `ollama pull x`"), "m") == "run `ollama pull x`"
    assert "RuntimeError: boom" in explain_model_error(RuntimeError("boom"), "m")
