"""Write docs/leaderboard.md and docs/leaderboard-heads-up.md from the folders in docs/results,
refusing any that fail.

uv run python scripts/build_leaderboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from poker_table.benchmark import (
    benchmark_problems,
    heads_up_problems,
    render_heads_up_leaderboard,
    render_leaderboard,
)

ROOT = Path(__file__).resolve().parent.parent


def checked(pattern: str, problems_of) -> list[Path] | None:
    folders = sorted(f for f in (ROOT / "docs" / "results").glob(pattern) if f.is_dir())
    bad = {folder.name: problems_of(folder) for folder in folders}
    bad = {name: problems for name, problems in bad.items() if problems}
    for name, problems in bad.items():
        print(f"{name}: {problems[0]}", file=sys.stderr)
    return None if bad else folders


def main() -> int:
    five = checked("bench-*", benchmark_problems)
    heads_up = checked("hu-*", heads_up_problems)
    if five is None or heads_up is None:
        return 1
    (ROOT / "docs" / "leaderboard.md").write_text(render_leaderboard(five), encoding="utf-8")
    print(f"docs/leaderboard.md: {len(five)} entrants")
    if heads_up:
        page = ROOT / "docs" / "leaderboard-heads-up.md"
        page.write_text(render_heads_up_leaderboard(heads_up), encoding="utf-8")
        print(f"docs/leaderboard-heads-up.md: {len(heads_up)} matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
