"""Solve the heads-up push/fold game and write the nash:pushfold seat's data files.

    uv run python scripts/solve_pushfold.py                 # equity (about 18 min), then solve
    uv run python scripts/solve_pushfold.py --compare       # the check against a published chart

1. The all-in equity of every class of starting hand against every other, exact: every board
   is counted once through its class of suit-renamed boards (134,459 of them). The integer
   counts are cached in ``--cache`` so the later steps rerun in seconds. The equities do not
   depend on the ante, so every chart below uses the same ones.
2. CFR+ (``solvers/cfr.py``) on the push/fold game at every half big blind from 1 to 20, with no
   ante and with each ante in ``ANTES``, and an exact best response against what is stored, in
   milli-big-blinds per hand. The log gives the time of each chart and the load average.
3. ``--compare``: the largest stack at which each hand shoves and calls, on a 0.1 bb grid, next to
   the HoldemResources chart (https://www.holdemresources.net/hune, fetched 2026-11-26), for
   its two tabs: no ante, and an ante of 10% of the big blind. Their rule is "push when the
   value is larger than the effective stack"; ours is the largest stack where the shove (or
   call) probability is at least one half. They publish no chart for an ante of 1/8.

Deterministic: no random numbers at all, and one thread for numpy.
"""

from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from fractions import Fraction  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from poker_table.agents.handclass import CLASSES, RANKS, class_name, class_of_name  # noqa: E402
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
# The antes solved for besides none, as a share of the big blind: the default blind schedule's.
ANTES = (Fraction(1, 10), Fraction(1, 8))

