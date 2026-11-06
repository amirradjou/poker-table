"""Is a results folder what it says it is? Every hand is dealt again from its seed and played again.

This catches a file that was edited or cut short: different cards, a pot paid to the wrong seat, a
result that does not add up, an illegal move, a missing hand. It cannot show who made the decisions.
A results folder is a claim about a model, and a person could play those decks and say otherwise.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from poker_table.engine import Hand, IllegalAction, Player
from poker_table.experiments import Experiment, load_experiment
from poker_table.history import HandHistory, parse_action, read_jsonl

CANNOT_PROVE = (
    "This shows the hands are legal and the decks and results are true. It cannot show who "
    "made the decisions."
)


def _hand_number(history: HandHistory) -> int:
    """Hand ``t2-17`` is the 17th at its table, and the count starts at 1."""
    return int(history.hand_id.rpartition("-")[2])


def _replay_problem(history: HandHistory, experiment: Experiment, deck_seed: int) -> str | None:
    label = f"hand {history.hand_id}"
    if history.seed != deck_seed:
        return f"{label}: dealt from seed {history.seed}, and the league deals {deck_seed} here"
    seats = sorted(history.players, key=lambda p: p.seat)
    expected_button = (_hand_number(history) - 1) % len(seats)
    if history.button != expected_button:
        return f"{label}: the button is on seat {history.button}, and belongs on {expected_button}"
    if any(p.stack != experiment.stack for p in seats):
        return f"{label}: a seat started with other than {experiment.stack} chips"
    if (history.small_blind, history.big_blind, history.ante) != (
        experiment.small_blind,
        experiment.big_blind,
        experiment.ante,
    ):
        return f"{label}: the stakes are not the league's"
    hand = Hand(
        [Player(p.name, p.stack) for p in seats],
        button=history.button,
        small_blind=history.small_blind,
        big_blind=history.big_blind,
        ante=history.ante,
        seed=deck_seed,
        hand_id=history.hand_id,
    )
    for decision in history.decisions:
        if hand.finished:
            return f"{label}: decisions go on after the hand ended"
        actor = hand.actor
        if actor is None or actor.index != decision.seat:
            return f"{label}: seat {decision.seat} acts out of turn"
        try:
            hand.apply(parse_action(decision.applied))
        except (IllegalAction, ValueError) as exc:
            return f"{label}: {decision.applied!r} is not legal there ({exc})"
    if not hand.finished:
        return f"{label}: it stops before the hand is over"
    for player, seat in zip(seats, hand.seats, strict=True):
        if player.hole != [str(card) for card in seat.hole]:
            return f"{label}: {player.name} holds cards that deck does not deal"
        if player.net != seat.stack - player.stack:
            return f"{label}: {player.name}'s result is {player.net}, and should be {seat.stack - player.stack}"  # noqa: E501
    if history.board != [str(card) for card in hand.board]:
        return f"{label}: the board is not what that deck deals"
    if dict(history.payouts) != dict(hand.payouts):
        return f"{label}: the pot was paid wrongly"
    return None


def verify_folder(folder: Path | str) -> list[str]:
    """What is wrong with a results folder; nothing means it checks out."""
    folder = Path(folder)
    hands_file, experiment_file, run_file = (
        folder / name for name in ("hands.jsonl", "experiment.toml", "run.json")
    )
    missing = [p.name for p in (hands_file, experiment_file, run_file) if not p.exists()]
    if missing:
        return [f"{folder} has no {', '.join(missing)}"]
    experiment = load_experiment(experiment_file)
    run = json.loads(run_file.read_text(encoding="utf-8"))
    histories = list(read_jsonl(hands_file))

    problems = []
    if run.get("hands") != len(histories):
        problems.append(
            f"run.json says {run.get('hands')} hands, and the file has {len(histories)}"
        )
    planned = run.get("planned_hands", experiment.hands)
    if run.get("stopped") == "finished" and len(histories) != planned:
        problems.append(f"it says it finished, with {len(histories)} of {planned} hands")

    by_table: dict[int, list[HandHistory]] = defaultdict(list)
    for history in histories:
        try:
            by_table[experiment.table_of(history)].append(history)
        except (ValueError, IndexError):
            problems.append(f"hand {history.hand_id}: not a hand id this league gives")
    for table, hands in sorted(by_table.items()):
        numbers = [_hand_number(h) for h in hands]
        if numbers != list(range(1, len(numbers) + 1)):
            problems.append(f"table {table + 1} is missing hands, or has them out of order")
            continue
        decks = random.Random(experiment.table_seed(table))
        for history in hands:
            problem = _replay_problem(history, experiment, decks.randrange(2**63))
            if problem:
                problems.append(problem)
    return problems


def verify_folders(folders: Sequence[Path | str]) -> dict[str, list[str]]:
    return {str(folder): verify_folder(folder) for folder in folders}
