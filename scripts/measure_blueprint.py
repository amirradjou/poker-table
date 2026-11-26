"""How exploitable the shipped cfr:blueprint is inside its own abstract game.

    uv run python scripts/measure_blueprint.py
    uv run python scripts/measure_blueprint.py --strategy /tmp/bp500.bin --no-uniform

It rebuilds the abstract game the blueprint was trained in (the chance model that
scripts/train_blueprint.py saved, recounted from the same seed if that file is missing), reads the
shipped strategy as the seat plays it (every probability rounded to 1/255), and computes an exact
best response for each seat. The best response knows the strategy, sees its own buckets, and
remembers every bucket it has held, which the blueprint does not. The number is what that
opponent wins per hand, averaged over the two seats, in thousandths of a big blind.

This is exploitability in the model game (the tree's bet sizes, the buckets, chance that only
remembers the current pair of buckets), not in real hold'em. A uniform random strategy is
measured the same way for scale.
"""

from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_blueprint as training  # noqa: E402

from poker_table.agents.abstraction import Cards, read_strategy  # noqa: E402
from poker_table.agents.blueprint import shipped  # noqa: E402
from poker_table.solvers import blueprint as model  # noqa: E402
from poker_table.solvers.cfr import best_response_value, expected_value  # noqa: E402


def load() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


def chances(cards: Cards) -> dict[str, np.ndarray]:
    header = shipped().header
    rng = np.random.default_rng(header["seed"])
    total = header["cut_boards"] + header["boards"]
    dealt = np.array([rng.choice(52, 5, replace=False) for _ in range(total)])
    if model.strength_cuts(dealt[: header["cut_boards"]], training.QUANTILES) != cards:
        raise SystemExit("the cut points do not match the shipped strategy's")
    cache = Path.home() / ".cache" / "poker-table" / "blueprint-chances.npz"
    if cache.exists():
        saved = dict(np.load(cache))
        if saved["preflop_flop"].shape[2] == cards.buckets(1):
            return saved
    print("recounting the chance model (about 5 minutes)", file=sys.stderr)
    return training.chance_model(cards, dealt[header["cut_boards"] :], time.monotonic())


def strategy_arrays(strategy) -> list[np.ndarray]:
    out = []
    for node in strategy.nodes:
        width = len(node.labels)
        rows = strategy.cards.buckets(node.street)
        start = strategy.offsets[node.index]
        raw = np.frombuffer(strategy.table, dtype=np.uint8, count=rows * width, offset=start)
        out.append(raw.reshape(rows, width).astype(float) / 255)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strategy", type=Path, help="another strategy file (default: shipped)")
    parser.add_argument("--no-uniform", action="store_true", help="skip the uniform reference")
    args = parser.parse_args()
    print(f"start: {load()}", file=sys.stderr)
    strategy = read_strategy(args.strategy.read_bytes()) if args.strategy else shipped()
    _, game = training.build(chances(strategy.cards), strategy.betting)
    measured = [(f"{strategy.header['iterations']} iterations", strategy_arrays(strategy))]
    if not args.no_uniform:
        measured.append(("uniform", game.uniform()))
    for name, played in measured:
        started = time.monotonic()
        as_sb = best_response_value(game, played, 0)
        as_bb = best_response_value(game, played, 1)
        value = expected_value(game, played)
        gap = (as_sb + as_bb) / 2 * 1000
        print(
            f"{name}: a best response wins {gap:.0f} mbb/hand ({as_sb * 1000:.0f} as the small "
            f"blind, {as_bb * 1000:.0f} as the big blind); the small blind's value against "
            f"itself is {value * 1000:+.0f} mbb/hand; {time.monotonic() - started:.0f} s"
        )
    print(f"end: {load()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
