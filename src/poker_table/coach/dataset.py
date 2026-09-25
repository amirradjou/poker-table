"""Export decisions as labelled training data — the table as a label factory.

A Laya (or any other) decision model needs labelled examples: a state, the options that were
available, and which option was right. This table produces both kinds for free:

* ``coach`` — every spot the coach flagged, answered by the charts and the pot-odds maths
  (:func:`poker_table.coach.drills.accepted_actions`). These are *corrections*: the label is
  what should have been done, not what was.
* ``policy`` — every decision a chosen seat made. These are *imitations*: the label is what
  that policy did, which is only as good as the policy (``tag`` is the sane choice).

Each sample is written in the shape a ``choice`` question takes, so a fine-tune sees exactly
the state and option set the seat will see at the table.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from poker_table.agents.base import SeatView
from poker_table.agents.laya import INSTRUCTIONS, build_question, compact_state
from poker_table.coach.drills import accepted_actions
from poker_table.coach.facts import Fact, replay
from poker_table.engine import Action, ActionType
from poker_table.history import HandHistory

# When several actions are acceptable, label the least committal one — except when only a
# raise is right, where the smallest (the standard open or the min-raise) is the label.
_PREFERENCE = (ActionType.CHECK, ActionType.CALL, ActionType.FOLD)


@dataclass(frozen=True, slots=True)
class Sample:
    state: str
    instructions: str
    options: dict[str, str]
    answer: str
    source: str  # "coach" (what was right) or "policy" (what a seat did)
    tag: str  # the coach's leak tag, or the policy's name
    hand_id: str
    seat: int
    street: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))


def _label_for(options: dict[str, Action], action: Action) -> str | None:
    """The option label for an action, allowing the nearest size of the same kind."""
    for label, candidate in options.items():
        if candidate == action:
            return label
    same_kind = {
        label: candidate.amount
        for label, candidate in options.items()
        if candidate.type is action.type
    }
    if not same_kind:
        return None
    return min(same_kind, key=lambda label: abs(same_kind[label] - action.amount))


def _label_for_accepted(options: dict[str, Action], accepted: frozenset[ActionType]) -> str | None:
    """The option label that answers an ``accepted_actions`` set."""
    for kind in _PREFERENCE:
        if kind in accepted:
            for label, action in options.items():
                if action.type is kind:
                    return label
    raises = {
        label: action.amount
        for label, action in options.items()
        if action.type in (ActionType.BET, ActionType.RAISE) and action.type in accepted
    }
    return min(raises, key=lambda label: raises[label]) if raises else None


def _sample(view: SeatView, answer: str, source: str, tag: str) -> Sample:
    question = build_question(view)[1]
    return Sample(
        state=compact_state(view),
        instructions=INSTRUCTIONS,
        options=question["action"]["criteria"],
        answer=answer,
        source=source,
        tag=tag,
        hand_id=view.hand_id,
        seat=view.seat,
        street=view.street.value,
    )


def from_coach(histories: Sequence[HandHistory], facts: Iterable[Fact]) -> Iterator[Sample]:
    """One sample per flagged spot, answered by the charts and the maths."""
    by_id = {h.hand_id: h for h in histories}
    wanted = [f for f in facts if f.ok is False]
    for fact in wanted:
        history = by_id.get(fact.hand_id)
        if history is None:
            continue
        for view, action in replay(history):
            if (
                view.seat == fact.seat
                and view.street.value == fact.street
                and str(action) == fact.action
            ):
                accepted = accepted_actions(fact, view)
                options = build_question(view)[0]
                label = _label_for_accepted(options, accepted) if accepted else None
                if label is not None:
                    yield _sample(view, label, "coach", fact.tag)
                break


def from_policy(histories: Sequence[HandHistory], player: str) -> Iterator[Sample]:
    """One sample per decision the named seat made, labelled with what it did."""
    for history in histories:
        seat_of = {p.name: p.seat for p in history.players}
        if player not in seat_of:
            continue
        me = seat_of[player]
        for view, action in replay(history):
            if view.seat != me:
                continue
            options = build_question(view)[0]
            label = _label_for(options, action)
            if label is not None:
                yield _sample(view, label, "policy", player)


def write_jsonl(path: Path | str, samples: Iterable[Sample]) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as fh:
        for sample in samples:
            fh.write(sample.to_json() + "\n")
            count += 1
    return count


def summarize(samples: Sequence[Sample]) -> str:
    """A line per source and answer, so an export can be eyeballed before training on it."""
    if not samples:
        return "no samples"
    by_source: dict[str, int] = {}
    by_answer: dict[str, int] = {}
    for s in samples:
        by_source[s.source] = by_source.get(s.source, 0) + 1
        kind = s.answer.split(" ")[0]
        by_answer[kind] = by_answer.get(kind, 0) + 1
    sources = ", ".join(f"{k} {v}" for k, v in sorted(by_source.items()))
    answers = ", ".join(f"{k} {v} ({v / len(samples):.0%})" for k, v in sorted(by_answer.items()))
    return f"{len(samples)} samples · {sources}\nanswers: {answers}"
