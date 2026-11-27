# hu-station-vs-station

The heads-up track: station against station.

400 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| station | station | +0 | 10 | +0 | 10 | +4 | 0 | - | - | 0 | 0 |
| entrant | station | -0 | 10 | -0 | 10 | -4 | 0 | - | - | 0 | 0 |

0 of the 400 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Too close to break-even to call: station, entrant.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands  net  bb/100  vpip  pfr  3bet  f3b  af  wtsd  w$sd  bluff  illegal   ms  $/hand
station    400    4     0.5   50%   0%     -    -   -  100%   53%      -       0%  0.0       -
entrant    400   -4    -0.5   50%   0%     -    -   -  100%   52%      -       0%  0.0       -
```
