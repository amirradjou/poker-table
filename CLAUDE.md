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
| Import site hands | `uv run poker-table import HH*.txt -o real.jsonl` |
| Leak report | `uv run poker-table coach live.jsonl --player me [--narrate]` |
| Drill flagged spots | `uv run poker-table drill live.jsonl --player me -n 10` |
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
  `/api/stats`, `/api/bankroll`) and a single-file vanilla-JS replay viewer (paper-on-felt look,
  Source Sans 3; the bankroll chart uses the dataviz reference palette, validated on `#efe9da`).
- `web/live.py` — `serve --live`: `LiveSession` runs a league on a thread, appends to the JSONL
  and publishes SSE (`/api/events`): `hand_start` (seats + kinds; everyone's cards only in
  spectator mode), `acting`, one `step` per engine event (decision details ride on action
  steps; reasoning/cost only for spectators), `hand`, `turn`, `done`. `pace` sleeps after
  `acting` for bots so they visibly think; `POST /api/live/pace` changes it live. `WebHumanAgent`
  blocks in `act()` until `POST /api/act`; `/api/live` carries the in-progress hand (`current`).
- `web/static/app.js` — one file, no build: `stateAt()`/`render()` draw any hand (replay or
  live) from its steps; animations (deal, flip, chips to pot / to winner, bubbles) are decided
  by comparing the new state with `prev` inside `render()`, so replay stepping and live
  streaming share them. Motion is skipped under `prefers-reduced-motion`.
- `coach/ranges.py` — 169 hand classes, range notation (`22+, A2s+, T9s-65s`), reference
  6-max charts in one dict (`CHARTS`), sampling combos from a range.
- `coach/equity.py` — Monte Carlo equity vs random hands, class ranges or explicit combos
  (cached, seeded), exact heads-up river enumeration, `pot_odds`, `board_texture`,
  `narrow_range()` (combos consistent with a bet/call on the board + a deterministic air share).
- `coach/facts.py` — `replay()` rebuilds a history on the engine (stacked deck + actions; unknown
  hole cards get filler); `tag_hand()` grades every decision of one player: preflop vs charts,
  postflop calls/folds vs equity (opponent ranges from the preflop line, narrowed street by
  street by their actions) and price, c-bets/bets/checks as observations. `ok=False` = leak.
- `coach/report.py` — `build_report()`: leaks ranked by frequency with cited example hands,
  by-position/street splits, observations, first-half vs second-half trend; `render()`/`to_dict()`.
- `coach/narrate.py` — Claude notes per leak (structured output); notes citing leaks or hands
  not in the report are dropped. Injectable client; tests use a fake.
- `coach/drills.py` — flagged spots as quizzes on the real view, graded by `accepted_actions()`,
  Leitner boxes in `<file>.<player>.drills.json`.
- `coach/importers/pokerstars.py` — PokerStars and GGPoker text (cash + tournament) →
  `HandHistory`; cents as chips when blinds have decimals; header date → `played_at` (ET for
  Stars, UTC for GG); skips antes/straddles/missed blinds/run-it-twice with a reason.
  Fixtures in `tests/fixtures/`. `importers/__init__.py::detect_format` is where a new site goes.
- `web/app.py` also serves `/api/coach?player=` (report cached until more hands) with the replay
  step of every example, and `/api/coach/narrate`.
- `cli.py` — `play`, `stats`, `replay`, `serve`, `import`, `coach`, `drill`.
- `tests/` mirror the modules; `test_engine_betting.py` has a 400-hand random fuzz.

## Conventions
- See global preferences in `~/.claude/CLAUDE.md` (conventional commits, feature branches, etc.).
- Run tests and lint before declaring work done. One commit per logical step.
- The engine is deterministic: every hand takes a seed and never reads global random state.
- The engine never exposes another seat's hole cards through a `SeatView`; if a feature needs
  them (replays, stats), read the finished `HandHistory` instead.
- Amounts in `Action.bet/raise_to` are street totals ("raise to"), never increments.
- Add behaviour to `Hand` with a test first (stacked deck + explicit actions), then wire agents.

## Status / how to continue (as of 2026-09-18)
Merged: PR #1 (engine, bots, LLM seats, human seats, histories, stats, league, CLI, viewer,
live tables), PR #2 (poker-coach), PR #3 (Coach tab, PokerStars importer, range narrowing),
PR #4 (`played_at` + weekly trend, GGPoker import, fast narrowing, `docs/baseline.md`).
Branch `feat/watch-mode` (PR #5): cinematic watch mode — real-time streaming of hands with a
pace, seat kinds, avatars, chip stacks, animations, bubbles, thinking indicator. 271 tests.
The development plan for everything after this lives in the approved plan
(`~/.claude/plans/try-to-create-a-generic-raven.md`): M2 coach on real hands, M3 antes +
tournaments, M4 packaging + static demo, M5 LLM league (needs a key), M6 solver (conditional).
**Not done yet, in this order:**

1. **Live smoke test of the LLM seat** — no API key on this machine yet. Run
   `ANTHROPIC_API_KEY=... uv run poker-table play -n 2 --seats llm:nerd,tag --show` and check
   the request shape (`output_config.format` json_schema + `effort`) is accepted; fix
   `agents/llm.py::_call` if the API rejects anything. Load the `claude-api` skill before
   touching that file.
2. **Per-model comparison**: same personality on opus-5 / sonnet-5 / haiku-4-5, report bb/100
   vs $/hand; commit the JSONL + leaderboard under `docs/` as the first published result.
3. **888poker importer** (a genuinely different text format: `** Dealing down cards **`,
   `[ Ah, Kd ]`, `posts small blind [$0.01]`) — a second parser module + fixture + `detect_format`
   branch. Antes/straddles would need engine support (`Hand` has none) — skip unless asked.
4. **Coach tab: date range** — the API could take `since`/`until` like the CLI; a week picker in
   the tab would make the weekly trend browsable.
5. Web viewer polish if wanted: seat filter on the chart, hide cards until showdown by default.
6. **Solver-grade postflop facts** would be the next real step up (an open-source solver or a
   simplified abstraction) — big; only if the chart/equity facts prove insufficient on real hands.

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
- `narrow_range` has its own integer-op scoring loop (`equity.py::_narrow`) — keep it in sync
  with `strength.quick_made_hand`, which is the readable version and is tested against
  `classify()` (99.8% agreement; the rest are board-plays-itself cases).
- Imported hands: nets are rake-adjusted (they do not sum to zero), `seed=0`, no decision
  traces; `replay()` fills unknown villain cards with filler, so villain showdown results in a
  replay can differ from the real ones — the coach only reads the hero's views.
- Coach thresholds live at the top of `coach/facts.py` (`CALL_MARGIN`, `FOLD_MARGIN`, `STRONG`,
  `BLUFF_SHARE`, `FLOAT_SHARE`)
  and the charts in `coach/ranges.py::CHARTS`; the TAG bot disagrees with the charts in places
  (Chen thresholds vs range charts), which the coach report on `tag` shows — that is expected.
- Stats: a BB check is not VPIP; 3-bet opportunity = acting with exactly one raise in front and
  not being the opener; bluff = postflop bet/raise with no pair and no draw (uses shown cards).
