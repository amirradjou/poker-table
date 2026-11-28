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
| `nash:pushfold` | the push/fold equilibrium | heads-up, up to 15 bb effective, small blind half the big blind, no ante or an ante of 1/10 or 1/8 of the big blind | more than two seats, a table where no level has a chart |
| `cfr:blueprint` | a CFR+ blueprint on a coarse abstraction | heads-up, no ante, 90-110 bb effective, small blind half the big blind | more than two seats, an ante, other blinds, a table that starts outside 90-110 bb |

Both hand a spot they were not built for to the scripted `tag` bot and count it. Each decision
is recorded as `own` (its strategy, on its own tree), `translated` (its strategy, after reading an
action its tree does not have as one it does) or `fallback` (tag played it), in the decision's
`method`. `play` prints the split after a session, the heads-up leaderboard has it as a column,
and the first decision of each hand records the paper the seat follows. The viewer shows the
seat's kind under its name and links that paper.

## nash:pushfold

**What it is.** The equilibrium of the push/fold game: the small blind (the button, heads-up)
shoves all in or folds, and the big blind calls or folds. It is solved with no ante, and with
each player posting an ante of 1/10 or 1/8 of the big blind: the two sizes in the default blind
schedule (`DEFAULT_SCHEDULE` in `league.py`, levels 4-8 and 9-12). Miltersen and Sørensen solved
this game for a heads-up tournament and found jam/fold near-optimal for short stacks
([AAMAS 2007](https://doi.org/10.1145/1329125.1329357), [archived PDF](https://web.archive.org/web/20240429153436/https://www.itu.dk/~trbj/papers/poker.pdf)); that is the paper the seat links.

**How it is computed** (`scripts/solve_pushfold.py`):

1. The all-in equity of each of the 169 classes of starting hands against each other, exact:
   every board is counted once through its class of suit-renamed boards (134,459 classes for
   2,598,960 boards), with whole-number counts. That took 12.9 minutes on one core, starting at a
   load average of 0.5. The
   equities ship to six decimals in `agents/data/pushfold_equity.json`. They do not depend on
   the ante, so all three charts use them.
2. CFR+ (4,000 iterations) on the push/fold game at every half big blind from 1 to 20, with the
   right number of hands for each pair of classes (two aces leave fewer aces for the other
   player), three times: no ante, 1/10 and 1/8. An ante is dead money: a fold loses it with the
   blind, and a shove nobody calls wins the other ante and the big blind. The three charts took
   314 s together, 1.7 to 3.4 s a stack, from a load average of 2.1 that rose to 4.4 with other
   work on the laptop. They ship in `agents/data/pushfold.json`: the no-ante chart where it always
   was, which came out the same to the last digit, and the other two under `antes`.

A stack solves in seconds, so the seat could have solved at whatever ante a table has. It
does not: numpy is only a development dependency, and a whole chart takes about two minutes. So
it ships one chart per ante and plays no other.

The engine deals the game that was solved: heads-up, the button posts the small blind and acts
first before the flop (a test checks this). The effective stack is the smaller of the two stacks
before the antes and blinds, and the chart is read at the nearest half big blind.

**Checks.**

- An exact best response inside the push/fold game, against the chart as shipped (probabilities
  rounded to a thousandth), gains at most 0.0002 mbb/hand at any of the 39 stacks with no ante,
  0.0003 with an ante of 1/10 and 0.0004 with 1/8. A test recomputes them in plain Python from
  the shipped files, with the antes' payoffs. The same best response wins 7.4 mbb/hand at 10 bb
  against the no-ante chart played with an ante of 1/10, and 11.3 with 1/8; a test checks
  that too.
- A test solves a two-hand toy game, with and without an ante, whose equilibrium has a closed
  form. Another solves the full game at 1 bb with an ante of 1/8, where the big blind is all in
  from its post: everything shoves and calls, and the game is worth zero to both seats.
- With more dead money the big blind calls wider, at every stack. The small blind shoves wider
  with either ante than with none. From 1/10 to 1/8 its range can narrow by up to 0.4 points (at
  5.5, 9.5, 11 and 16 bb), because it now meets that wider calling range. The no-ante range
  wobbles as much from one stack to the next.
- Against the [HoldemResources chart](https://www.holdemresources.net/hune) (fetched
  2026-11-26), every cell of its two tabs: no ante, and an ante of 10% of the big blind. Their
  cell is the largest stack at which a hand shoves or calls. Their rule is "a value larger than
  the stack is a push"; ours is "a probability of at least one half". Where a hand mixes near a
  half, ours splits into pieces inside those ranges (T3s, T2s, 86o, 84s and the gap hands), which
  their page says it simplifies away. `scripts/solve_pushfold.py --compare` prints both, on a
  0.1 bb grid.
  - **No ante.** The largest stack agrees to 0.1 bb for 337 of the 338 cells (169 hands, push
    and call), including the three hands with gaps (63s shoves at 5.1-7.1 bb and below 2.3 bb;
    53s at 3.8-12.9 and below 2.4; 43s at 4.9-10.0 and below 2.2). The other is Q4o, which ours
    also shoves at 8.3 bb alone, above a range that ends at their 7.9.
  - **An ante of 1/10.** 308 of 338 agree: 153 pushes and 155 calls. Seven more differ only by
    a piece of mixing above a range that ends where theirs does. In the other 23 ours is
    tighter, by 0.2 to 0.7 bb: 9 pushes (76o 12.8 against their 13.5, K3o 13.9 against 14.3)
    and 14 calls, nearly all offsuit (JTo 14.5 against 15.1, K8o 15.7 against 16.1). The cause
    was not found. Their page does not say how its ante is counted. What is known: the no-ante
    tab, solved by the same code, agrees cell for cell; the best response above says ours is an
    equilibrium of the game it states; and a test checks that the engine pays what that game
    pays (a fold at 10/20+2 loses 12, an unanswered shove wins 22).
  - **An ante of 1/8.** No published chart was found: their raw data is for no ante only, and a
    search found no other primary source. That chart rests on the best response above.

  The no-ante table, for some of the cells:

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

- Exactly two seats, or it refuses before the first hand.
- Blinds and an ante it has a chart for: the small blind half the big blind, and no ante or an
  ante of exactly 1/10 or 1/8 of the big blind. The ante is matched by its exact share and never
  rounded: 3 on a big blind of 20 is 3/20, and tag plays that hand. Each decision tag makes says
  why in its `meta`, as `off_chart`: `ante`, `blinds`, `deep`, `flop`, `spot` or `seats`. Each
  decision at a level with an ante it has a chart for records that chart as `ante` (`"1/10"`),
  whoever plays it; a hand with no ante leaves that out, as before.
- It refuses a table only when no hand at it could use a chart: a cash table whose blinds or ante
  have none (`--ante 1` at 1/2), or a freezeout where no level has one. A freezeout where only
  some levels have none is seated, with a note naming them. To decide this, a freezeout's seats
  are now shown the whole schedule, not only the first level.
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
blinds 1/2, no ante, all-in adjusted bb/100 with a 95% interval. Tag playing the same decks is the
row to beat. "Chart's share" is how many of its decisions were its own or translated rather than
tag's.

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

**Measured in freezeouts** (`scripts/measure_pushfold_freezeouts.py`, 5.5 minutes from a load
average of 5.3, other work on the laptop included). Before the ante charts, the chart could only
play at levels 1-3 of the default schedule, where the stacks are rarely short: the antes arrive
at level 4. Now every level has a chart. 5,000 heads-up freezeouts against each bot, on the
default schedule (1/2 to 800/1600+200, antes from level 4, twenty hands a level), 200 chips
each, so 100 bb to start. Then the same 5,000 with `tag` in its seat. Tournament k of both has the same seed: the
same decks, the same first button, the same seed for the opponent and for tag. The seat is tag
plus a chart, so the difference between the two is what the chart is worth. It is paired, one
tournament against the other, with a 95% t interval; each share won has a Wilson interval.

| vs | nash:pushfold won | tag in its seat won | difference (points) | own | translated |
|---|---:|---:|---:|---:|---:|
| tag | 80.5% (79%-82%) | 50.1% (49%-51%) | +30.4 ±1.4 | 16.9% | 0.6% |
| rock | 93.9% (93%-95%) | 78.4% (77%-79%) | +15.6 ±1.1 | 19.0% | 0.2% |
| maniac | 68.0% (67%-69%) | 63.4% (62%-65%) | +4.6 ±0.7 | 2.2% | 1.7% |
| station | 93.8% (93%-94%) | 94.2% (94%-95%) | -0.4 ±0.4 | 1.2% | 1.0% |

- The chart wins tournaments against tag, rock and the maniac, clear of zero. Against tag it
  turns an even match into four wins in five. Against the station it is no better than tag, and
  the interval only just reaches zero. The station calls every shove, and an equilibrium range
  is made for a big blind who folds some of the time.
- `own` is the share of the seat's decisions played from a chart on its own tree; `translated`
  is from a chart after reading a raise as a shove or answering a limp. `own` is small because
  most decisions come before the stacks are short: 80-96% of them went to tag for being deeper than
  15 bb, and at most 1.6% for the flop of a limped pot. None went to tag for the ante. Against
  the maniac and the station most tournaments end early, while the stacks are still deep.
- Which chart played: against tag and rock, 89% and 92% of the chart's decisions were at a
  level with an ante of 1/10. Against the maniac and the station, 81% and 91% had no ante,
  because those tournaments end early (a median of 21 and 30 hands, against 88). The 1/8 chart
  played under 0.5% of them in every matchup: its levels start at hand 161.
- A tournament where the chart never played must come out as tag's, with the same winner and
  the same number of hands. It did, in all 831, 416, 2,419 and 1,581 of them; the script stops
  if one does not.

[`docs/results/freezeout-pushfold`](results/freezeout-pushfold/leaderboard.md) holds twelve of
them against tag, from `experiments/freezeout-pushfold.toml`: 902 hands, 1.8 MB, under the 2 MB
a published hand file may take. They are the script's first twelve, with the same winners and
lengths (`scripts/measure_pushfold_freezeouts.py 12 --folder docs/results/freezeout-pushfold`
checks). `poker-table verify` passes on the folder; a test checks that, rebuilds its report,
and finds decisions from the 1/10 chart in it.

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

**Which file it plays.** Since v0.4.0, version 2: after the flop it also remembers which of 3
preflop groups its hand is in ([below](#version-2)). It is 373,683 bytes in
`agents/data/blueprint.bin`, sha256 `a458be82ac2af4a2...`. Version 1, whose buckets forget
everything from earlier streets, shipped up to v0.3.0. The training, the exploitability and the
search for why that number rises, below, were measured on version 1. Its file comes back with
`git show v0.3.0:src/poker_table/agents/data/blueprint.bin > v1.bin`. Version 2 was chosen after
its numbers were seen, and one condition fixed before them is still not met; the end of the
version 2 section says which.

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
  0.975). In version 1 a street's bucket forgets the earlier ones, so the players have
  imperfect recall. Version 2 keeps one thing from before the flop, a group of the 169 classes,
  and forgets the rest, so its recall is still imperfect.
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

**Training version 1** (`scripts/train_blueprint.py --groups 0`). 2,000 iterations, a fixed number,
so the clock never decides what is written. On this laptop (12 cores, no GPU, shared with other
jobs) one process took 18.3 minutes: 5.0 counting the chance model, 13.3 for CFR+, with a load
average of 2.0 when it started and 1.6 when it ended. A second run took 19.0 minutes (load 0.9 to
2.3). The run gives up without writing if it passes 30 minutes. The same seed gives the same file
from any process: the second run, under another `PYTHONHASHSEED`, wrote the same bytes (sha256
`a4f2a007b702a86c...`), and a test trains a small blueprint twice to check it. The strategy, every
probability rounded to 1/255, was 128 KB in `agents/data/blueprint.bin` and shipped in the wheel up
to v0.3.0.

**Version 1's exploitability inside its small game** (`scripts/measure_blueprint.py --strategy
v1.bin`). An exact best response wins 441 mbb/hand against version 1, averaged over the two seats
(508 as the small blind, 373 as the big blind). That opponent knows the strategy, sees its own
buckets and, unlike the blueprint, remembers every bucket it has held. A uniform random strategy
loses 13,508 mbb/hand to the same opponent. Each measurement takes about 2.5 minutes (the load
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
| 2,000 (version 1) | 441 | 440 | 458 |

The average of iterations 1,001 to 2,000 alone (a delayed average) is 450.

**Why it rises** (`scripts/diagnose_blueprint.py`). Five causes were possible. The script's
docstring says which run tests each one, and what result would rule it out.

- **Rounding: ruled out.** Rounding to 1/255 moves the number by less than 1 mbb/hand: 380.1
  unrounded and 379.7 rounded at 250 iterations, 440.1 and 440.8 at 2,000.
- **A different game: ruled out.** Trained again from the saved chance model, the
  2,000-iteration average rounds to version 1's table byte for byte, so the best
  response plays the game that was trained. Made to play a fixed random strategy, the best response's walk
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
  averaging tried beats version 1 at 2,000.
- **Imperfect recall: likely why that level is not zero; not shown on version 1's game.** The
  same CFR+ and best response were run on two small games built from version 1's game, each
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
at least 41 against version 1: 40 after 400 of its own iterations and 41 after 1,200. The
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
and no other averaging does better. Why the level is not zero is likely imperfect recall, but on
version 1's game that is not measured. Version 1 shipped as the 2,000-iteration strategy, as
planned, not the least exploitable one: picking by this number would be tuning on the
measurement.

**Trained on to 4,000 iterations, it levels off.** CFR+ was run again to 4,000 from the saved
chance model, saving every 500 from 2,000 (`checkpoints --iterations 4000 --minutes 45`: 27.7
minutes, load 1.5 when it started and 2.3 when it ended). Its 2,000-iteration checkpoint is the
earlier run's, array for array. Before any number was seen, three readings were written down:
the average "levels off" if it ends within 10 mbb/hand of its value at 2,000, "keeps climbing"
if it ends more than 10 above, "turns down" if more than 10 below. The swap rule was written
down too. A best response wins, in mbb/hand (each measurement 2.3 to 3.2 minutes, three at a
time, load 1.1 to 9.2):

| iterations | average | last iterate |
|---:|---:|---:|
| 2,000 | 440.1 | 457.6 |
| 2,500 | 440.9 | |
| 3,000 | 441.7 | 456.7 |
| 3,500 | 442.5 | |
| 4,000 | 442.8 (443.8 rounded as shipped) | 456.8 |

The average of iterations 2,001 to 4,000 alone is 446.7. The average gains 2.7 mbb/hand over
the extra 2,000 iterations, so it levels off, and the last iterate stops falling at about 457.
CFR+ on this abstraction has settled between about 440 and 457: doubling the training did not
bring the number down. The 4,000-iteration strategy is no less exploitable than version 1
(443.8 rounded, against 441), so version 1 stayed as it was. A lower number was more likely to
come from a different abstraction, such as buckets that remember the earlier streets, than from
more iterations. Version 2 is that.

<a id="version-2"></a>**Version 2, which keeps a preflop group: less exploitable, shipped since
v0.4.0.** Version 2 keeps a little memory. After the flop its state is (preflop group, bucket), so
it has 30 states a street instead of 10. The 169 classes are split into 3 groups by equity against a
random hand. Each group is about a third of the deals: 62, 57 and 50 classes. Everything else is
version 1's game: the same tree, buckets, payoffs and saved chance model. Chance never looks at the
group. It is carried along as a label (`solvers/recall.py`, `remember_groups`), so only what the
player remembers changes. The strategy file has a format 2 that lists the groups. The seat reads
formats 1 and 2 and refuses any other.

Nothing was recounted, on purpose. Chances counted by (group, bucket) would make a different
game, and its numbers would not compare with 441. In this game they do. Version 1,
lifted so that each (group, bucket) plays what its bucket plays, measures 440.8 mbb/hand (508.4
as the small blind, 373.3 as the big blind). That is its number in its own game, to the last
bit (`measure_blueprint.py --groups 3 --no-uniform`: 4.8 minutes, load 2.4 to 5.6; without
`--groups`: 2.7 minutes, load 2.4 to 4.2). The best response knows its class in both games, so
it knows the same things.

Five conditions to ship it were fixed before any v2 number was seen. They are in pull request
#50. A best response wins, in mbb/hand:

| condition | needed | v2 | met |
|---|---|---|---|
| exploitable, at 2,000 iterations, rounded as shipped | under 441 | 345.3 (446.1 / 244.6) | yes |
| not rising: 2,000 iterations against 1,000 | at most 338.2 | 345.3 | **no** |
| training from the saved chance model | 30 minutes, same bytes twice | 21.8 minutes, same bytes | yes |
| heads-up track, v2 minus v1, hand for hand | interval not all below 0 | +42.7 ±78.6 bb/100 | yes |
| the file | 500 KB, no numpy, versioned | 373,683 bytes | yes |

- **Training** (`train_blueprint.py --groups 3 --reuse --out v2.bin --half v2-1000.bin`). Two
  runs at once, under `PYTHONHASHSEED` 1 and 987: 21.8 minutes each, load 3.6 when they started
  and 4.7 when they ended. Both wrote the same bytes, sha256 `a458be82ac2af4a2...` at 2,000
  iterations and `8c5b4d9f3d7cf72a...` at 1,000.
- **Exploitability** (`measure_blueprint.py --strategy v2.bin --no-uniform`): 345.3 at 2,000
  (4.7 minutes, load 4.3 to 3.9) and 338.2 at 1,000 (441.2 / 235.3; 5.1 minutes, load 4.2 to
  4.9).
- **Heads-up track** (`compare_blueprints.py play v2.bin --out DIR`, then `compare_blueprints.py
  pair DIR`: 21 seconds, load 3.3). It is played into a scratch folder and paired with the
  published `hu-cfr-blueprint-*` hands: same seeds, same hole cards, all-in adjusted chips. A
  replay of version 1's file, then published, paired to exactly zero in all 1,600 hands, so the
  bots and the decks were as published. `pair` also prints each run on its own: v2 makes +164
  ±94 bb/100 (tag +71, rock +76, maniac +306, station +205), and version 1's hands gave +122 ±89
  from the leaderboard.
- **The file.** 373,683 bytes. The wheel packages `agents/data/blueprint.bin` (checked with version
  1's file, and again with version 2's for v0.4.0), numpy is still only a dev dependency, and the
  tests play a format-1 and a format-2 file with numpy blocked and refuse an unknown format.

So keeping a preflop group takes about 96 mbb/hand off what a best response wins (441 to 345),
and the track shows no harm. But like version 1, its average gets more exploitable
as it trains: 338.2 at 1,000 iterations, 345.3 at 2,000. The fixed conditions asked for no rise,
so #50 left version 1 in place. Changing a condition after seeing the numbers would be
tuning on the measurement. Not measured: a game whose chances are counted by (group, bucket).

**Run on to 4,000 iterations, v2 levels off too.** The reading was written down before the run,
with version 1's tolerance above: the average "levels off" if it ends within 10
mbb/hand of its value at 2,000. The run checked itself on the way: at 2,000 iterations its
rounded average was v2's file, table for table (`checkpoints --groups 3 --like v2.bin`: 38.1
minutes, load 0.8 when it started and 2.9 when it ended). A best response wins, in mbb/hand (each
measurement 4 to 5 minutes, one at a time, load 1.6 to 5.0):

| iterations | v2 average | version 1's average, from above |
|---:|---:|---:|
| 2,000 | 344.8 | 440.1 |
| 3,000 | 350.1 | 441.7 |
| 4,000 | 351.6 (352.3 rounded) | 442.8 |

It ends 6.8 above its value at 2,000, so it levels off. Its last iterate at 4,000 wins 374.8
(479 as the small blind, 271 as the big blind), and the average of iterations 2,001 to 4,000 wins
358.6. As with version 1, the average climbs toward iterates that sit above it, and
the climb slows (5.3, then 1.5). v2 stays 91 to 95 mbb/hand less exploitable at each checkpoint.

**The decision (2026-11-28).** The owner chose to ship v2 after seeing these numbers, under the
"levels off" reading. The "not rising" condition fixed in #50 is still not met (338.2 at 1,000
iterations, 345.3 at 2,000), and the table of conditions above keeps that "no". Version 1 would
fail it too: from 1,000 to 2,000 its average rose from 426 to 440, twice v2's rise. The fixed
conditions exist to stop a choice made after the numbers are in. This is one, recorded as that,
not as a pass. The shipped file is the 2,000-iteration one from #50, the bytes both runs wrote,
not the 4,000-iteration average: that took 38 minutes, past the 30-minute rule, and is no less
exploitable.

What it changes: a best response in the small game wins 345.3 mbb/hand against the seat instead
of 441. The bare `measure_blueprint.py` now prints 345.3 (446.1 as the small blind, 244.6 as the
big blind), and a uniform strategy loses 13,507.8 in this game, as in version 1's (9 minutes for
both, load 2.5 to 2.1). What it does not show: that the seat plays better at the table. On the
heads-up track, version 2 minus version 1, hand for hand, is +42.7 ±78.6 bb/100, an interval
that covers zero.

The default command, `train_blueprint.py` with no options, now trains version 2 and counts the
chance model again. It wrote the same bytes in 22.4 minutes (load 1.5 when it started and 2.4 when
it ended), under the 30-minute rule.

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
- A raise past three a street hands the rest of the hand to tag. Against maniac that was 11 of
  1,421 decisions, against tag and rock one each, and against station none.

**Measured on the [heads-up track](leaderboard-heads-up.md)**: 400 hands against each bot, from
seat 0 on the track's decks, at 100 bb.

| entrant | all-in adj. bb/100 | ±95% | vs tag | vs rock | vs maniac | vs station | its own method |
|---|---:|---:|---:|---:|---:|---:|---:|
| cfr:blueprint, version 2 | +164 | 94 | +71 ±109 | +76 ±82 | +306 ±310 | +205 ±162 | 99.7% |
| version 1, up to v0.3.0 | +122 | 89 | +60 ±108 | +61 ±37 | +211 ±289 | +155 ±177 | 99.6% |
| tag (reference) | +47 | 56 | -42 ±43 | -11 ±36 | +81 ±199 | +160 ±87 | - |

Over all 1,600 hands it is clear of break-even, though its interval overlaps tag's. Its estimate
is second on the board, after the exploiter's +167. Against station alone it is clear. Against
tag (+71 ±109), rock (+76 ±82) and maniac (+306 ±310) the intervals also cover losing. Version 1
was clear against rock and not against station, but the track cannot tell the two apart: paired
hand for hand, version 2 minus version 1 is +42.7 ±78.6. Against maniac about half its decisions
came after translating a bet size its tree does not have (752 of 1,421), and 11 were tag's,
after a raise past the cap. The rest of the board, with the other bots as entrants, is on the
[heads-up leaderboard](leaderboard-heads-up.md).

**What it is not.** Not Libratus, not Pluribus, and not close to their strength: no search
during play, ten buckets a street (thirty with version 2's preflop group), three bet sizes,
imperfect recall, a chance model that forgets, and 2,000 iterations on one laptop core. Its
exploitability is measured in its own small game; how much a best response would win in real
hold'em is not known, and is surely more.

## Rerun everything

```sh
uv run python scripts/solve_pushfold.py              # equities (13 min), then the chart
uv run python scripts/solve_pushfold.py --compare    # the published-chart comparison
uv run python scripts/measure_pushfold.py            # the short-stack table
uv run python scripts/train_blueprint.py             # the shipped blueprint, version 2
uv run python scripts/measure_blueprint.py           # its exploitability in the abstract game
git show v0.3.0:src/poker_table/agents/data/blueprint.bin > v1.bin   # version 1, for the runs below
uv run python scripts/measure_blueprint.py --strategy v1.bin         # 441
uv run python scripts/train_blueprint.py --groups 0 --iterations 250 --reuse --out bp250.bin   # the trend
uv run python scripts/measure_blueprint.py --strategy bp250.bin --no-uniform        # (after a full run): one row
uv run python scripts/diagnose_blueprint.py checkpoints --like v1.bin --out bp   # why it rises (after a full run)
uv run python scripts/diagnose_blueprint.py measure bp avg:250:rounded avg:250 avg:2000:rounded avg:2000 avg:60
uv run python scripts/diagnose_blueprint.py measure bp current:250 current:2000 window:1000-2000 avg:1000 avg:500 current:1000
uv run python scripts/diagnose_blueprint.py exploiter bp avg:250:rounded avg:2000:rounded
uv run python scripts/diagnose_blueprint.py exploiter bp avg:2000:rounded --at 400,1200
uv run python scripts/diagnose_blueprint.py match
uv run python scripts/diagnose_blueprint.py recall --groups 3,2,2,3
uv run python scripts/diagnose_blueprint.py recall --groups 6,2,1,2
uv run python scripts/diagnose_blueprint.py recall --groups 3,2,2,3 --arms own-reach --no-check
uv run python scripts/diagnose_blueprint.py checkpoints --out bp4000 --iterations 4000 --at 2000,2500,3000,3500,4000 --minutes 45
uv run python scripts/diagnose_blueprint.py measure bp4000 avg:2500 avg:3000 avg:3500 avg:4000 avg:4000:rounded current:3000 current:4000 window:2000-4000
uv run python scripts/measure_blueprint.py --strategy v1.bin --groups 3 --no-uniform   # version 1 in v2's game
uv run python scripts/train_blueprint.py --reuse --out v2.bin --half v2-1000.bin   # v2: the shipped bytes
uv run python scripts/measure_blueprint.py --strategy v2.bin --no-uniform
uv run python scripts/measure_blueprint.py --strategy v2-1000.bin --no-uniform
uv run python scripts/compare_blueprints.py play src/poker_table/agents/data/blueprint.bin --out replay
uv run python scripts/compare_blueprints.py pair replay              # must be zero in every hand
uv run python scripts/compare_blueprints.py play v1.bin --out v1-track
uv run python scripts/compare_blueprints.py pair v1-track            # version 1 minus version 2: -42.7 ±78.6
uv run python scripts/diagnose_blueprint.py checkpoints --groups 3 --like src/poker_table/agents/data/blueprint.bin --out bpv2 --iterations 4000 --at 1000,2000,3000,4000 --minutes 60
uv run python scripts/diagnose_blueprint.py measure --groups 3 bpv2 avg:2000 avg:3000 avg:4000 avg:4000:rounded current:4000 window:2000-4000
uv run python scripts/run_heads_up_track.py          # the heads-up track, then the leaderboards
uv run pytest -q tests/test_solvers.py               # Kuhn and Leduc
uv run pytest -q tests/test_recall.py                # forgetting and remembering
```

numpy is only needed for these scripts and is in the dev dependency group; the seats run without
it.
