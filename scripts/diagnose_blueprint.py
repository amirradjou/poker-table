"""Why the cfr:blueprint gets more exploitable with more training: the runs behind the answer.

    uv run python scripts/diagnose_blueprint.py match                  # same game? (3 min)
    uv run python scripts/diagnose_blueprint.py checkpoints --out DIR  # CFR+ again (15 min)
    uv run python scripts/diagnose_blueprint.py measure DIR avg:250 avg:250:rounded current:250
    uv run python scripts/diagnose_blueprint.py exploiter DIR avg:250:rounded --at 100,400
    uv run python scripts/diagnose_blueprint.py recall --groups 3,2,2,3  # a small game, twice
    uv run python scripts/diagnose_blueprint.py recall --arms own-reach --no-check

A best response takes about 2.5 minutes, so ``measure`` takes that long per variant;
docs/solver-seats.md has every command behind its numbers, with times and load averages.

All of them read the chance model that scripts/train_blueprint.py saved (it is recounted, not
saved, when it is missing) and never write it or the shipped strategy.

Five causes were possible. The run that tells each one apart, and what would clear it:

1. **Imperfect recall** (CFR+ has no guarantee when players forget). ``recall`` plays one small
   game twice, with the same tree, chance and best response: once with buckets that forget, once
   with buckets that remember. If the remembering game goes to zero and the forgetting one does
   not, forgetting is enough to stop CFR+ converging.
2. **Rounding to 1/255.** ``measure`` on ``avg:N`` and ``avg:N:rounded``: if the two are close
   at every N, rounding is not it.
3. **Averaging.** ``measure`` on ``current:N`` (the last iterate) and ``window:A-B`` (the average
   of iterations A to B only, a delayed average). If they rise as well, the averaging scheme is
   not it.
4. **A different game.** ``checkpoints`` retrains from the saved chance model and checks the
   2,000-iteration strategy is the shipped one, byte for byte: the model ``measure`` uses is the
   trained one. ``match`` walks the shipped game three ways (the best response's walk with the
   responder made to play a fixed strategy, ``expected_value``, and the trainer's own walk) and
   checks they agree on what a random strategy is worth from both seats: one tree, one chance
   model, one set of payoffs. What the best response knows beyond the strategy is its own memory,
   and ``recall`` checks that it is no more than that.
5. **A plain bug.** The anchors (Kuhn, Leduc) and the remembering game converging say the solver
   and the best response are right where the theory says they must be.

``exploiter`` trains an opponent held to the same buckets as the blueprint (it forgets too) with
CFR+, against the fixed strategy. What it wins is a lower bound on what a forgetting opponent can
win, next to the best response's upper bound.
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
import measure_blueprint as measuring  # noqa: E402
import train_blueprint as training  # noqa: E402

from poker_table.agents.abstraction import (  # noqa: E402
    FLOP,
    RIVER,
    TURN,
    Strategy,
    number,
    quantize,
    read_strategy,
)
from poker_table.agents.blueprint import shipped  # noqa: E402
from poker_table.solvers.cfr import (  # noqa: E402
    CHANCE,
    FOLD,
    SHOWDOWN,
    CFRPlus,
    Game,
    Node,
    _utility0,
    best_response_value,
    expected_value,
    regret_matching,
)
from poker_table.solvers.recall import (  # noqa: E402
    coarsen,
    equity_groups,
    lift,
    remember_groups,
    with_recall,
)

LIMIT_SECONDS = 30 * 60


def log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def load() -> str:
    """The load average, as uptime prints it."""
    out = subprocess.run(["uptime"], capture_output=True, text=True).stdout
    return out.strip().split("load average: ")[-1]


def shipped_game(groups: int = 0) -> Game:
    """The game the shipped file was trained in; ``groups`` keeps that many preflop groups
    after the flop, the game ``train_blueprint.py --groups`` trains (format 2)."""
    strategy = shipped()
    _, game = training.build(measuring.chances(strategy.cards), strategy.betting)
    if [n.index for n in number(strategy.root)] != [n.index for n in game.decisions]:
        raise SystemExit("the tree is not the shipped strategy's")
    if groups:
        game = remember_groups(game, equity_groups(game, groups))
    return game


def table_of(solver: CFRPlus) -> bytes:
    """The solver's average, rounded and laid out as a strategy file's table."""
    return b"".join(quantize(row) for rows in solver.average() for row in rows.tolist())


def flat(arrays) -> np.ndarray:
    return np.concatenate([a.ravel() for a in arrays])


def unflat(values: np.ndarray, like) -> list[np.ndarray]:
    out, at = [], 0
    for a in like:
        out.append(values[at : at + a.size].reshape(a.shape))
        at += a.size
    return out


def normalised(weights: np.ndarray) -> np.ndarray:
    total = weights.sum(axis=1, keepdims=True)
    uniform = np.full_like(weights, 1.0 / weights.shape[1])
    return np.where(total > 0, weights / np.where(total > 0, total, 1.0), uniform)


def rounded(strategy) -> list[np.ndarray]:
    """As the seat plays it: each row rounded to 1/255 the way the strategy file is."""
    return [
        np.frombuffer(b"".join(quantize(row) for row in rows.tolist()), dtype=np.uint8)
        .reshape(rows.shape)
        .astype(float)
        / 255
        for rows in strategy
    ]


# ----- checkpoints ---------------------------------------------------------------------------


def checkpoints(
    out: Path,
    iterations: int,
    at: list[int],
    minutes: float = 30,
    groups: int = 0,
    like: Strategy | None = None,
) -> None:
    """CFR+ on the shipped game (``groups``: the format-2 game), saved at each of ``at``.

    ``like`` is a strategy file trained in this game: when the run reaches that file's number of
    iterations, its rounded average must be that file's table, or the run stops there."""
    started = time.monotonic()
    log(f"start, load {load()}")
    game = shipped_game(groups)
    solver = CFRPlus(game)
    if like is not None and len(set(like.groups)) != groups:
        raise SystemExit(f"--like has {len(set(like.groups))} preflop groups, not {groups}")

    def tick(n: int) -> None:
        if time.monotonic() - started > minutes * 60:
            raise SystemExit(f"stopped at iteration {n}: past {minutes:g} minutes")
        if like is not None and n == like.header["iterations"]:
            same = table_of(solver) == like.table
            log(f"  iteration {n}: the rounded average is the --like file's table: {same}")
            if not same:
                raise SystemExit("this is not the game the --like file was trained in")
        if n in at:
            np.savez(
                out / f"cfr-{n}.npz", weights=flat(solver.weights), regrets=flat(solver.regrets)
            )
            log(f"  iteration {n}: saved, {(time.monotonic() - started) / 60:.1f} min")

    out.mkdir(parents=True, exist_ok=True)
    solver.run(iterations, tick)
    minutes = (time.monotonic() - started) / 60
    print(f"{iterations} iterations in {minutes:.1f} min, load {load()}")
    if not groups and iterations == shipped().header["iterations"]:
        same = table_of(solver) == shipped().table
        print(f"the {iterations}-iteration average, rounded, is the shipped strategy: {same}")
        if not same:
            raise SystemExit("the saved chance model is not the game the shipped file came from")


