"""One of our seats against Slumbot, through Slumbot's free public API. A benchmark, not a seat.

    uv run python scripts/bench_slumbot.py tag --hands 2000 --out docs/results-slumbot/tag
    uv run python scripts/bench_slumbot.py tag --hands 2000 --out docs/results-slumbot/tag --resume
    uv run python scripts/bench_slumbot.py --report docs/results-slumbot/*/

Slumbot's server deals, at its own stakes: blinds 50/100 and 20,000 chips (200 big blinds), reset
every hand. A session starts in the big blind and the button moves every hand. The numbers count
the same number of hands from each position, so a seat has the button in exactly half of them.
A hand only waits for its other half when a run stops, or a hand is refused, at the wrong moment.
Then the first hands of each position count and the rest are kept but left out. Which hands
count depends on the position alone, never on the cards or the result.

The folder gets, as hands finish:

- ``hands-NNN.jsonl``: our side of each hand as a hand history (``poker-table serve`` replays it);
- ``api-NNN.jsonl``: per hand, every request and answer with the token taken out, our position,
  Slumbot's reported winnings and its baseline, or why the hand was refused;
- ``run.json``: the seat, the target and each run's times, requests and why it stopped.

New files start before one would pass 1.9 MB. ``--resume`` carries on from the files and shows
a seat that learns (has ``observe``) every recorded hand first, from its own chair.

Slumbot's side is not ours to seed, so a rerun repeats the procedure, not the numbers. Its
"baseline" per hand is our result minus what Slumbot would have won with our cards in our seat,
playing itself: Slumbot's own variance reduction, which we report but cannot check.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from poker_table.agents.base import seating_notes, seating_problems
from poker_table.agents.registry import make_agent
from poker_table.history import HandHistory
from poker_table.league import show_hand
from poker_table.margins import all_in_early, margin_of_error, seat_margins
from poker_table.slumbot import BIG, BIG_BLIND, NAME, OURS, SHAPE, SMALL, Api, play_hand

SEED = 2026
MAX_BYTES = 1_900_000  # each file stays under 2 MB, the most the repo takes for a hand file
REFUSED_IN_A_ROW = 3  # this many refused hands in a row stop a run


def _say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


# ----- the folder -------------------------------------------------------------------------


def _lines(folder: Path, prefix: str) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(folder.glob(f"{prefix}-*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            rows += [json.loads(line) for line in fh if line.strip()]
    return rows


def _append(folder: Path, prefix: str, row: dict[str, Any]) -> None:
    line = json.dumps(row, separators=(",", ":")) + "\n"
    files = sorted(folder.glob(f"{prefix}-*.jsonl"))
    path = files[-1] if files else folder / f"{prefix}-001.jsonl"
    if path.exists() and path.stat().st_size + len(line.encode()) > MAX_BYTES:
        path = folder / f"{prefix}-{len(files) + 1:03d}.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


def _write_run(folder: Path, meta: dict[str, Any]) -> None:
    path = folder / "run.json"
    part = path.with_suffix(".json.part")
    part.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    part.replace(path)


def read_folder(folder: Path | str) -> tuple[list[dict[str, Any]], dict[str, HandHistory]]:
    """Every hand's API record, in the order played, and the histories by hand id."""
    folder = Path(folder)
    histories = {h["hand_id"]: HandHistory.from_dict(h) for h in _lines(folder, "hands")}
    return _lines(folder, "api"), histories


def counted(records: Sequence[dict[str, Any]], histories: dict[str, HandHistory]) -> list[str]:
    """The hands the numbers count, in order: as many from the big blind as from the button.

    Each position counts its first hands, up to the smaller of the two counts. The choice looks
    at nothing but the position.
    """
    played = [r for r in records if not r["refused"] and r["hand"] in histories]
    per = min(sum(r["client_pos"] == p for r in played) for p in (BIG, SMALL))
    taken = {BIG: 0, SMALL: 0}
    ids = []
    for r in played:
        if taken[r["client_pos"]] < per:
            taken[r["client_pos"]] += 1
            ids.append(r["hand"])
    return ids


# ----- a run ------------------------------------------------------------------------------


