"""One fixed test, so that seats can be compared: the same opponents, the same decks.

Anyone can run it on their own key and send in the folder, which is how the leaderboard gets numbers
for paid models without this project paying for them. Everyone sits in the same seat and is dealt
the same cards, so differences between entrants are not differences in luck of the deal.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from poker_table.experiments import load_experiment, seat_results
from poker_table.history import read_jsonl
from poker_table.honesty import honesty
from poker_table.margins import seat_margins
from poker_table.verify import CANNOT_PROVE, verify_folder

ENTRANT = "entrant"
OPPONENTS = ("tag", "rock", "maniac", "station")
HANDS = 200
SEED = 2026


def folder_name(seat: str) -> str:
    """``llm:nerd@groq/some-model:free`` to ``bench-nerd-groq-some-model-free``."""
    plain = re.sub(r"^llm:", "", seat.strip())
    return "bench-" + re.sub(r"[^A-Za-z0-9._-]+", "-", plain).strip("-.")


def benchmark_file(seat: str, budget_usd: float | None = None) -> str:
    """The experiment file for one entrant. Only the seat and the budget can differ."""
    seats = [f"{ENTRANT}:{seat.strip()}", *OPPONENTS]
    lines = [
        f"name = {json.dumps(folder_name(seat))}",
        f"description = {json.dumps(f'The benchmark: {seat.strip()} against the four bots.')}",
        f"seats = {json.dumps(seats)}",
        f"hands = {HANDS}",
        f"seed = {SEED}",
        "coach = false",
    ]
    if budget_usd is not None:
        lines.append(f"budget_usd = {budget_usd}")
    return "\n".join(lines) + "\n"


def benchmark_problems(folder: Path | str) -> list[str]:
    """What keeps a folder off the leaderboard: a false record, or not being the benchmark."""
    folder = Path(folder)
    problems = verify_folder(folder)
    if problems and not (folder / "experiment.toml").exists():
        return problems
    experiment = load_experiment(folder / "experiment.toml")
    if tuple(experiment.seats[1:]) != OPPONENTS or not experiment.seats[0].startswith(
        f"{ENTRANT}:"
    ):
        problems.append(f"the seats are not the benchmark's: {', '.join(experiment.seats)}")
    if (experiment.hands, experiment.seed, experiment.tables) != (HANDS, SEED, 1):
        problems.append(f"it is not {HANDS} hands from seed {SEED} on one table")
    if (experiment.small_blind, experiment.big_blind, experiment.ante, experiment.stack) != (
        1,
        2,
        0,
        200,
    ):
        problems.append("the stakes are not the benchmark's")
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    if run.get("stopped") != "finished":
        problems.append(f"it did not finish: {run.get('stopped')}")
    return problems


@dataclass(frozen=True, slots=True)
class Entry:
    entrant: str
    adjusted: float
    margin: float
    bb_per_100: float
    illegal: float
    failed: int
    seconds_per_call: float | None
    dollars_per_hand: float | None
    played_on: str
    named_wrongly: str  # "5 of 18": claims about its own cards, and how many were wrong


def entry_of(folder: Path | str) -> Entry:
    folder = Path(folder)
    histories = list(read_jsonl(folder / "hands.jsonl"))
    row = next(r for r in seat_results(histories) if r.name == ENTRANT)
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    return Entry(
        entrant=row.plays_as,
        adjusted=row.adjusted_bb_per_100,
        margin=row.adjusted_margin or 0.0,
        bb_per_100=row.bb_per_100,
        illegal=row.illegal / row.decisions if row.decisions else 0.0,
        failed=row.failed,
        seconds_per_call=row.seconds_per_call,
        dollars_per_hand=row.cost_usd / row.hands if row.cost_usd else None,
        played_on=str(run.get("last_saved", ""))[:10],
        named_wrongly=_named_wrongly(histories),
    )


def _named_wrongly(histories) -> str:
    said = honesty(histories).get(ENTRANT)
    return f"{said.wrong} of {said.claims}" if said and said.decisions else "-"


def render_leaderboard(folders: Sequence[Path | str]) -> str:
    entries = sorted((entry_of(f) for f in folders), key=lambda e: e.adjusted, reverse=True)
    lines = [
        "# Leaderboard",
        "",
        f"Every entrant plays the same {HANDS} hands from the same seat, against the same four "
        f"bots ({', '.join(OPPONENTS)}), so they all get the same cards.",
        "",
        "| entrant | all-in adj. bb/100 | ±95% | bb/100 | illegal | failed "
        "| named its cards wrongly | s/call | $/hand | played |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for e in entries:
        seconds = f"{e.seconds_per_call:.1f}" if e.seconds_per_call is not None else "-"
        dollars = f"{e.dollars_per_hand:.4f}" if e.dollars_per_hand is not None else "-"
        lines.append(
            f"| {e.entrant} | {e.adjusted:+.0f} | {e.margin:.0f} | {e.bb_per_100:+.0f} "
            f"| {e.illegal:.0%} | {e.failed} | {e.named_wrongly} | {seconds} | {dollars} "
            f"| {e.played_on} |"
        )
    lines += [
        "",
        "Read the intervals before the order. Two entrants whose intervals overlap may not be "
        f"different at all, and {HANDS} hands is a small sample: {HANDS * 4} would halve those "
        "intervals. $/hand is blank where the model is free, or where its provider does not "
        'report a price. "Named its cards wrongly" counts the times its reasoning claimed '
        "two cards it was not holding, out of the times it named its own cards at all.",
        "",
        "## Add yours",
        "",
        "It runs on your own key, or free on a local model. Nothing here pays for it.",
        "",
        "```sh",
        "poker-table benchmark llm:nerd@groq/<model> --trial 5   # what it would cost, first",
        "poker-table verify results/bench-nerd-groq-<model>        # check the folder",
        "```",
        "",
        "Copy the folder into `docs/results/`, run `uv run python scripts/build_leaderboard.py` "
        "and open a pull request. CI replays every hand from its deck seed.",
        "",
        f"{CANNOT_PROVE} The decks are public, so treat a row as a claim by whoever sent it. The "
        "reasoning for every decision is in its `hands.jsonl`, for anyone who wants to read it.",
        "",
        "Seats that only play heads-up have their own [heads-up track](leaderboard-heads-up.md).",
    ]
    return "\n".join(lines) + "\n"


# ----- the heads-up track ------------------------------------------------------------------

HEADS_UP_HANDS = 400  # the hands of the wildest match just fit the 2 MB limit


def heads_up_name(seat: str, opponent: str) -> str:
    """``cfr:blueprint`` against tag: ``hu-cfr-blueprint-vs-tag``."""
    return "hu-" + folder_name(seat).removeprefix("bench-") + f"-vs-{opponent}"


def heads_up_file(seat: str, opponent: str, budget_usd: float | None = None) -> str:
    """One match of the heads-up track: the entrant against one bot, on the track's decks."""
    seat = seat.strip()
    lines = [
        f"name = {json.dumps(heads_up_name(seat, opponent))}",
        f"description = {json.dumps(f'The heads-up track: {seat} against {opponent}.')}",
        f"seats = {json.dumps([f'{ENTRANT}:{seat}', opponent])}",
        f"hands = {HEADS_UP_HANDS}",
        f"seed = {SEED}",
        "coach = false",
    ]
    if budget_usd is not None:
        lines.append(f"budget_usd = {budget_usd}")
    return "\n".join(lines) + "\n"


