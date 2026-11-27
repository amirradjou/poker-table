# hu-random-vs-maniac

The heads-up track: random against maniac.

400 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took a second or less and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| maniac | maniac | +1635 | 575 | +1752 | 458 | +13082 | 0 | - | - | 0 | 0 |
| entrant | random | -1635 | 575 | -1752 | 458 | -13082 | 0 | - | - | 0 | 0 |

80 of the 400 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Clear of break-even: maniac, entrant.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands     net   bb/100  vpip  pfr  3bet  f3b   af  wtsd  w$sd  bluff  illegal   ms  $/hand
maniac     400   13082   1635.2   76%  66%   80%   5%  4.5   52%   47%    48%       0%  0.0       -
entrant    400  -13082  -1635.2   62%  37%   30%  43%  1.6   52%   58%    44%       0%  0.0       -
```