def variant(folder: Path, name: str, like) -> list[np.ndarray]:
    """``avg:N``, ``avg:N:rounded``, ``current:N``, ``window:A-B`` or ``shipped``."""
    if name == "shipped":
        return measuring.strategy_arrays(shipped())
    kind, _, rest = name.partition(":")
    when, _, rounding = rest.partition(":")
    if kind == "window":
        first, last = (np.load(folder / f"cfr-{n}.npz")["weights"] for n in when.split("-"))
        strategy = [normalised(w) for w in unflat(last - first, like)]
    else:
        saved = np.load(folder / f"cfr-{when}.npz")
        if kind == "avg":
            strategy = [normalised(w) for w in unflat(saved["weights"], like)]
        elif kind == "current":
            strategy = [regret_matching(r) for r in unflat(saved["regrets"], like)]
        else:
            raise SystemExit(f"not a variant: {name}")
    return rounded(strategy) if rounding == "rounded" else strategy


def measure(folder: Path, names: list[str], groups: int = 0) -> None:
    log(f"start, load {load()}")
    game = shipped_game(groups)
    like = game.uniform()
    for name in names:
        started = time.monotonic()
        strategy = variant(folder, name, like)
        sb, bb = (best_response_value(game, strategy, seat) * 1000 for seat in (0, 1))
        print(
            f"{name}: a best response wins {(sb + bb) / 2:.1f} mbb/hand ({sb:.0f} as the small "
            f"blind, {bb:.0f} as the big blind); {time.monotonic() - started:.0f} s, "
            f"load {load()}",
            flush=True,
        )


