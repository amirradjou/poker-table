# freezeout-pushfold

nash:pushfold against tag over heads-up freezeouts, the default blind schedule.

12 freezeouts of 2 seats with 200 chips each, levels 1/2 to 800/1600+200 at 20 hands a level, seed 2026, the seats drawn again for each one. 902 hands in all. It took a second or less and $0.0000.

| seat | plays as | average finish | ±95% | wins | share won | 95% interval | hands lasted | model calls | s/call | $/tournament | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| entrant | nash:pushfold | 1.25 | 0.29 | 9 | 75% | 47%–91% | 75 | 0 | - | - | 0 | 0 |
| tag | tag | 1.75 | 0.29 | 3 | 25% | 9%–53% | 75 | 0 | - | - | 0 | 0 |

With 2 seats, chance is an average finish of 1.50 and 50% of the wins. Each tournament is one sample, however long it ran: the average finish has a 95% t interval over the tournaments, and the share won a 95% Wilson score interval. Hands lasted is how many hands a seat was dealt into.

Too close to chance to call: entrant, tag.
Too close to call against the seat below: entrant and tag.

## How they played

Over every hand of every tournament, so the blinds grow as it goes.

```
name     hands  vpip  pfr  3bet  f3b   af  wtsd  w$sd  bluff  illegal   ms  $/hand
entrant    902   22%  19%    9%   0%  2.9   60%   42%    25%       0%  0.0       -
tag        902   14%  12%    1%  67%  3.1   60%   61%    14%       0%  0.0       -
```