def run(
    seat: str,
    hands: int,
    out: Path | str,
    *,
    resume: bool = False,
    api: Api | None = None,
    seed: int = SEED,
    say: Callable[[str], None] = _say,
) -> int:
    """Play until ``hands`` hands count, half from each position. 0 when that is reached."""
    if hands <= 0 or hands % 2:
        raise SystemExit(
            f"--hands is {hands}: it must be even, because Slumbot gives our seat the button "
            "every other hand and the numbers count as many hands from each position"
        )
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    records, histories = read_folder(folder)
    meta_path = folder / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    if (records or meta) and not resume:
        raise SystemExit(f"{folder} already has hands: add --resume to carry on, or pick a new one")
    if meta and meta.get("seat") != seat:
        raise SystemExit(f"{folder} holds {meta.get('seat')}'s hands, not {seat}'s")
    agent = make_agent(seat, seed=seed + len(records), taken=[NAME])
    problems = seating_problems([agent], SHAPE)
    if problems:
        raise SystemExit(f"{problems[0]}. Slumbot plays 200 big blinds deep, at 50/100.")
    for note in seating_notes([agent], SHAPE):
        say(f"note: {note}")
    for history in (histories[i] for i in (r["hand"] for r in records) if i in histories):
        show_hand([agent], history)  # a seat that learns starts from what it saw before

    api = api or Api()
    number = len(meta.get("runs", [])) + 1
    entry: dict[str, Any] = {
        "run": number,
        "seed": seed + len(records),
        "started": _now(),
        "ended": "",
        "seconds": 0.0,
        "hands": 0,
        "refused": 0,
        "requests": 0,
        "stopped": "running",
    }
    meta = {
        "seat": seat,
        "kind": getattr(agent, "kind", ""),
        "against": "Slumbot, through its public API (https://www.slumbot.com/)",
        "stakes": "blinds 50/100, 20,000 chips (200 big blinds), reset every hand",
        "hands": hands,
        "runs": [*meta.get("runs", []), entry],
    }
    _write_run(folder, meta)
    next_id = max((int(r["hand"]) for r in records), default=0) + 1
    started = time.monotonic()
    in_a_row = 0
    in_hand = False
    stopped = "finished"
    say(f"{seat} against Slumbot, run {number}: until {hands:,} hands count, into {folder}")
    try:
        while len(counted(records, histories)) < hands:
            in_hand = True
            played = play_hand(agent, api, str(next_id))
            in_hand = False
            next_id += 1
            record = played.record(number)
            _append(folder, "api", record)  # first, so no history is ever without its record
            records.append(record)
            entry["hands"] += 1
            if played.history is not None:
                _append(folder, "hands", played.history.to_dict())
                histories[played.hand_id] = played.history
                show_hand([agent], played.history)
            if played.refused:
                entry["refused"] += 1
                in_a_row += 1
                say(f"hand {played.hand_id} refused: {played.refused}")
                if played.stop or in_a_row >= REFUSED_IN_A_ROW:
                    stopped = f"stopped after hand {played.hand_id}: {played.refused}"
                    break
            else:
                in_a_row = 0
            entry.update(requests=api.requests, seconds=round(time.monotonic() - started, 1))
            _write_run(folder, meta)
            if entry["hands"] % 100 == 0:
                say(f"{entry['hands']:,} hands, {api.requests:,} requests, {_elapsed(started)}")
    except KeyboardInterrupt:
        stopped = "interrupted"
        if in_hand and api.log:  # keep the unfinished hand, refused, so every request is on file
            _append(
                folder,
                "api",
                {
                    "hand": str(next_id),
                    "run": number,
                    "client_pos": None,
                    "refused": "the run was interrupted in the middle of the hand",
                    "winnings": None,
                    "baseline_winnings": None,
                    "exchanges": api.log,
                },
            )
            entry["hands"] += 1
            entry["refused"] += 1
    finally:
        entry.update(
            ended=_now(),
            seconds=round(time.monotonic() - started, 1),
            requests=api.requests,
            stopped=stopped,
        )
        _write_run(folder, meta)
    say(f"{stopped}: {entry['hands']:,} hands, {api.requests:,} requests, {_elapsed(started)}")
    print(render([summarise(folder)]))
    return 0 if stopped == "finished" else 1


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _elapsed(started: float) -> str:
    return f"{(time.monotonic() - started) / 60:.1f} min"


