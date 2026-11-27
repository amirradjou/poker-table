# Freezeouts: who survives, not who wins chips

A cash league answers one question — over many hands at the same stakes, who makes the most
money? A freezeout asks a different one. The blinds rise, nobody rebuys, and the tournament
ends when one seat holds every chip. Chip EV and survival are not the same skill, so the two
leaderboards need not agree.

```sh
uv run poker-table play --tournament --seats tag,rock,maniac,station,random
uv run poker-table play --tournament --levels 2/4,5/10+1,20/40+5 --level-hands 10 -o tt.jsonl
```

A tournament prints finishing places rather than bb/100, because bb/100 means little when the
big blind is 400 by the end:

```
$ uv run poker-table play --tournament --seats tag,rock,maniac,station,random --seed 13 -q
126 hands, 200 chips each, reached level 7 (25/50+5)
place  name     hands  out at
    1  tag        126  won with 1000
    2  rock       126  level 7
    3  maniac      58  level 3
    4  random       2  level 1
    5  station      1  level 1
```

`hands` is how many hands the seat was dealt into, so it doubles as how long it lasted.

## Watch one

```sh
poker-table watch --tournament                           # five bots, live in your browser
poker-table watch --tournament --seats me:human,tag,maniac,rock   # or play it yourself
poker-table serve tt.jsonl                               # replay one played with -o
```

Each hand of a freezeout records its level, how many seats it began with and who went out in it,
with the place they finished. The viewer shows the level beside the blinds, marks the hands where
someone went out, and puts the standings first on the Leaderboard tab: who is left with how many
chips, and who went out in which hand. The chip table under them has no bb/100 or margins, since
the big blind keeps growing. A live table says the level and how many are left as it plays. If you go out, the table plays on to the end, and you watch.

## Measured: 500 seeded freezeouts

`uv run python scripts/measure_tournaments.py` — the default turbo schedule (1/2 doubling to
800/1600+200, twenty hands a level), 200 chips each, five scripted bots, one seed per
tournament so the whole table is reproducible. 15 ms per tournament.

```
500 freezeouts, 200 chips each, levels 1/2 to 800/1600+200, 20 hands each
length: median 107 hands (shortest 5, longest 218), median finishing level 6
name      wins  mean place   1.   2.   3.   4.   5.
tag      53.8%        1.81  269   81  134    7    9
rock      9.8%        2.09   49  368   78    1    4
random   13.6%        3.50   68   13   62  315   42
maniac   17.2%        3.72   86   16   95   58  245
station   5.6%        3.88   28   22  131  119  200
```

With five seats, chance is 20% wins and a mean place of 3.00.

## What changed from the cash game

The cash-game order over 1,000 hands ([docs/baseline.md](baseline.md)) is
`tag` **+823** bb/100, `maniac` **+630**, `rock` **+298**, `station` **−73**, `random` **−1679**.
The survival order is `tag`, `rock`, `random`, `maniac`, `station`. Three things moved:

- **`tag` wins either way.** 53.8% of freezeouts and the best mean place; playing well is not a
  format trick.
- **`rock` trades wins for survival, and it is not the same thing.** It is second in mean place
  yet only wins one freezeout in ten: it finishes **second in 368 of 500** — three times out of
  four it is still there at the end and cannot close. Folding almost everything survives the
  early levels and then hands the last pot over, because the blinds eventually eat a seat that
  will not play.
- **`maniac` falls from second to fourth, and `random` rises from last to third.** In a cash
  game a maniac's losses are refilled every hand and its edge over the calling station shows up
  over a thousand hands. In a freezeout the same variance busts it: it goes out **fifth in 245
  of 500**. Meanwhile `random` — the worst seat in the game by a factor of two — wins **13.6%**
  of tournaments, more often than the disciplined `rock`. One lucky double-up in a five-handed
  turbo is worth more than a hundred hands of good folding.

That last line is the honest caveat on the whole table: this is a **turbo** structure and a
100-big-blind start, so luck is doing a lot of the work. A slower schedule
(`--level-hands 100`) or deeper stacks (`--stack 2000`) would give skill more room, at the cost
of longer runs. The numbers above are what this structure says, not what poker says.

