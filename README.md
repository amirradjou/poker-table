# poker-table

A no-limit Texas hold'em table where LLM agents with different personalities play
each other for play money — and you can pull up a chair.

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

## Planned stack

Go engine and server, WebSocket web UI for human seats, SQLite for hand histories and
stats. Model backends behind one interface.

## Milestones

- **v0** — engine with scripted players; hand histories; correctness tests against known
  side-pot edge cases.
- **v1** — LLM seats with structured actions; leaderboard; reasoning traces.
- **v2** — human seat in the browser; personality library; opponent-modelling stats.
- **v3** — tournaments (sit-and-go), commentary track, published league results.

## Status

Idea stage — nothing runs yet.
