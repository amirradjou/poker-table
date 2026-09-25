# A Laya seat

[Laya](https://huggingface.co/convaiinnovations/laya) is an open (Apache-2.0) **decision**
model: an encoder with a decision head that scores a set of named options in one forward pass
and returns a calibrated probability for each. It is not a chat model — it emits **zero output
tokens** — and it is free to run on your own machine.

That makes it an interesting seat at this table, because the table is a measuring instrument:
every seat is scored on the same axes (bb/100, illegal-action rate, latency, cost per hand), so
"can a 421M decision model play poker?" becomes a question with a number rather than a vibe.

```sh
uv sync --extra laya                      # pulls torch; the core install stays light
uv run poker-table play -n 50 --seats laya,tag,station -o laya.jsonl
uv run poker-table serve laya.jsonl --open
```

## How the seat asks

Each decision is one `choice` question:

- **State** — a compact few lines (`agents/laya.py::compact_state`), because Laya's usable
  input is a few hundred tokens. Position, hole cards, board, pot, price, live stacks, and this
  hand's action so far. Never another seat's cards.
- **Options** — the legal actions of *this* decision: fold, check or call, and the min raise,
  the half-pot and pot-sized raises and the shove, deduplicated and clamped. Because the option
  set *is* the legal set, **the seat cannot pick an illegal action** — its illegal rate on the
  leaderboard is 0% by construction, where an LLM seat has to be sanitized into legality.
- **Wording** — every option is phrased positively ("give up the hand now…", "pay 12 to stay
  in…"). Laya has a published negation bug (#377) where "do not X" can score X highly, so no
  option or instruction is written as a prohibition.

```
6-handed no-limit hold'em, blinds 1/2.
You are in the BTN with Ah Kd, stack 194.
Street: flop. Board: 8s 7d 2c. Pot 34, to call 12, pot odds 2.8:1.
Still in: sb 188 (in for 12), bb 200.
preflop: utg raise 6, you call, bb call
flop: bb check, utg bet 12
```

## How the seat answers

The model returns a probability per option plus two confidences. The seat takes the argmax, and
gates on the **calibrated `confidence`** — not on `act_probability`, which is documented as
unusable for gating. Below the threshold (default 0.40) the scripted chart bot plays the spot
instead and the seat counts it as gated. Errors, missing answers and answers outside the option
set fall back the same way, so one bad seat never stops a table.

Every decision records `model`, `latency_ms`, `input_tokens`, `output_tokens: 0`,
`cost_usd: 0.0`, the winning confidence and the full probability distribution — so a replay in
the viewer shows exactly what the model thought of each option.

## Measured: the base checkpoint

Run on this machine (CPU, no GPU) against the published base checkpoint
`convaiinnovations/laya`, laya 0.3.20, with `uv run python scripts/measure_laya.py`.
Three questions, three answers.

**1. Does it agree with the coach?** 76 spots the coach flagged in a 60-hand bot session, asked
with the gate off, scored against `accepted_actions()` — what the charts and the pot-odds maths
say was right:

| | |
|---|---|
| agrees with the coach | **45%** (34 / 76) |
| always folding would score | **76%** (58 of the 76 answers are "fold") |
| median latency | 454 ms (CPU; the model card reports ~33 ms on a T4) |
| mean calibrated confidence | 0.09 |

It is **below the majority-class baseline** — a seat that folded every flagged spot would agree
with the coach more often. This is the model's own documented position for base checkpoints on
typed decisions, reproduced here on poker.

**2. What happens at the table with the gate on (default 0.40)?** 40 hands, seed 2026:

```
name     hands   net  bb/100  vpip  pfr  illegal     ms  $/hand
laya        40   423   528.8   25%  25%       0%  461.8  0.0000
tag         40   134   167.5   20%  18%       0%    0.0       -
rock        40  -196  -245.0    8%   2%       0%    0.0       -
station     40  -361  -451.2   90%   0%       0%    0.0       -
```

**That +528.8 bb/100 is not Laya playing.** Its mean confidence (0.09) sits far below the 0.40
gate, so the chart played 90 of its 92 spots and the model played 2. The row is the fallback's
row wearing Laya's name — which is exactly why `play` now prints a line saying so.

**3. What happens when it has to decide every spot?** Same 40 hands, gate off:

```
name     hands   net  bb/100  vpip  pfr  illegal     ms  $/hand
tag         40   124   155.0   12%  10%       0%    0.0       -
station     40    92   115.0   80%   0%       0%    0.0       -
rock        40    -9   -11.2    8%   0%       0%    0.0       -
laya        40  -207  -258.8   50%  25%       0%  436.8  0.0000
```

59 decisions, no errors, no illegal actions, 437 ms each, $0 — and last place, behind the
calling station. It chose fold 35 times, call 12, raise 10, bet 2: it plays half its hands and
then gives up.

**A calibration caveat, printed by the library itself:** loading the checkpoint warns that it
"ships invalid temperatures or values outside [0.5, 5] … treat confidence from the affected
entries as uncalibrated". So the confidences above — and therefore where the gate bites — are
not trustworthy until a temperature is refit on your own data.

### What to take from this

- The harness works on it exactly as on any other seat: **0% illegal actions** (the options
  *are* the legal actions), 437 ms per decision on a CPU, **$0.0000 per hand** against an LLM
  seat's real money, and every probability visible in the replay.
- The base checkpoint cannot play poker. It is a baseline, and the honest number is
  −258.8 bb/100 with the gate off, not the flattering row the gate produces.
- The interesting experiment is therefore the fine-tune below — and the comparison to beat is
  not the LLM seats, it is `tag`: a few hundred lines of chart that costs nothing and wins.


## Training it on this table's own decisions

The base checkpoint is **not zero-shot competitive** — that is the model's own documented
position, and the numbers above are consistent with it. The fix is labelled data, which this
repo produces for free:

```sh
# what the charts and the pot-odds maths say was right, in every spot the coach flagged
uv run poker-table dataset hands.jsonl -p me -o corrections.jsonl --source coach

# what a known-good policy actually did, decision by decision
uv run poker-table dataset hands.jsonl -p tag -o imitation.jsonl --source policy
```

Each line is one sample in the same shape the seat asks at the table:

```json
{"state": "…", "instructions": "…", "options": {"fold": "…", "call 12": "…"},
 "answer": "call 12", "source": "coach", "tag": "call_vs_bet", "hand_id": "41", "seat": 2,
 "street": "flop"}
```

Two things to keep honest when you train on it:

- **`policy` data is only as good as the policy.** `tag` follows a Chen-formula chart and
  simple postflop rules; imitating it caps you at its strength, and it disagrees with the
  coach's charts in places (see `docs/baseline.md`).
- **`coach` data is corrections, not play.** It only contains spots the coach flagged, so its
  answer distribution is skewed towards folds. Mixing it with `policy` data, or reweighting, is
  a decision to make deliberately rather than by accident.

After fine-tuning, point a seat at your checkpoint:

```sh
uv run poker-table play -n 200 --seats "mine:laya@/path/to/checkpoint,tag,station"
```

`laya@repo#subfolder` also works, for a repo that bundles several checkpoints
(`laya@convaiinnovations/laya#typed-decisions`).

## What this is not

It is not a claim that a decision model is the right tool for poker. It is a seat at a table
that measures seats, added because the comparison is cheap to run and the result is interesting
either way: a free, instant, always-legal seat on one end of the cost curve, and an LLM seat
that costs real money per hand on the other.
