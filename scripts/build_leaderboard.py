"""Write docs/leaderboard.md from the benchmark folders in docs/results, refusing any that fail.

uv run python scripts/build_leaderboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from poker_table.benchmark import benchmark_problems, render_leaderboard

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    folders = sorted((ROOT / "docs" / "results").glob("bench-*"))
    bad = {folder.name: benchmark_problems(folder) for folder in folders}
    bad = {name: problems for name, problems in bad.items() if problems}
    if bad:
        for name, problems in bad.items():
            print(f"{name}: {problems[0]}", file=sys.stderr)
        return 1
    (ROOT / "docs" / "leaderboard.md").write_text(render_leaderboard(folders), encoding="utf-8")
    print(f"docs/leaderboard.md: {len(folders)} entrants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
