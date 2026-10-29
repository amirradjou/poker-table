"""Measure the aids: does a model decide better when handed the numbers a strong player would
work out? Prints one table row per set of aids.

    ollama pull qwen2.5:7b
    uv run python scripts/measure_aids.py [model] [questions]

Every row is one `poker-table eval` run — the same questions (150 seeded bot hands graded by
the coach, half mistakes and half fine plays) asked of the same model with different aids —
so any single row can be re-run by hand:

    uv run poker-table eval --seats llm:nerd@ollama/qwen2.5:7b --aids odds,ranges -n 120

Reads and notes are left out: they are built from earlier hands, and eval questions are
asked one at a time, with no session behind them.
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

from poker_table.cli import main as cli

CONFIGS = [
    ("none", "none"),
    ("odds", "odds"),
    ("odds + ranges", "odds,ranges"),
    ("odds + ranges + playbook", "odds,ranges,playbook"),
]


def run(model: str, questions: int, aids: str, out: Path) -> dict:
    argv = ["eval", "--seats", f"llm:nerd@{model}", "--aids", aids, "-n", str(questions)]
    buffer = io.StringIO()
    started = time.time()
    code = cli([*argv, "--json", str(out)], out=buffer)
    if code != 0:
        raise SystemExit(f"eval failed ({code}): {' '.join(argv)}")
    print(buffer.getvalue(), flush=True)
    data = json.loads(out.read_text())
    data["minutes"] = round((time.time() - started) / 60, 1)
    return data


def main(argv: list[str]) -> int:
    model = argv[1] if len(argv) > 1 else "ollama/qwen2.5:7b"
    questions = int(argv[2]) if len(argv) > 2 else 120
    scratch = Path("measure_aids")
    scratch.mkdir(exist_ok=True)
    rows = []
    for label, aids in CONFIGS:
        data = run(model, questions, aids, scratch / f"{aids.replace(',', '-')}.json")
        (row,) = data["scores"]
        rows.append((label, row, data["minutes"]))
        print(f"{label}: {json.dumps(row)}", flush=True)
    print(f"\n{model}, {questions} questions from {data['source']}\n")
    print("| aids | agrees | fixes mistakes | keeps good plays | errors | median s |")
    print("|---|---|---|---|---|---|")
    for label, row, _minutes in rows:
        print(
            f"| {label} | {row['agrees']:.0%} | {row['fixes mistakes']:.0%} | "
            f"{row['keeps good plays']:.0%} | {row['errors']} | {row['ms'] / 1000:.1f} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
