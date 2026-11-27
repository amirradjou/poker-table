# Heads-up leaderboard

Every entrant plays 400 hands heads-up against each of the four bots (tag, rock, maniac, station), 1,600 hands in all, from the same seat and the same decks, at 200 chips and blinds 1/2 (100 big blinds). Seats that only play heads-up, such as the [solver seats](solver-seats.md), are measured here.

| entrant | all-in adj. bb/100 | ±95% | vs tag | vs rock | vs maniac | vs station | bb/100 | its own method | illegal | failed | played |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| exploiter | +167 | 78 | -42 ±43 | -11 ±36 | +432 ±261 | +290 ±160 | +158 | 18.5% | 0% | 0 | 2026-11-27 |
| cfr:blueprint | +122 | 89 | +60 ±108 | +61 ±37 | +211 ±289 | +155 ±177 | +125 | 99.6% | 0% | 0 | 2026-11-26 |
| nash:pushfold | +47 | 56 | -42 ±43 | -11 ±36 | +81 ±199 | +160 ±87 | +40 | 0% | 0% | 0 | 2026-11-27 |
| tag | +47 | 56 | -42 ±43 | -11 ±36 | +81 ±199 | +160 ±87 | +40 | - | 0% | 0 | 2026-11-26 |
| rock | -10 | 28 | -46 ±55 | +0 ±6 | +8 ±98 | -1 ±18 | -10 | - | 0% | 0 | 2026-11-26 |
| maniac | -13 | 176 | -294 ±226 | +26 ±76 | -4 ±578 | +220 ±322 | +12 | - | 0% | 0 | 2026-11-26 |
| station | -29 | 81 | -191 ±91 | -2 ±18 | +79 ±309 | -0 ±10 | -29 | - | 0% | 0 | 2026-11-26 |
| random | -385 | 212 | -70 ±101 | -189 ±129 | -1752 ±458 | +472 ±679 | -378 | - | 0% | 0 | 2026-11-26 |

Read the intervals before the order: two rows whose intervals overlap may not be different at all. The overall column pools all 1,600 hands; each match column is its 400 hands alone, so its interval is about twice as wide. "Its own method" is the share of decisions a research seat made with its own strategy rather than the scripted fallback it hands spots to, or, for exploiter, the share a read changed from tag's play; a dash means the seat has no such fallback. A row that reads 0% is the fallback's row.

## Add yours

```sh
poker-table benchmark <seat> --heads-up -o results   # four matches, one folder each
poker-table verify results/hu-*                       # check the folders
```

Copy the four folders into `docs/results/`, run `uv run python scripts/build_leaderboard.py` and open a pull request. CI replays every hand from its deck seed. This shows the hands are legal and the decks and results are true. It cannot show who made the decisions.
