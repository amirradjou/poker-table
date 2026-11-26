"""Train the cfr:blueprint seat's strategy and write src/poker_table/agents/data/blueprint.bin.

    uv run python scripts/train_blueprint.py             # about 20 minutes on one core
    uv run python scripts/train_blueprint.py --check     # a tiny run, twice, to show it repeats

One fixed game: heads-up, 200-chip stacks, blinds 1/2, no ante (the table's defaults).

1. Cut points for the hand-strength buckets, from 400 seeded boards.
2. The chance model (``solvers/blueprint.py``): how the 169 classes land in flop buckets (exact,
   every flop), how the pair of buckets moves to the turn and river and who wins a showdown
   (4,000 seeded boards, every pair of hands on each).
3. CFR+ for a fixed number of iterations: never stopped by the clock, so the same file comes
   out of any run. It gives up, writing nothing, if the whole run passes 30 minutes.
4. The average strategy, each probability rounded to 1/255, written with the game's settings.

The chance model is also saved to ``--cache`` so scripts/measure_blueprint.py can compute the
best response in exactly this abstract game without recounting it.
"""

from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse  # noqa: E402
import hashlib  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from poker_table.agents.abstraction import (  # noqa: E402
    FLOP,
    RIVER,
    TURN,
    Betting,
    Cards,
    build_tree,
    number,
    quantize,
    write_strategy,
)
from poker_table.agents.blueprint import DATA, PAPER, TRANSLATION_PAPER  # noqa: E402
from poker_table.agents.pushfold import equities  # noqa: E402
from poker_table.solvers import blueprint as model  # noqa: E402
from poker_table.solvers.cfr import CFRPlus  # noqa: E402
from poker_table.solvers.pushfold import deal_chances  # noqa: E402

SEED = 2026
ITERATIONS = 2000
CUT_BOARDS = 400
BOARDS = 4000
QUANTILES = {
    FLOP: (0.3, 0.55, 0.75, 0.9),
    TURN: (0.3, 0.55, 0.75, 0.9),
    RIVER: (0.15, 0.3, 0.45, 0.58, 0.7, 0.8, 0.87, 0.93, 0.97),
}
LIMIT_SECONDS = 30 * 60


def log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def load() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


def chance_model(
    cards: Cards, boards: np.ndarray, started: float, flops: int | None = None
) -> dict[str, np.ndarray]:
    def progress(name: str):
        def report(n: int, total: int) -> None:
            if n % max(1, total // 4) == 0:
                log(f"  {name}: {n}/{total}, {(time.monotonic() - started) / 60:.1f} min")

        return report

    flop = model.preflop_to_flop(cards, progress("preflop to flop"), limit=flops)
    later = model.later_streets(cards, boards, progress("later streets"))
    return {"preflop_flop": flop, **later}


def build(chances: dict[str, np.ndarray], betting: Betting):
    equity = np.array(equities())
    share = {
        0: equity,
        1: chances[f"share_{FLOP}"],
        2: chances[f"share_{TURN}"],
        3: chances[f"share_{RIVER}"],
    }
    root = build_tree(betting)
    moves = (chances["preflop_flop"], chances["flop_turn"], chances["turn_river"])
    return root, model.game(root, deal_chances(), moves, share, float(betting.big_blind))


def train(
    iterations: int,
    cut_boards: int,
    boards: int,
    out: Path,
    cache: Path | None,
    flops: int | None = None,
) -> str:
    started = time.monotonic()
    log(f"start: {load()}")
    rng = np.random.default_rng(SEED)
    dealt = np.array([rng.choice(52, 5, replace=False) for _ in range(cut_boards + boards)])
    cards = model.strength_cuts(dealt[:cut_boards], QUANTILES)
    log(f"cut points: {cards}")
    chances = chance_model(cards, dealt[cut_boards:], started, flops)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, **chances)
    betting = Betting()
    root, game = build(chances, betting)
    spots = number(root)
    assert [s.index for s in spots] == [n.index for n in game.decisions]
    minutes = (time.monotonic() - started) / 60
    log(f"{len(spots)} decision nodes; chance model done in {minutes:.1f} min")
    solver = CFRPlus(game)
    every = max(1, iterations // 10)

    def tick(n: int) -> None:
        elapsed = time.monotonic() - started
        if elapsed > LIMIT_SECONDS:
            raise SystemExit(f"stopped at iteration {n}: past 30 minutes, nothing written")
        if n % every == 0:
            log(f"  iteration {n}/{iterations}, {elapsed / 60:.1f} min")

    solver.run(iterations, tick)
    rows = []
    for spot, strategy in zip(spots, solver.average(), strict=True):
        rows.extend(quantize(row) for row in strategy.tolist())
        assert len(strategy) == cards.buckets(spot.street)
    header = {
        "about": "cfr:blueprint strategy, from scripts/train_blueprint.py",
        "method": "CFR+ (vector form, alternating updates, linearly weighted average)",
        "paper": PAPER,
        "translation": TRANSLATION_PAPER,
        "iterations": iterations,
        "seed": SEED,
        "cut_boards": cut_boards,
        "boards": boards,
        "quantiles": {str(k): v for k, v in QUANTILES.items()},
    }
    write_strategy(out, betting, cards, rows, header)
    seconds = time.monotonic() - started
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    log(f"wrote {out} ({out.stat().st_size} bytes, sha256 {digest[:16]}) in {seconds / 60:.1f} min")
    log(f"end: {load()}")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DATA)
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path.home() / ".cache" / "poker-table" / "blueprint-chances.npz",
    )
    parser.add_argument(
        "--check", type=Path, metavar="DIR", help="train a tiny blueprint into DIR, for tests"
    )
    args = parser.parse_args()
    if args.check is not None:
        args.check.mkdir(parents=True, exist_ok=True)
        print(train(2, 6, 6, args.check / "blueprint.bin", None, flops=12))
        return 0
    train(ITERATIONS, CUT_BOARDS, BOARDS, args.out, args.cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
