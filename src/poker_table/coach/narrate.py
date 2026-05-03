"""Plain-language coaching notes from the report, written by Claude and checked against it.

The model only ever sees the leaks the math found and the hands that prove them; its
output is a structured note per leak, and any note that names a leak or a hand that is
not in the report is discarded, so it cannot invent a leak the facts do not support.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from poker_table.agents.llm import DEFAULT_MODEL, MessagesClient, estimate_cost
from poker_table.coach.report import Report

SYSTEM = """\
You are a no-limit hold'em coach reviewing one player's session. You receive a list of
recurring leaks that were found by comparing every decision with standard preflop charts
and with pot odds versus equity, each with example hands.

Write one short note per leak (60-120 words): what the player keeps doing, why it costs
money, and the one adjustment to make. Quote the example hand ids you rely on. Only discuss
the leaks you were given and only cite hands from their examples — never invent a hand,
a number or a leak. Be direct and specific; skip praise and disclaimers.
"""

NOTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "note": {"type": "string"},
                    "cited_hands": {"type": "array", "items": {"type": "string"}},
                    "one_thing": {"type": "string"},
                },
                "required": ["tag", "note", "cited_hands", "one_thing"],
                "additionalProperties": False,
            },
        },
        "focus": {"type": "string"},
    },
    "required": ["notes", "focus"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class Note:
    tag: str
    title: str
    note: str
    cited_hands: tuple[str, ...]
    one_thing: str


@dataclass(frozen=True, slots=True)
class Narration:
    notes: tuple[Note, ...]
    focus: str
    dropped: int  # notes the model produced that cited leaks or hands not in the report
    model: str
    cost_usd: float

    def render(self) -> str:
        lines = []
        for n in self.notes:
            lines.append(f"## {n.title}")
            lines.append(n.note)
            lines.append(f"Do this: {n.one_thing}")
            lines.append("Hands: " + ", ".join(f"#{h}" for h in n.cited_hands))
            lines.append("")
        if self.focus:
            lines.append(f"The one thing to work on: {self.focus}")
        return "\n".join(lines).rstrip()


def report_for_model(report: Report, *, top: int = 5, examples: int = 6) -> dict[str, Any]:
    """The slice of the report the model gets: leaks with examples, no raw fact dump."""
    return {
        "player": report.player,
        "hands": report.hands,
        "net_chips": report.net,
        "bb_per_100": round(report.bb_per_100, 1),
        "leaks": [
            {
                "tag": leak.tag,
                "title": leak.title,
                "count": f"{leak.leaks} of {leak.opportunities} ({leak.rate:.0%})",
                "by_position": {k: f"{n}/{d}" for k, (n, d) in leak.by_position.items() if n},
                "examples": [
                    {
                        "hand": f.hand_id,
                        "street": f.street,
                        "position": f.position,
                        "what": f.detail,
                        "pot": f.pot,
                        **({"equity": f.equity, "price": f.price} if f.equity is not None else {}),
                    }
                    for f in leak.examples[:examples]
                ],
            }
            for leak in report.leaks[:top]
        ],
        "observations": report.observations,
    }


def narrate(
    report: Report,
    *,
    client: MessagesClient | None = None,
    model: str = DEFAULT_MODEL,
    top: int = 5,
) -> Narration:
    if not report.leaks:
        return Narration((), "", 0, model, 0.0)
    if client is None:
        import anthropic

        client = anthropic.Anthropic()
    payload = report_for_model(report, top=top)
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": json.dumps(payload, indent=1)}],
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": NOTES_SCHEMA},
        },
    )
    cost = estimate_cost(model, getattr(response, "usage", None))
    stop = getattr(response, "stop_reason", None)
    if stop in ("refusal", "max_tokens"):
        return Narration((), "", 0, model, cost)
    text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "{}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return Narration((), "", 0, model, cost)

    allowed = {leak.tag: leak for leak in report.leaks[:top]}
    notes: list[Note] = []
    dropped = 0
    for raw in data.get("notes", []):
        tag = str(raw.get("tag", ""))
        leak = allowed.get(tag)
        if leak is None:
            dropped += 1
            continue
        proof = {f.hand_id for f in leak.examples}
        cited = tuple(str(h).lstrip("#") for h in raw.get("cited_hands", []))
        if not cited or any(h not in proof for h in cited):
            dropped += 1
            continue
        notes.append(
            Note(
                tag=tag,
                title=leak.title,
                note=str(raw.get("note", "")).strip(),
                cited_hands=cited,
                one_thing=str(raw.get("one_thing", "")).strip(),
            )
        )
    return Narration(tuple(notes), str(data.get("focus", "")).strip(), dropped, model, cost)
