# Seats: smarter LLMs, and your own agents

A seat is anything with `act(view) -> Decision`. The table ships scripted bots (`tag`, `rock`,
`maniac`, `station`, `random`), a scripted seat that reads the table (`exploiter`), four LLM
personalities (`llm:nerd`, `llm:maniac`, `llm:rock`, `llm:storyteller`), a [Laya](laya.md) seat
and a human seat. This page covers:

- what an LLM seat is handed on top of the bare table;
- `exploiter`, a tag that adapts to what it has seen of each opponent;
- how to define your own seats in a TOML file, with no code, or in Python;
- how to run them on your own key, or on a local model for free;
- and how to measure whether any of it helps.

```sh
uv run poker-table agents                          # every kind a seat can be
uv run poker-table agents --example > agents.toml  # a commented starter file
uv run poker-table play --agents agents.toml --seats shark,nit,tag,station --show
uv run poker-table play --seats llm:nerd@ollama/qwen2.5:7b,tag,station --show   # no API key
uv run poker-table eval --seats llm:nerd@ollama/qwen2.5:7b,tag -n 40            # score them
```

## What an LLM seat is handed

A bare seat sees what a player at the table sees: its cards, the board, the stacks, the action
so far, the table talk and the legal moves (`SeatView.describe()`). A strong player also carries
things in their head that a model would otherwise have to guess, or would get wrong. Each of
those is an **aid**. Every aid can be switched off per personality or per seat, so its worth can
be measured (see [Does it help?](#does-it-help)). The first five are on by default.

| aid | what the model gets | where it comes from |
|---|---|---|
| `odds` | equity against random hands, outs, pot odds, break-even equity, the value of a call | [`odds.py`](odds.md), from the seat's own cards and the board |
| `ranges` | each opponent's range read from this hand's action, and the seat's equity against it | the coach's range narrowing (`coach.facts.read_ranges`) run on the live view |
| `reads` | per opponent: sample size, style, VPIP/PFR/3-bet, aggression, fold-to-bet, showdown rate, the last hands they turned over | `agents/reads.py`, built only from hands the seat was shown |
| `notes` | a notebook: the reply gains a `note` field, and the latest twelve lines come back in later prompts | the model itself |
| `playbook` | a short primer on sizing, hand selection and opponent types | the system prompt |
| `sizes` | off by default. Named sizes (min, half pot, pot, all in) instead of a number, the same four a person gets as buttons | `named_sizes` in the seat's view |

The `ranges` aid is the one that changes decisions most visibly. The calculator answers "how
often do I win against any two cards". Nobody holds any two cards after opening from early
position and betting the flop. Here is the end of the prompt the big blind gets holding K♥Q♦
against a button open:

```
Legal: fold, call 4, raise to 10..200
The numbers (your cards against random hands for everyone still in):
  Win 57.9% · tie 2.3% · lose 39.8%
  Equity 59.1% (1200 samples, ±1.4%)
  Pot 9, to call 4: pot odds 2.2:1, break-even equity 30.8%
  Calling is +3.7 chips against folding — 28.3% more equity than the price asks
Their likely hands, read from this hand's action:
  p0: opened from the BTN — about 44% of hands
  Your equity against those ranges: 51.4% (±2.0%, 600 samples)
Decide now.
```

After a few dozen hands, `reads` adds lines like these, from a seeded 80-hand bot session:

```
What you have seen of them (80 hands):
  maniac (80 hands): loose-aggressive. Plays 92%, raises first 66%, 3-bets 65%. After the
  flop: aggression 10.8, folds to a bet 12%, goes to showdown 97%. Showed Ah 4s for two pair,
  aces and jacks (flop check, turn check, river bet; won, hand 79).
  station (80 hands): loose-passive, calls down. Plays 91%, raises first 0%. ...
```

**What a seat can never see.** A seat learns across hands through `observe(history)`, which
the league calls after every hand with `history.public_view()`. That view keeps the seat's own
cards, every card turned over at showdown, the actions and the table talk. It drops folded
cards and every other seat's reasoning, requested action and model metadata. The tests pin
this down: a seat's range reading does not change when an opponent's hidden cards do, and a
folded hand never reaches a read.

## exploiter: a tag that reads the table

`exploiter` plays tag's game, and keeps reads on each opponent from what it has seen. The reads
are the same HUD the `reads` aid gives an LLM seat, built only from the hands the seat was shown.
It makes no model calls and needs no training. It plays at any table from 2 to 9 seats, and the
browser demo can run it.

```sh
uv run poker-table play -n 200 --seats exploiter,station --show
```

**When a read counts.** An opponent needs 20 hands seen before any read of it counts. Its fold
rate also needs 10 bets faced after the flop, and its aggression 10 bets, raises and calls after
the flop. Until then the seat plays exactly as tag.

| an opponent is | when it |
|---|---|
| a caller | plays at least 40% of hands, and folds to at most 25% of bets after the flop |
| a passive caller | is a caller with an aggression factor of 1 or less |
| a folder | folds to at least 60% of bets after the flop |
| very aggressive | raises first in at least 35% of hands, with an aggression factor of 3 or more |

**The five rules.** Each starts from tag's decision and changes it only in one kind of spot.

| rule | against | what changes |
|---|---|---|
| `defend_wide` | very aggressive | before the flop, call a raise of up to 4 big blinds with a Chen score of 5 or more, where tag folds |
| `call_down` | very aggressive | facing a bet after the flop, call with top pair at any price, and with a weaker pair at the price tag pays with top pair (up to 60% of the pot) |
| `no_bluff` | a caller | tag's bets with no pair, its c-bets and semi-bluffs, become checks |
| `thin_value` | a passive caller | when nobody has bet: bet a weaker pair for half the pot, top pair for three quarters, two pair or better for the pot |
| `bluff_folder` | a folder | on the turn and river, once everyone still in has checked, bet two thirds of the pot with no pair where tag checks |

**Multi-way pots.** A rule fires only when its read holds for every opponent still in the hand.
Before the flop that includes the seats yet to act. If one opponent does not fit, or has not been
seen enough, tag plays.

**What it records.** Every decision's `meta` says how it was made: `{"method": "tag"}`, or
`{"method": "adjusted", "rule": "thin_value", "against": ["station"], "tag": "check"}`, where
`tag` is the action tag would have played. The reasoning adds the read behind the change.
`play` ends with a line that counts the decisions a read changed, by rule.

**Same answers every time.** The tag inside is asked at every decision, so its random choices
run as a tag seat's would, and the rules draw nothing at random. Until a rule fires, its hands
are a tag seat's hands. A resumed freezeout asks it again and rebuilds its reads from the same
hands, so it carries on exactly.

### Measured: against tag on the same deals

The hypothesis was written down before any of these hands were played
([PR #51](https://github.com/amirradjou/poker-table/pull/51)): exploiter beats the station and
the maniac by more than tag does, and is no worse than tag against tag and rock. Each match is
2,000 hands from seed 2026, played twice: once with exploiter in seat 0, once with tag in that
seat on the same deals. The difference is paired hand for hand, in all-in adjusted bb/100 with a
95% interval. It took 1.4 minutes on 12 cores at a load average of about 4.

| table | exploiter | tag in its seat | difference | how it decided |
|---|---:|---:|---:|---|
| vs tag | -19 ±31 | -19 ±31 | +0 ±0 | tag 100% |
| vs rock | +6 ±20 | +6 ±20 | +0 ±0 | tag 100% |
| vs maniac | +546 ±132 | +238 ±98 | +308 ±121 | tag 89%, adjusted 11% |
| vs station | +423 ±85 | +191 ±43 | +232 ±48 | tag 64%, adjusted 36% |
| five-handed | +728 ±188 | +609 ±183 | +120 ±99 | tag 97%, adjusted 3% |

Which rules changed decisions in those matches, and the first hand one did:

| table | decisions | changed | `defend_wide` | `call_down` | `no_bluff` | `thin_value` | `bluff_folder` | first change |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| vs tag | 1,380 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| vs rock | 1,341 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| vs maniac | 4,476 | 509 (11.4%) | 216 (4.8%) | 257 (5.7%) | 36 (0.8%) | 0 | 0 | hand 35 |
| vs station | 5,783 | 2,078 (35.9%) | 0 | 0 | 259 (4.5%) | 1,819 (31.5%) | 0 | hand 29 |
| five-handed | 3,272 | 102 (3.1%) | 0 | 0 | 23 (0.7%) | 79 (2.4%) | 0 | hand 24 |

What this supports:

- **Against the station and the maniac, the hypothesis held.** It won 232 ±48 and 308 ±121
  bb/100 more than tag in its seat, and both intervals sit well above zero. Against the station
  that is `thin_value` and `no_bluff`; against the maniac, `call_down` and `defend_wide`.
- **Against tag and rock it is no worse, but only because it did nothing.** No rule fired, so
  those matches are tag's own hands and the difference is exactly zero. The reads called tag a
  folder in 20 hands and rock in 25. But `bluff_folder` needs the turn or the river checked to it
  with no pair, and in 2,000 hands that came up twice against tag and three times against rock,
  never while the read said folder. Rock plays 3 to 10% of hands, too few to be a caller.
- **Reads start changing decisions after 24 to 35 hands.** The earliest possible is hand 21.
- **Five-handed, it adjusts 3% of decisions.** The maniac and the station each play about 90%
  of hands, so most pots have both in, and a rule waits until every opponent still in fits it.
  The gain is smaller, +120 ±99, with its interval just clear of zero.
- **`bluff_folder` never fired in any of these runs.** So it is untried in play; only its
  hand-built test checks it.

What it does not show: the opponents are scripted bots with fixed styles, and the rules are the
textbook answers to those styles. It says nothing about opponents that adapt back. It is one
seed. The published runs, on the leaderboard's 200 and 400 hands, are in
[the leaderboard](leaderboard.md) and [the heads-up track](leaderboard-heads-up.md).

```sh
uv run python scripts/measure_vs_tag.py exploiter --five-handed   # the paired table
uv run python scripts/exploiter_rules.py --matches                # which rules fired in it
uv run poker-table benchmark exploiter -o docs/results            # the benchmark folder
uv run python scripts/run_heads_up_track.py exploiter --replace   # the heads-up track
uv run python scripts/exploiter_rules.py                          # rules in those folders
```

## Your own seats

An agents file is TOML. Each `[agents.<name>]` table defines a kind that `--seats` can name like
a built-in one. `poker-table agents --agents FILE` checks the file and lists what it defines.
Any mistake is reported by name: a typo like `modle` gets an error that lists the fields the
type takes. Pass `--agents` more than once to combine files.

### An LLM persona, no code

```toml
[agents.shark]
description = "Patient, then ruthless"
type = "llm"
model = "claude-sonnet-5"          # or "ollama/qwen2.5:7b"
prompt = """
You are THE SHARK. You play few hands, and when you play one you play it hard...
"""
effort = "medium"                  # low | medium | high | xhigh | max (Claude only)
auto_fold_below = 6                # fold junk facing a raise without a call; false = never
talkative = false
aids = ["odds", "ranges", "reads", "notes", "playbook"]
```

| field | default | meaning |
|---|---|---|
| `prompt` | required, unless `extends` | the personality, in the model's system prompt |
| `extends` | none | start from `nerd`, `maniac`, `rock` or `storyteller`; any other field overrides it |
| `model` | `claude-opus-5-5` | a Claude model, or `ollama/<name>` for a local one |
| `effort` | `medium` | Claude's effort level; ignored by local models |
| `auto_fold_below` | `5` | preflop, facing a raise, hands under this Chen score fold without a model call |
| `talkative` | `true` | `false` tells the model it almost never talks |
| `aids` | all five | what the seat is handed, from the table above |
| `max_tokens` | `8000` | the reply's token cap |
| `options` | none | Ollama options (`temperature`, `num_ctx`, ...), local models only |

A persona is not tied to its model. To put the same persona on another model for one seat, add
it after `@`: `--seats shark@ollama/qwen2.5:7b,ann:shark@claude-haiku-4-5,tag`. This is how
you compare models with everything else held equal.

### A tuned bot

```toml
[agents.nit]
type = "bot"
base = "tag"          # tag | rock | maniac | station | random
tightness = 3         # tag/rock: higher folds more preflop
aggression = 0.5      # tag/rock: below 1 calls where it would have bet
# raise_rate = 0.9    # maniac: how often it raises when it can
```

### A Python class

```toml
[agents.bluffer]
type = "python"
class = "my_bots.py:Bluffer"       # a file beside the TOML, or "package.module:Class"
options = { rate = 0.4 }           # keyword arguments
```

```python
# my_bots.py
import random

from poker_table.agents.base import Decision
from poker_table.engine import Action


class Bluffer:
    """Bets the pot when checked to, some of the time; gives up otherwise."""

    def __init__(self, name, rate=0.3, seed=0):  # seed is passed when the class takes one
        self.name, self.rate, self.rng = name, rate, random.Random(seed)

    def act(self, view):  # view: SeatView — never another seat's cards
        legal = view.legal
        if legal.can_check and legal.can_raise and self.rng.random() < self.rate:
            amount = min(max(view.pot, legal.min_raise_to), legal.max_raise_to)
            return Decision(Action.bet(amount), reasoning="nobody wants it")
        return Decision(Action.check() if legal.can_check else Action.fold())

    def observe(self, history):  # optional: every hand, as this seat saw it
        pass
```

An illegal answer is never fatal. The table maps slips such as bet-for-raise or an amount
off the range to the nearest legal action. Anything else becomes a check or a fold and counts
against the seat on the leaderboard. A `python` kind runs the code it names, so load only
files you trust.

## Your key

> **Disclaimer: you are responsible for what your key spends.** Costs shown here are estimates, and
> budgets are a convenience, not a guarantee. They only see Claude and OpenRouter. On any other
> provider the cost reads $0 and a budget does nothing, so set a spending limit in your provider's
> account before you start. The software comes with no warranty ([MIT](../LICENSE)).

This project has no shared key. A Claude seat runs on the key of whoever starts the table, read by
the Anthropic SDK from `ANTHROPIC_API_KEY`, so the bill is yours and so is the limit. The key is
never written to a hand file.

`play`, `serve --live`, `eval` and `experiment run` look the model up before the first hand. That
lookup is free, and a missing key, a refused key, a wrong model name or no network each stop the
table there with a plain message, instead of a seat folding every hand. `--budget USD` stops
`play` and `serve --live` at that spend, and they warn you when no limit is set. An experiment
refuses a paid seat with no `budget_usd`.

`coach --narrate` runs on your key too, or free with `--model ollama/<name>`.

A table holds your key while it runs, so it is careful who it talks to. It answers only to
`localhost` and the address it is bound to, so a web page you visit cannot reach it by pointing its
own name at your machine. Behind a tunnel, add the tunnel's name with `serve --allow-host NAME`.
`serve --host 0.0.0.0` answers to any name unless you give it some, and says so when it starts.
At a table with friends, Claude's coach notes are off, so a seat link cannot spend your key.

No paid model has been called from this repo. The requests are tested through the real SDK against
a stand-in for the API that checks them against its published rules
([tests/test_anthropic_wire.py](../tests/test_anthropic_wire.py)), so your first run is also the
first live one. Start with [`experiment run FILE --trial 5`](experiments.md#try-before-you-pay) and a
small budget.

## Other providers

A seat runs on any OpenAI-compatible API, on your own key. Put the provider in front of its model
name, exactly as the provider spells it:

| seat | key it reads | can a budget limit it? |
|---|---|---|
| `llm:nerd@openai/<model>` | `OPENAI_API_KEY` | no |
| `llm:nerd@groq/<model>` | `GROQ_API_KEY` | no |
| `llm:nerd@openrouter/<model>` | `OPENROUTER_API_KEY` | yes |
| `llm:nerd@gemini/<model>` | `GEMINI_API_KEY` | no |
| `llm:nerd@compat/<model>` | `POKER_TABLE_API_KEY`, if your server wants one | no |

`compat/` is for a server of your own, such as LM Studio or vLLM. It reads the address from
`POKER_TABLE_BASE_URL`, for example `http://localhost:1234/v1`, and a real `OPENAI_API_KEY` is never
sent to it. `poker-table agents` shows which keys are set.

```sh
export GROQ_API_KEY=...
uv run poker-table play -n 20 --seats llm:nerd@groq/<model>,llm:nerd2:nerd@openrouter/<model>:free,tag
```

**What a budget can see.** This table knows Claude's prices, and OpenRouter reports what each call
cost. The others do not say, so their cost reads $0, `--budget` and `budget_usd` cannot limit them,
and the table says so when it starts. Use the spending limit in your provider's account.

**What has been tested.** The client has run against a real model through Ollama's OpenAI-compatible
endpoint, and against a stand-in server for everything else. The provider addresses were checked to
answer, but no call has been made to OpenAI, Groq, OpenRouter or Gemini themselves, because nobody
here pays for model calls. Their free tiers can be slow, so a seat waits out rate limits. If a model
ignores the requested JSON format, the seat steps down to plain JSON and then to asking in words,
and any reply it cannot read counts in the `failed` column.

## Local models

`ollama/<name>` in any model field runs the seat on [Ollama](https://ollama.com) on this
machine. It needs no API key and costs $0. The seat sends the same question it sends Claude:
the JSON schema becomes Ollama's structured-output `format`, and the reply comes back through
the same parsing, fallbacks and accounting. `play` checks the server and the model before the
first hand. `OLLAMA_HOST` points it at another machine.

```sh
ollama pull qwen2.5:7b
uv run poker-table agents --ollama                     # what this Ollama has pulled
uv run poker-table play -n 20 --seats llm:nerd@ollama/qwen2.5:7b,tag,station --show
```

These are the only LLM hands this project has played itself, because it does not pay for model
calls. On this machine (12 CPU cores, no GPU) `qwen2.5:7b` takes about 18 seconds
a decision with every aid on. That is fine for watching and for evals, and slow for leagues of
thousands of hands.

## A budget

`play --budget 2.50` stops dealing once the seats' model calls have cost $2.50. The hand that
crosses the line is finished, not cut short. The output says how many hands were played. A
tournament stopped by its budget is reported as unfinished.

## Does it help?

`poker-table eval` asks seats questions whose answer is known, without playing a session.
It works in four steps:

1. It plays 150 seeded hands between `tag`, `rock`, `station`, `maniac` and `random`.
2. The coach grades every decision against the charts and the maths.
3. Each graded decision becomes a question: this exact view, what do you do?
4. Half the questions are spots the coach calls mistakes, where the right answer is its
   correction. The other half are spots it accepts, where the same move, or any way to keep
   playing, is right.

Every question is asked fresh, with an empty notebook. Beside the seats it prints the best score
a seat giving one fixed answer could get.

That last number matters. **Always folding agrees with the coach about 65% of the time**,
because folding is the right answer more often than any other. A seat is only doing something
if it clears that. The bots, on 80 questions:

| seat | agrees | fixes mistakes | keeps good plays |
|---|---|---|---|
| tag | 85% | 82% | 88% |
| rock | 76% | 75% | 78% |
| random | 41% | 40% | 42% |
| maniac | 26% | 10% | 42% |
| station | 19% | 2% | 35% |
| always fold | 65% | | |

`tag` plays by rules close to the coach's charts, and some of the questions are its own
decisions, so its row is a ceiling for a chart-follower, not an independent result. For LLM seats
the questions are always other seats' decisions.

`scripts/measure_aids.py` asks one model the same questions with no aids, then odds, ranges and
the playbook added one at a time, and prints a row for each.

### What the aids were worth to a small local model

`qwen2.5:3b` on a laptop CPU, 80 questions per row, about an hour in all:

```sh
uv run python scripts/measure_aids.py ollama/qwen2.5:3b 80
```

| aids | agrees | fixes mistakes | keeps good plays | folds | median s |
|---|---|---|---|---|---|
| none | 51% | 50% | 52% | 40 | 11.3 |
| odds | 64% | 62% | 65% | 37 | 12.6 |
| odds + ranges | 64% | 60% | 68% | 35 | 13.5 |
| odds + ranges + playbook | 66% | 68% | 65% | 48 | 14.8 |
| *always fold* | *65%* | | | *80* | |

One row at 80 questions is good to about ±10 points, so the rows are best compared question by
question, since every row answered the same 80:

| from → to | only the first right | only the second right | exact p |
|---|---|---|---|
| none → odds | 3 | 13 | 0.02 |
| odds → odds + ranges | 6 | 6 | 1.00 |
| odds + ranges → + playbook | 10 | 12 | 0.83 |
| everything → always fold | 15 | 14 | 1.00 |

What this supports:

- **The odds help, and not by making the model fold.** It folded slightly less with them. The gain
  is on the 28 questions where folding is wrong, which went from 12 right to 20. This is the only
  step that clears the noise, and at p = 0.02 across three steps it is evidence rather than proof.
- **Ranges show no effect here.** They turned 6 wrong answers right and 6 right answers wrong.
- **The playbook made it tighter, not better.** It folded 48 times instead of 35, gaining on
  questions where folding is right (31 → 38 of 52) and losing where it is wrong (20 → 15 of 28).
- **The full set only ties always folding,** and it gets different questions right.

What it does not say: this is one small model and one sample, and a run on another model could
come out differently. Reads and notes are not in it, because these questions are asked one at a
time with no session behind them. A model's answers also vary, so a re-run will not match exactly.

### What named sizes did for a small local model

The first league found `qwen2.5:3b` shoving: 91 of 131 bets and raises were all in, and 54 asked for
more chips than a stack holds, up to `raise 1000000000000000`. That looked like a model fumbling
numbers, so the `sizes` aid replaces the number with a choice of four. Here are the same seats on the
same decks, over the league's first 12 hands:

| | bare numbers | named sizes |
|---|---:|---:|
| model decisions | 26 | 28 |
| after the flop | 0 | 3 |
| bets and raises | 16 | 18 |
| of those all in | 12 | 12 |
| asked for more than a stack | 9 | 0 |
| illegal moves | 4 | 1 |

It fixed the mistakes and not the habit. Impossible amounts are gone, as they must be, and illegal
moves fell. But offered four clean choices, the model still picked all in for most of its bets, so
the shoving is what it wants to do and not a slip with numbers. Twelve hands can show the impossible
amounts going, and cannot show a small change in how often it shoves. The aid stays off by default,
because turning it on would also change every seat's play and break comparisons between runs.

```sh
uv run python scripts/measure_sizes.py 12    # free, about a minute a hand
```

### Does it know what it holds?

A model's reasoning sometimes names cards it was not dealt. In the first league, `qwen2.5:3b`
playing maniac wrote "With a strong AA, I'm betting big" while holding 8♦ 9♠, and every reason after
that was about the wrong hand. Reports check each model seat's reasoning against its cards, under
"What they said they held", and the leaderboard has a column for it. The published runs so far:

| `qwen2.5:3b` as | decisions | named its own cards | wrongly |
|---|---:|---:|---:|
| nerd, first league | 86 | 9 | 2 |
| maniac, first league | 125 | 3 | 3 |
| nerd, benchmark | 186 | 18 | 5 |

The check is plain rules, so it is free and gives the same answer every time. It counts only clear
claims about the seat's own two cards, such as "my KQ", "I have TT" or "raising with JJ", and skips
any sentence that names another seat, so "maniac shoves with AK" is never held against anyone.
Claims it cannot read are not counted at all, so read it as a floor. This model seldom names its
cards, so 10 wrong of 30 is a warning worth having and too few claims to give a rate.
