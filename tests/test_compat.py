"""Seats on OpenAI-compatible APIs, over real HTTP against a stand-in, since nobody here pays."""

from __future__ import annotations

import dataclasses
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from poker_table.agents import LLMAgent, make_view
from poker_table.agents.compat import (
    PROVIDERS,
    CompatClient,
    clean_reply,
    is_compat,
    is_priced,
    provider_of,
)
from poker_table.agents.llm import ACTION_SCHEMA, estimate_cost
from poker_table.agents.registry import check_seats_ready, make_agent
from poker_table.cli import main
from poker_table.coach.narrate import narrate
from poker_table.coach.report import Leak, Report
from poker_table.engine import Hand, Player
from poker_table.experiments import Projection, run_experiment

KEY = "sk-not-a-real-key"
ANSWER = {"action": "fold", "amount": 0, "table_talk": "", "reasoning": "not this one", "note": ""}


def completion(content: str | None = None, finish: str = "stop", **usage) -> dict:
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(ANSWER) if content is None else content,
                },
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, **usage},
    }


class StandIn:
    """Answers like an OpenAI-compatible server, and remembers what it was sent."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.replies: list[tuple] = []  # (status, body, headers), used up before the default
        self.models: list[str] | None = ["good-model", "other-model"]
        self.status_of_models = 200
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def reply(self, status: int, body: dict | str, headers: dict | None = None) -> None:
                data = (body if isinstance(body, str) else json.dumps(body)).encode()
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                stand_in.requests.append(self._record(None))
                if self.path.endswith("/models"):
                    listed = [{"id": name} for name in stand_in.models or []]
                    self.reply(stand_in.status_of_models, {"data": listed})
                else:
                    self.reply(200, {"data": {}})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                stand_in.requests.append(self._record(body))
                if stand_in.replies:
                    status, reply, headers = stand_in.replies.pop(0)
                    self.reply(status, reply, headers)
                else:
                    self.reply(200, completion())

            def _record(self, body: dict | None) -> dict:
                return {
                    "method": self.command,
                    "path": self.path,
                    "auth": self.headers.get("authorization"),
                    "agent": self.headers.get("user-agent"),
                    "body": body,
                }

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    @property
    def chats(self) -> list[dict]:
        return [r["body"] for r in self.requests if r["method"] == "POST"]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    stand_in = StandIn()
    threading.Thread(target=stand_in.server.serve_forever, daemon=True).start()
    for name in ("OPENAI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("POKER_TABLE_API_KEY", raising=False)
    monkeypatch.setenv("POKER_TABLE_BASE_URL", stand_in.url)
    for prefix in ("openai", "groq", "openrouter", "gemini"):
        real = PROVIDERS[prefix]
        monkeypatch.setitem(PROVIDERS, prefix, dataclasses.replace(real, base_url=stand_in.url))
    yield stand_in
    stand_in.server.shutdown()
    stand_in.server.server_close()


def view_for(name: str = "alice"):
    hand = Hand(
        [Player(name, 200), Player("bob", 200)], button=0, small_blind=1, big_blind=2, seed=3
    )
    assert hand.actor is not None
    return make_view(hand, hand.actor.index)


# ----- the names ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("openai/gpt-x", "openai"),
        ("groq/llama-x", "groq"),
        ("openrouter/meta/llama-x:free", "openrouter"),
        ("gemini/gemini-x", "gemini"),
        ("compat/anything", "compat"),
    ],
)
def test_a_prefix_picks_the_provider(model: str, provider: str) -> None:
    assert is_compat(model) and provider_of(model) is PROVIDERS[provider]


@pytest.mark.parametrize("model", ["claude-opus-5-5", "ollama/qwen2.5:3b", "mistral/x", "plain"])
def test_anything_else_is_not_for_this_client(model: str) -> None:
    assert not is_compat(model)


@pytest.mark.parametrize(
    "spec",
    [
        "llm:nerd@openrouter/Meta-Llama/Llama-X:free",
        "bob:llm:nerd@openrouter/Meta-Llama/Llama-X:free",
        "LLM:NERD@openrouter/Meta-Llama/Llama-X:free",
    ],
)
def test_a_model_name_keeps_its_case_and_its_colons(spec: str) -> None:
    assert make_agent(spec).model == "openrouter/Meta-Llama/Llama-X:free"


# ----- the request -------------------------------------------------------------------------


def test_a_seat_sends_what_these_apis_take(api: StandIn, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    agent = LLMAgent("alice", "maniac", model="groq/good-model")
    decision = agent.act(view_for())
    assert str(decision.action) == "fold" and decision.reasoning == "not this one"
    assert not decision.meta.get("failed")

    sent = api.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["auth"] == f"Bearer {KEY}"
    assert sent["agent"].startswith("poker-table/")  # Groq refuses Python's default outright
    body = sent["body"]
    assert body["model"] == "good-model"
    assert body["messages"][0]["role"] == "system" and body["messages"][-1]["role"] == "user"
    assert body["max_tokens"] == 8000
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    for extra in ("output_config", "system", "cache_control", "thinking"):
        assert extra not in body


def test_openai_names_its_limit_differently(api: StandIn, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    LLMAgent("alice", "maniac", model="openai/good-model").act(view_for())
    assert api.chats[0]["max_completion_tokens"] == 8000 and "max_tokens" not in api.chats[0]


def test_your_openai_key_never_goes_to_a_server_of_your_own(api: StandIn, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    LLMAgent("alice", "maniac", model="compat/good-model").act(view_for())
    assert api.requests[0]["auth"] is None


def test_a_server_of_your_own_can_have_a_key_of_its_own(api: StandIn, monkeypatch) -> None:
    monkeypatch.setenv("POKER_TABLE_API_KEY", "local-key")
    LLMAgent("alice", "maniac", model="compat/good-model").act(view_for())
    assert api.requests[0]["auth"] == "Bearer local-key"


# ----- the replies -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('<think>hm { maybe }</think>\n{"a": 1}', '{"a": 1}'),
        ('Sure! Here it is: {"a": 1} Good luck.', '{"a": 1}'),
        ("no json here", "no json here"),
        ("", ""),
    ],
)
def test_a_reply_is_cleaned_before_the_seat_reads_it(raw: str, expected: str) -> None:
    assert clean_reply(raw) == expected


def test_a_chatty_reasoning_model_still_plays(api: StandIn, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    wrapped = f"<think>let me see</think>\n```json\n{json.dumps(ANSWER)}\n```"
    api.replies.append((200, completion(wrapped), None))
    decision = LLMAgent("alice", "maniac", model="groq/good-model").act(view_for())
    assert not decision.meta.get("failed") and decision.reasoning == "not this one"


@pytest.mark.parametrize(
    ("finish", "word"), [("length", "max_tokens"), ("content_filter", "refusal")]
)
def test_a_reply_cut_short_is_a_fallback(api, monkeypatch, finish: str, word: str) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    api.replies.append((200, completion("{", finish), None))
    agent = LLMAgent("alice", "maniac", model="groq/good-model")
    decision = agent.act(view_for())
    assert decision.meta["failed"] and word in decision.reasoning and agent.usage.errors == 1


def test_a_provider_with_no_schema_support_steps_down_and_stays_there(api, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    rejected = (400, {"error": {"message": "response_format json_schema not supported"}}, None)
    api.replies += [rejected, rejected]
    agent = LLMAgent("alice", "maniac", model="groq/good-model")
    assert not agent.act(view_for()).meta.get("failed")
    formats = [chat.get("response_format", {}).get("type") for chat in api.chats]
    assert formats == ["json_schema", "json_object", None]
    assert "Reply with one JSON object" in api.chats[-1]["messages"][0]["content"]
    agent.act(view_for())
    assert len(api.chats) == 4 and "response_format" not in api.chats[-1]  # no more wasted calls


def test_a_rate_limit_is_waited_out(api: StandIn, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    waits: list[float] = []
    client = CompatClient("groq/good-model", sleep=waits.append)
    limited = {"error": {"message": "slow down"}}
    api.replies += [(429, limited, {"Retry-After": "7"}), (503, limited, None)]
    decision = LLMAgent("alice", "maniac", model="groq/good-model", client=client).act(view_for())
    assert not decision.meta.get("failed") and waits == [7.0, 2.0]


def test_a_rate_limit_that_never_ends_becomes_a_fallback(api: StandIn, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    waits: list[float] = []
    client = CompatClient("groq/good-model", sleep=waits.append)
    api.replies += [(429, {"error": {"message": "slow down"}}, {"Retry-After": "9999"})] * 4
    agent = LLMAgent("alice", "maniac", model="groq/good-model", client=client)
    assert agent.act(view_for()).meta["failed"] and agent.usage.errors == 1
    assert len(waits) == 3 and max(waits) == 60.0


# ----- the key -----------------------------------------------------------------------------


def test_an_error_that_quotes_the_key_does_not_reach_the_hand_file(api, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    api.replies.append((418, {"error": {"message": f"odd request with key {KEY}"}}, None))
    decision = LLMAgent("alice", "maniac", model="groq/good-model").act(view_for())
    assert decision.meta["failed"]
    assert KEY not in decision.reasoning and "***" in decision.reasoning


def test_a_hand_file_never_holds_the_key(api, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    out = tmp_path / "hands.jsonl"
    seats = "llm:maniac@groq/good-model,tag"
    assert main(["play", "-n", "3", "--seats", seats, "-o", str(out), "-q"]) == 0
    assert api.chats and KEY not in out.read_text()


def test_a_redirect_is_not_followed_with_the_key(api, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    moved = {"Location": api.url + "/elsewhere"}
    api.replies.append((302, "", moved))
    agent = LLMAgent("alice", "maniac", model="groq/good-model")
    assert agent.act(view_for()).meta["failed"]
    assert not any("elsewhere" in r["path"] for r in api.requests)


# ----- finding out before the first hand ---------------------------------------------------


def check(model: str) -> None:
    check_seats_ready([LLMAgent("alice", "nerd", model=model)])


def test_no_key_is_found_before_the_first_hand(api: StandIn) -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY is not set"):
        check("groq/good-model")
    assert not api.requests


def test_no_key_stops_the_table_and_says_which(api, tmp_path: Path, capsys) -> None:
    out = tmp_path / "hands.jsonl"
    assert main(["play", "-n", "5", "--seats", "llm:nerd@groq/good-model,tag", "-o", str(out)]) != 0
    assert "GROQ_API_KEY" in capsys.readouterr().err and not out.exists()


def test_a_refused_key_is_found_before_the_first_hand(api, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    api.status_of_models = 401
    with pytest.raises(ValueError, match="refused the key in GROQ_API_KEY"):
        check("groq/good-model")


def test_a_key_refused_with_a_400_counts_too(api, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    api.status_of_models = 400
    client = CompatClient("gemini/good-model")
    real = client._send

    def refuse(path, body):
        from poker_table.agents.compat import CompatError

        if path == "/models":
            raise CompatError(400, "Please pass a valid API key")
        return real(path, body)

    client._send = refuse
    agent = LLMAgent("alice", "nerd", model="gemini/good-model", client=client)
    with pytest.raises(ValueError, match="refused the key in GEMINI_API_KEY"):
        check_seats_ready([agent])


def test_a_wrong_name_is_found_and_close_ones_are_offered(api, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    with pytest.raises(ValueError, match="no model named good-modle.*Close: good-model"):
        check("groq/good-modle")
    assert not api.chats


def test_a_right_name_passes_and_the_check_costs_nothing(api, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    check("groq/good-model")
    assert not api.chats


def test_gemini_style_names_are_understood(api, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    api.models = ["models/good-model"]
    check("gemini/good-model")


def test_a_server_with_no_model_list_is_not_a_fault(api, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    api.status_of_models = 404
    check("gemini/good-model")


def test_openrouter_is_asked_about_the_key_because_its_list_is_public(api, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    check("openrouter/good-model")
    assert [r["path"] for r in api.requests] == ["/v1/key", "/v1/models"]


def test_a_server_of_your_own_needs_an_address(api, monkeypatch) -> None:
    monkeypatch.delenv("POKER_TABLE_BASE_URL")
    with pytest.raises(ValueError, match="POKER_TABLE_BASE_URL"):
        check("compat/good-model")


def test_a_server_of_your_own_needs_no_key(api: StandIn) -> None:
    check("compat/good-model")


def test_nothing_listening_is_found_before_the_first_hand(api, monkeypatch) -> None:
    monkeypatch.setenv("POKER_TABLE_BASE_URL", "http://127.0.0.1:9/v1")
    with pytest.raises(ValueError, match="cannot reach"):
        check("compat/good-model")


def test_a_model_with_no_prefix_is_told_about_prefixes(monkeypatch, tmp_path: Path) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="needs its prefix: openai/, groq/"):
        check("gpt-x")


# ----- what things cost --------------------------------------------------------------------


def test_only_openrouter_and_claude_can_be_priced() -> None:
    assert is_priced("claude-opus-5-5") and is_priced("openrouter/x") and is_priced("ollama/x")
    assert not is_priced("groq/x") and not is_priced("openai/x") and not is_priced("compat/x")


def test_a_reported_cost_is_used_and_nothing_is_invented(api, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    api.replies.append((200, completion(cost=0.0123), None))
    paid = LLMAgent("alice", "maniac", model="openrouter/good-model")
    assert paid.act(view_for()).meta["cost_usd"] == pytest.approx(0.0123)
    unknown = LLMAgent("alice", "maniac", model="groq/good-model")
    assert unknown.act(view_for()).meta["cost_usd"] == 0
    assert estimate_cost("groq/x", None) == 0


def test_a_budget_stops_a_provider_that_reports_its_cost(api, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    api.replies += [(200, completion(cost=0.5), None)] * 40
    path = tmp_path / "x.toml"
    path.write_text(
        'name = "x"\ncoach = false\nseats = ["llm:maniac@openrouter/good-model", "station"]\n'
        "hands = 200\nbudget_usd = 1\n"
    )
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    assert outcome.stopped == "budget" and outcome.hands < 200


def test_a_table_it_cannot_price_says_a_budget_will_not_help(api, monkeypatch, capsys) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    seats = "llm:maniac@groq/good-model,tag"
    assert main(["play", "-n", "1", "--seats", seats, "--budget", "1"]) == 0
    captured = capsys.readouterr()
    assert "does not know that provider's prices" in captured.err
    assert "--budget cannot limit it" in captured.err
    assert "cost not known here" in captured.out


def test_an_experiment_it_cannot_price_needs_no_budget_and_says_why(
    api, monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    path = tmp_path / "x.toml"
    path.write_text(
        'name = "x"\ncoach = false\nseats = ["llm:maniac@groq/good-model", "station"]\nhands = 2\n'
    )
    said: list[str] = []
    outcome = run_experiment(path, tmp_path / "out", log=said.append)
    assert outcome.hands == 2
    assert any("budget_usd cannot limit it" in line for line in said)
    report = (outcome.folder / "leaderboard.md").read_text()
    assert "Cost reads $0 for maniac" in report


def test_an_experiment_with_a_provider_that_reports_needs_a_budget(
    api, monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    path = tmp_path / "x.toml"
    path.write_text(
        'name = "x"\ncoach = false\nseats = ["llm:maniac@openrouter/good-model", "station"]\n'
        "hands = 2\n"
    )
    with pytest.raises(ValueError, match="set budget_usd"):
        run_experiment(path, tmp_path / "out", log=lambda _: None)


def test_a_trial_never_calls_an_unknown_cost_free(api, monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    path = tmp_path / "x.toml"
    path.write_text(
        'name = "x"\ncoach = false\nseats = ["llm:maniac@groq/good-model", "station"]\nhands = 50\n'
    )
    argv = ["experiment", "run", str(path), "-o", str(tmp_path / "out"), "--trial", "2"]
    assert main(argv) == 0
    printed = capsys.readouterr().out
    assert "for free" not in printed and "does not know what these models cost" in printed
    assert "for free" not in Projection(2, 8, 0.0, 10.0, None, priced=False).describe()


# ----- the coach, and what the agents command shows ----------------------------------------


def test_the_coach_notes_can_run_on_another_provider(api: StandIn, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    leak = Leak(
        tag="t", title="a leak", leaks=2, opportunities=4, by_position={}, by_street={}, examples=[]
    )
    report = Report(
        player="me", hands=10, decisions=4, net=0, big_blind=2, leaks=[leak], observations=[]
    )
    api.replies.append((200, completion(json.dumps({"notes": [], "focus": "fold more"})), None))
    assert narrate(report, model="groq/good-model").focus == "fold more"


def test_the_agents_command_shows_which_keys_are_set_without_showing_them(
    api, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    assert main(["agents"]) == 0
    printed = capsys.readouterr().out
    assert (
        "groq/" in printed and "GROQ_API_KEY set" in printed and "OPENAI_API_KEY not set" in printed
    )
    assert KEY not in printed


def test_the_schema_the_seat_asks_for_is_one_these_apis_can_read() -> None:
    assert ACTION_SCHEMA["additionalProperties"] is False
    assert sorted(ACTION_SCHEMA["required"]) == sorted(ACTION_SCHEMA["properties"])