# ----- the report -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Summary:
    seat: str
    hands: int  # counted
    played: int  # every hand played to its end, counted or not
    refused: int
    bb_per_100: float
    margin: float | None
    adjusted: float
    adjusted_margin: float | None
    all_in: int  # counted hands that were all in before the last card
    baseline: float | None  # Slumbot's own baseline, bb/100
    baseline_margin: float | None
    requests: int
    minutes: float
    played_on: str


def summarise(folder: Path | str) -> Summary:
    folder = Path(folder)
    records, histories = read_folder(folder)
    meta = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    ids = counted(records, histories)
    if not ids:
        raise SystemExit(f"{folder} has no counted hands yet")
    chosen = [histories[i] for i in ids]
    by_id = {r["hand"]: r for r in records}
    for history in chosen:  # every counted hand was checked as it was played; check again
        if history.players[OURS].net != by_id[history.hand_id]["winnings"]:
            raise SystemExit(f"hand {history.hand_id}: our net and Slumbot's winnings differ")
    margin = seat_margins(chosen)[chosen[0].players[OURS].name]
    baselines = [by_id[i]["baseline_winnings"] for i in ids]
    known = [b / BIG_BLIND for b in baselines if b is not None]
    runs = meta.get("runs", [])
    return Summary(
        seat=meta["seat"],
        hands=len(ids),
        played=sum(1 for r in records if not r["refused"] and r["hand"] in histories),
        refused=sum(1 for r in records if r["refused"]),
        bb_per_100=margin.bb_per_100,
        margin=margin.margin,
        adjusted=margin.adjusted_bb_per_100,
        adjusted_margin=margin.adjusted_margin,
        all_in=sum(all_in_early(h) for h in chosen),
        baseline=statistics.fmean(known) * 100 if len(known) == len(ids) else None,
        baseline_margin=margin_of_error(known) if len(known) == len(ids) else None,
        requests=sum(r.get("requests", 0) for r in runs),
        minutes=sum(r.get("seconds", 0.0) for r in runs) / 60,
        played_on=max((str(r.get("ended") or r.get("started", ""))[:10] for r in runs), default=""),
    )


def _cell(value: float | None, margin: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.0f} ±{margin:.0f}" if margin is not None else f"{value:+.0f}"


def render(summaries: Sequence[Summary]) -> str:
    lines = [
        "| our seat | hands | bb/100 | all-in adj. bb/100 | Slumbot's baseline bb/100 "
        "| all in early | refused | requests | time | played |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.seat} | {s.hands:,} | {_cell(s.bb_per_100, s.margin)} "
            f"| {_cell(s.adjusted, s.adjusted_margin)} | {_cell(s.baseline, s.baseline_margin)} "
            f"| {s.all_in} | {s.refused} | {s.requests:,} | {s.minutes:.0f} min | {s.played_on} |"
        )
    lines += [
        "",
        "At 200 big blinds, Slumbot's format, with a 95% interval. Slumbot's side is not ours to "
        "seed, so a rerun repeats the procedure, not these numbers.",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, *, api: Api | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("seat", nargs="?", help="our seat, as --seats takes it: tag, station, ...")
    parser.add_argument("--hands", type=int, default=2000, help="hands to count (even; 2,000)")
    parser.add_argument("--out", type=Path, help="the folder for the hands")
    parser.add_argument("--resume", action="store_true", help="carry on in a folder with hands")
    parser.add_argument(
        "--seed", type=int, default=SEED, help="our seat's seed (Slumbot's is not ours)"
    )
    parser.add_argument(
        "--report", type=Path, nargs="+", metavar="DIR", help="only print the table"
    )
    args = parser.parse_args(argv)
    if args.report:
        print(render([summarise(folder) for folder in args.report]))
        return 0
    if not args.seat or not args.out:
        parser.error("give a seat and --out, or --report")
    return run(args.seat, args.hands, args.out, resume=args.resume, api=api, seed=args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
