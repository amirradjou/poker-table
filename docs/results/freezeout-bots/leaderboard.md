# freezeout-bots

The five scripted bots over twelve freezeouts, the blinds rising every five hands.

12 freezeouts of 5 seats with 200 chips each, levels 1/2 to 800/1600+200 at 5 hands a level, seed 1, the seats drawn again for each one. 498 hands in all. It took a second or less and $0.0000.

| seat | plays as | average finish | ±95% | wins | share won | 95% interval | hands lasted | model calls | s/call | $/tournament | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| tag | tag | 2.08 | 0.63 | 4 | 33% | 14%–61% | 35 | 0 | - | - | 0 | 0 |
| rock | rock | 2.17 | 0.46 | 2 | 17% | 5%–45% | 37 | 0 | - | - | 0 | 0 |
| station | station | 3.25 | 0.90 | 2 | 17% | 5%–45% | 18 | 0 | - | - | 0 | 0 |
| random | random | 3.67 | 0.73 | 1 | 8% | 1%–35% | 11 | 0 | - | - | 0 | 0 |
| maniac | maniac | 3.83 | 1.11 | 3 | 25% | 9%–53% | 11 | 0 | - | - | 0 | 0 |

With 5 seats, chance is an average finish of 3.00 and 20% of the wins. Each tournament is one sample, however long it ran: the average finish has a 95% t interval over the tournaments, and the share won a 95% Wilson score interval. Hands lasted is how many hands a seat was dealt into.

Better than chance: tag, rock.
Too close to chance to call: station, random, maniac.
Too close to call against the seat below: tag and rock, rock and station, station and random, random and maniac.

## How they played

Over every hand of every tournament, so the blinds grow as it goes.

```
name     hands  vpip  pfr  3bet  f3b    af  wtsd  w$sd  bluff  illegal   ms  $/hand
tag        424   15%  15%    2%  40%   8.0   85%   62%    11%       0%  0.0       -
maniac     131   72%  49%   55%   0%  11.8   70%   36%    51%       0%  0.0       -
rock       446    7%   3%    0%   0%   1.7   86%   59%     0%       0%  0.0       -
station    218   74%   0%    0%    -   0.0  100%   46%      -       0%  0.0       -
random     128   52%  33%   30%  17%   9.2   73%   34%    70%       0%  0.0       -
```
