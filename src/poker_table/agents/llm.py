"""An LLM in a seat: one structured-output call per decision, with cost accounting."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

from poker_table.agents.base import Decision, SeatView
from poker_table.agents.personalities import Personality, get_personality
from poker_table.agents.strength import chen_score
from poker_table.engine import Action, ActionType, Street

DEFAULT_MODEL = "claude-opus-5"

# USD per million tokens (input, output); cache reads cost 10%, cache writes 125% of input.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

RULES = """\
You are playing no-limit Texas hold'em for play money against other bots and, sometimes, humans.
Each turn you get the state of the hand as seen from your seat and the list of legal actions.

Interface rules:
- Reply with exactly one action from the legal list.
- Amounts for bet/raise are the TOTAL you will have put in on this street ("raise to 12"),
  and must lie inside the legal range given. Use 0 for fold/check/call.
- "reasoning" is private. Think about ranges, position, pot odds, stack depth and what the
  action so far says about each opponent. Keep it under 80 words.
- "table_talk" is shown to every other seat. Leave it empty unless you have something to say
  in character. You may bluff in words as well as in chips; nobody has to believe you.
- Illegal actions are converted to a check or a fold and counted against you.
"""

ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["fold", "check", "call", "bet", "raise"]},
        "amount": {"type": "integer"},
        "table_talk": {"type": "string"},
        "reasoning": {"type": "string"},
    },
    "required": ["action", "amount", "table_talk", "reasoning"],
    "additionalProperties": False,
}


class MessagesClient(Protocol):
    """The slice of ``anthropic.Anthropic`` we use, so tests can pass a fake."""

    messages: Any


@dataclass(slots=True)
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    errors: int = 0
    gated: int = 0  # decisions answered without a model call


def estimate_cost(model: str, usage: Any) -> float:
    price_in, price_out = PRICES.get(model, PRICES[DEFAULT_MODEL])
    uncached = getattr(usage, "input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    output = getattr(usage, "output_tokens", 0) or 0
    return (
        uncached * price_in
        + cache_read * price_in * 0.1
        + cache_write * price_in * 1.25
        + output * price_out
    ) / 1_000_000


class LLMAgent:
    """A seat driven by a Claude model with a personality."""

    def __init__(
        self,
        name: str,
        personality: Personality | str,
        *,
        model: str = DEFAULT_MODEL,
        client: MessagesClient | None = None,
        max_tokens: int = 8000,
    ) -> None:
        self.name = name
        self.personality = (
            get_personality(personality) if isinstance(personality, str) else personality
        )
        self.model = model
        self.max_tokens = max_tokens
        self.usage = Usage()
        self._client = client

    @property
    def client(self) -> MessagesClient:
        if self._client is None:
            import anthropic  # imported lazily so scripted-only sessions never need a key

            self._client = anthropic.Anthropic()
        return self._client

    @property
    def system_prompt(self) -> str:
        talk = "" if self.personality.talkative else "\nYou almost never use table_talk."
        return f"{RULES}\nYour personality: {self.personality.prompt}{talk}\nYou are {self.name}."

    # -- deciding --

    def act(self, view: SeatView) -> Decision:
        gated = self._gate(view)
        if gated is not None:
            self.usage.gated += 1
            return gated
        started = time.perf_counter()
        try:
            response = self._call(view)
        except Exception as exc:  # noqa: BLE001 - any API failure degrades to a safe action
            self.usage.errors += 1
            return self._fallback(view, f"api error: {type(exc).__name__}: {exc}")
        elapsed = (time.perf_counter() - started) * 1000
        return self._interpret(view, response, elapsed)

    def _gate(self, view: SeatView) -> Decision | None:
        threshold = self.personality.auto_fold_below
        if threshold is None or view.street is not Street.PREFLOP:
            return None
        facing_raise = view.current_bet > view.big_blind and view.to_call > 0
        if not facing_raise:
            return None
        score = chen_score(view.hole)
        if score >= threshold:
            return None
        return Decision(
            Action.fold(),
            reasoning=f"auto-fold: junk facing a raise (Chen {score:g} < {threshold:g})",
        )

    def _call(self, view: SeatView) -> Any:
        return self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": self._prompt(view)}],
            output_config={
                "effort": self.personality.effort,
                "format": {"type": "json_schema", "schema": ACTION_SCHEMA},
            },
        )

    def _prompt(self, view: SeatView) -> str:
        lines = [view.describe()]
        if view.talk:
            lines.append("Table talk this hand:")
            lines += [f'  {who}: "{text}"' for who, text in view.talk]
        lines.append("Decide now.")
        return "\n".join(lines)

    def _interpret(self, view: SeatView, response: Any, elapsed_ms: float) -> Decision:
        usage = getattr(response, "usage", None)
        meta = {
            "model": self.model,
            "latency_ms": round(elapsed_ms, 1),
        }
        if usage is not None:
            cost = estimate_cost(self.model, usage)
            self.usage.calls += 1
            self.usage.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.usage.output_tokens += getattr(usage, "output_tokens", 0) or 0
            self.usage.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
            self.usage.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
            self.usage.cost_usd += cost
            meta |= {
                "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
                "cost_usd": round(cost, 6),
            }
        stop = getattr(response, "stop_reason", None)
        if stop in ("refusal", "max_tokens"):
            self.usage.errors += 1
            return self._fallback(view, f"model stopped early ({stop})", meta)
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            self.usage.errors += 1
            return self._fallback(view, f"unparseable reply: {text[:80]!r}", meta)
        try:
            kind = ActionType(str(data.get("action", "")).lower())
        except ValueError:
            self.usage.errors += 1
            return self._fallback(view, f"unknown action {data.get('action')!r}", meta)
        amount = int(data.get("amount") or 0)
        action = Action(kind, amount if kind in (ActionType.BET, ActionType.RAISE) else 0)
        return Decision(
            action,
            reasoning=str(data.get("reasoning", "")).strip(),
            table_talk=str(data.get("table_talk", "")).strip(),
            meta=meta,
        )

    def _fallback(self, view: SeatView, why: str, meta: dict[str, Any] | None = None) -> Decision:
        action = Action.check() if view.legal.can_check else Action.fold()
        return Decision(action, reasoning=why, meta=meta or {"model": self.model})
