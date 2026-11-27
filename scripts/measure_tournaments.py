"""Reproduce the numbers in docs/tournaments.md: who survives a freezeout, not who wins chips.

    uv run python scripts/measure_tournaments.py [tournaments] [--level-hands N] [--seating S]

Every tournament is seeded (decks and bots), so the table is the same on every machine. The
question is whether the cash-game order still holds when the blinds rise and there is no rebuy.

--seating says who sits where: ``fixed`` keeps the order below for every tournament (the
original measurement), ``turned`` turns it one place a tournament, and ``drawn`` shuffles it
for each one, as an experiment file does. All three deal the same decks to the same bots.
"""

from __future__ import annotations

import argparse
import random
import time
from collections import Counter

from poker_table.agents.registry import make_agents
from poker_table.league import DEFAULT_SCHEDULE, TournamentConfig, run_tournament

SEATS = ["tag", "rock", "maniac", "station", "random"]
TOURNAMENTS = 500


def seated(seed: int, seating: str) -> list:
    # The bots are seeded per tournament too, so seed N is one repeatable tournament.
    agents = make_agents(SEATS, seed=seed)
    if seating == "turned":
        turn = seed % len(agents)
        return agents[turn:] + agents[:turn]
    if seating == "drawn":
        random.Random(f"seats/{seed}").shuffle(agents)
    return agents


def main(count: int = TOURNAMENTS, level_hands: int = 20, seating: str = "fixed") -> None:
    places: dict[str, list[int]] = {name: [] for name in SEATS}
    hands: list[int] = []
    levels: list[int] = []
    started = time.perf_counter()
    for seed in range(count):
        config = TournamentConfig(seed=seed, hands_per_level=level_hands)
        result = run_tournament(seated(seed, seating), config, keep_histories=False)
        if not result.complete:
            raise SystemExit(f"tournament {seed} hit the hand limit; raise max_hands")
        for name, place in result.finishes.items():
            places[name].append(place)
        hands.append(result.hands)
        levels.append(result.level)
    elapsed = time.perf_counter() - started

    schedule = f"{DEFAULT_SCHEDULE[0]} to {DEFAULT_SCHEDULE[-1]}"
    seats = "" if seating == "fixed" else f", seats {seating}"
    print(f"{count} freezeouts, 200 chips each, levels {schedule}, {level_hands} hands each{seats}")
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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tournaments", type=int, nargs="?", default=TOURNAMENTS)
    parser.add_argument("--level-hands", type=int, default=20, help="hands a level (20)")
    parser.add_argument("--seating", choices=["fixed", "turned", "drawn"], default="fixed")
    args = parser.parse_args()
    main(args.tournaments, args.level_hands, args.seating)
