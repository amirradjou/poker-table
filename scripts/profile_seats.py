"""How each seat plays, by the HUD numbers: VPIP, PFR, fold to a bet, aggression and its label.

    uv run python scripts/profile_seats.py fish lag calculator trapper tilter

Each seat sits in seat 0 of the benchmark's five-handed table (tag, rock, maniac, station) for
2,000 hands from seed 2026: the same seats, deals and seed as the seat's own arm of
``scripts/measure_vs_tag.py SEAT --five-handed``. The numbers are ``stats.compute_stats`` and
the label is ``reads.style_of``, the HUD that ``exploiter`` and the ``reads`` aid use. The time
is the mean of the seat's ``latency_ms`` over its decisions.
"""

from __future__ import annotations

import argparse
import statistics

from poker_table.agents.reads import style_of
from poker_table.agents.registry import make_agents
from poker_table.league import LeagueConfig, run_league
from poker_table.stats import compute_stats

SEED = 2026
HANDS = 2000
OPPONENTS = ("tag", "rock", "maniac", "station")
SEAT = "entrant"


def profile(seat: str, hands: int = HANDS, seed: int = SEED) -> dict[str, object]:
    agents = make_agents([f"{SEAT}:{seat}", *OPPONENTS], seed=seed)
    result = run_league(agents, LeagueConfig(hands=hands, buy_in=200, seed=seed))
    s = compute_stats(result.histories)[SEAT]
    faced = s.postflop_folds + s.postflop_calls
    times = [
        d.latency_ms
        for h in result.histories
        for d in h.decisions
        if h.players[d.seat].name == SEAT
    ]
    return {
        "seat": seat,
        "vpip": s.vpip or 0.0,
        "pfr": s.pfr or 0.0,
        "share": (s.pfr or 0.0) / s.vpip if s.vpip else 0.0,
        "fold_to_bet": s.postflop_folds / faced if faced else None,
        "aggression": s.aggression_factor,
        "style": style_of(s),
        "ms": statistics.fmean(times) if times else 0.0,
        "decisions": len(times),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("seats", nargs="+", help="seats as --seats takes them: fish, lag, ...")
    parser.add_argument("--hands", type=int, default=HANDS, help=f"hands a seat ({HANDS:,})")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    print(
        f"Each seat in seat 0 with {', '.join(OPPONENTS)}: {args.hands:,} hands from seed "
        f"{args.seed}."
    )
    print()
    print(
        "| seat | VPIP | PFR | PFR/VPIP | folds to a bet | aggression | style_of | ms a decision |"
    )
    print("|---|---:|---:|---:|---:|---:|---|---:|")
    for seat in args.seats:
        p = profile(seat, args.hands, args.seed)
        fold = "-" if p["fold_to_bet"] is None else f"{p['fold_to_bet']:.0%}"
        af = p["aggression"]
        aggression = "never calls" if af == float("inf") else f"{af:.1f}"
        print(
            f"| {seat} | {p['vpip']:.0%} | {p['pfr']:.0%} | {p['share']:.2f} | {fold} "
            f"| {aggression} | {p['style'] or '-'} | {p['ms']:.1f} |",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
