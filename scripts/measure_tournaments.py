"""Reproduce the numbers in docs/tournaments.md: who survives a freezeout, not who wins chips.

    uv run python scripts/measure_tournaments.py [tournaments]

Every tournament is seeded (decks and bots), so the table is the same on every machine. The
question is whether the cash-game order still holds when the blinds rise and there is no rebuy.
"""

from __future__ import annotations

import sys
import time
from collections import Counter

from poker_table.agents.registry import make_agents
from poker_table.league import DEFAULT_SCHEDULE, TournamentConfig, run_tournament

SEATS = ["tag", "rock", "maniac", "station", "random"]
TOURNAMENTS = 500


def main(count: int = TOURNAMENTS) -> None:
    places: dict[str, list[int]] = {name: [] for name in SEATS}
    hands: list[int] = []
    levels: list[int] = []
    started = time.perf_counter()
    for seed in range(count):
        # The bots are seeded per tournament too, so seed N is one repeatable tournament.
        result = run_tournament(
            make_agents(SEATS, seed=seed), TournamentConfig(seed=seed), keep_histories=False
        )
        if not result.complete:
            raise SystemExit(f"tournament {seed} hit the hand limit; raise max_hands")
        for name, place in result.finishes.items():
            places[name].append(place)
        hands.append(result.hands)
        levels.append(result.level)
    elapsed = time.perf_counter() - started

    schedule = f"{DEFAULT_SCHEDULE[0]} to {DEFAULT_SCHEDULE[-1]}"
    print(f"{count} freezeouts, 200 chips each, levels {schedule}, 20 hands each")
    print(f"{elapsed:.1f}s total, {elapsed / count * 1000:.0f} ms each")
    ordered = sorted(hands)
    print(
        f"length: median {ordered[count // 2]} hands "
        f"(shortest {ordered[0]}, longest {ordered[-1]}), "
        f"median finishing level {sorted(levels)[count // 2]}"
    )
    header = ["name", "wins", "mean place", *(f"{p}." for p in range(1, len(SEATS) + 1))]
    rows = [header]
    for name in sorted(SEATS, key=lambda n: sum(places[n]) / len(places[n])):
        counted = Counter(places[name])
        rows.append(
            [
                name,
                f"{counted[1] / count:.1%}",
                f"{sum(places[name]) / count:.2f}",
                *(str(counted[p]) for p in range(1, len(SEATS) + 1)),
            ]
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    for row in rows:
        sized = enumerate(zip(row, widths, strict=True))
        print("  ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in sized).rstrip())


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else TOURNAMENTS)
