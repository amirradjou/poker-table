"""Solve the heads-up push/fold game and write the nash:pushfold seat's data files.

    uv run python scripts/solve_pushfold.py                 # equity (about 18 min), then solve
    uv run python scripts/solve_pushfold.py --compare       # the check against a published chart

1. The all-in equity of every class of starting hand against every other, exact: every board
   is counted once through its class of suit-renamed boards (134,459 of them). The integer
   counts are cached in ``--cache`` so the later steps rerun in seconds.
2. CFR+ (``solvers/cfr.py``) on the push/fold game at every half big blind from 1 to 20, and an
   exact best response against what is stored, in milli-big-blinds per hand.
3. ``--compare``: the largest stack at which each hand shoves and calls, on a 0.1 bb grid, next to
   the HoldemResources chart (https://www.holdemresources.net/hune, no-ante tables, fetched
   2026-11-26). Their rule is "push when the value is larger than the effective stack"; ours is the
   largest stack where the shove (or call) probability is at least one half.

Deterministic: no random numbers at all, and one thread for numpy.
"""

from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from poker_table.agents.handclass import CLASSES, class_name, class_of_name  # noqa: E402
from poker_table.agents.pushfold import DATA, EQUITY, PAPER, VALID_UP_TO  # noqa: E402
from poker_table.solvers.cfr import CFRPlus, best_response_value  # noqa: E402
from poker_table.solvers.pushfold import (  # noqa: E402
    canonical_boards,
    equity_counts,
    equity_matrix,
    game,
)

ROOT = Path(__file__).resolve().parent.parent
STACKS = [1.0 + 0.5 * i for i in range(39)]  # 1.0 .. 20.0
ITERATIONS = 4000
DECIMALS = 6  # the equities are stored to a millionth

# HoldemResources, no-ante tables, as fetched on 2026-11-26: the stacks (bb, effective, before the
# blinds) at which a hand is pushed or called, as ranges. "20+" (any stack up to 20) is 99 here.
PUBLISHED = {
    "push": {
        "K2o": [(0, 11.6)], "Q2o": [(0, 7.0)], "J2o": [(0, 4.6)], "T3s": [(0, 7.7)],
        "T2s": [(0, 6.5)], "72o": [(0, 1.6)], "32o": [(0, 1.4)], "J7o": [(0, 8.5)],
        "64s": [(0, 16.3)], "K3s": [(0, 19.9)], "86o": [(0, 7.0)], "Q4s": [(0, 16.3)],
        "93s": [(0, 4.9)], "J5s": [(0, 14.7)], "95s": [(0, 14.4)], "K7o": [(0, 16.1)],
        "84s": [(0, 10.1)], "54s": [(0, 99)], "A2o": [(0, 99)],
        "63s": [(5.1, 7.1), (0, 2.3)], "53s": [(3.8, 12.9), (0, 2.4)],
        "43s": [(4.9, 10.0), (0, 2.2)],
    },
    "call": {
        "A2o": [(0, 15.8)], "K2o": [(0, 8.1)], "Q2o": [(0, 5.6)], "22": [(0, 15.0)],
        "K9o": [(0, 17.1)], "Q9o": [(0, 11.7)], "J2s": [(0, 5.6)], "T8s": [(0, 9.3)],
        "76s": [(0, 5.4)], "72o": [(0, 2.6)], "32o": [(0, 2.6)], "K8s": [(0, 17.6)],
        "A4o": [(0, 18.3)], "98o": [(0, 6.0)], "QJo": [(0, 19.5)], "JTo": [(0, 12.7)],
        "Q9s": [(0, 16.1)], "AJo": [(0, 99)],
    },
}  # fmt: skip


def log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def load_counts(cache: Path) -> tuple[np.ndarray, np.ndarray]:
    if cache.exists():
        data = np.load(cache)
        return data["wins2"], data["deals"]
    boards, weights = canonical_boards(5)
    log(f"{len(boards)} classes of boards ({weights.sum()} boards); counting every deal")
    started = time.monotonic()

    def progress(n: int) -> None:
        if n % 10000 == 0:
            log(f"  {n}/{len(boards)} boards, {(time.monotonic() - started) / 60:.1f} min")

    wins2, deals = equity_counts(boards, weights, progress)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, wins2=wins2, deals=deals)
    log(f"equity done in {(time.monotonic() - started) / 60:.1f} min")
    return wins2, deals


