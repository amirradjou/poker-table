# hu-cfr-blueprint-vs-tag

The heads-up track: cfr:blueprint against tag.

400 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| entrant | cfr:blueprint | +60 | 108 | +60 | 108 | +480 | 0 | - | - | 0 | 0 |
| tag | tag | -60 | 108 | -60 | 108 | -480 | 0 | - | - | 0 | 0 |

0 of the 400 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Too close to break-even to call: entrant, tag.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands   net  bb/100  vpip  pfr  3bet  f3b   af  wtsd  w$sd  bluff  illegal   ms  $/hand
entrant    400   480    60.0   50%  37%    5%  50%  1.1   29%   48%    55%       0%  3.0       -
tag        400  -480   -60.0   17%  12%    1%   0%  3.8   29%   62%    12%       0%  0.0       -
```
