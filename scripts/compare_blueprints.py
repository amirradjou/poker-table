"""Play the heads-up track with another cfr:blueprint strategy, and pair it with a published one.

    uv run python scripts/compare_blueprints.py play v2.bin --out DIR    # the track, into DIR
    uv run python scripts/compare_blueprints.py pair DIR                 # against docs/results
    uv run python scripts/compare_blueprints.py pair DIR --against OTHER

``play`` seats the file as cfr:blueprint and plays the heads-up track into DIR (400 hands against
each of tag, rock, maniac and station, on the track's decks, as ``poker-table benchmark
cfr:blueprint --heads-up`` does). It never writes docs/results. Like run_heads_up_track.py it
refuses a run where the entrant made an illegal move or had an agent error.

``pair`` matches two sets of hu-cfr-blueprint-* folders hand for hand: same opponent, same hand
number, and it checks that each pair of hands has the same seed and the same hole cards. In each
hand it takes the entrant's all-in adjusted chips (``margins.all_in_adjusted``) in the first set
minus the second. The mean of those 1,600 differences, with a 95% interval of 1.96 standard
errors, is in bb/100. Pairing removes most of the card luck the two runs share, so the interval is
much narrower than the two runs' own.

A replay of the published strategy (``play`` with it) must pair to exactly zero in every hand:
that shows the bots and the decks have not changed since the published run.
"""

from __future__ import annotations

import argparse
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_heads_up_track import broken  # noqa: E402

from poker_table.agents import blueprint  # noqa: E402
from poker_table.agents.abstraction import read_strategy  # noqa: E402
from poker_table.benchmark import ENTRANT, OPPONENTS, heads_up_name  # noqa: E402
from poker_table.cli import main as cli  # noqa: E402
from poker_table.history import read_jsonl  # noqa: E402
from poker_table.margins import all_in_adjusted  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "docs" / "results"
SEAT = "cfr:blueprint"
LIMIT_BYTES = 2048 * 1024  # .pre-commit-config.yaml's limit for a published hands.jsonl


def load() -> str:
    out = subprocess.run(["uptime"], capture_output=True, text=True).stdout
    return out.strip().split("load average: ")[-1]


def play(strategy_file: Path, out: Path) -> int:
    if out.resolve() == RESULTS.resolve() or RESULTS.resolve() in out.resolve().parents:
        raise SystemExit("play writes a scratch folder, never docs/results")
    strategy = read_strategy(strategy_file.read_bytes())
    blueprint.shipped = lambda: strategy  # every cfr:blueprint seat made from here plays it
    started = time.monotonic()
    print(f"start, load {load()}", file=sys.stderr)
    code = cli(["benchmark", SEAT, "--heads-up", "-o", str(out)])
    if code:
        return code
    for opponent in OPPONENTS:
        folder = out / heads_up_name(SEAT, opponent)
        problems = broken(folder)
        if problems:
            print(f"{folder.name}: {len(problems)} broken decisions, e.g. {problems[0]}")
            return 1
        size = (folder / "hands.jsonl").stat().st_size
        fits = "fits" if size <= LIMIT_BYTES else "is over"
        print(f"{folder.name}: hands.jsonl is {size:,} bytes, {fits} the 2 MB limit")
    print(
        f"format {strategy.version}, {strategy.header['iterations']} iterations: played in "
        f"{time.monotonic() - started:.0f} s, load {load()}"
    )
    return 0


def per_hand(folder: Path) -> dict[str, tuple[int, tuple, float]]:
    """Hand number -> (seed, hole cards, the entrant's all-in adjusted big blinds)."""
    found = {}
    for history in read_jsonl(folder / "hands.jsonl"):
        seat = next(p.seat for p in history.players if p.name == ENTRANT)
        worth = all_in_adjusted(history)
        chips = history.players[seat].net if worth is None else worth[seat]
        holes = tuple(tuple(p.hole) for p in history.players)
        found[history.hand_id] = (history.seed, holes, chips / history.big_blind)
    return found


def interval(differences: list[float]) -> tuple[float, float]:
    """The mean in bb/100 and half its 95% interval."""
    mean = statistics.fmean(differences) * 100
    half = 1.96 * statistics.stdev(differences) / math.sqrt(len(differences)) * 100
    return mean, half


def pair(first: Path, second: Path) -> int:
    started = time.monotonic()
    every: list[float] = []
    print(f"{first} minus {second}, all-in adjusted, in bb/100:")
    for opponent in OPPONENTS:
        name = heads_up_name(SEAT, opponent)
        a, b = per_hand(first / name), per_hand(second / name)
        if a.keys() != b.keys():
            raise SystemExit(f"{name}: the two runs have different hands")
        differences = []
        for hand, (seed, holes, chips) in a.items():
            if (seed, holes) != b[hand][:2]:
                raise SystemExit(f"{name}, hand {hand}: not the same deal")
            differences.append(chips - b[hand][2])
        every += differences
        mean, half = interval(differences)
        same = sum(d == 0 for d in differences)
        print(
            f"  vs {opponent}: {mean:+.1f} ±{half:.1f} over {len(differences)} hands "
            f"({same} hands the same)"
        )
    mean, half = interval(every)
    low, high = mean - half, mean + half
    print(f"  all {len(every)} hands: {mean:+.1f} ±{half:.1f} (from {low:+.1f} to {high:+.1f})")
    if all(d == 0 for d in every):
        print("  every hand is the same: the two runs played alike")
    print(f"{time.monotonic() - started:.0f} s, load {load()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("play", help="the heads-up track with this strategy file, into --out")
    p.add_argument("strategy", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("pair", help="hand-for-hand differences between two sets of folders")
    p.add_argument("folder", type=Path)
    p.add_argument("--against", type=Path, default=RESULTS)
    args = parser.parse_args()
    if args.command == "play":
        return play(args.strategy, args.out)
    return pair(args.folder, args.against)


if __name__ == "__main__":
    raise SystemExit(main())
