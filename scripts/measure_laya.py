"""Reproduce the numbers in docs/laya.md for a Laya checkpoint.

    uv sync --extra laya
    uv run python scripts/measure_laya.py [model] [subfolder]

Three measurements, all on seeded sessions so they can be compared run to run:
1. agreement with the coach on flagged spots (and the always-fold baseline on the same spots),
2. a table with the confidence gate on — where most spots may be played by the fallback,
3. the same table with the gate off, which is what the model itself is worth.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter

import laya

from poker_table.agents.laya import LayaAgent, build_question
from poker_table.agents.scripted import CallingStation, TightAggressive
from poker_table.coach.drills import spots_from
from poker_table.coach.facts import tag_hands
from poker_table.league import LeagueConfig, run_league
from poker_table.stats import compute_stats, format_table

HANDS = 40
SEED = 2026


def bots() -> list:
    return [
        TightAggressive("tag", seed=1),
        CallingStation("station"),
        TightAggressive("rock", tightness=2, aggression=0.3, seed=2),
    ]


def agreement(runner, spots) -> dict:
    """How often the model picks an action the charts and the maths accept."""
    seat = LayaAgent("laya", client=runner, confidence=0.0)  # no gate: measure the model
    agree, latencies, confidences = 0, [], []
    for spot in spots:
        started = time.time()
        decision = seat.act(spot.view)
        latencies.append((time.time() - started) * 1000)
        confidences.append(decision.meta.get("confidence", 0.0))
        agree += decision.action.type in spot.accepted
    gold = Counter()
    for spot in spots:
        for action in build_question(spot.view)[0].values():
            if action.type in spot.accepted:
                gold[action.type.value] += 1
                break
    return {
        "asked": len(spots),
        "agree": agree,
        "rate": agree / max(1, len(spots)),
        "majority_baseline": max(gold.values()) / max(1, sum(gold.values())) if gold else 0.0,
        "gold": dict(gold),
        "median_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "mean_confidence": round(sum(confidences) / max(1, len(confidences)), 3),
    }


def table(runner, *, confidence: float) -> dict:
    seat = LayaAgent("laya", client=runner, confidence=confidence)
    result = run_league([seat, *bots()], LeagueConfig(hands=HANDS, seed=SEED))
    stats = compute_stats(result.histories)
    print(format_table(stats), flush=True)
    picked = Counter(
        d.applied.split(" ")[0]
        for h in result.histories
        for d in h.decisions
        if h.players[d.seat].name == "laya"
    )
    return {
        "gate": confidence,
        "bb100": {name: round(s.bb_per_100, 1) for name, s in stats.items()},
        "decisions": seat.usage.decisions,
        "gated": seat.usage.gated,
        "errors": seat.usage.errors,
        "avg_ms": round(seat.usage.avg_latency_ms or 0, 1),
        "chose": dict(picked),
    }


def main(argv: list[str]) -> int:
    model = argv[1] if len(argv) > 1 else "convaiinnovations/laya"
    subfolder = argv[2] if len(argv) > 2 else None
    runner = laya.load(model, subfolder=subfolder)

    histories = run_league(bots(), LeagueConfig(hands=60, seed=99)).histories
    facts = tag_hands(histories, "station", samples=80)
    spots = spots_from(histories, facts)[:80]
    out = {"model": model, "subfolder": subfolder, "spots": agreement(runner, spots)}
    print("agreement:", json.dumps(out["spots"]), flush=True)

    out["gated"] = table(runner, confidence=0.40)
    print("with the gate:", json.dumps(out["gated"]), flush=True)
    out["ungated"] = table(runner, confidence=0.0)
    print("without the gate:", json.dumps(out["ungated"]), flush=True)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
