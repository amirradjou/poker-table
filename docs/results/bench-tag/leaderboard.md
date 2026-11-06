# bench-tag

The benchmark: tag against the four bots.

200 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| entrant | tag | +677 | 652 | +493 | 542 | +2707 | 0 | - | - | 0 | 0 |
| tag | tag | +592 | 631 | +549 | 597 | +2370 | 0 | - | - | 0 | 0 |
| rock | rock | +539 | 494 | +436 | 413 | +2157 | 0 | - | - | 0 | 0 |
| station | station | -898 | 884 | -828 | 812 | -3593 | 0 | - | - | 0 | 0 |
| maniac | maniac | -910 | 833 | -650 | 801 | -3641 | 0 | - | - | 0 | 0 |

20 of the 200 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Clear of break-even: rock, station.
Too close to break-even to call: entrant, tag, maniac.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands    net  bb/100  vpip  pfr  3bet  f3b    af  wtsd  w$sd  bluff  illegal   ms  $/hand
entrant    200   2707   676.8   20%  14%    4%   8%   2.1   51%   68%    14%       0%  0.0       -
tag        200   2370   592.5   16%  11%    3%  25%   0.6   48%   69%     0%       0%  0.0       -
rock       200   2157   539.2    8%   2%    0%   0%   0.9   50%  100%     0%       0%  0.0       -
station    200  -3593  -898.2   93%   0%    0%    -   0.0  100%   42%      -       0%  0.0       -
maniac     200  -3641  -910.2   89%  70%   73%   0%  20.5   98%   43%    46%       0%  0.0       -
```
