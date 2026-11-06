# bench-maniac

The benchmark: maniac against the four bots.

200 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| entrant | maniac | +1667 | 1794 | +78 | 1130 | +6668 | 0 | - | - | 0 | 0 |
| rock | rock | +404 | 574 | +307 | 478 | +1617 | 0 | - | - | 0 | 0 |
| tag | tag | +166 | 503 | +191 | 392 | +665 | 0 | - | - | 0 | 0 |
| maniac | maniac | -418 | 1703 | +606 | 1202 | -1673 | 0 | - | - | 0 | 0 |
| station | station | -1819 | 1644 | -1183 | 1136 | -7277 | 0 | - | - | 0 | 0 |

143 of the 200 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Clear of break-even: station.
Too close to break-even to call: entrant, rock, tag, maniac.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands    net   bb/100  vpip  pfr  3bet  f3b   af  wtsd  w$sd  bluff  illegal   ms  $/hand
entrant    200   6668   1667.0   93%  78%   67%  10%  2.7   95%   44%    61%       0%  0.0       -
rock       200   1617    404.2    6%   2%    1%    -  0.4   27%  100%     0%       0%  0.0       -
tag        200    665    166.2   14%   6%    2%   0%  0.6   39%   57%     0%       0%  0.0       -
maniac     200  -1673   -418.2   94%  76%   65%   3%  2.5   96%   35%    51%       0%  0.0       -
station    200  -7277  -1819.2  100%   0%    0%    -  0.0  100%   32%      -       0%  0.0       -
```
