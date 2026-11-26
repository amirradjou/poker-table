# hu-cfr-blueprint-vs-maniac

The heads-up track: cfr:blueprint against maniac.

400 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took 7 seconds and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| entrant | cfr:blueprint | +224 | 305 | +211 | 289 | +1794 | 0 | - | - | 0 | 0 |
| maniac | maniac | -224 | 305 | -211 | 289 | -1794 | 0 | - | - | 0 | 0 |

12 of the 400 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Too close to break-even to call: entrant, maniac.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands    net  bb/100  vpip  pfr  3bet  f3b   af  wtsd  w$sd  bluff  illegal   ms  $/hand
entrant    400   1794   224.2   84%  50%   17%  19%  0.7   24%   82%    32%       0%  4.5       -
maniac     400  -1794  -224.2   88%  70%   66%   3%  9.9   24%   24%    47%       0%  0.0       -
```