## Many freezeouts in one file

One freezeout says little: `tag`, the best seat above, loses almost half of them. An
[experiment file](experiments.md) with `tournaments` in place of `hands` plays many, keeps every
hand in one results folder, and ranks the seats by where they finish:

```toml
name = "freezeout-bots"
seats = ["tag", "rock", "maniac", "station", "random"]
tournaments = 12
level_hands = 5          # and levels and max_hands: --levels, --level-hands and the hand limit
seed = 1
```

```sh
uv run poker-table experiment run experiments/freezeout-bots.toml -o docs/results
uv run poker-table verify docs/results/freezeout-bots
uv run poker-table serve docs/results/freezeout-bots/hands.jsonl --open
```

`stack` is the starting stack. `blinds` and `ante` belong to cash games and are refused here, as
is a file with both `hands` and `tournaments`.

- **Each tournament stands alone.** Tournament k deals from its own seed, made from the file's
  `seed` and k, and gets fresh seats seeded the same way. So it repeats on its own, and nothing
  carries over from one tournament to the next, which keeps them independent for the intervals.
  It also means a model seat's reads and notes last one tournament.
- **The seats are drawn for each one**, as a real tournament draws them, and the first seat drawn
  starts on the button. [Why](#why-the-seats-are-drawn).
- **One table.** Each hand starts from the stacks the last one left, so one tournament cannot be
  shared out across tables, and `tables` is refused. Several tournaments could play at once, but
  then a stopped run leaves several half played, and the file mixes their hands.
- **Hand ids look like `f3-17`**, hand 17 of tournament 3. A cash run on several tables writes
  `t3-17`, so the two never mix, and each hand's `tournament` record also says `"id": 3`.

In the viewer, the Leaderboard tab picks one tournament at a time for its standings, and "Its
hands" lists that tournament's hands.

### The report

bb/100 means little when the big blind ends at 1,600, so the report has none. For each seat it
gives the average finish with a 95% interval, the wins and the share won with its interval, the
hands it lasted on average (the hands it was dealt into), and the usual model calls, cost and
failures. The sample is the tournament: each one counts once, however long it ran. The average
finish has a t interval (the mean ± t × the standard error, t for n − 1 degrees of freedom), and
the share won has a Wilson score interval, which stays above zero when a seat never wins.

It names the seats that finish better or worse than chance (an average of 3.00 with five seats),
and the seats next to each other in the table whose intervals overlap, so their order is too
close to call. Under 10 tournaments it calls nobody.

**Unfinished tournaments count; cut-off ones wait.** A tournament that reaches `max_hands` with
more than one seat in counts, with the seats still in placed by their chips, as `play
--tournament` places them, and the report names it as unfinished. A tournament cut off when the
run stops (its budget, a failing seat, Ctrl-C) does not count yet. The report names it, and
`--resume` plays it to the end.

### Stopping and carrying on

`--resume` carries a half-played freezeout on from the hand it stopped at. The hands already in
the file are played again through the same code with the actions they record, so the stacks, who
is still in, the button, the places and the decks all come out as before. A scripted bot is asked
again at each of its decisions and its answer only compared, so its own random choices move on as
they did. For scripted seats, the resumed file is the file of a run that never stopped: the tests
cut a run after a bust-out, just before one, on a tournament's last hand and between two, and
compare. A model seat is not asked again, because a call costs money. It gets its notes back from
the file and decides afresh from there. To play more tournaments, raise `tournaments` and resume.

Which seats are asked again goes by `usage`: a seat with one is a model seat, the way the budget
counts it, and a seat without one is asked again. So a `python` seat of your own that calls a
paid API should keep a `usage` attribute with `cost_usd`, `calls` and `errors`, as the model
seats do. Without one, a resume calls it again for every decision it made in the earlier hands.

`--trial 2` plays the first two tournaments whole, then says what the rest should take and cost,
and asks before going on. `--hands` counts cash hands, so it is refused for these files.

`poker-table verify` deals every hand again from its tournament's seed and checks it against the
hand before it: the seats as drawn, stacks carried over (the starting stack on hand 1), the level's
blinds and ante, the button moving on past seats that are out, how many were left, who went out
in what place, no hand or tournament missing, and nothing dealt once a tournament has its winner.

### Measured: twelve freezeouts of the five bots

[`docs/results/freezeout-bots`](results/freezeout-bots/leaderboard.md), from the command above. A
published hand file stays under 2 MB. This folder's 498 hands take 1.6 MB, about 3.3 KB a hand,
and at the default twenty hands a level a tournament runs to a median of 107 hands, so only about
six would fit. This run raises the blinds every five hands instead, for a median of 39 hands.

| seat | average finish | ±95% | wins | share won | 95% interval | hands lasted |
|---|---:|---:|---:|---:|---:|---:|
| tag | 2.08 | 0.63 | 4 | 33% | 14%–61% | 35 |
| rock | 2.17 | 0.46 | 2 | 17% | 5%–45% | 37 |
| station | 3.25 | 0.90 | 2 | 17% | 5%–45% | 18 |
| random | 3.67 | 0.73 | 1 | 8% | 1%–35% | 11 |
| maniac | 3.83 | 1.11 | 3 | 25% | 9%–53% | 11 |

Twelve is enough to say that `tag` and `rock` finish better than chance. It is too few to put
those two in order, or to say anything about the other three, whose intervals all hold 3.00.

**It agrees with the script.** `uv run python scripts/measure_tournaments.py 2000 --level-hands 5
--seating drawn` plays 2,000 tournaments of the same structure with the seats drawn: `tag` 2.18,
`rock` 2.22, `maniac` 3.39, `random` 3.49, `station` 3.73. Each is inside the interval the twelve
give it. Against the 500 at twenty hands a level above, the top two stay on top, and the faster
blinds leave less room for skill: `tag` averages 2.18 instead of 1.81 and wins 28% of the time
instead of 54%, while `maniac` wins 30% instead of 17%.

**Heads-up.** Two seats make a heads-up freezeout. `nash:pushfold` plays its push/fold chart once
the stacks are short, antes included: [solver-seats.md](solver-seats.md#nashpushfold) has 5,000
of them against each bot, next to `tag` in its seat.

### Why the seats are drawn

The 500 above always seat `tag, rock, maniac, station, random` in that order, with `tag` on the
button first. That fixes more than the first button: each seat always has the same neighbours.
The same 2,000 decks and bots, seated three ways
(`uv run python scripts/measure_tournaments.py 2000 --seating fixed`, `turned` or `drawn`):

| seat | fixed | turned | drawn |
|---|---:|---:|---:|
| tag | 1.83 | 1.82 | 1.80 |
| rock | 2.10 | 2.09 | 2.11 |
| random | 3.45 | 3.54 | 3.58 |
| maniac | 3.77 | 3.67 | 3.61 |
| station | 3.86 | 3.89 | 3.90 |

Only the seating differs, so the gaps are where the seats sit. The fixed order is worth 0.13
places to `random` and costs `maniac` 0.16. Turning the order one place a tournament gives every
seat the first button as often, but keeps the neighbours, and gets only part of the way. Drawing
the seats takes out both, so an experiment draws them. The 500-tournament table above was
measured with the fixed order and is left as it was; its order of seats is the same as drawn.

## What the engine had to learn

Freezeouts need antes, and antes are **dead money**: they build the pot and count for side-pot
eligibility, but they buy no part of the blind — a seat that anted still owes the whole big
blind to play. That is the one rule worth stating, because getting it wrong makes every later
raise read short. `Hand(..., ante=N)` posts one per seat starting left of the button, and
`poker-table play --ante 1` deals a cash game with them.

Hands with antes can also now be **imported**, which is most tournament exports. One shape is
still refused, by name rather than silently: the modern big blind ante, where a single seat pays
for the table (`uneven antes: hero posted 0 of 600`). The engine posts the same ante for every
seat, so such a hand cannot be replayed seat for seat, and importing it would mean chips that do
not add up.
