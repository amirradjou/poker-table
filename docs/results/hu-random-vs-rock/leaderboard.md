# hu-random-vs-rock

The heads-up track: random against rock.

400 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| rock | rock | +209 | 139 | +189 | 129 | +1673 | 0 | - | - | 0 | 0 |
| entrant | random | -209 | 139 | -189 | 129 | -1673 | 0 | - | - | 0 | 0 |

2 of the 400 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Clear of break-even: rock, entrant.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands    net  bb/100  vpip  pfr  3bet   f3b   af  wtsd  w$sd  bluff  illegal   ms  $/hand
rock       400   1673   209.1    6%   2%    1%  100%  1.3   20%   94%     0%       0%  0.0       -
entrant    400  -1673  -209.1   36%  18%   25%    0%  8.4   20%    6%    51%       0%  0.0       -
```
