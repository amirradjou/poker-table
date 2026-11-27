"""Play the heads-up track for the scripted bots and the solver seats into docs/results.

    uv run python scripts/run_heads_up_track.py            # every entrant that has no folder yet
    uv run python scripts/run_heads_up_track.py cfr:blueprint --replace

Each entrant plays 400 hands against each of tag, rock, maniac and station, from seat 0 on the
same decks (``poker-table benchmark SEAT --heads-up``). A run that has an illegal move or an
"agent error" from the entrant is refused, because the table folds for a seat that crashes and
the row would then look like a legal, losing one. Then the leaderboards are rebuilt.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from poker_table.benchmark import ENTRANT, OPPONENTS, heads_up_name
from poker_table.cli import main as cli
from poker_table.history import read_jsonl

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "docs" / "results"
ENTRANTS = (
    "tag",
    "rock",
    "exploiter",
    "maniac",
    "station",
    "random",
    "nash:pushfold",
    "cfr:blueprint",
)


def broken(folder: Path) -> list[str]:
    found = []
    for history in read_jsonl(folder / "hands.jsonl"):
        for decision in history.decisions:
            if history.players[decision.seat].name != ENTRANT:
                continue
            if decision.illegal or decision.reasoning.startswith("agent error"):
                found.append(f"hand {history.hand_id}: {decision.applied} ({decision.reasoning})")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("entrants", nargs="*", default=list(ENTRANTS))
    parser.add_argument("--replace", action="store_true", help="play again over existing folders")
    args = parser.parse_args()
    for seat in args.entrants:
        names = [heads_up_name(seat, o) for o in OPPONENTS]
        if all((RESULTS / n).exists() for n in names) and not args.replace:
            print(f"{seat}: already played", file=sys.stderr)
            continue
        with tempfile.TemporaryDirectory() as temp:
            started = time.monotonic()
            code = cli(["benchmark", seat, "--heads-up", "-o", temp])
            if code:
                return code
            seconds = time.monotonic() - started
            load = subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()
            print(f"{seat}: {len(names)} matches in {seconds:.0f} s ({load})", file=sys.stderr)
            for name in names:
                problems = broken(Path(temp) / name)
                if problems:
                    print(f"{name}: {len(problems)} broken decisions, e.g. {problems[0]}")
                    return 1
            for name in names:
                shutil.rmtree(RESULTS / name, ignore_errors=True)
                shutil.copytree(Path(temp) / name, RESULTS / name)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_leaderboard.py")]
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
