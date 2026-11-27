"""How nash:pushfold does in heads-up freezeouts, next to tag sitting in the same seat.

    uv run python scripts/measure_pushfold_freezeouts.py      # the table in docs/solver-seats.md
    uv run python scripts/measure_pushfold_freezeouts.py 20 --folder docs/results/freezeout-pushfold

The seat is tag plus a chart, so the comparison that isolates the chart is nash:pushfold against
a bot, next to tag in its place against the same bot, on the same tournaments. Tournament k of
both is seeded the way an experiment file with ``seed = SEED`` seeds its tournament k: the same
decks, the same draw for the first button and the same seed for the opponent (and for tag, which
is also the seat's fallback). So the two differ only in who answers in the seat, and a
tournament where the chart never played must come out the same in both, which is checked.

The default blind schedule (1/2 up to 800/1600+200, antes from level 4), twenty hands a level,
200 chips each: 100 big blinds at the start. For each opponent: the share of freezeouts won by
each, the paired difference with a 95% t interval, and how the seat's decisions were made.

``--folder`` checks a published results folder of nash:pushfold against tag: its tournaments
must be this script's first ones, with the same winners and the same number of hands.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from poker_table.agents.registry import make_agents
from poker_table.experiments import Experiment
from poker_table.history import read_jsonl
from poker_table.league import run_tournament, standings_of
from poker_table.margins import t_margin, wilson

SEED = 2026
OPPONENTS = ("tag", "rock", "maniac", "station")
TOURNAMENTS = 5000
ENTRANT = "entrant"


def load() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


def freezeout(seat: str, opponent: str, number: int) -> dict:
    """Tournament ``number`` with ``seat`` as the entrant: who won, and how the seat decided."""
    experiment = Experiment(
        name="measure", seats=(f"{ENTRANT}:{seat}", opponent), hands=0, seed=SEED, tournaments=1
    )
    agents = make_agents(experiment.seats, seed=experiment.tournament_seed(number))
    entrant = agents[0]
    seated = [agents[i] for i in experiment.seating(number)]
    reasons: Counter[str] = Counter()
    charts: Counter[str] = Counter()  # the chart's own and translated decisions, by ante

    def on_decision(record, hand) -> None:
        if hand.seats[record.seat].name != ENTRANT:
            return
        if record.illegal or record.reasoning.startswith("agent error"):
            raise SystemExit(f"{seat} broke in tournament {number}: {record.reasoning}")
        if record.meta.get("method") == "fallback":
            reasons[record.meta.get("off_chart", "?")] += 1
        elif record.meta.get("method") in ("own", "translated"):
            charts[record.meta.get("ante", "0")] += 1

    result = run_tournament(
        seated,
        experiment.tournament_config(number),
        on_decision=on_decision,
        keep_histories=False,
    )
    usage = getattr(entrant, "usage", None)
    return {
        "won": result.finishes[ENTRANT] == 1,
        "complete": result.complete,
        "hands": result.hands,
        "own": usage.own if usage else 0,
        "translated": usage.translated if usage else 0,
        "fallback": usage.fallback if usage else 0,
        "reasons": reasons,
        "charts": charts,
    }


def share(wins: int, n: int) -> str:
    low, high = wilson(wins, n)
    return f"{wins / n:.1%} ({low:.0%}-{high:.0%})"


def measure(opponent: str, count: int) -> tuple[str, str, list[dict]]:
    chart = [freezeout("nash:pushfold", opponent, k) for k in range(1, count + 1)]
    plain = [freezeout("tag", opponent, k) for k in range(1, count + 1)]
    for k, (a, b) in enumerate(zip(chart, plain, strict=True), start=1):
        if a["own"] + a["translated"] == 0 and (a["won"], a["hands"]) != (b["won"], b["hands"]):
            raise SystemExit(f"tournament {k} against {opponent}: no chart decision, other result")
    untouched = sum(a["own"] + a["translated"] == 0 for a in chart)
    differences = [int(a["won"]) - int(b["won"]) for a, b in zip(chart, plain, strict=True)]
    mean = statistics.fmean(differences)
    margin = t_margin(differences) or 0.0
    decisions = sum(a["own"] + a["translated"] + a["fallback"] for a in chart)
    own = sum(a["own"] for a in chart)
    translated = sum(a["translated"] for a in chart)
    unfinished = sum(not r["complete"] for r in chart + plain)
    row = (
        f"| {opponent} | {count} | {share(sum(a['won'] for a in chart), count)} "
        f"| {share(sum(b['won'] for b in plain), count)} "
        f"| {mean * 100:+.1f} ±{margin * 100:.1f} "
        f"| {own / decisions:.1%} | {translated / decisions:.1%} "
        f"| {statistics.median(a['hands'] for a in chart):g} |"
    )
    reasons: Counter[str] = Counter()
    for a in chart:
        reasons.update(a["reasons"])
    fallbacks = sum(reasons.values())
    charts: Counter[str] = Counter()
    for a in chart:
        charts.update(a["charts"])
    played = sum(charts.values())
    detail = (
        f"{opponent}: {decisions} decisions, {fallbacks} by the fallback ("
        + ", ".join(f"{why} {n / decisions:.1%}" for why, n in reasons.most_common())
        + f"), {played} from a chart ("
        + ", ".join(f"ante {key} {charts[key] / played:.0%}" for key in ("0", "1/10", "1/8"))
        + f"); the chart never played in {untouched} tournaments, and those came out the same "
        f"as tag's; {unfinished} unfinished"
    )
    return row, detail, chart


def check_folder(folder: Path, chart: list[dict]) -> None:
    standings = standings_of(read_jsonl(folder / "hands.jsonl"))
    for standing in standings:
        number = standing.number or 0
        if not 1 <= number <= len(chart):
            raise SystemExit(f"{folder}: tournament {number} is not one of this script's")
        if chart[number - 1]["won"] != (standing.places()[ENTRANT] == 1):
            raise SystemExit(f"{folder}: tournament {number} has another winner")
        if len(standing.hands) != chart[number - 1]["hands"]:
            raise SystemExit(f"{folder}: tournament {number} has a different number of hands")
    count = len(standings)
    print(f"{folder}: its {count} tournaments are this script's first, same winners, same lengths")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tournaments", type=int, nargs="?", default=TOURNAMENTS)
    parser.add_argument("--folder", type=Path, help="check a published folder against tag")
    args = parser.parse_args()
    started = time.monotonic()
    print(f"start: {load()}", file=sys.stderr)
    if args.folder:
        _, _, chart = measure("tag", args.tournaments)
        check_folder(args.folder, chart)
        return 0
    print(
        "| vs | freezeouts | nash:pushfold won | tag in its seat won | difference (points) "
        "| own | translated | median hands |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    details = []
    for opponent in OPPONENTS:
        row, detail, _ = measure(opponent, args.tournaments)
        print(row, flush=True)
        details.append(detail)
    print()
    for detail in details:
        print(f"- {detail}")
    took = (time.monotonic() - started) / 60
    print(f"took {took:.1f} min; end: {load()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