def heads_up_problems(folder: Path | str) -> list[str]:
    """What keeps a folder off the heads-up leaderboard."""
    folder = Path(folder)
    problems = verify_folder(folder)
    if problems and not (folder / "experiment.toml").exists():
        return problems
    experiment = load_experiment(folder / "experiment.toml")
    seats = experiment.seats
    if len(seats) != 2 or not seats[0].startswith(f"{ENTRANT}:") or seats[1] not in OPPONENTS:
        problems.append(f"the seats are not a heads-up match: {', '.join(seats)}")
    if (experiment.hands, experiment.seed, experiment.tables) != (HEADS_UP_HANDS, SEED, 1):
        problems.append(f"it is not {HEADS_UP_HANDS} hands from seed {SEED} on one table")
    if (experiment.small_blind, experiment.big_blind, experiment.ante, experiment.stack) != (
        1,
        2,
        0,
        200,
    ):
        problems.append("the stakes are not the benchmark's")
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    if run.get("stopped") != "finished":
        problems.append(f"it did not finish: {run.get('stopped')}")
    return problems


@dataclass(frozen=True, slots=True)
class HeadsUpEntry:
    entrant: str
    adjusted: float
    margin: float
    bb_per_100: float
    against: dict[str, tuple[float, float]]  # opponent -> (all-in adj. bb/100, its ±95%)
    own_method: str  # how many decisions the seat's own method made, for research seats
    illegal: float
    failed: int
    played_on: str


# The scripted play underneath a seat: a research seat's fallback, or the plain tag play of an
# exploiter when no read changed the decision.
UNDERNEATH = ("fallback", "tag")


