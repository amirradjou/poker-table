# bench-station

The benchmark: station against the four bots.

200 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| tag | tag | +1083 | 903 | +1034 | 746 | +4332 | 0 | - | - | 0 | 0 |
| rock | rock | +1076 | 837 | +861 | 698 | +4306 | 0 | - | - | 0 | 0 |
| entrant | station | -100 | 1288 | +131 | 1178 | -402 | 0 | - | - | 0 | 0 |
| station | station | -775 | 1246 | -827 | 1134 | -3099 | 0 | - | - | 0 | 0 |
| maniac | maniac | -1284 | 1150 | -1198 | 1053 | -5137 | 0 | - | - | 0 | 0 |

25 of the 200 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Clear of break-even: tag, rock, maniac.
Too close to break-even to call: entrant, station.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands    net   bb/100  vpip  pfr  3bet  f3b    af  wtsd  w$sd  bluff  illegal   ms  $/hand
tag        200   4332   1083.0   16%  13%    2%  21%   1.0   68%   50%    24%       0%  0.0       -
rock       200   4306   1076.5    8%   2%    0%   0%   0.3   68%   69%     0%       0%  0.0       -
entrant    200   -402   -100.5   92%   0%    0%    -   0.0  100%   37%      -       0%  0.0       -
station    200  -3099   -774.8   96%   0%    0%    -   0.0  100%   27%      -       0%  0.0       -
maniac     200  -5137  -1284.2   90%  70%   70%   0%  46.0   99%   29%    46%       0%  0.0       -
```
