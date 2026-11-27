# hu-maniac-vs-tag

The heads-up track: maniac against tag.

400 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| tag | tag | +326 | 253 | +294 | 226 | +2610 | 0 | - | - | 0 | 0 |
| entrant | maniac | -326 | 253 | -294 | 226 | -2610 | 0 | - | - | 0 | 0 |

18 of the 400 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Clear of break-even: tag, entrant.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands    net  bb/100  vpip  pfr  3bet  f3b   af  wtsd  w$sd  bluff  illegal   ms  $/hand
tag        400   2610   326.2   17%  12%    3%  12%  3.0   33%   79%     0%       0%  0.0       -
entrant    400  -2610  -326.2   55%  43%   73%   0%  4.0   33%   24%    43%       0%  0.0       -
```
