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
| Browser viewer | `uv run poker-table serve hands.jsonl --open` |
| Live table / play in browser | `uv run poker-table serve live.jsonl --live -n 20 --seats me:human,tag,maniac --open` |
| Sit down yourself | `uv run poker-table play -n 10 --seats me:human,tag,maniac` |
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
- `agents/llm.py` — `LLMAgent`: one `messages.create` per decision, JSON-schema structured
  output, cached personality system prompt, usage/cost in `Decision.meta`, Chen-score auto-fold
  gate, API errors → check/fold. Client is injectable (tests use a fake; never call the API in CI).
- `agents/personalities.py` — maniac / rock / nerd / storyteller prompts + effort + gate knobs.
- `agents/registry.py` — `make_agents(["tag", "alice:maniac", "llm:nerd", "b:llm:rock@model"])`.
- `table.py` — `play_hand()`: asks agents, sanitizes slips, records illegal decisions + reasoning.
- `history.py` — `HandHistory` (JSON/JSONL, `render(reasoning=True)`).
- `stats.py` — VPIP/PFR/3-bet/F3B/AF/WTSD/W$SD/bluff/illegal/bb-100 + `format_table`.
- `league.py` — `run_league()`: N hands, rotating button, seeded, top-up or carry stacks.
- `agents/human.py` — terminal seat (`--seats me:human,...`), kind `human`.
- `web/app.py` + `web/static/index.html` — `poker-table serve FILE`: FastAPI JSON API over a
  JSONL file (`/api/hands`, `/api/hands/{id}` with decision traces joined to action steps,
  `/api/stats`) and a single-file vanilla-JS replay viewer (paper-on-felt look, Source Sans 3).
- `web/live.py` — `serve --live`: `LiveSession` runs a league on a thread, appends to the JSONL
  and publishes SSE (`/api/events`); `WebHumanAgent` blocks in `act()` until `POST /api/act`;
  `/api/live` returns the pending turn as the viewer's hand shape (own cards only).
- `cli.py` — `play`, `stats`, `replay`, `serve`.
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
Done on branch `feat/engine` (PR #1): engine, evaluator, side pots, scripted bots, LLM seats
(structured output, personalities, cost accounting, auto-fold gate), table talk, human terminal
seat, hand histories, stats, league, CLI, browser replay viewer, live sessions with a browser
human seat. 211 tests green. HQ registered. **Not done yet, in this order:**

1. **Live smoke test of the LLM seat** — no API key on this machine yet. Run
   `ANTHROPIC_API_KEY=... uv run poker-table play -n 2 --seats llm:nerd,tag --show` and check
   the request shape (`output_config.format` json_schema + `effort`) is accepted; fix
   `agents/llm.py::_call` if the API rejects anything. Load the `claude-api` skill before
   touching that file.
2. **Per-model comparison**: same personality on opus-5 / sonnet-5 / haiku-4-5, report bb/100
   vs $/hand; commit the JSONL + leaderboard under `docs/` as the first published result.
3. **Web viewer polish** if wanted: bankroll-over-time chart on the leaderboard tab
   (load the `dataviz` skill first), seat filter, "hide cards until showdown" default.
4. Phase 2 poker-coach: see README.

## Gotchas / decisions
- `uv` lives in `~/.local/bin`, which is not on PATH in non-login shells.
- Pre-commit runs ruff; the format hook rewrites files, so re-`git add` if a commit fails.
- `LegalActions.min_raise_to` equals `max_raise_to` when only an all-in short of a full raise is
  possible; a raise smaller than the last full raise does not reopen the action (`Seat.acted`).
- Scripted bots hold their own `random.Random(seed)`: two `run_league` calls with the *same
  agent objects* diverge — build fresh agents for reproducibility tests.
- The web viewer is one HTML file with inline CSS/JS on purpose (no build step); the browser
  caches it aggressively during development — reload with a query string (`/?v=2`).
- `pkill -f "poker-table serve"` kills the shell that runs it too; use `pgrep -f "[s]erve"`.
- SSE + Starlette `TestClient`: a stream that never ends hangs the test; `LiveSession.sse()`
  returns once the session is finished and its queue is drained, so tests run it after `join()`.
- The live human turn is rendered by reusing the replay renderer: `live_view_payload()` reports
  *starting* stacks (current + contributed) so `stateAt()` can replay the public events.
- Stats: a BB check is not VPIP; 3-bet opportunity = acting with exactly one raise in front and
  not being the opener; bluff = postflop bet/raise with no pair and no draw (uses shown cards).
