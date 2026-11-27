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
from typing import Any

from poker_table.engine import Hand, IllegalAction, Player
from poker_table.experiments import Experiment, load_experiment
from poker_table.history import HandHistory, parse_action, read_jsonl
from poker_table.league import TournamentConfig

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
    return _played_problem(history, deck_seed)


def _played_problem(history: HandHistory, deck_seed: int) -> str | None:
    """Deal the hand again from its seed, play the actions it records, and compare the rest."""
    label = f"hand {history.hand_id}"
    seats = sorted(history.players, key=lambda p: p.seat)
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
    if experiment.tournaments:
        return _freezeout_problems(experiment, run, histories)

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


def _freezeout_problems(
    experiment: Experiment, run: dict[str, Any], histories: Sequence[HandHistory]
) -> list[str]:
    """A folder of freezeouts, checked tournament by tournament from the settings alone."""
    problems = []
    if run.get("hands") != len(histories):
        problems.append(
            f"run.json says {run.get('hands')} hands, and the file has {len(histories)}"
        )
    by_number: dict[int, list[HandHistory]] = defaultdict(list)
    for history in histories:
        number = history.tournament.get("id")
        if not isinstance(number, int) or history.hand_id.partition("-")[0] != f"f{number}":
            problems.append(f"hand {history.hand_id}: not a hand id this run gives")
            continue
        by_number[number].append(history)
    numbers = sorted(by_number)
    if numbers != list(range(1, len(numbers) + 1)):
        problems.append(f"a tournament is missing: the file has {', '.join(map(str, numbers))}")
    seats: list[str] | None = None  # every seat's name, in the order the file lists the kinds
    over = 0
    for number in numbers:
        found, done, seats = _tournament_problems(experiment, number, by_number[number], seats)
        problems += found
        over += done
        if not found and not done and number != numbers[-1]:
            problems.append(f"tournament {number} stops partway, and another was played after it")
    planned = run.get("planned_tournaments", experiment.tournaments)
    if run.get("tournaments") != over:
        problems.append(
            f"run.json says {run.get('tournaments')} tournaments are over, and {over} are"
        )
    if run.get("stopped") == "finished" and over < planned:
        problems.append(f"it says it finished, with {over} of {planned} tournaments over")
    return problems


def _tournament_problems(
    experiment: Experiment, number: int, hands: Sequence[HandHistory], seats: list[str] | None
) -> tuple[list[str], bool, list[str] | None]:
    """What is wrong with one tournament, whether nothing is left to play in it, and the seats'
    names. Each hand is dealt again and checked against what run_tournament would have done
    with the hand before it. The first problem stops the check, since the rest then follows."""
    if [_hand_number(h) for h in hands] != list(range(1, len(hands) + 1)):
        return [f"tournament {number} is missing hands, or has them out of order"], False, seats
    config = experiment.tournament_config(number)
    order = experiment.seating(number)
    names = [p.name for p in sorted(hands[0].players, key=lambda p: p.seat)]
    if len(names) != len(order):
        found = f"hand {hands[0].hand_id}: {len(names)} seats, and the experiment has {len(order)}"
        return [found], False, seats
    if seats is None:  # the first tournament's draw says which name belongs to which kind
        seats = [""] * len(order)
        for at, index in enumerate(order):
            seats[index] = names[at]
    seating = [seats[index] for index in order]
    if names != seating:
        return [f"hand {hands[0].hand_id}: the seats are not as drawn for it"], False, seats

    decks = random.Random(config.seed)
    stacks = dict.fromkeys(seating, config.starting_stack)
    live = list(seating)
    button = 0  # an index into seating; it moves on even past a seat that is out
    for count, history in enumerate(hands):
        if len(live) < 2:
            return [f"hand {history.hand_id}: dealt after the tournament had a winner"], True, seats
        while seating[button] not in live:  # run_tournament's own rule
            button = (button + 1) % len(seating)
        problem = _freezeout_hand_problem(
            history, config, count, decks.randrange(2**63), stacks, live, seating[button]
        )
        if problem:
            return [problem], False, seats
        after = {p.name: p.stack + p.net for p in history.players}
        stacks.update(after)
        live = [name for name in live if after[name] > 0]
        button = (button + 1) % len(seating)
    if len(hands) > config.max_hands:
        return [f"tournament {number} goes past its {config.max_hands}-hand limit"], True, seats
    return [], len(live) == 1 or len(hands) == config.max_hands, seats


def _freezeout_hand_problem(
    history: HandHistory,
    config: TournamentConfig,
    count: int,
    deck_seed: int,
    stacks: dict[str, int],
    live: list[str],
    button: str,
) -> str | None:
    """One hand of a freezeout, against the stacks, the seats left and the button the hands
    before it leave."""
    label = f"hand {history.hand_id}"
    t = history.tournament
    if history.seed != deck_seed:
        return f"{label}: dealt from seed {history.seed}, and the tournament deals {deck_seed} here"
    players = sorted(history.players, key=lambda p: p.seat)
    if [p.name for p in players] != live:
        return f"{label}: the seats in it are not the ones still in"
    for player in players:
        if player.stack != stacks[player.name]:
            if count == 0:
                return (
                    f"{label}: {player.name} starts with other than {config.starting_stack} chips"
                )
            return (
                f"{label}: {player.name} starts with {player.stack} chips, and ended the hand "
                f"before with {stacks[player.name]}"
            )
    level = config.level(count)
    if (history.small_blind, history.big_blind, history.ante) != (
        level.small_blind,
        level.big_blind,
        level.ante,
    ):
        return f"{label}: the blinds are not level {config.level_number(count)}'s ({level})"
    if t.get("level") != config.level_number(count):
        return f"{label}: it says level {t.get('level')}, and it is played at level {config.level_number(count)}"  # noqa: E501
    if history.button != live.index(button):
        return (
            f"{label}: the button is on seat {history.button}, and belongs on "
            f"{live.index(button)}, {button}'s"
        )
    if t.get("left") != len(live):
        return f"{label}: it says {t.get('left')} were left, and {len(live)} were"
    problem = _played_problem(history, deck_seed)
    if problem:
        return problem
    # who went out, and in what place: the one that sat down with more chips finishes higher
    after = {p.name: p.stack + p.net for p in players}
    busted = sorted((name for name in live if after[name] == 0), key=lambda name: stacks[name])
    survivors = len(live) - len(busted)
    out = {name: survivors + len(busted) - offset for offset, name in enumerate(busted)}
    if t.get("out") != out:
        return f"{label}: it says {t.get('out')} went out, and the cards say {out}"
    return None


def verify_folders(folders: Sequence[Path | str]) -> dict[str, list[str]]:
    return {str(folder): verify_folder(folder) for folder in folders}
