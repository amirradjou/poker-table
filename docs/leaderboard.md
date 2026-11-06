# Leaderboard

Every entrant plays the same 200 hands from the same seat, against the same four bots (tag, rock, maniac, station), so they all get the same cards.

| entrant | all-in adj. bb/100 | ±95% | bb/100 | illegal | failed | s/call | $/hand | played |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| tag | +493 | 542 | +677 | 0% | 0 | - | - | 2026-11-06 |
| rock | +233 | 327 | +233 | 0% | 0 | - | - | 2026-11-06 |
| station | +131 | 1178 | -100 | 0% | 0 | - | - | 2026-11-06 |
| maniac | +78 | 1130 | +1667 | 0% | 0 | - | - | 2026-11-06 |
| random | -2346 | 763 | -1629 | 0% | 0 | - | - | 2026-11-06 |

Read the intervals before the order. Two entrants whose intervals overlap may not be different at all, and 200 hands is a small sample: 800 would halve those intervals. $/hand is blank where the model is free, or where its provider does not report a price.

## Add yours

It runs on your own key, or free on a local model. Nothing here pays for it.

```sh
poker-table benchmark llm:nerd@groq/<model> --trial 5   # what it would cost, first
poker-table verify results/bench-nerd-groq-<model>        # check the folder
```

Copy the folder into `docs/results/`, run `uv run python scripts/build_leaderboard.py` and open a pull request. CI replays every hand from its deck seed.

This shows the hands are legal and the decks and results are true. It cannot show who made the decisions. The decks are public, so treat a row as a claim by whoever sent it. The reasoning for every decision is in its `hands.jsonl`, for anyone who wants to read it.
