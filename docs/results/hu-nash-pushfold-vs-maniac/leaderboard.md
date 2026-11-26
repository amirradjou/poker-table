# hu-nash-pushfold-vs-maniac

The heads-up track: nash:pushfold against maniac.

400 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| entrant | nash:pushfold | +60 | 207 | +81 | 199 | +484 | 0 | - | - | 0 | 0 |
| maniac | maniac | -60 | 207 | -81 | 199 | -484 | 0 | - | - | 0 | 0 |

10 of the 400 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Too close to break-even to call: entrant, maniac.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands   net  bb/100  vpip  pfr  3bet  f3b   af  wtsd  w$sd  bluff  illegal   ms  $/hand
entrant    400   484    60.5   16%  11%    1%  40%  2.7   26%   68%     3%       0%  0.0       -
maniac     400  -484   -60.5   57%  46%   80%   0%  4.1   26%   36%    49%       0%  0.0       -
```
