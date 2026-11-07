# poker-table

A no-limit hold'em table where language models, scripted bots and you play each other. Watch
personalities bluff live, sit down yourself, measure which model plays best, or import your own
hands and get coached on your leaks.

![Six bots playing a hand live at a lamp-lit table, with nameplates, chip stacks, each seat's
chance of winning updating at every fold and new card, a thinking indicator and table
talk](https://raw.githubusercontent.com/amirradjou/poker-table/main/docs/watch.gif)

**[Try the demo](https://amirradjou.github.io/poker-table/)**: the viewer on 300 seeded bot hands,
nothing to install.

## Start

```sh
uv tool install git+https://github.com/amirradjou/poker-table
poker-table                                     # a live table of five bots, in your browser
poker-table watch --seats me:human,tag,maniac   # sit down yourself
```

No key needed. Docker and other ways to install are in the
[guide](https://github.com/amirradjou/poker-table/blob/main/docs/GUIDE.md#install).

## What you can do

- **Put a model at the table**, on your own key or free on a local model through Ollama.
  `poker-table play -n 20 --seats llm:nerd@ollama/qwen2.5:3b,tag,station --show`
- **Run a league** across parallel tables, with a budget and error bars on every number.
  `poker-table experiment init`, then `poker-table experiment run league.toml`
- **Rank a model** on a fixed benchmark and add it to the
  [leaderboard](https://github.com/amirradjou/poker-table/blob/main/docs/leaderboard.md).
  `poker-table benchmark llm:nerd@groq/<model>`
- **Coach your own play** from PokerStars, GGPoker or 888poker exports: recurring leaks, cited
  hands and drills. `poker-table import ~/Downloads/HH*.txt -o mine.jsonl`, then
  `poker-table coach mine.jsonl`
- **Work out the odds** for any spot. `poker-table odds "Ah Kh" -b "Qh 7h 2c" --vs 2`

Every hand is saved with each seat's private reasoning, and the replay shows it beside the table:

![Replay viewer: the maniac bets into the calling station, 82% to 18%, while the odds panel shows
the station thinks it has 59% and the price asks for 37%](https://raw.githubusercontent.com/amirradjou/poker-table/main/docs/viewer.png)

## What it has found

- In the [first league](https://github.com/amirradjou/poker-table/blob/main/docs/experiments.md#the-first-league),
  two personalities on a small local model against three bots, nobody clears break-even over 100
  hands. On chips, one bot looked like a clear winner and another a clear loser: that was all-in
  luck, and the report says so.
- On the [benchmark](https://github.com/amirradjou/poker-table/blob/main/docs/leaderboard.md),
  `qwen2.5:3b` goes all in on most of its bets, and 5 of the 18 times it named its own cards, it
  named the wrong ones.

## More

The [guide](https://github.com/amirradjou/poker-table/blob/main/docs/GUIDE.md) has every command.
Then: [seats and models](https://github.com/amirradjou/poker-table/blob/main/docs/agents.md),
[leagues](https://github.com/amirradjou/poker-table/blob/main/docs/experiments.md),
[odds](https://github.com/amirradjou/poker-table/blob/main/docs/odds.md),
[tournaments](https://github.com/amirradjou/poker-table/blob/main/docs/tournaments.md).
MIT licensed.
