"""How nash:pushfold does with short stacks, heads-up against each bot, next to tag doing the same.

    uv run python scripts/measure_pushfold.py     # prints the table in docs/solver-seats.md

Every match is 2,000 hands on the same decks (seed 2026), the entrant in seat 0, the stacks
topped up to the same depth every hand, blinds 1/2, no ante. Results are all-in adjusted
bb/100 with a 95% interval (``margins.py``), the same numbers the leaderboards use.
"""

from __future__ import annotations

import subprocess
import sys
import time

from poker_table.agents.registry import make_agents
from poker_table.league import LeagueConfig, run_league
from poker_table.margins import seat_margins

DEPTHS = (5, 10, 15)  # big blinds
OPPONENTS = ("tag", "rock", "maniac", "station")
ENTRANTS = ("nash:pushfold", "tag")
HANDS = 2000
SEED = 2026


def match(entrant: str, opponent: str, depth: int) -> tuple[float, float, str]:
    agents = make_agents([f"entrant:{entrant}", opponent], seed=SEED)
    result = run_league(agents, LeagueConfig(hands=HANDS, buy_in=depth * 2, seed=SEED))
    for history in result.histories:
        for d in history.decisions:
            if history.players[d.seat].name == "entrant" and (
                d.illegal or d.reasoning.startswith("agent error")
            ):
                raise SystemExit(f"{entrant} broke in hand {history.hand_id}: {d.reasoning}")
    margin = seat_margins(result.histories)["entrant"]
    usage = getattr(agents[0], "usage", None)
    share = "-"
    if usage is not None and usage.decisions:
        share = f"{(usage.own + usage.translated) / usage.decisions:.0%}"
    return margin.adjusted_bb_per_100, margin.adjusted_margin or 0.0, share


def main() -> int:
    started = time.monotonic()
    print(f"start: {subprocess.run(['uptime'], capture_output=True, text=True).stdout.strip()}",
          file=sys.stderr)  # fmt: skip
    print("| entrant | depth | " + " | ".join(f"vs {o}" for o in OPPONENTS) + " | chart's share |")
    print("|---|---:|" + "---:|" * len(OPPONENTS) + "---:|")
    for depth in DEPTHS:
        for entrant in ENTRANTS:
            cells, shares = [], []
            for opponent in OPPONENTS:
                adjusted, margin, share = match(entrant, opponent, depth)
                cells.append(f"{adjusted:+.0f} ±{margin:.0f}")
                shares.append(share)
            shown = shares[0] if len(set(shares)) == 1 else "/".join(shares)
            print(f"| {entrant} | {depth} bb | " + " | ".join(cells) + f" | {shown} |", flush=True)
    load = subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()
    print(f"took {(time.monotonic() - started) / 60:.1f} min; end: {load}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
