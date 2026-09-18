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
| Leak report | `uv run poker-table coach real.jsonl [--player me] [--since D] [--margins call=0.05] [--narrate]` |
| Drill flagged spots | `uv run poker-table drill real.jsonl -n 10` (or the Drill tab in `serve`) |
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
  by-position/street splits, observations, weekly (or halves) trend, "against whom" (villains
  you leak against most, with their VPIP/PFR/AF); `render()`/`to_dict()`.
- `coach/facts.py::Margins` — the postflop thresholds as a value (`Margins.parse("call=0.05")`,
  CLI `coach --margins`); facts carry `opponents` (seats still in the pot).
- `coach/narrate.py` — Claude notes per leak (structured output); notes citing leaks or hands
  not in the report are dropped. Injectable client; tests use a fake.
- `coach/drills.py` — flagged spots as quizzes on the real view, graded by `accepted_actions()`,
  Leitner boxes in `<file>.<player>.drills.json`; `DrillSession` is the one-sitting state the
  web Drill tab uses (never repeats a spot in a sitting).
- `coach/importers/builder.py` — `HandBuilder`, the shared half of every site parser (seat/blind
  validation, per-street bets, uncalled returns — inferred when the site never prints them,
  showdown descriptions, rake-adjusted nets, `hero`). `pokerstars.py` (also GGPoker's dialect)
  and `eight88.py` only recognise lines. `importers/__init__.py::detect_format` routes a file.
  Fixtures in `tests/fixtures/`; antes/straddles/missed blinds/run-it-twice are skipped with a reason.
- `history.py` also has `hero` on every hand (importer's "Dealt to" seat, or the lone human seat
  of a league), `filter_by_date()` and `weeks_of()`.
- `web/app.py` also serves `/api/coach?player=&since=&until=` (report cached per period until
  more hands) with the replay step of every example, `/api/coach/narrate`, `/api/hands?ids=`,
  `/api/drill/next` and `/api/drill/answer`.
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
PR #4 (`played_at` + weekly trend, GGPoker import, fast narrowing, `docs/baseline.md`),
PR #5 (cinematic watch mode). Branch `feat/coach-real-hands` (PR #6, milestone 2): hero on every
hand + `--player` optional, `HandBuilder` + 888poker importer, "against whom", `--margins`,
Coach tab week picker / street chips / leak → hands, Drill tab. 279 tests.
The development plan lives in `~/.claude/plans/try-to-create-a-generic-raven.md`; done: M1, M2.
**Next, in this order:**

1. **M3 — antes and tournaments** (`engine.Hand(..., ante=0)`, `post_ante` event, stats/coach/
   importers follow — drop the "unsupported post: the ante" skip; tournament mode in `league.py`
   with a blind schedule, no top-up, bust-outs, finishing positions; `play --tournament`).
2. **M4 — packaging and a live demo** (`serve --export DIR` static site → GitHub Pages from the
   seeded baseline; PyPI release workflow on `v*` tags; Dockerfile + compose; architecture SVG).
3. **M5 — LLM league** (needs `ANTHROPIC_API_KEY`; load the `claude-api` skill first): smoke test
   `play -n 2 --seats llm:nerd,tag --show`, budget guard, experiment runner, offline decision
   evals from `drills.spots_from`, opponent memory, parallel tables, results pages.
4. **M6 — solver-grade postflop facts**, only if real-hand reviews show the chart/equity facts
   missing what a player would call obvious.
5. Viewer polish if wanted: seat filter on the chart, hide cards until showdown by default.

## Gotchas / decisions
- `uv` lives in `~/.local/bin`, which is not on PATH in non-login shells.
- Pre-commit runs ruff; the format hook rewrites files, so re-`git add` if a commit fails.
- `LegalActions.min_raise_to` equals `max_raise_to` when only an all-in short of a full raise is
  possible; a raise smaller than the last full raise does not reopen the action (`Seat.acted`).
- Scripted bots hold their own `random.Random(seed)`: two `run_league` calls with the *same
  agent objects* diverge — build fresh agents for reproducibility tests.
- The web viewer is `index.html` + `app.css` + `app.js` with no build step; the browser caches
  them aggressively during development — reload with a query string (`/?v=2`).
- A drill spot and a live turn are the same shape (`live_view_payload`) and share the action
  bar; `drillSpot`/`turn` decide where `sendAction()` posts. `stateAt()` treats `hand.drill`
  like `hand.streaming` for "who is on the move".
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
- The history was rewritten once (2026-09-18) to re-date commits; local branches were reset to
  the rewritten refs. If hashes in notes do not match, trust `git log`.
- Coach thresholds live at the top of `coach/facts.py` (`CALL_MARGIN`, `FOLD_MARGIN`, `STRONG`,
  `BLUFF_SHARE`, `FLOAT_SHARE`)
  and the charts in `coach/ranges.py::CHARTS`; the TAG bot disagrees with the charts in places
  (Chen thresholds vs range charts), which the coach report on `tag` shows — that is expected.
- Stats: a BB check is not VPIP; 3-bet opportunity = acting with exactly one raise in front and
  not being the opener; bluff = postflop bet/raise with no pair and no draw (uses shown cards).
