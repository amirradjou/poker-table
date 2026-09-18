# Baseline: five scripted bots, 1,000 hands

Reproducible with one command (seeded decks, seeded bots, no API calls):

```sh
uv run poker-table play -n 1000 --seats tag,rock,maniac,station,random --seed 2026 -o baseline.jsonl
uv run poker-table coach baseline.jsonl --player station
```

Blinds 1/2, 200-chip buy-in topped up every hand, button rotating. Chips are conserved,
so the five nets sum to zero.

## Leaderboard

```
name     hands     net   bb/100  vpip  pfr  3bet  f3b    af  wtsd  w$sd  bluff  illegal   ms  $/hand
tag       1000   16466    823.3   15%  11%    3%  13%   1.4   49%   75%     6%       0%  0.0       -
maniac    1000   12605    630.2   92%  76%   73%   6%  10.9   98%   45%    51%       0%  0.0       -
rock      1000    5963    298.1    5%   2%    0%   0%   0.5   47%   79%     0%       0%  0.0       -
station   1000   -1464    -73.2   97%   0%    0%    -   0.0  100%   42%      -       0%  0.0       -
random    1000  -33570  -1678.5   65%  38%   33%  42%   1.5   60%   37%    50%       0%  0.0       -
```

`vpip` voluntarily put money in preflop · `pfr` raised preflop · `3bet` re-raised facing one raise ·
`f3b` folded the open to a 3-bet · `af` postflop (bets+raises)/calls · `wtsd` went to showdown after
seeing the flop · `w$sd` won at showdown · `bluff` postflop bets/raises with no pair and no draw.

## What the coach says about each bot

The coach grades every decision against simplified 6-max charts (preflop) and against equity
versus the opponents' ranges — narrowed by their actions — and the price (postflop). The bots
are scripted, so the report is also a check on the coach: it should name exactly what each
bot was written to do.

### tag

Bankroll: +16466 chips (+823.3 bb/100)

- Wrong decision facing a 3-bet — 44 of 178 (25%)
- Folding hands the chart opens — 65 of 408 (16%)
- Calling without the right price — 20 of 76 (26%)
- c-bet frequency: 38% on dry flops (3/8), 80% on paired flops (4/5), 55% on wet flops (12/22)
- bluffs: 24% of postflop bets and raises (26/109)
- raise-first-in: 19% of unopened pots (93/501)

### rock

Bankroll: +5963 chips (+298.1 bb/100)

- Folding hands the chart opens — 126 of 546 (23%)
- Open-limping instead of raising or folding — 28 of 28 (100%)
- Missing value on the river — 6 of 6 (100%)
- c-bet frequency: 0% on dry flops (0/3), 0% on paired flops (0/1), 100% on wet flops (1/1)
- bluffs: 15% of postflop bets and raises (3/20)
- raise-first-in: 2% of unopened pots (13/559)

### maniac

Bankroll: +12605 chips (+630.2 bb/100)

- Opening hands outside the chart — 391 of 547 (71%)
- 3-betting hands outside the chart — 201 of 209 (96%)
- Wrong decision facing a 3-bet — 208 of 235 (89%)
- c-bet frequency: 70% on dry flops (71/101), 77% on paired flops (60/78), 70% on wet flops (152/217)
- bluffs: 60% of postflop bets and raises (800/1333)
- raise-first-in: 94% of unopened pots (547/581)

### station

Bankroll: -1464 chips (-73.2 bb/100)

- Calling without the right price — 875 of 1556 (56%)
- Calling a raise with hands outside the range — 545 of 636 (86%)
- Wrong decision facing a 3-bet — 438 of 447 (98%)

### random

Bankroll: -33570 chips (-1678.5 bb/100)

- Open-limping instead of raising or folding — 164 of 164 (100%)
- 3-betting hands outside the chart — 160 of 166 (96%)
- Calling a raise with hands outside the range — 142 of 165 (86%)
- c-bet frequency: 56% on dry flops (5/9), 44% on paired flops (4/9), 52% on wet flops (14/27)
- bluffs: 66% of postflop bets and raises (227/345)
- raise-first-in: 54% of unopened pots (197/368)

## Reading it

- **tag** wins most: tight, positionally aware, value-bets. Its leaks are where its Chen-formula
  thresholds disagree with the range charts (folding hands the chart opens on the button).
- **maniac** is second despite a 92% VPIP: the calling station and the random bot pay it off.
  Against better opposition its 51% bluff rate and 73% 3-bet rate would be its downfall.
- **rock** grinds a small profit by never being in a bad spot — and never in a good one.
- **station** loses slowly: it never folds, so every bad price is paid.
- **random** is the sink: 42% fold-to-3-bet with a 33% 3-bet rate is money on fire.
