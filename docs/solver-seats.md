# Solver seats

Two seats that compute a game-theory strategy here, from the methods in the poker-AI research.
Both play heads-up only. They are small reimplementations on our own coarse games, not the
programs from the papers. Which papers exist, what they claim and what they released is in the
survey, [research-seats.md](research-seats.md).

```sh
poker-table play -n 200 --seats nash:pushfold,tag --stack 20     # 10 big blinds
poker-table watch --seats me:human,cfr:blueprint                 # sit down against the blueprint
poker-table benchmark cfr:blueprint --heads-up                    # the heads-up track
```

| seat | what it is | plays its own strategy | refuses |
|---|---|---|---|
| `nash:pushfold` | the push/fold equilibrium | heads-up, no ante, up to 15 bb effective | more than two seats, an ante |
| `cfr:blueprint` | a CFR+ blueprint on a coarse abstraction | heads-up, no ante, 90-110 bb effective, small blind half the big blind | more than two seats, an ante, other blinds, a table that starts outside 90-110 bb |

Both hand a spot they were not built for to the scripted `tag` bot and count it. Each decision
is recorded as `own` (its strategy, on its own tree), `translated` (its strategy, after reading an
action its tree does not have as one it does) or `fallback` (tag played it), in the decision's
`method`. `play` prints the split after a session, the heads-up leaderboard has it as a column,
and the first decision of each hand records the paper the seat follows. The viewer shows the
seat's kind under its name and links that paper.

## nash:pushfold