# ----- an exploiter that forgets -------------------------------------------------------------


def forgetting_exploiter(game: Game, strategy, seat: int, at: list[int]) -> list[float]:
    """What an opponent with the strategy's own buckets wins against it, trained by CFR+ with
    the strategy fixed, after each number of iterations in ``at``. The better of its average
    and its last strategy: each is a strategy that forgets, so either is a lower bound on the
    best one that does."""
    solver = CFRPlus(game)
    for node, rows, fixed in zip(game.decisions, solver.regrets, strategy, strict=True):
        if node.player != seat:
            rows[...] = fixed  # regret matching on a strategy row gives that row back
    found = []
    for n in at:
        solver.run(n - solver.iterations, players=(seat,))
        best = -np.inf
        for mine in (solver.average(), solver.current()):
            played = [
                mine[d.index] if d.player == seat else strategy[d.index] for d in game.decisions
            ]
            value = expected_value(game, played)
            best = max(best, value if seat == 0 else -value)
        found.append(best)
    return found


def exploiter(folder: Path, names: list[str], at: list[int]) -> None:
    log(f"start, load {load()}")
    game = shipped_game()
    like = game.uniform()
    for name in names:
        started = time.monotonic()
        strategy = variant(folder, name, like)
        sb, bb = (forgetting_exploiter(game, strategy, seat, at) for seat in (0, 1))
        for n, x, y in zip(at, sb, bb, strict=True):
            print(
                f"{name}: an exploiter that forgets wins at least {(x + y) / 2 * 1000:.1f} "
                f"mbb/hand ({x * 1000:.0f} as the small blind, {y * 1000:.0f} as the big blind) "
                f"after {n} iterations",
                flush=True,
            )
        print(f"  {time.monotonic() - started:.0f} s, load {load()}", flush=True)


# ----- the same small game, forgetting and remembering ---------------------------------------


def level_groups(buckets: int, levels: int, count: int, draws: bool) -> np.ndarray:
    """Group a street's buckets by strength level alone (dropping the draw flag on the flop and
    turn), ``count`` groups of about as many levels each."""
    level = np.arange(buckets) % levels if draws else np.arange(buckets)
    return (level * count) // levels


