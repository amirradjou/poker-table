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