**What it is.** The equilibrium of the push/fold game: the small blind (the button, heads-up)
shoves all in or folds, and the big blind calls or folds. Miltersen and Sørensen solved this game
for a heads-up tournament and found jam/fold near-optimal for short stacks
([AAMAS 2007](https://www.itu.dk/~trbj/papers/poker.pdf)); that is the paper the seat links.

**How it is computed** (`scripts/solve_pushfold.py`):

1. The all-in equity of each of the 169 classes of starting hands against each other, exact:
   every board is counted once through its class of suit-renamed boards (134,459 classes for
   2,598,960 boards), with whole-number counts. That took 12.9 minutes on one core, starting at a
   load average of 0.5. The
   equities ship to six decimals in `agents/data/pushfold_equity.json`.
2. CFR+ (4,000 iterations) on the push/fold game at every half big blind from 1 to 20, with the
   right number of hands for each pair of classes (two aces leave fewer aces for the other
   player). About 1.5 s a stack. The chart ships in `agents/data/pushfold.json`.

The engine deals the game that was solved: heads-up, the button posts the small blind and acts
first before the flop (a test checks this). The effective stack is the smaller of the two stacks
before the blinds, and the chart is read at the nearest half big blind.

**Checks.**

- An exact best response inside the push/fold game, against the chart as shipped (probabilities
  rounded to a thousandth), gains at most 0.0002 mbb/hand at any of the 39 stacks. A test
  recomputes it in plain Python from the shipped files.
- Against the [HoldemResources chart](https://www.holdemresources.net/hune) (no ante, fetched
  2026-11-26), the largest stack at which each hand shoves or calls agrees to 0.1 bb in all 40
  cells checked, including the three hands with gaps (63s shoves at 5.1-7.1 bb and below 2.3 bb;
  53s at 3.8-12.9 and below 2.4; 43s at 4.9-10.0 and below 2.2). Their rule is "a value larger
  than the stack is a push"; ours is "a probability of at least one half". Where a hand mixes
  near a half, ours splits into pieces inside those ranges (T3s, T2s, 86o, 84s and the gap
  hands), which their page says it simplifies away. `scripts/solve_pushfold.py --compare`
  prints the table on a 0.1 bb grid:

| hand | action | published (bb) | ours (bb) |
|---|---|---|---|
| K2o | push | up to 11.6 | up to 11.6 |
| Q2o | push | up to 7.0 | up to 7.0 |
| T3s | push | up to 7.7 | 7.7, 7.4-7.5, up to 7.2 |
| 72o | push | up to 1.6 | up to 1.6 |
| 63s | push | 5.1-7.1, up to 2.3 | 6.1-7.1, 5.7, 5.1-5.2, up to 2.3 |
| A2o | call | up to 15.8 | up to 15.8 |
| 22 | call | up to 15.0 | up to 15.0 |
| QJo | call | up to 19.5 | up to 19.5 |
| K2o | call | up to 8.1 | up to 8.1 |

**Where it plays, and what it does off the chart.**

- Exactly two seats and no ante, or it refuses before the first hand. It was solved without
  antes; with them, the dead money changes every range.
- Up to 15 bb effective. The chart goes to 20, but deeper than that a shove-or-fold strategy
  gives away the small raises and the play after the flop; HoldemResources recommend push/fold up
  to around 10 bb and call it almost certainly a bad idea past 20. Deeper hands go to tag, and a
  table that starts deeper says so when it is seated.
- As the big blind facing any raise, of any size, it uses its calling range against a shove: a
  hand that would call a shove shoves (or calls, when calling is all in), the rest fold.
- As the big blind facing a limp, the calling range shoves and the rest check.
- After the flop, which only a limped pot reaches, tag plays.

**Measured at short stacks** (`scripts/measure_pushfold.py`, 2.3 minutes from a load average of
2.2): 2,000 hands
heads-up against each bot from seat 0 on the same decks (seed 2026), stacks topped up every hand,
blinds 1/2, all-in adjusted bb/100 with a 95% interval. Tag playing the same decks is the row to
beat. "Chart's share" is how many of its decisions were its own or translated rather than tag's.

| entrant | depth | vs tag | vs rock | vs maniac | vs station | chart's share |
|---|---:|---:|---:|---:|---:|---:|
| nash:pushfold | 5 bb | +33 ±4 | +43 ±3 | +13 ±6 | +29 ±7 | 100%/100%/89%/65% |
| tag | 5 bb | +0 ±2 | +3 ±2 | -42 ±5 | +21 ±8 | - |
| nash:pushfold | 10 bb | +37 ±3 | +41 ±3 | +36 ±11 | +83 ±14 | 100%/100%/84%/51% |
| tag | 10 bb | +0 ±2 | +4 ±2 | -25 ±8 | +56 ±14 | - |
| nash:pushfold | 15 bb | +27 ±4 | +31 ±3 | +76 ±16 | +122 ±18 | 100%/100%/79%/48% |
| tag | 15 bb | +0 ±2 | +4 ±2 | -5 ±12 | +79 ±17 | - |

It wins against every bot at every depth, clear of zero. Its estimate is above tag's in every
cell, and the intervals only overlap against the station at 5 and 10 bb.
Against the station half its decisions are tag's: the station limps, the chart checks its weak
big blinds, and tag plays the flop.

On the [heads-up track](leaderboard-heads-up.md), at 100 bb, every decision is the fallback's,
so its row is tag's row. That is the right answer, not a result about push/fold.

In a freezeout with the default blind schedule (`play --tournament`), the antes arrive at level 4,
before the stacks are short, so the chart never plays: in one heads-up freezeout against tag, 0
of its 88 decisions were the chart's. Give it a schedule without antes (`--levels`) to see it
play the short-stack end of a tournament.

**What it is not.** Not a full short-stack strategy: no min-raises, no limps, no play after the
flop. Not ICM-aware, so not a tournament strategy where finishing places pay. Exact only inside
the push/fold game.

## cfr:blueprint

**What it is.** A strategy for heads-up no-limit at 100 big blinds, worked out by CFR+ on a small
game that stands in for the real one, then played at the real table by mapping each real spot
onto the small game. Libratus (Brown and Sandholm, Science 2018) started the same way: a
blueprint computed with an improved Monte Carlo CFR on an abstraction of the game, then subgames
solved again during play. This seat is the first half only, on a far coarser game, and
it does no search while it plays. It links the CFR+ paper,
[Tammelin 2014](https://arxiv.org/abs/1407.5042).

**The small game** (`agents/abstraction.py`):

- **Stakes.** Heads-up, both players 200 chips, blinds 1/2, no ante: the table's defaults.
- **Bets.** A player who has not faced a bet on this street may check, bet half the pot, bet the
  pot or go all in; one facing a bet may fold, call, raise the pot or go all in. Sizes are
  fractions of the pot after calling, never under the table's minimum raise. At most three bets
  and raises a street (the big blind does not count), then only fold or call. 7,744 decision
  nodes.
- **Cards.** Preflop, the 169 classes of starting hands, nothing lost. On the flop and turn, ten
  buckets: five levels of hand strength (the chance of beating one random hand on this board,
  ties as a half), times whether the hand has a flush draw or an open-ended straight draw that
  uses a hole card. On the river, ten levels of strength. The cut points sit at fixed quantiles
  of strength over 400 seeded boards (flop 0.300, 0.552, 0.763, 0.916; river from 0.146 to
  0.975). A street's bucket forgets the earlier ones, so the players have imperfect recall.
- **Chance.** How the 169 classes land in flop buckets is counted exactly, over every flop. How a
  pair of buckets moves to the turn and the river, and who wins a showdown between two buckets,
  is counted over every pair of hands on 4,000 seeded boards. An all-in before the flop uses the
  exact equities from `nash:pushfold`. Chance only remembers the current pair of buckets, so the
  small game is a model of hold'em, not hold'em.

**Why CFR+ and not external-sampling MCCFR.** One CFR+ iteration here is an exact pass over the
whole tree for every pair of buckets at once, as numpy matrices: 0.42 seconds. Sampling one deal
per traversal in Python would give far fewer useful updates in the same 30 minutes, and the
exact pass has the shape of a best response, so the exploitability below came from the same code.

**Checked on games with known answers first** (`tests/test_solvers.py`). The same `CFRPlus`
reaches -0.05556 on Kuhn poker after 1,000 iterations, the known -1/18 for the first player,
and one of its known equilibria (the queen always checks first, the king bets three times as
often as the jack). On Leduc hold'em, which has a second street and a public card, the
exploitability falls from 0.61 after 10 iterations to 0.00004 after 3,000.

**Training** (`scripts/train_blueprint.py`). 2,000 iterations, a fixed number, so the clock
never decides what is written. On this laptop (12 cores, no GPU, shared with other jobs) one
process took 18.3 minutes: 5.0 counting the chance model, 13.3 for CFR+, with a load average of
2.0 when it started and 1.6 when it ended. A second run took 19.0 minutes (load 0.9 to 2.3).
The run gives up without writing if it passes 30 minutes. The same seed gives the same file from
any process: the second run, under another `PYTHONHASHSEED`, wrote the same bytes (sha256
`a4f2a007b702a86c...`), and a test trains a small blueprint twice to check it. The strategy,
every probability rounded to 1/255, is 128 KB in `agents/data/blueprint.bin` and ships in the
wheel.

**Exploitability inside its small game** (`scripts/measure_blueprint.py`). An exact best
response wins 441 mbb/hand against the shipped strategy, averaged over the two seats (508 as the
small blind, 373 as the big blind). That opponent knows the strategy, sees its own buckets and,
unlike the blueprint, remembers every bucket it has held. A uniform random strategy loses
13,508 mbb/hand to the same opponent. Each measurement takes about 2.5 minutes (the load
average was between 1 and 4 while they ran).

More training did not lower that number. CFR+ was run again from the saved chance model, with
the strategy saved along the way (`diagnose_blueprint.py checkpoints`: 14.8 minutes, load 1.7
when it started and 2.9 when it ended). Each saved strategy was then measured the same way
(`diagnose_blueprint.py measure`: 1.8 to 2.8 minutes each, load 2.8 to 6.4):

| iterations | average, rounded as shipped | average, not rounded | last iterate |
|---:|---:|---:|---:|
| 60 | | 637 | |
| 250 | 380 | 380 | 851 |
| 500 | | 391 | |
| 1,000 | | 426 | 545 |
| 2,000 (shipped) | 441 | 440 | 458 |

The average of iterations 1,001 to 2,000 alone (a delayed average) is 450.

**Why it rises** (`scripts/diagnose_blueprint.py`). Five causes were possible. The script's
docstring says which run tests each one, and what result would rule it out.

- **Rounding: ruled out.** Rounding to 1/255 moves the number by less than 1 mbb/hand: 380.1
  unrounded and 379.7 rounded at 250 iterations, 440.1 and 440.8 at 2,000.
- **A different game: ruled out.** Trained again from the saved chance model, the
  2,000-iteration average rounds to the shipped file byte for byte, so the best response plays
  the game that was trained. Made to play a fixed random strategy, the best response's walk
  values it at -0.060099167687 bb for the small blind, the same to 12 decimal places as
  `expected_value` and the trainer's own walk (`match`: 3.1 minutes, load 3.2 to 3.5). So all
  three use one tree, one chance model and one set of payoffs. Beyond the abstraction, the best
  response knows its own memory and nothing else: it wins the same against a strategy as
  against that strategy in a copy of the game whose buckets remember (`tests/test_recall.py`,
  and `recall` below).
- **A plain bug: none found.** Kuhn and Leduc still reach their known answers. On small copies
  of the game whose buckets remember, the same CFR+ and best response go from about 900 to 16
  or less in 1,000 iterations (below).
- **Averaging: it shapes the curve, not its level.** The script's test expected the other ways
  of averaging to rise too, and they do not: the last iterate falls (851, 545, 458) while the
  average rises (380, 426, 441), and the delayed average is 450. All three are near 450 at
  2,000, so by then CFR+ plays strategies that a best response beats by about 450 mbb/hand,
  whichever of them is measured. The early average, a blend of early iterates, happens to be
  harder to exploit than that, and the average climbs as the early iterates lose weight. No
  averaging tried beats the shipped one at 2,000.
- **Imperfect recall: likely why that level is not zero; not shown on the shipped game.** The
  same CFR+ and best response were run on two small games built from the shipped one, each
  played twice on the same tree and chance model: once with buckets that forget, once with
  buckets that remember (`recall`). The groups merge the 169 classes by equity and the later
  streets' buckets by strength. A best response wins, in mbb/hand:

  | iterations | 3, 2, 2, 3 groups: forget | remember | 6, 2, 1, 2 groups: forget | remember |
  |---:|---:|---:|---:|---:|
  | 30 | 1,051 | 955 | 935 | 909 |
  | 100 | 279 | 241 | 245 | 270 |
  | 300 | 104 | 75 | 43 | 42 |
  | 1,000 | 84 | 16 | 5 | 4 |

  With 3, 2, 2 and 3 groups per street, forgetting stops CFR+ well short of zero while
  remembering keeps falling (18.9 minutes, load 3.0 to 4.2). With 6, 2, 1 and 2 groups, whose
  turn bucket carries no card information at all, both go to zero together (15.7 minutes, load
  2.6 to 2.8). So forgetting can stop CFR+ short of zero, but it does not always, and neither
  small game shows the rise.

An opponent held to the blueprint's own buckets (it forgets too), trained by CFR+ against the
fixed strategy (`exploiter`), wins at least 104 mbb/hand against the 250-iteration strategy and
at least 41 against the shipped one: 40 after 400 of its own iterations and 41 after 1,200. The
best response that remembers wins 380 and 441. The exploiter's numbers are lower bounds, since
a better forgetting opponent may exist (3.2 minutes for each 400-iteration run, load 2.7 to
3.9; 8.6 minutes for the 1,200, load 4.5 to 4.2).

A different trainer, which weighs a player's forgotten earlier buckets by how often its own
strategy really came from them, did worse on the first small game: 114 mbb/hand after 1,000
iterations against 84 (`recall --arms own-reach`: 6.7 minutes, load 3.8 to 3.4). The blueprint
is not trained with it.

What this leaves: the number rises because by 2,000 iterations CFR+ on this abstraction plays
strategies that an opponent who remembers beats by about 450 mbb/hand, and the average starts
below that level. It is not rounding, a mismatched game or a bug these checks would catch,
and no other averaging does better. Why the level is not zero is likely imperfect recall, but on the shipped game that
is not measured. Whether the average levels off near 450 or keeps climbing would need a run of
about 4,000 iterations, past the 30-minute cap. The shipped strategy is the 2,000-iteration one
as planned, not the least exploitable one: picking by this number would be tuning on the
measurement.

**Where it plays, and what it does off its tree.**

- Two seats, no ante, the small blind half the big blind, and a table that starts within 90-110
  big blinds (200 chips at 1/2, or 1,000 at 5/10). Anything else is refused before the first
  hand, with the reason.
- During a session stacks can drift (`play --carry`, or a freezeout with rising blinds). A hand
  whose effective stack is outside 90-110 big blinds goes to tag, and is counted. In a freezeout
  that is most hands after the first few.
- An opponent's bet or raise that is not one of the tree's sizes is read as the neighbouring
  sizes with the pseudo-harmonic mapping of
  [Ganzfried and Sandholm (IJCAI 2013)](https://www.ijcai.org/Proceedings/13/Papers/028.pdf):
  a bet of x between tree sizes A and B (all as fractions of the pot, and a check or call counts
  as 0) is read as A with probability (B - x)(1 + A) / ((B - A)(1 + x)), and as B otherwise,
  drawn from a seeded generator. An all-in is read as all in. Tests check the formula's values
  from the paper.
- When that reading closes the betting while the table still asks the seat to act (a small bet
  read as a check), it calls. When the tree is all in and the table is not, it checks or calls
  to the end.
- A raise past three a street hands the rest of the hand to tag. Against maniac that was 16 of
  1,434 decisions; against the other bots none.

**Measured on the [heads-up track](leaderboard-heads-up.md)**: 400 hands against each bot, from
seat 0 on the track's decks, at 100 bb.

| entrant | all-in adj. bb/100 | ±95% | vs tag | vs rock | vs maniac | vs station | its own method |
|---|---:|---:|---:|---:|---:|---:|---:|
| cfr:blueprint | +122 | 89 | +60 ±108 | +61 ±37 | +211 ±289 | +155 ±177 | 99.6% |
| tag (reference) | +47 | 56 | -42 ±43 | -11 ±36 | +81 ±199 | +160 ±87 | - |

Over all 1,600 hands it is clear of break-even, and its estimate is the highest on the board,
though its interval overlaps tag's. Against tag alone it is too close to call: +60 with an
interval of ±108 also covers losing. It beats rock clearly. Against maniac and station the intervals are too wide to say more than
"probably wins". Against maniac about half its decisions came after translating a bet size its
tree does not have (727 of 1,434), and 16 were tag's, after a raise past the cap. The rest of
the board, with the other bots as entrants, is on the [heads-up leaderboard](leaderboard-heads-up.md).

**What it is not.** Not Libratus, not Pluribus, and not close to their strength: no search
during play, ten buckets a street, three bet sizes, imperfect recall, a chance model that
forgets, and 2,000 iterations on one laptop core. Its exploitability
is measured in its own small game; how much a best response would win in real hold'em is not
known, and is surely more.

## Rerun everything

```sh
uv run python scripts/solve_pushfold.py              # equities (13 min), then the chart
uv run python scripts/solve_pushfold.py --compare    # the published-chart comparison
uv run python scripts/measure_pushfold.py            # the short-stack table
uv run python scripts/train_blueprint.py             # the blueprint
uv run python scripts/measure_blueprint.py           # its exploitability in the abstract game
uv run python scripts/train_blueprint.py --iterations 250 --reuse --out bp250.bin   # the trend (after
uv run python scripts/measure_blueprint.py --strategy bp250.bin --no-uniform        # a full run): one row
uv run python scripts/diagnose_blueprint.py checkpoints --out bp   # why it rises (after a full run)
uv run python scripts/diagnose_blueprint.py measure bp avg:250:rounded avg:250 avg:2000:rounded avg:2000 avg:60
uv run python scripts/diagnose_blueprint.py measure bp current:250 current:2000 window:1000-2000 avg:1000 avg:500 current:1000
uv run python scripts/diagnose_blueprint.py exploiter bp avg:250:rounded avg:2000:rounded
uv run python scripts/diagnose_blueprint.py exploiter bp avg:2000:rounded --at 400,1200
uv run python scripts/diagnose_blueprint.py match
uv run python scripts/diagnose_blueprint.py recall --groups 3,2,2,3
uv run python scripts/diagnose_blueprint.py recall --groups 6,2,1,2
uv run python scripts/diagnose_blueprint.py recall --groups 3,2,2,3 --arms own-reach --no-check
uv run python scripts/run_heads_up_track.py          # the heads-up track, then the leaderboards
uv run pytest -q tests/test_solvers.py               # Kuhn and Leduc
uv run pytest -q tests/test_recall.py                # forgetting and remembering
```

numpy is only needed for these scripts and is in the dev dependency group; the seats run without
it.