# HoldemResources, as fetched on 2026-11-26: for each hand, the largest effective stack (bb,
# before the blinds) at which it is pushed or called, laid out as on their page: a row is the
# first card, suited hands above the diagonal, offsuit below. "20+" is any stack up to 20. The
# three gap hands are marked * and spelled out in GAPS, as their footnotes give them.
PUBLISHED = {
    ("0", "push"): """
        20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+
        20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+ 19.9 19.3
        20+  20+  20+  20+  20+  20+  20+  20+  20+  20+ 16.3 13.5 12.7
        20+  20+  20+  20+  20+  20+  20+  20+ 18.6 14.7 13.5 10.6  8.5
        20+  20+  20+  20+  20+  20+  20+  20+  20+ 11.9 10.5  7.7  6.5
        20+  20+  20+  20+  20+  20+  20+  20+  20+ 14.4  6.9  4.9  3.7
        20+ 18.0 13.0 13.3 17.5  20+  20+  20+  20+ 18.8 10.1  2.7  2.5
        20+ 16.1 10.3  8.5  9.0 10.8 14.7  20+  20+  20+ 13.9  2.5  2.1
        20+ 15.1  9.6  6.5  5.7  5.2  7.0 10.7  20+  20+ 16.3    *  2.0
        20+ 14.2  8.9  6.0  4.1  3.5  3.0  2.6  2.4  20+  20+    *  2.0
        20+ 13.1  7.9  5.4  3.8  2.7  2.3  2.1  2.0  2.1  20+    *  1.8
        20+ 12.2  7.5  5.0  3.4  2.5  1.9  1.8  1.7  1.8  1.6  20+  1.7
        20+ 11.6  7.0  4.6  2.9  2.2  1.8  1.6  1.5  1.5  1.4  1.4  20+
    """,
    ("0", "call"): """
        20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+
        20+  20+  20+  20+  20+  20+ 17.6 15.2 14.3 13.2 12.1 11.4 10.7
        20+  20+  20+  20+  20+ 16.1 13.0 10.5  9.9  8.9  8.4  7.8  7.2
        20+  20+ 19.5  20+ 18.0 13.4 10.6  8.8  7.0  6.9  6.1  5.8  5.6
        20+  20+ 15.3 12.7  20+ 11.5  9.3  7.4  6.3  5.2  5.2  4.8  4.5
        20+ 17.1 11.7  9.5  8.4  20+  8.2  7.0  5.8  5.0  4.3  4.1  3.9
        20+ 13.8  9.7  7.6  6.6  6.0  20+  6.5  5.6  4.8  4.1  3.6  3.5
        20+ 12.4  8.0  6.4  5.5  5.0  4.7  20+  5.4  4.8  4.1  3.6  3.3
        20+ 11.0  7.3  5.4  4.6  4.2  4.1  4.0  20+  4.9  4.3  3.8  3.3
        20+ 10.2  6.8  5.1  4.0  3.7  3.6  3.6  3.7  20+  4.6  4.0  3.6
       18.3  9.1  6.2  4.7  3.8  3.3  3.2  3.2  3.3  3.5  20+  3.8  3.4
       16.6  8.7  5.9  4.5  3.6  3.1  2.9  2.9  2.9  3.1  3.0  20+  3.3
       15.8  8.1  5.6  4.2  3.5  3.0  2.8  2.6  2.7  2.8  2.7  2.6 15.0
    """,
    ("1/10", "push"): """
        20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+
        20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+
        20+  20+  20+  20+  20+  20+  20+  20+  20+  20+ 18.6 15.5 14.5
        20+  20+  20+  20+  20+  20+  20+  20+  20+ 18.5 15.4 12.4 10.3
        20+  20+  20+  20+  20+  20+  20+  20+  20+ 13.6 12.4  8.9  7.4
        20+  20+  20+  20+  20+  20+  20+  20+  20+ 16.5  8.0  6.0  4.7
        20+  20+ 14.9 15.4  20+  20+  20+  20+  20+  20+ 11.8  3.3  3.0
        20+ 18.3 11.8  9.7 10.4 12.4 16.8  20+  20+  20+ 15.9  3.0  2.5
        20+ 17.4 11.3  7.5  6.7  6.5  8.5 13.5  20+  20+ 18.8    *  2.4
        20+ 16.4 10.3  7.1  4.9  4.2  3.8  3.3  3.0  20+  20+    *  2.4
        20+ 15.0  9.5  6.4  4.6  3.2  2.8  2.6  2.5  2.5  20+    *  2.2
        20+ 14.3  8.6  5.8  4.2  3.0  2.3  2.2  2.1  2.1  2.0  20+  2.1
        20+ 13.3  8.1  5.5  3.7  2.7  2.2  1.9  1.8  1.9  1.7  1.6  20+
    """,
    ("1/10", "call"): """
        20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+  20+
        20+  20+  20+  20+  20+  20+  20+ 17.4 16.4 15.1 13.9 12.9 12.3
        20+  20+  20+  20+  20+ 18.5 14.9 12.0 11.4 10.2  9.7  8.8  8.2
        20+  20+  20+  20+  20+ 15.4 12.2 10.0  8.1  7.9  7.0  6.6  6.4
        20+  20+ 17.8 15.1  20+ 13.0 10.6  8.5  7.2  6.0  5.9  5.5  5.1
        20+ 19.8 13.5 10.9  9.7  20+  9.5  8.0  6.7  5.6  4.8  4.6  4.3
        20+ 16.1 11.3  8.8  7.7  6.9  20+  7.4  6.4  5.5  4.7  4.0  3.9
        20+ 14.3  9.2  7.4  6.5  5.7  5.4  20+  6.0  5.5  4.7  4.0  3.6
        20+ 12.8  8.4  6.2  5.2  4.9  4.6  4.5  20+  5.5  4.7  4.2  3.7
        20+ 11.8  7.8  5.9  4.5  4.1  4.0  4.0  4.1  20+  5.1  4.5  3.9
        20+ 10.5  7.1  5.4  4.3  3.7  3.5  3.6  3.7  3.8  20+  4.2  3.8
       19.1  9.9  6.7  5.0  4.2  3.6  3.2  3.2  3.3  3.5  3.3  20+  3.6
       18.1  9.4  6.4  4.8  3.9  3.4  3.1  2.9  3.0  3.1  3.0  2.9 16.9
    """,
}
GAPS = {
    "0": {"63s": [(5.1, 7.1), (1.0, 2.3)], "53s": [(3.8, 12.9), (1.0, 2.4)],
          "43s": [(4.9, 10.0), (1.0, 2.2)]},
    "1/10": {"63s": [(4.7, 8.9), (1.0, 2.8)], "53s": [(3.8, 15.2), (1.0, 2.9)],
             "43s": [(4.7, 11.5), (1.0, 2.6)]},
}  # fmt: skip
# The cells of the no-ante table in docs/solver-seats.md.
SHOWN = {
    "push": ["K2o", "Q2o", "J2o", "T3s", "T2s", "72o", "32o", "J7o", "64s", "K3s", "86o", "Q4s",
             "93s", "J5s", "95s", "K7o", "84s", "54s", "A2o", "63s", "53s", "43s"],
    "call": ["A2o", "K2o", "Q2o", "22", "K9o", "Q9o", "J2s", "T8s", "76s", "72o", "32o", "K8s",
             "A4o", "98o", "QJo", "JTo", "Q9s", "AJo"],
}  # fmt: skip
TOP = 20.0  # the grid ends here; "20+" means pushed (or called) at every stack up to it


