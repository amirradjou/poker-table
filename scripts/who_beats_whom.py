"""Who beats whom: every bot against every other, heads-up, with an interval on every cell.

    uv run python scripts/who_beats_whom.py                       # the bots, 2,000 hands a pair
    uv run python scripts/who_beats_whom.py --kinds tag,rock,cfr:blueprint --hands 400
    uv run python scripts/who_beats_whom.py --out docs/who-beats-whom.md   # the docs page

Each pair plays one heads-up match from seed 2026 at the heads-up track's stakes (blinds 1/2,
200 chips topped up every hand), the first of the two kinds in seat 0. The button moves every
hand, so each sits in each position half the time. A cell is the row's all-in adjusted bb/100
against the column, with a 95% interval. Heads-up is zero-sum, so the column's cell against the
row is the same number with the sign turned. A win is in bold only when its interval is wholly
above zero; any other cell is too close to call, whatever its sign. "Beats" and "loses to"
count only those clear cells.

A match with an illegal move or an "agent error" is refused, because the table folds for a seat
that crashes and the cell would then look like a legal, losing one.
"""

from __future__ import annotations

import argparse
import itertools
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from poker_table.agents.registry import make_agents
from poker_table.league import LeagueConfig, run_league
from poker_table.margins import seat_margins

SEED = 2026
HANDS = 2000
# nash:pushfold is left out: at 100 big blinds it hands every spot to tag (its row in
# docs/leaderboard-heads-up.md is tag's), so its row here would be tag's again.
KINDS = ("tag", "rock", "exploiter", "maniac", "station", "random", "cfr:blueprint")


@dataclass(frozen=True, slots=True)
class Cell:
    first: str  # the kind in seat 0
    second: str
    hands: int
    value: float  # the first kind's all-in adjusted bb/100 against the second
    margin: float | None


def load() -> str:
    out = subprocess.run(["uptime"], capture_output=True, text=True).stdout
    return out.strip().split("load average: ")[-1]


def match(first: str, second: str, hands: int = HANDS, seed: int = SEED) -> Cell:
    """One heads-up match, ``first`` in seat 0."""
    agents = make_agents([first, second], seed=seed)
    result = run_league(agents, LeagueConfig(hands=hands, buy_in=200, seed=seed))
    for history in result.histories:
        for d in history.decisions:
            if d.illegal or d.reasoning.startswith("agent error"):
                who = history.players[d.seat].name
                raise SystemExit(f"{who} broke in hand {history.hand_id}: {d.reasoning}")
    m = seat_margins(result.histories)[agents[0].name]
    return Cell(first, second, m.hands, m.adjusted_bb_per_100, m.adjusted_margin)


def play(kinds: list[str], hands: int = HANDS, seed: int = SEED) -> list[Cell]:
    """Every pair once, in the order ``kinds`` lists them, the first of each pair in seat 0."""
    return [match(a, b, hands, seed) for a, b in itertools.combinations(kinds, 2)]


def results(cells: list[Cell]) -> dict[tuple[str, str], tuple[float, float | None]]:
    """Every ordered pair: the row's result against the column, from the one match they played."""
    out = {}
    for c in cells:
        out[c.first, c.second] = (c.value, c.margin)
        out[c.second, c.first] = (-c.value, c.margin)
    return out


def clear(value: float, margin: float | None) -> bool:
    return margin is not None and abs(value) > margin


@dataclass(frozen=True, slots=True)
class Row:
    kind: str
    beats: int
    loses_to: int
    average: float  # the mean of its cells, every opponent counted once


def rows(kinds: list[str], cells: list[Cell]) -> list[Row]:
    """The kinds in the grid's order: most clear wins net of clear losses, then the average."""
    got = results(cells)
    out = []
    for k in kinds:
        mine = [got[k, o] for o in kinds if o != k]
        out.append(
            Row(
                k,
                beats=sum(1 for v, m in mine if v > 0 and clear(v, m)),
                loses_to=sum(1 for v, m in mine if v < 0 and clear(v, m)),
                average=statistics.fmean(v for v, _ in mine),
            )
        )
    return sorted(out, key=lambda r: (r.loses_to - r.beats, -r.average))


def cell_text(value: float, margin: float | None) -> str:
    text = f"{value:+.0f} ±{margin:.0f}" if margin is not None else f"{value:+.0f}"
    return f"**{text}**" if value > 0 and clear(value, margin) else text


def grid(kinds: list[str], cells: list[Cell]) -> str:
    got = results(cells)
    order = [r.kind for r in rows(kinds, cells)]
    lines = [
        "| | " + " | ".join(f"vs {o}" for o in order) + " | beats | loses to |",
        "|---|" + "---:|" * len(order) + "---:|---:|",
    ]
    for r in rows(kinds, cells):
        texts = ["·" if o == r.kind else cell_text(*got[r.kind, o]) for o in order]
        lines.append(f"| {r.kind} | " + " | ".join(texts) + f" | {r.beats} | {r.loses_to} |")
    return "\n".join(lines)


def page(kinds: list[str], cells: list[Cell], command: str, seed: int, took: float) -> str:
    hands = cells[0].hands if cells else 0
    return f"""# Who beats whom

Every bot against every other, heads-up: {len(cells)} matches of {hands:,} hands, each from seed
{seed}, at blinds 1/2 with 200 chips topped up every hand (100 big blinds). A cell is the row's
all-in adjusted bb/100 against the column, with a 95% interval. A win is in bold only when its
interval is wholly above zero; any other cell is too close to call. Heads-up is zero-sum, so
each match fills two cells, one the other's mirror. "Beats" and "loses to" count clear cells only.

{grid(kinds, cells)}

Rows are ordered by clear wins less clear losses, then by their average cell. The order says
nothing where two rows' cells overlap. A bot that wins against one style and loses to another
is the point of the grid: no row beats every style by the most.

Rerun it ({took:.0f} minutes here):

```sh
{command}
```
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--kinds", default=",".join(KINDS), help="seat kinds, as --seats takes")
    parser.add_argument("--hands", type=int, default=HANDS, help=f"hands a match ({HANDS:,})")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, help="write the docs page here instead of the table")
    args = parser.parse_args()
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    if len(set(kinds)) != len(kinds) or len(kinds) < 2:
        parser.error("--kinds needs two or more different kinds")

    started = time.monotonic()
    print(f"start: load {load()}", file=sys.stderr)
    cells = play(kinds, args.hands, args.seed)
    took = (time.monotonic() - started) / 60
    print(f"took {took:.1f} min; end: load {load()}", file=sys.stderr)
    if args.out is None:
        print(grid(kinds, cells))
        return 0
    command = "uv run python scripts/who_beats_whom.py"
    if kinds != list(KINDS):
        command += f" --kinds {','.join(kinds)}"
    if args.hands != HANDS:
        command += f" --hands {args.hands}"
    if args.seed != SEED:
        command += f" --seed {args.seed}"
    command += f" --out {args.out}"
    args.out.write_text(page(kinds, cells, command, args.seed, took))
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
