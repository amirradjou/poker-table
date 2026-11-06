# bench-random

The benchmark: random against the four bots.

200 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| maniac | maniac | +1550 | 1493 | +1994 | 1102 | +6200 | 0 | - | - | 0 | 0 |
| rock | rock | +395 | 475 | +378 | 439 | +1580 | 0 | - | - | 0 | 0 |
| tag | tag | +110 | 356 | +30 | 262 | +442 | 0 | - | - | 0 | 0 |
| station | station | -426 | 1440 | -57 | 1053 | -1705 | 0 | - | - | 0 | 0 |
| entrant | random | -1629 | 1122 | -2346 | 763 | -6517 | 0 | - | - | 0 | 0 |

86 of the 200 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Clear of break-even: maniac, entrant.
Too close to break-even to call: rock, tag, station.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands    net   bb/100  vpip  pfr  3bet  f3b   af  wtsd  w$sd  bluff  illegal   ms  $/hand
maniac     200   6200   1550.0   88%  71%   54%   7%  8.7   98%   45%    49%       0%  0.0       -
rock       200   1580    395.0    7%   2%    0%  33%  0.4   53%   75%     0%       0%  0.0       -
tag        200    442    110.5   10%   8%    4%  50%  1.3   32%   71%    25%       0%  0.0       -
station    200  -1705   -426.2   96%   0%    0%    -  0.0  100%   43%      -       0%  0.0       -
entrant    200  -6517  -1629.2   68%  39%   37%  43%  2.4   57%   38%    54%       0%  0.0       -
```