def small_game(game: Game, sizes: tuple[int, int, int, int]) -> Game:
    cards = shipped().cards
    flop, turn = cards.buckets(FLOP), cards.buckets(TURN)
    groups = [
        equity_groups(game, sizes[0]),  # the same groups the format-2 blueprint keeps
        level_groups(flop, flop // 2, sizes[1], draws=True),
        level_groups(turn, turn // 2, sizes[2], draws=True),
        level_groups(cards.buckets(RIVER), cards.buckets(RIVER), sizes[3], draws=False),
    ]
    return coarsen(game, groups)


def both_seats(game: Game, strategy) -> float:
    return (best_response_value(game, strategy, 0) + best_response_value(game, strategy, 1)) / 2


class OwnReachCFR(CFRPlus):
    """CFR+ whose regrets weigh a player's forgotten earlier states by its own reach.

    With imperfect recall, a state's counterfactual value adds up every earlier state the
    player may have come from, as if it had played to get here from each one. Here those earlier
    states count as often as the player's own strategy really came from them (each row rescaled
    to keep its total). With perfect recall it is plain CFR+. Tried as a fix on the small game;
    it did worse there, so the trainer does not use it.
    """

    def run(self, iterations: int, on_iteration=None, players=(0, 1)) -> None:
        for _ in range(iterations):
            self.iterations += 1
            for player in players:
                p0 = self.game.p0
                self._walk_own(self.game.root, p0, p0, p0, p0, player)
            if on_iteration is not None:
                on_iteration(self.iterations)

    def _walk_own(self, node: Node, r0, r1, both, cf, player: int):
        """``CFRPlus._walk``, weighing by own reach. ``both`` is chance times both players'
        reach; ``cf`` replaces the other player's reach in the updating player's regrets. At
        each new round it is ``both`` with each of the updater's rows rescaled to the total the
        other player's reach gives that row, so a row weighs the updater's earlier states by how
        often it played from them. Within a round only the other player's actions scale it."""
        kind = node.kind
        if kind == FOLD or kind == SHOWDOWN:
            return _utility0(node, self.game.share)
        if kind == CHANCE:
            move = self.game.transitions[node.round]
            n0 = np.einsum("ab,abcd->cd", r0, move)
            n1 = np.einsum("ab,abcd->cd", r1, move)
            nb = np.einsum("ab,abcd->cd", both, move)
            axis = 1 if player == 0 else 0
            other = n1 if player == 0 else n0
            target = other.sum(axis=axis, keepdims=True)
            have = nb.sum(axis=axis, keepdims=True)
            cf = np.where(have > 0, nb * (target / np.where(have > 0, have, 1.0)), other)
            value = self._walk_own(node.children[0], n0, n1, nb, cf, player)
            if np.ndim(value) == 0:
                return value
            return np.einsum("abcd,cd->ab", move, value)

        sigma = regret_matching(self.regrets[node.index])
        actor = node.player
        n = r0.shape[0]
        total = np.zeros((n, n))
        values = []
        for a, child in enumerate(node.children):
            column = sigma[:, a]
            weight = column[:, None] if actor == 0 else column[None, :]
            value = self._walk_own(
                child,
                r0 * weight if actor == 0 else r0,
                r1 * weight if actor == 1 else r1,
                both * weight,
                cf if actor == player else cf * weight,
                player,
            )
            total += weight * value
            values.append(value)
        if actor == player:
            if actor == 0:
                gains = np.stack([(cf * v).sum(axis=1) for v in values], axis=1)
                reach = r0.sum(axis=1)
            else:
                gains = np.stack([-(cf * v).sum(axis=0) for v in values], axis=1)
                reach = r1.sum(axis=0)
            expected = (sigma * gains).sum(axis=1, keepdims=True)
            regrets = self.regrets[node.index]
            np.maximum(regrets + gains - expected, 0.0, out=regrets)
            self.weights[node.index] += self.iterations * reach[:, None] * sigma
        return total


ARMS = {
    "forgets": "buckets that forget",
    "remembers": "buckets that remember",
    "own-reach": "buckets that forget, CFR+ weighing them by own reach",
}


def recall(sizes: tuple[int, int, int, int], at: list[int], arms: list[str], check: bool) -> None:
    started = time.monotonic()
    log(f"start, load {load()}")
    forgets = small_game(shipped_game(), sizes)
    remembers = with_recall(forgets)
    print(
        f"groups per street {sizes}: states {forgets.states} forgetting, {remembers.states} "
        "remembering"
    )
    rows = {}
    for name in arms:
        game = remembers if name == "remembers" else forgets
        solver = (OwnReachCFR if name == "own-reach" else CFRPlus)(game)
        for n in at:
            solver.run(n - solver.iterations)
            if time.monotonic() - started > LIMIT_SECONDS:
                raise SystemExit("past 30 minutes")
            avg, cur = solver.average(), solver.current()
            rows[name, n] = (both_seats(game, avg) * 1000, both_seats(game, cur) * 1000)
            log(
                f"  {name} {n}: {rows[name, n][0]:.1f} average, {rows[name, n][1]:.1f} current, "
                f"{(time.monotonic() - started) / 60:.1f} min"
            )
            if check and name == "forgets" and n == at[-1]:
                lifted = lift(game, avg)
                for seat in (0, 1):
                    a = best_response_value(game, avg, seat)
                    b = best_response_value(remembers, lifted, seat)
                    assert abs(a - b) < 1e-9, (seat, a, b)
                print(
                    "the best response wins the same against the forgetting strategy and "
                    "against it lifted into the remembering game"
                )
    print("A best response (it remembers) wins, in mbb/hand, against CFR+ on")
    print(
        "| iterations | "
        + " | ".join(f"{ARMS[a]}, {k}" for a in arms for k in ("average", "current"))
        + " |"
    )
    print("|---:|" + "---:|" * (2 * len(arms)))
    for n in at:
        cells = " | ".join(f"{rows[a, n][0]:.1f} | {rows[a, n][1]:.1f}" for a in arms)
        print(f"| {n:,} | {cells} |")
    print(f"{(time.monotonic() - started) / 60:.1f} min, load {load()}")


# ----- game match ----------------------------------------------------------------------------


def match(seed: int) -> None:
    started = time.monotonic()
    log(f"start, load {load()}")
    game = shipped_game()
    for r, move in enumerate(game.transitions[1:], start=1):
        assert np.allclose(move.sum(axis=(2, 3)), 1.0), f"round {r} chances do not add to 1"
    rng = np.random.default_rng(seed)
    strategy = [rows + 0.05 for rows in (rng.random(s.shape) for s in game.uniform())]
    strategy = [rows / rows.sum(axis=1, keepdims=True) for rows in strategy]
    value = expected_value(game, strategy)
    trainer = CFRPlus(game)
    trainer.regrets = [rows.copy() for rows in strategy]  # so it plays ``strategy``
    walked = float((game.p0 * trainer._walk(game.root, game.p0, game.p0, 0)).sum())
    forced = [best_response_value(game, strategy, seat, responder=strategy) for seat in (0, 1)]
    print(f"a random strategy is worth {value:+.12f} bb to the small blind by expected_value,")
    print(f"{walked:+.12f} by the trainer's walk, and {forced[0]:+.12f} and {-forced[1]:+.12f} by")
    print("the best response's walk with each seat in turn made to play it")
    for other in (walked, forced[0], -forced[1]):
        assert abs(other - value) < 1e-9, (other, value)
    print(f"they agree to 1e-9; {(time.monotonic() - started) / 60:.1f} min, load {load()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("checkpoints", help="CFR+ on the shipped game, saved along the way")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--iterations", type=int, default=2000)
    p.add_argument("--at", default="60,250,500,1000,2000")
    p.add_argument(
        "--minutes", type=float, default=30, help="stop past this; longer needs the owner's yes"
    )
    p.add_argument("--groups", type=int, default=0, help="the format-2 game with G groups")
    p.add_argument("--like", type=Path, help="a file trained in this game, checked on the way")
    p = sub.add_parser("measure", help="an exact best response against saved strategies")
    p.add_argument("folder", type=Path)
    p.add_argument("variants", nargs="+")
    p.add_argument("--groups", type=int, default=0, help="the format-2 game with G groups")
    p = sub.add_parser("exploiter", help="an opponent with the same buckets, trained by CFR+")
    p.add_argument("folder", type=Path)
    p.add_argument("variants", nargs="+")
    p.add_argument("--at", default="100,400", help="report after these many iterations")
    p = sub.add_parser("recall", help="one small game, with buckets that forget and remember")
    p.add_argument("--groups", default="3,2,2,3", help="groups preflop, flop, turn, river")
    p.add_argument("--at", default="30,100,300,1000")
    p.add_argument("--arms", default="forgets,remembers", help=f"some of {', '.join(ARMS)}")
    p.add_argument("--no-check", action="store_true", help="skip the lifted best response")
    p = sub.add_parser("match", help="the best response and the trainer walk the same game")
    p.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if args.command == "checkpoints":
        at = [int(n) for n in args.at.split(",")]
        like = read_strategy(args.like.read_bytes()) if args.like else None
        checkpoints(args.out, args.iterations, at, args.minutes, args.groups, like)
    elif args.command == "measure":
        measure(args.folder, args.variants, args.groups)
    elif args.command == "exploiter":
        exploiter(args.folder, args.variants, [int(n) for n in args.at.split(",")])
    elif args.command == "recall":
        sizes = tuple(int(n) for n in args.groups.split(","))
        arms = args.arms.split(",")
        if not set(arms) <= set(ARMS):
            parser.error(f"--arms takes {', '.join(ARMS)}")
        recall(sizes, [int(n) for n in args.at.split(",")], arms, not args.no_check)
    else:
        match(args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
