# poker-table

A no-limit Texas hold'em table where LLM agents with different personalities play
each other for play money — and you can pull up a chair. Later, the same engine turns
around and coaches *you*: **poker-coach** analyses your own hand histories and explains
your recurring leaks.

## Status

Engine, evaluator, side pots, scripted bots, hand histories, stats, league and CLI are in
(Python 3.12, `uv`). LLM seats, table talk and the web UI are next — see `CLAUDE.md`.

```sh
uv sync
uv run poker-table play -n 500 --seats tag,rock,maniac,station,random -o hands.jsonl
uv run poker-table replay hands.jsonl --hand 7 -r     # one hand with every seat's reasoning
uv run poker-table stats hands.jsonl                  # VPIP, PFR, 3-bet, AF, WTSD, W$SD, bluff %
```

## What it is

- A correct hold'em engine: blinds, betting rounds, side pots, showdown, hand ranking.
- Seats filled by LLM agents ("the maniac", "the rock", "the math nerd", "the
  storyteller"), scripted baselines (tight-aggressive, calling station, GTO-ish
  pre-flop charts), and humans via a web UI.
- Every hand is logged as a standard hand history with each agent's private reasoning
  attached, so you can see *why* the bot shoved with seven-deuce.
- A bankroll leaderboard over thousands of hands, plus exploitability stats per player:
  VPIP, PFR, aggression factor, fold-to-3-bet, bluff frequency, showdown win rate.

## Why build it

Bluffing bots are fun to watch and to play against, and poker is a rare game where
"is the model actually reasoning under uncertainty?" has a scoreboard: the bankroll.
It is also a natural place to learn cost-per-decision engineering, because a loose
agent that thinks hard on every street will bankrupt you in API spend before it
bankrupts you in chips.

## How it works (planned)

```
engine (deterministic, seeded decks)
   │  each decision: legal actions + public state + seat's hole cards + hand history
   ▼
seat adapter ──► model call with a strict action schema
                 { action: fold|check|call|bet|raise, amount?, table_talk? }
   ▼
validator (illegal action ⇒ auto-check/fold + penalty stat)
   ▼
hand history + reasoning trace + stats ──► leaderboard, replay viewer
```

- **Personalities are prompts + parameters**, so "who wins" is a question about
  prompts and models, and the league answers it.
- **Cheap where it doesn't matter**: trivial decisions (fold pre-flop with junk facing a
  raise) can be delegated to a chart; the model is called when the pot is interesting.
- **Table talk** is optional free text shown to other seats — bluffing in words as well
  as chips.
- **Fairness**: the deck is seeded and logged; no seat ever sees another seat's cards.

## What gets measured

- Bankroll over time per config (model + personality), big blinds won per 100 hands.
- Exploitability stats; how each agent adapts (or doesn't) to opponents.
- Illegal-action rate (schema violations), decision latency, cost per hand.

## Phase 2 — poker-coach: find your recurring leaks

The chess-coach idea applied to poker. Once the table exists, point the same machinery at
*your* hands — from poker-table itself or from hand-history exports of real sites — and
get explanations of the mistakes you keep making, not just a per-hand verdict.

- **Import** hand histories (poker-table's own format plus common site exports).
- **Ground truth from solvers and math, not from the model**: preflop ranges from
  standard charts, equity and pot-odds calculations for every decision, and a postflop
  solver (an open-source one, or a simplified abstraction) for the spots that matter.
  Every decision gets a tagged fact: *called a 3-bet out of position with a hand outside
  the calling range*, *c-bet 88% on wet boards*, *folded getting 5:1 with 9 outs*.
- **Pattern mining across hands**: cluster the facts by street, position, stack depth,
  board texture and opponent type until the recurring leaks fall out.
- **Explanations that cite hands**: Claude narrates the clusters in plain language,
  quoting hand IDs and streets, and never invents a leak the facts don't support.
- **Drills**: the coach rebuilds your worst spots as quizzes against the engine and
  brings back the ones you keep failing (spaced repetition).
- **Weekly leak report**: what got better, what didn't, the one thing to work on.

What it measures: leak frequency over time per tag, quiz accuracy per leak, and — the
honest one — bankroll trend on the table after each report.

## Planned stack

Go engine and server, WebSocket web UI for human seats, SQLite for hand histories and
stats. Model backends behind one interface.

## Milestones

- **v0** — engine with scripted players; hand histories; correctness tests against known
  side-pot edge cases.
- **v1** — LLM seats with structured actions; leaderboard; reasoning traces.
- **v2** — human seat in the browser; personality library; opponent-modelling stats.
- **v3** — tournaments (sit-and-go), commentary track (via `commentator`), published
  league results.
- **v4 (poker-coach)** — hand-history import, equity/pot-odds facts, preflop range
  tagging, first leak report citing hands.
- **v5 (poker-coach)** — postflop solver integration, drills with spaced repetition,
  weekly leak reports.

## Status

Idea stage — nothing runs yet.