def log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def load() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


def published(ante: str, action: str) -> dict[str, list[tuple[float, float]]]:
    """Each hand's published ranges of stacks, as (low, high); 1.0 is the bottom of the grid."""
    rows = PUBLISHED[(ante, action)].split()
    out = {}
    for n, cell in enumerate(rows):
        row, col = divmod(n, 13)
        if row == col:
            hand = RANKS[row] * 2
        else:
            hand = f"{RANKS[row]}{RANKS[col]}s" if col > row else f"{RANKS[col]}{RANKS[row]}o"
        if cell == "*":
            out[hand] = GAPS[ante][hand]
        else:
            out[hand] = [(1.0, TOP if cell == "20+" else float(cell))]
    return out


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


def solve(stack: float, equity: np.ndarray, iterations: int, ante: float) -> list[np.ndarray]:
    solver = CFRPlus(game(stack, equity, ante=ante))
    solver.run(iterations)
    return solver.average()


def rounded(strategy: list[np.ndarray]) -> list[np.ndarray]:
    """What the data file stores: the probability of the aggressive action, to a thousandth."""
    out = []
    for rows in strategy:
        last = np.round(rows[:, 1] * 1000) / 1000
        out.append(np.stack([1 - last, last], axis=1))
    return out


def mbb(stack: float, equity: np.ndarray, strategy: list[np.ndarray], ante: float) -> float:
    g = game(stack, equity, ante=ante)
    gain = (best_response_value(g, strategy, 0) + best_response_value(g, strategy, 1)) / 2
    return gain * 1000


def solve_chart(equity: np.ndarray, ante: Fraction) -> dict[str, object]:
    """One chart: the push and call probabilities, per mille, and the best response's gain."""
    push: dict[str, list[int]] = {class_name(i): [] for i in range(CLASSES)}
    call: dict[str, list[int]] = {class_name(i): [] for i in range(CLASSES)}
    gains = []
    started = time.monotonic()
    log(f"ante {ante} of the big blind; {load()}")
    for stack in STACKS:
        stored = rounded(solve(stack, equity, ITERATIONS, float(ante)))
        for i in range(CLASSES):
            push[class_name(i)].append(int(round(stored[0][i, 1] * 1000)))
            call[class_name(i)].append(int(round(stored[1][i, 1] * 1000)))
        gains.append(round(mbb(stack, equity, stored, float(ante)), 4))
        log(f"  {stack:4.1f} bb: a best response gains {gains[-1]:.4f} mbb/hand")
    took = time.monotonic() - started
    log(f"ante {ante}: {len(STACKS)} stacks in {took:.0f} s ({took / len(STACKS):.1f} s a stack)")
    return {"push_per_mille": push, "call_per_mille": call, "best_response_gain_mbb": gains}


