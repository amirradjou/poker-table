"""A Laya decision model in a seat: one forward pass per decision, no tokens, no cost.

Laya (Convai Innovations, Apache-2.0) is a non-autoregressive *decision* model — an encoder
plus a decision head that scores a set of named options in a single pass and returns a
calibrated probability for each. Two properties make it a natural fit for a poker seat:

* the option set is assembled per request, so the legal actions of *this* decision can be the
  options — which also means this seat can never choose an illegal action;
* a decision costs no tokens and no money, so it is the cheap end of the table's cost curve.

It is **not** a chat model and it is **not zero-shot**: published base checkpoints score below
a majority-class baseline on typed decisions, so a base-checkpoint seat is a baseline to
measure, not a player to fear. `poker-table dataset` exports this table's own decisions in the
shape Laya fine-tunes on; see `docs/laya.md`.

Install the extra (`uv sync --extra laya`, or `pip install 'poker-table[laya]'`) to use it;
the core package does not depend on torch.
"""

from __future__ import annotations

import importlib.util
import time
from dataclasses import dataclass
from typing import Any, Protocol

from poker_table.agents.base import Decision, SeatView
from poker_table.agents.scripted import TightAggressive
from poker_table.cards import cards_str
from poker_table.engine import Action, ActionType, EventKind, Street

DEFAULT_MODEL = "convaiinnovations/laya"
DEFAULT_CONFIDENCE = 0.40  # below this the chart decides instead; see Usage.gated
QUESTION_ID = "action"
# Every option and instruction is phrased positively: Laya's published negation bug (#377)
# makes "do not ..." wording unreliable, and poker options are naturally positive anyway.
INSTRUCTIONS = (
    "You are a winning no-limit hold'em player. Pick the action that makes the most money "
    "over time from this exact spot."
)


def available() -> bool:
    """Is the optional dependency installed? (Checked without importing torch.)"""
    return importlib.util.find_spec("laya") is not None


INSTALL_HINT = (
    "the laya seat needs the optional dependency: "
    "uv sync --extra laya (or pip install 'poker-table[laya]')"
)


