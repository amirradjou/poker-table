# Seats: smarter LLMs, and your own agents

A seat is anything with `act(view) -> Decision`. The table ships scripted bots (`tag`, `rock`,
`maniac`, `station`, `random`), four LLM personalities (`llm:nerd`, `llm:maniac`, `llm:rock`,
`llm:storyteller`), a [Laya](laya.md) seat and a human seat. This page covers:

- what an LLM seat is handed on top of the bare table;
- how to define your own seats in a TOML file, with no code, or in Python;
- how to run them on a local model for free;
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
be measured (see [Does it help?](#does-it-help)). All five are on by default.

| aid | what the model gets | where it comes from |
|---|---|---|
| `odds` | equity against random hands, outs, pot odds, break-even equity, the value of a call | [`odds.py`](odds.md), from the seat's own cards and the board |
| `ranges` | each opponent's range read from this hand's action, and the seat's equity against it | the coach's range narrowing (`coach.facts.read_ranges`) run on the live view |
| `reads` | per opponent: sample size, style, VPIP/PFR/3-bet, aggression, fold-to-bet, showdown rate, the last hands they turned over | `agents/reads.py`, built only from hands the seat was shown |
| `notes` | a notebook: the reply gains a `note` field, and the latest twelve lines come back in later prompts | the model itself |
| `playbook` | a short primer on sizing, hand selection and opponent types | the system prompt |

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
| `model` | `claude-opus-5` | a Claude model, or `ollama/<name>` for a local one |
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

These are the first LLM hands this project has played, because no Claude key has been
available here yet. On this machine (12 CPU cores, no GPU) `qwen2.5:7b` takes about 18 seconds
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
the playbook added one at a time, and prints a row for each. At about 18 seconds a decision,
`qwen2.5:7b` on this machine needs a few hours for 120 questions per row.
