"""Train the cfr:blueprint seat's strategy and write src/poker_table/agents/data/blueprint.bin.

    uv run python scripts/train_blueprint.py             # the shipped file, under 30 minutes
    uv run python scripts/train_blueprint.py --check DIR     # a tiny run, for the tests
    uv run python scripts/train_blueprint.py --iterations 500 --reuse --out /tmp/bp500.bin
    uv run python scripts/train_blueprint.py --reuse --out v2.bin --half v2-1000.bin
    uv run python scripts/train_blueprint.py --groups 0 --reuse --out v1.bin    # version 1

One fixed game: heads-up, 200-chip stacks, blinds 1/2, no ante (the table's defaults).

1. Cut points for the hand-strength buckets, from 400 seeded boards.
2. The chance model (``solvers/blueprint.py``): how the 169 classes land in flop buckets (exact,
   every flop), how the pair of buckets moves to the turn and river and who wins a showdown
   (4,000 seeded boards, every pair of hands on each).
3. CFR+ for a fixed number of iterations: never stopped by the clock, so the same file comes
   out of any run. It gives up, writing nothing, if the whole run passes 30 minutes.
4. The average strategy, each probability rounded to 1/255, written with the game's settings.

The chance model is also saved to ``--cache``, when no file is there yet, so
scripts/measure_blueprint.py can compute the best response in exactly this abstract game without
recounting it. An existing file is never written over.

The shipped file is version 2: after the flop its states also keep one of 3 groups of the 169
classes (``solvers/recall.py``, ``remember_groups``), in the same game and the same chance model,
with a little more memory. It is format 2 of the strategy file. ``--groups 0`` trains version 1,
whose buckets forget (format 1, shipped up to v0.3.0). Groups that are not the shipped file's
need an explicit ``--out``, so an experiment never writes over the shipped file.
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
from poker_table.solvers.recall import equity_groups, remember_groups  # noqa: E402

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
SHIPPED_GROUPS = 3  # preflop groups the shipped file keeps after the flop (format 2); 0: format 1


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
    saved: Path | None = None,
    groups: int = 0,
    half: Path | None = None,
) -> str:
    started = time.monotonic()
    log(f"start: {load()}")
    rng = np.random.default_rng(SEED)
    dealt = np.array([rng.choice(52, 5, replace=False) for _ in range(cut_boards + boards)])
    cards = model.strength_cuts(dealt[:cut_boards], QUANTILES)
    log(f"cut points: {cards}")
    if saved is not None:  # a chance model this script saved before, from the same seed
        chances = dict(np.load(saved))
    else:
        chances = chance_model(cards, dealt[cut_boards:], started, flops)
        if cache is not None and not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez(cache, **chances)
    betting = Betting()
    root, game = build(chances, betting)
    spots = number(root)
    assert [s.index for s in spots] == [n.index for n in game.decisions]
    grouping: tuple[int, ...] = ()
    if groups:
        grouping = tuple(int(g) for g in equity_groups(game, groups))
        game = remember_groups(game, np.array(grouping))
        log(f"{groups} preflop groups of {np.bincount(grouping).tolist()} classes")
    minutes = (time.monotonic() - started) / 60
    log(f"{len(spots)} decision nodes; chance model done in {minutes:.1f} min")
    solver = CFRPlus(game)
    every = max(1, iterations // 10)

    def write(path: Path, done: int) -> str:
        rows = []
        for spot, strategy in zip(spots, solver.average(), strict=True):
            assert len(strategy) == game.states[spot.street]
            rows.extend(quantize(row) for row in strategy.tolist())
        header = {
            "about": "cfr:blueprint strategy, from scripts/train_blueprint.py",
            "method": "CFR+ (vector form, alternating updates, linearly weighted average)",
            "paper": PAPER,
            "translation": TRANSLATION_PAPER,
            "iterations": done,
            "seed": SEED,
            "cut_boards": cut_boards,
            "boards": boards,
            "quantiles": {str(k): v for k, v in QUANTILES.items()},
        }
        if grouping:
            header["recall"] = (
                "after the flop a state is (preflop group, bucket); the groups split the 169 "
                "classes by equity against a random hand, about equal weight each"
            )
        write_strategy(path, betting, cards, rows, header, groups=grouping)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        size, elapsed = path.stat().st_size, (time.monotonic() - started) / 60
        log(f"wrote {path} ({size} bytes, sha256 {digest[:16]}) at {elapsed:.1f} min")
        return digest

    def tick(n: int) -> None:
        elapsed = time.monotonic() - started
        if elapsed > LIMIT_SECONDS:
            raise SystemExit(f"stopped at iteration {n}: past 30 minutes, nothing more written")
        if n % every == 0:
            log(f"  iteration {n}/{iterations}, {elapsed / 60:.1f} min")
        if half is not None and n == iterations // 2:
            write(half, n)

    solver.run(iterations, tick)
    digest = write(out, iterations)
    log(f"end: {load()}")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="default: the shipped file, for its format")
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path.home() / ".cache" / "poker-table" / "blueprint-chances.npz",
    )
    parser.add_argument(
        "--check", type=Path, metavar="DIR", help="train a tiny blueprint into DIR, for tests"
    )
    parser.add_argument(
        "--iterations", type=int, default=ITERATIONS, help="for the trend in docs/solver-seats.md"
    )
    parser.add_argument(
        "--reuse", action="store_true", help="read the chance model from --cache, do not recount"
    )
    parser.add_argument(
        "--groups",
        type=int,
        default=SHIPPED_GROUPS,
        help="preflop groups kept after the flop (default: the shipped 3; 0: format 1)",
    )
    parser.add_argument("--half", type=Path, help="also write the average at half the iterations")
    args = parser.parse_args()
    if args.check is not None:
        args.check.mkdir(parents=True, exist_ok=True)
        print(train(2, 6, 6, args.check / "blueprint.bin", None, flops=12, groups=args.groups))
        return 0
    if args.out is None:
        if args.groups != SHIPPED_GROUPS:
            parser.error(f"--groups {args.groups} is not the shipped file's format: pass --out")
        args.out = DATA
    saved = args.cache if args.reuse else None
    train(
        args.iterations,
        CUT_BOARDS,
        BOARDS,
        args.out,
        args.cache,
        saved=saved,
        groups=args.groups,
        half=args.half,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