def solve(stack: float, equity: np.ndarray, iterations: int) -> list[np.ndarray]:
    solver = CFRPlus(game(stack, equity))
    solver.run(iterations)
    return solver.average()


def rounded(strategy: list[np.ndarray]) -> list[np.ndarray]:
    """What the data file stores: the probability of the aggressive action, to a thousandth."""
    out = []
    for rows in strategy:
        last = np.round(rows[:, 1] * 1000) / 1000
        out.append(np.stack([1 - last, last], axis=1))
    return out


def mbb(stack: float, equity: np.ndarray, strategy: list[np.ndarray]) -> float:
    g = game(stack, equity)
    gain = (best_response_value(g, strategy, 0) + best_response_value(g, strategy, 1)) / 2
    return gain * 1000


def write_data(equity: np.ndarray, counts_boards: int) -> None:
    push: dict[str, list[int]] = {class_name(i): [] for i in range(CLASSES)}
    call: dict[str, list[int]] = {class_name(i): [] for i in range(CLASSES)}
    gains = []
    for stack in STACKS:
        stored = rounded(solve(stack, equity, ITERATIONS))
        for i in range(CLASSES):
            push[class_name(i)].append(int(round(stored[0][i, 1] * 1000)))
            call[class_name(i)].append(int(round(stored[1][i, 1] * 1000)))
        gains.append(round(mbb(stack, equity, stored), 4))
        log(f"  {stack:4.1f} bb: a best response gains {gains[-1]:.4f} mbb/hand")
    payload = {
        "about": "Heads-up push/fold Nash equilibrium, no ante, from scripts/solve_pushfold.py.",
        "method": f"CFR+ ({ITERATIONS} iterations per stack), average strategy",
        "paper": PAPER,
        "stacks_bb": STACKS,
        "valid_up_to_bb": VALID_UP_TO,
        "push_per_mille": push,
        "call_per_mille": call,
        "best_response_gain_mbb": gains,
    }
    DATA.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    upper = [
        int(round(equity[i, j] * 10**DECIMALS)) for i in range(CLASSES) for j in range(i, CLASSES)
    ]
    EQUITY.write_text(
        json.dumps(
            {
                "about": "All-in equity of class i (row) against class j: wins plus half the "
                f"ties, over every board ({counts_boards} classes of suit-renamed boards).",
                "decimals": DECIMALS,
                "upper_triangle": upper,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def thresholds(probabilities: list[float], grid: list[float]) -> list[tuple[float, float]]:
    """The stack ranges where the probability is at least one half, as (low, high)."""
    ranges: list[tuple[float, float]] = []
    start = None
    for n, (stack, p) in enumerate(zip(grid, probabilities, strict=True)):
        if p >= 0.5 and start is None:
            start = stack
        if p < 0.5 and start is not None:
            ranges.append((start, grid[n - 1]))
            start = None
    if start is not None:
        ranges.append((start, grid[-1]))
    return ranges


def compare(equity: np.ndarray) -> None:
    grid = [round(1.0 + 0.1 * i, 1) for i in range(191)]
    pushes, calls = [], []
    for stack in grid:
        avg = solve(stack, equity, 1500)
        pushes.append(avg[0][:, 1])
        calls.append(avg[1][:, 1])

    def show(ranges: list[tuple[float, float]], top: float) -> str:
        parts = []
        for low, high in sorted(ranges, reverse=True):
            end = "20+" if high >= top else f"{high:.1f}"
            parts.append(f"up to {end}" if low <= 1.0 else f"{low:.1f}-{end}")
        return ", ".join(parts) or "never"

    print("| hand | action | published (bb) | ours (bb) |")
    print("|---|---|---|---|")
    for action, cells in PUBLISHED.items():
        table = pushes if action == "push" else calls
        for hand, published in cells.items():
            i = class_of_name(hand)
            ours = thresholds([float(row[i]) for row in table], grid)
            print(f"| {hand} | {action} | {show(published, 20.0)} | {show(ours, 20.0)} |")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path.home() / ".cache" / "poker-table" / "pushfold-equity-counts.npz",
    )
    parser.add_argument("--compare", action="store_true", help="only the chart comparison")
    args = parser.parse_args()
    wins2, deals = load_counts(args.cache)
    equity = equity_matrix(wins2, deals)
    if args.compare:
        compare(equity)
        return 0
    started = time.monotonic()
    write_data(equity, 134459)
    log(f"solved {len(STACKS)} stacks in {time.monotonic() - started:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
