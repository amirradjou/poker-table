"""How exploitable the shipped cfr:blueprint is inside its own abstract game.

    uv run python scripts/measure_blueprint.py
    uv run python scripts/measure_blueprint.py --strategy /tmp/bp500.bin --no-uniform
    uv run python scripts/measure_blueprint.py --groups 3 --no-uniform    # in the game with groups
    uv run python scripts/measure_blueprint.py --strategy v2.bin --no-uniform

It rebuilds the abstract game the blueprint was trained in (the chance model that
scripts/train_blueprint.py saved, recounted from the same seed if that file is missing), reads the
shipped strategy as the seat plays it (every probability rounded to 1/255), and computes an exact
best response for each seat. The best response knows the strategy, sees its own buckets, and
remembers every bucket it has held, which the blueprint does not. The number is what that
opponent wins per hand, averaged over the two seats, in thousandths of a big blind.

This is exploitability in the model game (the tree's bet sizes, the buckets, chance that only
remembers the current pair of buckets), not in real hold'em. A uniform random strategy is
measured the same way for scale.

A format-2 file, whose states after the flop keep a preflop group, is measured in the same game
with that group carried along (``solvers/recall.py``, ``remember_groups``). ``--groups G`` measures
a format-1 file there too, lifted: each (group, bucket) plays what the bucket played. The best
response must then win exactly what it wins in the game without groups, which is what puts the
two formats' numbers on one scale.
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
from poker_table.solvers.recall import equity_groups, lift_groups, remember_groups  # noqa: E402


def load() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


def chances(cards: Cards, header: dict | None = None) -> dict[str, np.ndarray]:
    """The chance model the strategy with this header was trained on (default: the shipped)."""
    header = shipped().header if header is None else header
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
        rows = strategy.rows(node.street)
        start = strategy.offsets[node.index]
        raw = np.frombuffer(strategy.table, dtype=np.uint8, count=rows * width, offset=start)
        out.append(raw.reshape(rows, width).astype(float) / 255)
    return out


def game_of(strategy, groups: int = 0):
    """The game a strategy is measured in, and the strategy as arrays for it. A format-2 file
    brings its groups; ``groups`` lifts a format-1 file into a game with that many."""
    _, game = training.build(chances(strategy.cards, strategy.header), strategy.betting)
    played = strategy_arrays(strategy)
    if strategy.groups:
        if groups and groups != max(strategy.groups) + 1:
            raise SystemExit(f"the file has {max(strategy.groups) + 1} groups, not {groups}")
        return remember_groups(game, np.array(strategy.groups)), played
    if groups:
        lifted = lift_groups(game, played, groups)
        return remember_groups(game, equity_groups(game, groups)), lifted
    return game, played


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strategy", type=Path, help="another strategy file (default: shipped)")
    parser.add_argument("--no-uniform", action="store_true", help="skip the uniform reference")
    parser.add_argument(
        "--groups", type=int, default=0, help="measure a format-1 file lifted into G groups"
    )
    args = parser.parse_args()
    print(f"start: {load()}", file=sys.stderr)
    strategy = read_strategy(args.strategy.read_bytes()) if args.strategy else shipped()
    game, played = game_of(strategy, args.groups)
    where = f"states {game.states}"
    measured = [(f"{strategy.header['iterations']} iterations, format {strategy.version}", played)]
    if not args.no_uniform:
        measured.append(("uniform", game.uniform()))
    for name, rows in measured:
        started = time.monotonic()
        as_sb = best_response_value(game, rows, 0)
        as_bb = best_response_value(game, rows, 1)
        value = expected_value(game, rows)
        gap = (as_sb + as_bb) / 2 * 1000
        print(
            f"{name}, {where}: a best response wins {gap:.1f} mbb/hand ({as_sb * 1000:.1f} as "
            f"the small blind, {as_bb * 1000:.1f} as the big blind); the small blind's value "
            f"against itself is {value * 1000:+.1f} mbb/hand; {time.monotonic() - started:.0f} s",
            flush=True,
        )
        print(f"  exact: {as_sb!r} {as_bb!r} {value!r}", file=sys.stderr)
    print(f"end: {load()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
