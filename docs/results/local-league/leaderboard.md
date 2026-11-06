# local-league

Two LLM personalities on a small local model, against three scripted bots.

100 hands at blinds 1/2, 200 chips topped up every hand, seed 2026. It took 1 h 48 min and $0.0000.

| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls | s/call | $/hand | illegal | failed |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| station | station | +2776 | 2452 | +659 | 1020 | +5552 | 0 | - | - | 0 | 0 |
| tag | tag | +424 | 630 | +243 | 421 | +848 | 0 | - | - | 0 | 0 |
| maniac | llm:maniac on ollama/qwen2.5:3b | +38 | 1752 | -475 | 859 | +77 | 125 | 30.4 | 0.0000 | 40 | 0 |
| nerd | llm:nerd on ollama/qwen2.5:3b | -604 | 1587 | +61 | 728 | -1209 | 86 | 30.9 | 0.0000 | 0 | 0 |
| random | random | -2634 | 1406 | -488 | 777 | -5268 | 0 | - | - | 0 | 0 |

95 of the 100 hands were all in before the last card. All-in adj. counts those at each seat's share of the pot at that moment, which takes out the luck of the cards still to come. It estimates the same win rate as bb/100 with less noise, so the calls below use it.

Too close to break-even to call: station, tag, maniac, nerd, random.

The interval treats hands as independent and looks at one seat at a time, so with many seats one can clear zero by luck.

## How they played

```
name     hands    net   bb/100  vpip  pfr  3bet   f3b   af  wtsd  w$sd  bluff  illegal       ms  $/hand
station    100   5552   2776.0  100%   0%    0%     -  0.0   98%   53%      -       0%      0.0       -
tag        100    848    424.0    2%   2%    0%    0%  0.0  100%  100%      -       0%      0.0       -
maniac     100     77     38.5   63%  63%   40%   80%  inf   95%   52%    62%      32%  30418.5  0.0000
nerd       100  -1209   -604.5   55%  54%   24%  100%    -  100%   38%      -       0%  23102.2  0.0000
random     100  -5268  -2634.0   57%  20%    4%   46%  1.5   95%   16%    33%       0%      0.0       -
```

## What the coach says

- [maniac](coach/maniac.md)
- [nerd](coach/nerd.md)
