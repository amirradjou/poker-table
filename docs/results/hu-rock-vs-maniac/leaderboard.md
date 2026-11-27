# hu-rock-vs-maniac

The heads-up track: rock against maniac.

400 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| entrant | rock | +8 | 98 | +8 | 98 | +61 | 0 | - | - | 0 | 0 |
| maniac | maniac | -8 | 98 | -8 | 98 | -61 | 0 | - | - | 0 | 0 |

0 of the 400 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Too close to break-even to call: entrant, maniac.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands  net  bb/100  vpip  pfr  3bet  f3b    af  wtsd  w$sd  bluff  illegal   ms  $/hand
entrant    400   61     7.6    6%   1%    0%  33%   0.4   27%   75%     0%       0%  0.0       -
maniac     400  -61    -7.6   52%  41%   60%    -  38.5   27%   25%    56%       0%  0.0       -
```
