"""Does a menu of named sizes change how a small local model bets?

    ollama pull qwen2.5:3b
    uv run python scripts/measure_sizes.py [hands] [folder]

Plays the published local league's seats on its decks with the `sizes` aid on, then compares the
same hands with what that league did. Free, and about a minute a hand. Run it again with the same
folder to reread the results without playing them again.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from poker_table.experiments import run_experiment

ROOT = Path(__file__).resolve().parent.parent
PUBLISHED = ROOT / "docs" / "results" / "local-league" / "hands.jsonl"
MODEL = "ollama/qwen2.5:3b"
ROWS = (
    "model decisions",
    "after the flop",
    "bets and raises",
    "of those all in",
    "asked for more than a stack",
    "illegal",
    "failed",
    "hands all in preflop",
)


def facts(path: Path, hands: int) -> tuple[collections.Counter[str], list[int]]:
    played = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()][:hands]
    out: collections.Counter[str] = collections.Counter()
    for hand in played:
        seats = {p["seat"]: p for p in hand["players"]}
        actions = [e for e in hand["events"] if e["kind"] == "action"]
        for decision, event in zip(hand["decisions"], actions, strict=True):
            seat = seats[decision["seat"]]
            if not seat["kind"].startswith("llm") or "cost_usd" not in decision["meta"]:
                continue
            out["model decisions"] += 1
            out["after the flop"] += decision["street"] != "preflop"
            out["illegal"] += decision["illegal"]
            out["failed"] += bool(decision["meta"].get("failed"))
            verb, *amount = decision["requested"].split()
            if verb in ("bet", "raise") and amount and int(amount[0]) > seat["stack"]:
                out["asked for more than a stack"] += 1
            if decision["applied"].split()[0] in ("bet", "raise"):
                out["bets and raises"] += 1
                out["of those all in"] += bool(event["all_in"])
        out["hands all in preflop"] += any(
            e["all_in"] and e["street"] == "preflop" for e in hand["events"]
        )
    return out, [hand["seed"] for hand in played]


def main() -> int:
    hands = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    folder = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("results") / "measure-sizes"
    folder.mkdir(parents=True, exist_ok=True)
    aids = '["odds", "ranges", "reads", "notes", "playbook", "sizes"]'
    (folder / "agents.toml").write_text(
        "".join(
            f'[agents.{p}-s]\ntype = "llm"\nextends = "{p}"\nmodel = "{MODEL}"\naids = {aids}\n\n'
            for p in ("nerd", "maniac")
        ),
        encoding="utf-8",
    )
    league = folder / "sizes.toml"
    league.write_text(
        'name = "sizes"\n'
        'seats = ["nerd:nerd-s", "maniac:maniac-s", "tag", "station", "random"]\n'
        f'agents = ["{(folder / "agents.toml").resolve()}"]\n'
        f"hands = {hands}\nseed = 2026\ncoach = false\n",
        encoding="utf-8",
    )
    played = folder / "sizes" / "hands.jsonl"
    run_experiment(league, folder, hands=hands, resume=played.exists())
    before, decks_before = facts(PUBLISHED, hands)
    after, decks_after = facts(played, hands)
    print(f"{hands} hands, same decks as the published league: {decks_before == decks_after}")
    print(f"{'':30}{'bare numbers':>14}{'named sizes':>13}")
    for row in ROWS:
        print(f"{row:30}{before[row]:>14}{after[row]:>13}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
