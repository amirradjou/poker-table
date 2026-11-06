# Experiments: one file, one results folder

`poker-table play` answers "what happens if these seats play?". An experiment answers the same
question so that someone else can check it: the league lives in a file, one command plays it,
and the folder it writes holds everything needed to read, rebuild or continue the result.

```sh
uv run poker-table experiment run experiments/local-league.toml
uv run poker-table experiment run experiments/local-league.toml --hands 5 -o /tmp/trial  # a short trial
uv run poker-table experiment run experiments/local-league.toml --resume                 # carry on
uv run poker-table experiment report results/local-league                                # rebuild the report
uv run poker-table serve results/local-league/hands.jsonl --open                         # watch the hands
```

## The file

```toml
name = "per-model"                      # also the folder's name
description = "One line for the report."
seats = ["opus:llm:nerd@claude-opus-5-5", "haiku:llm:nerd@claude-haiku-5-5", "tag", "station"]
hands = 200                             # in all, shared out across the tables
seed = 2026
blinds = "1/2"
ante = 0
stack = 200                             # every seat starts each hand with this
budget_usd = 20.0                       # required when a seat calls a paid model
tables = 4
agents = ["agents.toml"]                # your own kinds, next to this file (docs/agents.md)
coach = true                            # write a coach's report on each model seat
```

Only `name`, `seats` and `hands` are required. Seats are written as they are for `--seats`. A key
the runner does not know is an error, because a misspelt `budget_usd` must not mean no limit.
Stacks are topped up every hand, so hands stay comparable and can be counted as independent.

A run is refused before the first hand if a seat could not play it: a local model that is not
pulled, a paid model with no budget, a key that does not work or a model name that does not
exist. A human seat is refused too, because nobody is there to answer.

**Only run experiment files you trust.** `agents` can name a file with a `python` kind, which runs
the code it points at, exactly as `--agents` does ([docs/agents.md](agents.md)). Reading a results
folder is different: `experiment report` and `serve` never load agents, and a report is not written
through a link in the folder, so a results folder from someone else cannot overwrite your files.

## The folder

| file | what it holds |
|---|---|
| `experiment.toml` | the file it was run from |
| `hands.jsonl` | every hand, with every seat's reasoning, written as each hand ends |
| `run.json` | hands played and planned, why it stopped, seconds, dollars, version and commit |
| `leaderboard.md` | who won, with an error bar, and what it cost |
| `coach/<seat>.md` | the coach's report on each model seat |

`results/` is ignored by git. To publish a run, give it `-o docs/results`.

## Reading the leaderboard

The number to read is bb/100 with its interval, not the order of the rows. Poker is noisy: over
300 hands heads-up, a tight-aggressive bot beats a bot that plays at random by +173 bb/100, and
the 95% interval is ±201. That matchup is not close, and 300 hands still cannot show it. So the
report names the seats whose interval clears zero and says of the rest that they are too close to
call. The interval treats hands as independent and looks at one seat at a time, so with many seats
one can clear zero by luck.

Check the `failed` column before the winners. It counts decisions where the model's reply could
not be used, so the seat checked or folded instead, and that seat's result is partly its fallback's.
A run stops by itself once a seat has failed ten times and on at least half of its calls.

## Stopping and carrying on

A run can stop for four reasons, and each one keeps every finished hand and writes the report:

- it played every hand;
- the seats' model calls reached `budget_usd`, which is checked between hands, so each table may
  finish the hand it is in;
- a seat kept failing;
- you pressed Ctrl-C, which finishes the hands in progress.

If the process is killed outright, or the machine loses power, the finished hands are still there
and `--resume` carries on from them. Only the hand in progress is lost, and the time and cost in
the report can run a little short, because they are saved every twentieth of the run.

`--resume` carries on with the same decks, and its dollars count against the same budget. It also
extends a finished run: `--resume --hands 400` plays the next 200 hands of a 200-hand run. The
seats get back their reads and notes from the hands already played. The decks repeat exactly, but
the decisions need not: a model does not answer twice alike, and a scripted bot's random choices
restart. A run that was never interrupted is the one that repeats exactly, for scripted seats.

## Several tables

`tables = 4` plays four independent tables at once and pools their hands in one file, with ids
like `t2-17`. This is for seats that spend their time waiting on an API: 48 hands with seats that
wait 50 ms a decision took 22.4 seconds on one table, 11.5 on two, 6.1 on four and 3.0 on eight.
It does not speed up scripted bots, and it helps a local model only as far as Ollama serves
requests in parallel (`OLLAMA_NUM_PARALLEL`) and the machine has cores for them.

Every table has its own decks and its own copy of each seat, so one table's hands teach nothing
to another. That makes the hands more independent. It also means a seat that learns from its
opponents has fewer hands to learn from at each table, so compare runs with the same `tables`.
The budget is shared across the tables. A single table deals exactly what `poker-table play` deals
with the same seed and seats.

The three files in `experiments/`:

- `local-league`: free, on a small local model.
- `four-personalities`: the league the README asks for (needs a key).
- `per-model`: one personality on Opus, Sonnet and Haiku, bb/100 against $/hand (needs a key).

Neither of the last two has been run: this machine has no key. They have only been tested against
a fake client, so the first real run is also the first test of the request against the live API.
