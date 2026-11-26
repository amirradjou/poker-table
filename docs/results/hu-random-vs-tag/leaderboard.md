# hu-random-vs-tag

The heads-up track: random against tag.

400 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| tag | tag | +75 | 105 | +70 | 101 | +600 | 0 | - | - | 0 | 0 |
| entrant | random | -75 | 105 | -70 | 101 | -600 | 0 | - | - | 0 | 0 |

1 of the 400 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Too close to break-even to call: tag, entrant.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands   net  bb/100  vpip  pfr  3bet  f3b    af  wtsd  w$sd  bluff  illegal   ms  $/hand
tag        400   600    75.0   12%  12%    2%  74%  13.5    7%   80%    11%       0%  0.0       -
entrant    400  -600   -75.0   38%  20%   40%   0%  11.4    7%   20%    47%       0%  0.0       -
```
