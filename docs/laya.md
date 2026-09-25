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

<!-- MEASUREMENTS -->

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
