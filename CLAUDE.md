# poker-table

No-limit Texas hold'em table where LLM agents with different personalities play each other for
play money, with scripted baselines and (later) humans at the same table. Every hand is logged as
a hand history with each seat's private reasoning attached.

## Stack
- Language/runtime: Python 3.12
- Package manager: uv
- Tests: `uv run pytest`
- Lint/format: `uv run ruff check . && uv run ruff format --check .`

## Commands
| Task | Command |
|------|---------|
| Install deps | `uv sync` |
| Run | `uv run poker-table --help` |
| Test | `uv run pytest` |
| Lint + format | `uv run ruff check . && uv run ruff format .` |

## Layout
- `src/poker_table/` — application code
- `tests/` — tests mirror `src/` layout

## Conventions
- See global preferences in `~/.claude/CLAUDE.md` (conventional commits, feature branches, etc.).
- Run tests and lint before declaring work done.
- The engine is deterministic: every hand takes a seed and never reads global random state.
- The engine never exposes another seat's hole cards through a `SeatView`; if a feature needs
  them (replays, stats), read the finished `HandHistory` instead.

## Gotchas / decisions
- `uv` lives in `~/.local/bin`, which is not on PATH in non-login shells.
