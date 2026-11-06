# bench-rock

The benchmark: rock against the four bots.

200 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| tag | tag | +932 | 670 | +863 | 638 | +3726 | 0 | - | - | 0 | 0 |
| rock | rock | +439 | 453 | +356 | 386 | +1756 | 0 | - | - | 0 | 0 |
| entrant | rock | +233 | 327 | +233 | 327 | +931 | 0 | - | - | 0 | 0 |
| station | station | -360 | 732 | -334 | 701 | -1441 | 0 | - | - | 0 | 0 |
| maniac | maniac | -1243 | 702 | -1118 | 676 | -4972 | 0 | - | - | 0 | 0 |

9 of the 200 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Clear of break-even: tag, maniac.
Too close to break-even to call: rock, entrant, station.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands    net   bb/100  vpip  pfr  3bet  f3b    af  wtsd  w$sd  bluff  illegal   ms  $/hand
tag        200   3726    931.5   16%  12%    5%  27%   1.4   57%   80%    10%       0%  0.0       -
rock       200   1756    439.0    8%   4%    0%  20%   0.2   50%   90%     0%       0%  0.0       -
entrant    200    931    232.8    6%   0%    0%   0%   0.4   44%   67%     0%       0%  0.0       -
station    200  -1441   -360.2   94%   0%    0%    -   0.0  100%   43%      -       0%  0.0       -
maniac     200  -4972  -1243.0   92%  70%   65%   0%  28.7   99%   44%    47%       0%  0.0       -
```