def write_data(equity: np.ndarray, counts_boards: int) -> None:
    payload = {
        "about": "Heads-up push/fold Nash equilibrium from scripts/solve_pushfold.py: with no "
        "ante at the top level, and under antes the same game with each player posting that "
        "share of the big blind. The small blind is half the big blind.",
        "method": f"CFR+ ({ITERATIONS} iterations per stack), average strategy",
        "paper": PAPER,
        "stacks_bb": STACKS,
        "valid_up_to_bb": VALID_UP_TO,
        **solve_chart(equity, Fraction(0)),
        "antes": {
            str(ante): {"ante_bb": float(ante), **solve_chart(equity, ante)} for ante in ANTES
        },
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


def show(ranges: list[tuple[float, float]]) -> str:
    parts = []
    for low, high in sorted(ranges, reverse=True):
        end = "20+" if high >= TOP else f"{high:.1f}"
        if low <= 1.0:
            parts.append(f"up to {end}")
        else:
            parts.append(end if low == high else f"{low:.1f}-{end}")
    return ", ".join(parts) or "never"


def compare_chart(equity: np.ndarray, ante: str, table: bool) -> None:
    """Every cell of one published tab: does the largest stack at which we push (or call) agree
    with theirs to 0.1 bb, the rounding of their chart? ``table`` also prints the cells of the
    no-ante table in docs/solver-seats.md."""
    grid = [round(1.0 + 0.1 * i, 1) for i in range(191)]
    started = time.monotonic()
    log(f"comparing ante {ante}: {len(grid)} stacks; {load()}")
    solved = {"push": [], "call": []}
    for stack in grid:
        avg = solve(stack, equity, 1500, float(Fraction(ante)))
        solved["push"].append(avg[0][:, 1])
        solved["call"].append(avg[1][:, 1])
    log(f"  took {time.monotonic() - started:.0f} s; {load()}")
    title = "no ante" if ante == "0" else f"an ante of {ante} of the big blind"
    print(f"\n## {title}\n")
    if table:
        print("| hand | action | published (bb) | ours (bb) |")
        print("|---|---|---|---|")
    differ = []
    for action in ("push", "call"):
        theirs = published(ante, action)
        agree = 0
        for hand in theirs:
            i = class_of_name(hand)
            ours = thresholds([float(row[i]) for row in solved[action]], grid)
            top_ours = max((high for _, high in ours), default=0.0)
            top_theirs = max(high for _, high in theirs[hand])
            if abs(top_ours - top_theirs) <= 0.1 + 1e-9:
                agree += 1
            else:
                at = min(range(len(grid)), key=lambda n: abs(grid[n] - top_theirs))
                p = float(solved[action][at][i])
                differ.append((hand, action, show(theirs[hand]), show(ours), p))
            if table and hand in SHOWN[action]:
                print(f"| {hand} | {action} | {show(theirs[hand])} | {show(ours)} |")
        print(f"\n{action}: the largest stack agrees to 0.1 bb in {agree} of {len(theirs)} hands")
    if differ:
        print("\n| hand | action | published (bb) | ours (bb) | ours at their largest stack |")
        print("|---|---|---|---|---:|")
        for hand, action, theirs_text, ours_text, p in differ:
            print(f"| {hand} | {action} | {theirs_text} | {ours_text} | {p:.2f} |")


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
        compare_chart(equity, "0", table=True)
        compare_chart(equity, "1/10", table=False)
        return 0
    started = time.monotonic()
    write_data(equity, 134459)
    log(f"solved {1 + len(ANTES)} charts in {time.monotonic() - started:.0f} s; {load()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
