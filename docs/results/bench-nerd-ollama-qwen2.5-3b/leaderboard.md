# bench-nerd-ollama-qwen2.5-3b

The benchmark: llm:nerd@ollama/qwen2.5:3b against the four bots.

200 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took 82 minutes and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| entrant | llm:nerd on ollama/qwen2.5:3b | +1387 | 1460 | +224 | 409 | +5548 | 186 | 26.5 | 0.0000 | 0 | 0 |
| rock | rock | +427 | 449 | +381 | 411 | +1707 | 0 | - | - | 0 | 0 |
| tag | tag | +285 | 407 | +222 | 349 | +1140 | 0 | - | - | 0 | 0 |
| maniac | maniac | -712 | 1390 | -72 | 659 | -2846 | 0 | - | - | 0 | 0 |
| station | station | -1387 | 1439 | -754 | 643 | -5549 | 0 | - | - | 0 | 0 |

114 of the 200 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls use it.

Clear of break-even: station.
Too close to break-even to call: entrant, rock, tag, maniac.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands    net   bb/100  vpip  pfr  3bet   f3b    af  wtsd  w$sd  bluff  illegal       ms  $/hand
entrant    200   5548   1387.0   58%  58%   41%   67%   inf   95%   45%     0%       0%  21936.4  0.0000
rock       200   1707    426.8    4%   2%    0%  100%   0.7   55%   83%    12%       0%      0.0       -
tag        200   1140    285.0    8%   6%    2%   12%   1.3   40%   83%    11%       0%      0.0       -
maniac     200  -2846   -711.5   94%  51%   12%    3%  32.7  100%   40%    41%       0%      0.0       -
station    200  -5549  -1387.2   96%   0%    0%     -   0.0   99%   35%      -       0%      0.0       -
```

## What they said they held

Each model seat's reasoning, checked against the cards it was dealt. Only clear claims about its own two cards count, so this is a floor.

| seat | decisions | named its own cards | wrongly |
|---|---:|---:|---:|
| entrant | 186 | 18 | 5 |

- entrant, hand 24, holding J♠ J♥: "My JsJs are strong, and maniac has shown consistent aggression regardless of hand strength."
- entrant, hand 33, holding 7♠ Q♦: "Raising with JJ in early position puts good pressure on loose-aggressive players like station and maniac, while not overextending myself gi…"
- entrant, hand 66, holding Q♠ 6♥: "My QQ has poor equity against the maniac’s loose-aggressive style and station's passive play."