class LayaRunner(Protocol):
    """The slice of ``laya.Agent`` this seat uses, so tests can pass a fake."""

    def predict(self, state: str, questions: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class Usage:
    decisions: int = 0  # answered by the model
    gated: int = 0  # answered by the fallback policy because confidence was low
    errors: int = 0
    latency_ms_total: float = 0.0
    confidence_total: float = 0.0

    @property
    def avg_latency_ms(self) -> float | None:
        return None if not self.decisions else self.latency_ms_total / self.decisions

    @property
    def avg_confidence(self) -> float | None:
        return None if not self.decisions else self.confidence_total / self.decisions


# ----- what the model sees ---------------------------------------------------------------


def _street_lines(view: SeatView) -> list[str]:
    """This hand's public action, one line per street, in a few words each."""
    names = {p.seat: p.name for p in view.players}
    lines: list[str] = []
    current: list[str] = []
    label = "preflop"
    for e in view.events:
        if e.kind is EventKind.STREET:
            if current:
                lines.append(f"{label}: " + ", ".join(current))
            current, label = [], e.street.value
        elif e.kind is EventKind.ACTION and e.seat is not None:
            who = "you" if e.seat == view.seat else names.get(e.seat, "?")
            current.append(f"{who} {e.action}")
    if current:
        lines.append(f"{label}: " + ", ".join(current))
    return lines


def compact_state(view: SeatView) -> str:
    """The spot in a few short lines.

    Laya's usable input is a few hundred tokens, so this is deliberately much terser than
    ``SeatView.describe()`` — the facts a player needs and nothing else.
    """
    seats = len(view.players)
    street = "preflop" if view.street is Street.PREFLOP else view.street.value
    board = cards_str(view.board) or "none yet"
    price = f"to call {view.to_call}" if view.to_call else "no bet to you"
    odds = f", pot odds {view.pot / view.to_call:.1f}:1" if view.to_call else ""
    others = ", ".join(
        f"{p.name} {p.stack}" + (f" (in for {p.street_bet})" if p.street_bet else "")
        for p in view.players
        if p.seat != view.seat and not p.folded
    )
    lines = [
        f"{seats}-handed no-limit hold'em, blinds {view.small_blind}/{view.big_blind}.",
        f"You are in the {view.position} with {cards_str(view.hole)}, stack {view.stack}.",
        f"Street: {street}. Board: {board}. Pot {view.pot}, {price}{odds}.",
        f"Still in: {others or 'nobody'}.",
    ]
    lines += _street_lines(view)
    if view.talk:
        lines.append("Table talk: " + "; ".join(f'{who} said "{text}"' for who, text in view.talk))
    return "\n".join(lines)


def option_actions(view: SeatView) -> dict[str, Action]:
    """The legal actions as named options: fold, check/call, and a few raise sizes.

    Options are built from ``view.legal``, so whatever the model picks is legal by
    construction. Sizes are deduplicated and clamped, which keeps the set small (Laya is
    happiest well under twenty options).
    """
    legal = view.legal
    out: dict[str, Action] = {"fold": Action.fold()}
    if legal.can_check:
        out["check"] = Action.check()
    if legal.can_call:
        out[f"call {legal.call_amount}"] = Action.call()
    if legal.can_raise:
        verb = "bet" if legal.is_bet else "raise to"
        pot_after_call = view.pot + legal.call_amount
        wanted = {
            "small": legal.min_raise_to,
            "half the pot": view.current_bet + round(pot_after_call / 2),
            "the size of the pot": view.current_bet + pot_after_call,
            "all-in": legal.max_raise_to,
        }
        for _shape, raw in wanted.items():
            amount = max(legal.min_raise_to, min(raw, legal.max_raise_to))
            label = f"{verb} {amount}"
            if label not in out:
                out[label] = Action.bet(amount) if legal.is_bet else Action.raise_to(amount)
    return out


def option_criteria(view: SeatView, options: dict[str, Action]) -> dict[str, str]:
    """A short description per option — what the model actually reads for each choice."""
    legal = view.legal
    pot = view.pot
    criteria: dict[str, str] = {}
    for label, action in options.items():
        match action.type:
            case ActionType.FOLD:
                criteria[label] = f"give up the hand now and leave the {pot} in the pot"
            case ActionType.CHECK:
                criteria[label] = "check: stay in the hand and see the next card for free"
            case ActionType.CALL:
                criteria[label] = (
                    f"pay {legal.call_amount} to stay in a pot that then holds "
                    f"{pot + legal.call_amount}"
                )
            case ActionType.BET | ActionType.RAISE:
                after = pot + action.amount
                shape = (
                    "a small bet"
                    if action.amount <= pot * 0.4
                    else "a pot-sized bet"
                    if action.amount >= pot * 0.9
                    else "a medium bet"
                )
                if action.amount >= legal.max_raise_to:
                    shape = "every chip you have"
                criteria[label] = f"put in {action.amount} ({shape}), making the pot about {after}"
    return criteria


def build_question(view: SeatView) -> tuple[dict[str, Action], dict[str, Any]]:
    """The options and the Laya ``choice`` question for this decision."""
    options = option_actions(view)
    question = {
        QUESTION_ID: {
            "type": "choice",
            "instructions": INSTRUCTIONS,
            "criteria": option_criteria(view, options),
        }
    }
    return options, question


@dataclass(frozen=True, slots=True)
class Choice:
    """A Laya ``choice`` answer: the option, every option's probability, two confidences.

    ``confidence`` is the calibrated signal to gate on. ``answer_confidence`` is simply the
    winning option's probability, and the published ``act_probability`` is not usable as a
    gate at all, so this seat ignores it.
    """

    option: str
    probabilities: dict[str, float]
    confidence: float
    answer_confidence: float
    input_tokens: int = 0


def read_choice(result: dict[str, Any]) -> Choice:
    """Pull the choice out of a Laya prediction (``result["answers"][question_id]``)."""
    answer = (result.get("answers") or {}).get(QUESTION_ID)
    if not isinstance(answer, dict) or "choice" not in answer:
        raise ValueError(f"no {QUESTION_ID!r} choice in the Laya result: {sorted(result)}")
    probabilities = {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
    option = str(answer["choice"])
    return Choice(
        option=option,
        probabilities=probabilities,
        confidence=float(answer.get("confidence", probabilities.get(option, 0.0))),
        answer_confidence=float(answer.get("answer_confidence", probabilities.get(option, 0.0))),
        input_tokens=int((result.get("usage") or {}).get("input_tokens", 0)),
    )


# ----- the seat --------------------------------------------------------------------------


class LayaAgent:
    """A seat whose decisions come from a Laya checkpoint's ``choice`` primitive."""

    kind = "laya"

    def __init__(
        self,
        name: str = "laya",
        *,
        model: str = DEFAULT_MODEL,
        subfolder: str | None = None,
        device: str | None = None,
        client: LayaRunner | None = None,
        confidence: float = DEFAULT_CONFIDENCE,
        fallback: Any | None = None,
        seed: int = 0,
    ) -> None:
        self.name = name
        self.model = model
        self.subfolder = subfolder
        self.device = device
        self.confidence = confidence
        self.usage = Usage()
        self._client = client
        # When the model is not sure enough, the chart plays instead of a coin flip.
        self.fallback = fallback if fallback is not None else TightAggressive(name, seed=seed)

    @property
    def loadable(self) -> bool:
        """Can this seat actually run? A seat with an injected client always can."""
        return self._client is not None or available()

    @property
    def client(self) -> LayaRunner:
        if self._client is None:
            try:
                import laya  # imported lazily: the core package does not depend on torch
            except ImportError as exc:  # noqa: TRY003 - the install hint is the whole message
                raise ImportError(INSTALL_HINT) from exc
            self._client = laya.load(self.model, device=self.device, subfolder=self.subfolder)
        return self._client

    def act(self, view: SeatView) -> Decision:
        options, question = build_question(view)
        state = compact_state(view)
        started = time.perf_counter()
        try:
            choice = read_choice(self.client.predict(state, question))
        except Exception as exc:  # noqa: BLE001 - a broken seat must not stop the table
            self.usage.errors += 1
            return self._fallback(view, f"laya error: {type(exc).__name__}: {exc}")
        elapsed = (time.perf_counter() - started) * 1000
        action = options.get(choice.option)
        if action is None:  # an answer outside the option set is the model's error, not ours
            self.usage.errors += 1
            return self._fallback(view, f"laya answered {choice.option!r}, not an option")

        meta: dict[str, Any] = {
            "model": f"laya:{self.subfolder or 'english'}",
            "latency_ms": round(elapsed, 1),
            "cost_usd": 0.0,
            "input_tokens": choice.input_tokens,
            "output_tokens": 0,
            "confidence": round(choice.confidence, 4),
            "answer_confidence": round(choice.answer_confidence, 4),
            "probabilities": {k: round(v, 4) for k, v in choice.probabilities.items()},
        }
        if choice.confidence < self.confidence:
            self.usage.gated += 1
            why = (
                f"{choice.option} at {choice.answer_confidence:.0%} but only "
                f"{choice.confidence:.0%} sure, under {self.confidence:.0%}: chart instead"
            )
            return self._fallback(view, why, meta | {"gated": True})

        self.usage.decisions += 1
        self.usage.latency_ms_total += elapsed
        self.usage.confidence_total += choice.confidence
        return Decision(
            action,
            reasoning=f"{choice.option} at {choice.answer_confidence:.0%} "
            f"(confidence {choice.confidence:.0%})",
            meta=meta,
        )

    def _fallback(self, view: SeatView, why: str, meta: dict[str, Any] | None = None) -> Decision:
        decision = self.fallback.act(view)
        return Decision(
            decision.action,
            reasoning=f"{why} — {decision.reasoning}",
            meta=meta or {"model": f"laya:{self.subfolder or 'english'}", "cost_usd": 0.0},
        )
