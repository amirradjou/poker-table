# poker-table

No-limit Texas hold'em table where LLM agents with different personalities play each other for
play money, with scripted baselines and (later) humans at the same table. Every hand is logged as
a hand history with each seat's private reasoning attached.

## Stack
- Language/runtime: Python 3.12
- Package manager: uv (`uv` lives in `~/.local/bin`, not on PATH in non-login shells)
- Tests: `uv run pytest`
- Lint/format: `uv run ruff check . && uv run ruff format --check .`

## Commands
| Task | Command |
|------|---------|
| Install deps | `uv sync` |
| Play a session | `uv run poker-table play -n 500 --seats tag,rock,maniac,station,random -o hands.jsonl` |
| Watch hands live | `uv run poker-table play -n 5 --seats tag,maniac --show` |
| Leaderboard from a file | `uv run poker-table stats hands.jsonl` |
| Replay with reasoning | `uv run poker-table replay hands.jsonl --hand 7 -r` |
| Test | `uv run pytest` |
| Lint + format | `uv run ruff check . && uv run ruff format .` |

## Layout (src/poker_table/)
- `cards.py` — Card/Rank/Suit, seeded `Deck`, `Deck.stacked()` for deterministic tests.
- `evaluator.py` — best 5-of-7 `HandRank` (comparable; `describe()` for showdown text).
- `pots.py` — pure side-pot math: `build_pots`, `uncalled_amount`, `split_amount`.
- `engine.py` — `Hand` state machine: `actor` → `legal_actions()` → `apply(action)`; emits `events`.
- `agents/base.py` — `SeatView` (what a seat may know), `Decision`, `Agent` protocol, `make_view`.
- `agents/scripted.py` — RandomAgent, CallingStation, TightAggressive (rock via knobs), Maniac.
- `agents/strength.py` — Chen score, made-hand class, draws (shared by bots and stats).
- `agents/registry.py` — `make_agents(["tag", "alice:maniac"])` for the CLI.
- `table.py` — `play_hand()`: asks agents, sanitizes slips, records illegal decisions + reasoning.
- `history.py` — `HandHistory` (JSON/JSONL, `render(reasoning=True)`).
- `stats.py` — VPIP/PFR/3-bet/F3B/AF/WTSD/W$SD/bluff/illegal/bb-100 + `format_table`.
- `league.py` — `run_league()`: N hands, rotating button, seeded, top-up or carry stacks.
- `cli.py` — `play`, `stats`, `replay`.
- `tests/` mirror the modules; `test_engine_betting.py` has a 400-hand random fuzz.

## Conventions
- See global preferences in `~/.claude/CLAUDE.md` (conventional commits, feature branches, etc.).
- Run tests and lint before declaring work done. One commit per logical step.
- The engine is deterministic: every hand takes a seed and never reads global random state.
- The engine never exposes another seat's hole cards through a `SeatView`; if a feature needs
  them (replays, stats), read the finished `HandHistory` instead.
- Amounts in `Action.bet/raise_to` are street totals ("raise to"), never increments.
- Add behaviour to `Hand` with a test first (stacked deck + explicit actions), then wire agents.

## Status / how to continue (as of 2026-09-17)
Done on branch `feat/engine` (PR open): engine, evaluator, side pots, scripted bots, hand
histories, stats, league, CLI. 186 tests green. **Not done yet, in this order:**

1. **LLM seats** (`agents/llm.py`): load the `claude-api` skill first. Use the Anthropic SDK with
   a single forced tool `act` whose input schema is
   `{action: fold|check|call|bet|raise, amount?: int, table_talk?: str, reasoning: str}`; prompt
   body = `view.describe()`; system prompt = personality. Register kinds like `llm:maniac`,
   `llm:rock`, `llm:nerd`, `llm:storyteller` in `agents/registry.py` (needs `ANTHROPIC_API_KEY`,
   fail with a clear error otherwise). Record model, input/output tokens and cost per decision
   in `DecisionRecord` (add fields) so `stats.py` can show cost per hand. Keep `table.py`'s
   sanitizer as the only place illegal actions are handled. Add a "cheap where it doesn't
   matter" gate: fold-to-a-raise-with-junk preflop via `chen_score` without a model call.
   Mock the client in tests; never call the API in CI.
2. **Table talk visibility**: `DecisionRecord.table_talk` is recorded but not shown to other
   seats yet — add a `talk` list to `SeatView` built from previous decisions in the hand.
3. **Tune TightAggressive**: VPIP is ~8% 5-handed (too tight); loosen `_open_threshold` /
   "call a raise" rule and re-check `tests/test_scripted.py`.
4. **Web UI / human seat** (later): FastAPI + websocket, a `HumanAgent` that awaits input.
5. **Register in HQ**: add `poker-table` to `~/Projects/hq/projects.yaml` (track `hobby`,
   local `~/Projects/Fun/poker-table`) and write `roadmaps/poker-table.md`.
6. Phase 2 poker-coach: see README.

## Gotchas / decisions
- `uv` lives in `~/.local/bin`, which is not on PATH in non-login shells.
- Pre-commit runs ruff; the format hook rewrites files, so re-`git add` if a commit fails.
- `LegalActions.min_raise_to` equals `max_raise_to` when only an all-in short of a full raise is
  possible; a raise smaller than the last full raise does not reopen the action (`Seat.acted`).
- Scripted bots hold their own `random.Random(seed)`: two `run_league` calls with the *same
  agent objects* diverge — build fresh agents for reproducibility tests.
- Stats: a BB check is not VPIP; 3-bet opportunity = acting with exactly one raise in front and
  not being the opener; bluff = postflop bet/raise with no pair and no draw (uses shown cards).