def _own_method(histories) -> str:
    methods = [
        d.meta["method"]
        for h in histories
        for d in h.decisions
        if h.players[d.seat].name == ENTRANT and "method" in d.meta
    ]
    if not methods:
        return "-"
    own = sum(m not in UNDERNEATH for m in methods)
    if 0 < own < len(methods):  # never round a mixed record up to 100% or down to 0%
        return f"{own / len(methods):.1%}"
    return f"{own / len(methods):.0%}"


def heads_up_entries(folders: Sequence[Path | str]) -> list[HeadsUpEntry]:
    by_entrant: dict[str, dict[str, Path]] = {}
    for folder in map(Path, folders):
        seats = load_experiment(folder / "experiment.toml").seats
        by_entrant.setdefault(seats[0].removeprefix(f"{ENTRANT}:"), {})[seats[1]] = folder
    entries = []
    for seat, matches in by_entrant.items():
        missing = [o for o in OPPONENTS if o not in matches]
        if missing:
            raise ValueError(f"{seat} has no heads-up match against {', '.join(missing)}")
        histories, against, runs = [], {}, []
        for opponent in OPPONENTS:
            played = list(read_jsonl(matches[opponent] / "hands.jsonl"))
            margin = seat_margins(played)[ENTRANT]
            against[opponent] = (margin.adjusted_bb_per_100, margin.adjusted_margin or 0.0)
            histories += played
            runs.append(json.loads((matches[opponent] / "run.json").read_text(encoding="utf-8")))
        row = next(r for r in seat_results(histories) if r.name == ENTRANT)
        entries.append(
            HeadsUpEntry(
                entrant=row.plays_as,
                adjusted=row.adjusted_bb_per_100,
                margin=row.adjusted_margin or 0.0,
                bb_per_100=row.bb_per_100,
                against=against,
                own_method=_own_method(histories),
                illegal=row.illegal / row.decisions if row.decisions else 0.0,
                failed=row.failed,
                played_on=max(str(r.get("last_saved", ""))[:10] for r in runs),
            )
        )
    return sorted(entries, key=lambda e: e.adjusted, reverse=True)


def render_heads_up_leaderboard(folders: Sequence[Path | str]) -> str:
    entries = heads_up_entries(folders)
    total = HEADS_UP_HANDS * len(OPPONENTS)
    lines = [
        "# Heads-up leaderboard",
        "",
        f"Every entrant plays {HEADS_UP_HANDS} hands heads-up against each of the four bots "
        f"({', '.join(OPPONENTS)}), {total:,} hands in all, from the same seat and the same decks, "
        "at 200 chips and blinds 1/2 (100 big blinds). Seats that only play heads-up, such as "
        "the [solver seats](solver-seats.md), are measured here.",
        "",
        "| entrant | all-in adj. bb/100 | ±95% | "
        + " | ".join(f"vs {o}" for o in OPPONENTS)
        + " | bb/100 | its own method | illegal | failed | played |",
        "|---|---:|---:|" + "---:|" * len(OPPONENTS) + "---:|---:|---:|---:|---|",
    ]
    for e in entries:
        cells = " | ".join(f"{adj:+.0f} ±{m:.0f}" for adj, m in e.against.values())
        lines.append(
            f"| {e.entrant} | {e.adjusted:+.0f} | {e.margin:.0f} | {cells} | {e.bb_per_100:+.0f} "
            f"| {e.own_method} | {e.illegal:.0%} | {e.failed} | {e.played_on} |"
        )
    lines += [
        "",
        "Read the intervals before the order: two rows whose intervals overlap may not be "
        f"different at all. The overall column pools all {total:,} hands; each match column is "
        f'its {HEADS_UP_HANDS} hands alone, so its interval is about twice as wide. "Its own '
        'method" is the share of decisions a research seat made with its own strategy rather '
        "than the scripted fallback it hands spots to, or, for exploiter, the share a read "
        "changed from tag's play; a dash means the seat has no such fallback. A row that "
        "reads 0% is the fallback's row.",
        "",
        "## Add yours",
        "",
        "```sh",
        "poker-table benchmark <seat> --heads-up -o results   # four matches, one folder each",
        "poker-table verify results/hu-*                       # check the folders",
        "```",
        "",
        "Copy the four folders into `docs/results/`, run "
        "`uv run python scripts/build_leaderboard.py` and open a pull request. CI replays every "
        f"hand from its deck seed. {CANNOT_PROVE}",
    ]
    return "\n".join(lines) + "\n"
