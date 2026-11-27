# Against Slumbot

[Slumbot](https://www.slumbot.com/) is a strong heads-up no-limit bot by Eric Jackson. Its site says
the 2018 version won the Annual Computer Poker Competition. Several papers measure against it (rows
7 to 10, 12, 16 and 17 of the [survey](research-seats.md)). Its site offers a free API "so that
researchers and hobbyists can test their bots against Slumbot". We use that API to measure our own
seats.

It is a benchmark, not a seat. Slumbot's server deals the cards and plays its own format. So
Slumbot never sits at our table: one of our seats goes and plays on its server.

## The results

Played on 2026-11-27. This prints the table from the folders in
[`results-slumbot/`](results-slumbot/):

```sh
uv run python scripts/bench_slumbot.py --report docs/results-slumbot/tag docs/results-slumbot/exploiter docs/results-slumbot/station
```

| our seat | hands | bb/100 | all-in adj. bb/100 | Slumbot's baseline bb/100 | all in early | how it decided | refused | requests | time |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| tag | 2,000 | -61 ±57 | -61 ±57 | -26 ±88 | 0 | - | 0 | 4,672 | 19 min |
| exploiter | 2,000 | -27 ±47 | -43 ±44 | -50 ±69 | 1 | tag 99.8%, adjusted 0.2% | 0 | 4,584 | 19 min |
| station | 1,000 | -135 ±67 | -135 ±67 | -144 ±103 | 0 | - | 0 | 5,629 | 23 min |

What it says:

- **All three lose to Slumbot.** tag loses 0.6 big blinds a hand, exploiter 0.3 to 0.4, and
  station, which calls everything, 1.35.
- **station's loss is clear of zero, and tag's only just.** exploiter's intervals reach zero.
- **tag and exploiter cannot be told apart here.** A read of Slumbot changed 6 of exploiter's
  2,584 decisions (5 `bluff_folder`, 1 `defend_wide`). Every other decision was tag's. So the two
  rows differ by the luck of different cards, not by the reads. Slumbot seldom fits one of
  exploiter's profiles (a calling station, a folder or a very aggressive player), so its rules
  stayed quiet.

How to read them:

- **This is a 200 big blind result.** Slumbot plays blinds of 50 and 100 with 20,000 chips each,
  reset after every hand. Our table's default is 100 big blinds, and our seats were built there.
  So these rows do not compare with our [leaderboards](leaderboard-heads-up.md).
- **bb/100** is big blinds won per 100 hands. **All-in adj.** counts a hand that was all in before
  the last card at our seat's share of the pot at that moment, which takes out the luck of the
  cards still to come ([margins.py](../src/poker_table/margins.py)). Slumbot shows its cards at the
  end of every hand, so both hands are known whenever that applies.
- **Slumbot's baseline** is Slumbot's own number. For each hand it plays itself on the same cards,
  in our seat, and reports our result minus its own. Its site says that takes out much of the luck
  of the cards. We record it from each answer but cannot check it: the API does not show that
  second game. Here its interval came out wider than the plain one, not narrower, so read bb/100
  first.
- **How it decided** counts the `method` each of our seat's decisions records. `exploiter` plays
  tag's move unless a read of Slumbot changes it (`adjusted`). Its reads need 20 hands of Slumbot
  before any rule can fire.
- **±** is a 95% interval. It treats hands as independent. Two rows whose intervals overlap may not
  differ at all.
- Slumbot's side is not ours to seed. **A rerun repeats the procedure, not the numbers.**

## How a hand is played

The API has two endpoints, `new_hand` and `act`, both HTTPS POST with JSON bodies. Every answer
carries the whole action string so far, such as `b200c/kb400c/kk/kk`: `k` check, `c` call, `f` fold,
`b<n>` a bet or raise to n chips on this street, and `/` between streets. The protocol and a
sample client are on its home page, under "API" and "Sample Code". Our client is our own, written
from that protocol.

Our side of each hand runs on our own engine, in Slumbot's chips:

- The engine is fed the cards as the API shows them. Our seat is asked through the same view as at
  our table: its own cards, the board so far, every action, the stacks and the legal actions.
- Slumbot's seat holds two stand-in cards. Its real cards reach the engine only with the answer
  that ends the hand, after our seat's last decision. A test checks our seat never sees them.
- Three things must agree at every step: the API's answers, the API's rules (read by our own
  parser, written from its protocol) and our engine. Every move of ours is checked against the
  API's rules before it is sent. At the end, our engine's result must equal the winnings Slumbot
  reports, to the chip.
- A hand that does not fit is refused: recorded with its reason, never counted.
- Our table lets a seat fold when it could check. The API does not, so such a fold is sent as a
  check, and the record says so (`"asked": "fold"`).
- A seat that learns (has `observe`) is shown each hand from its own chair, as at our table: it
  does not see the cards Slumbot shows after a fold.

A session starts in the big blind, and the button moves every hand. The numbers count as many
hands from each position, so a seat has the button in exactly half of them. If a run stops between
the two, or a hand is refused, the first hands of each position count and the rest are kept but
left out. Which hands count depends on the position alone, never on the cards or the result.

## Running it

```sh
uv run python scripts/bench_slumbot.py tag --hands 2000 --out docs/results-slumbot/tag
uv run python scripts/bench_slumbot.py tag --hands 2000 --out docs/results-slumbot/tag --resume
```

`--hands` must be even. Every hand is appended as it finishes, so `--resume` carries on after a
stop. A seat that learns is shown every recorded hand first.

The client is polite: one request at a time, at most four a second, a growing pause after HTTP 429
or 5xx, and a stop after six failures in a row. A move whose answer was lost is never sent again,
because it may already have been played: that hand is refused instead. No login, no account, and
Python's own User-Agent.

Each folder holds:

- `hands-NNN.jsonl`: our side of each hand, as a hand history. `poker-table serve` and
  `poker-table replay` read them.
- `api-NNN.jsonl`: per hand, every request and answer with the session token taken out, our
  position, Slumbot's reported winnings and its baseline, or why the hand was refused.
- `run.json`: the seat, the target and each run's times, requests and why it stopped.

New files start before one would pass 1.9 MB. These folders live outside `docs/results/` on
purpose: `poker-table verify` and the leaderboards deal our own decks again, and Slumbot's deck
cannot be dealt again. A hand history's `seed` is 0 here for that reason.

## The runs

Every run is in each folder's `run.json`, with its start, end, requests and why it stopped.

| seat | command | hands played | requests | time |
|---|---|---:|---:|---:|
| tag | `bench_slumbot.py tag --hands 2000 --out docs/results-slumbot/tag`, then `--resume` | 2,000 | 4,672 | 12 + 7 min |
| exploiter | `bench_slumbot.py exploiter --hands 2000 --out docs/results-slumbot/exploiter` | 2,000 | 4,584 | 19 min |
| station | `bench_slumbot.py station --hands 1000 --out docs/results-slumbot/station` | 1,000 | 5,629 | 23 min |

- tag's first run crashed on the hand after 1,240, before our seat's first decision. Our hole
  cards were two of the three deuces the client took Slumbot's stand-in cards from. It now picks
  from four. The crash also left `run.json` saying "finished". That entry was corrected by hand,
  and a crash now says so itself. The lost hand has no record, and the resumed run played on.
- Before these runs, 37 hands tried the protocol out: 7 by hand and 30 with maniac. They are not
  in the folders. The 7, and exploiter's hand 942 (all in on the turn), are the recorded hands the
  tests replay.
- No hand was refused. Our net equalled Slumbot's winnings on all 5,000.

## Seats not run

- `cfr:blueprint` was trained for 100 big blinds and refuses tables outside 90 to 110. At 200 it
  would be guessing.
- `nash:pushfold` has charts up to 15 big blinds. At 200 its tag fallback plays every hand, so
  its row would be tag's.
- LLM seats would run on the key of whoever starts them. None was run.

## What is left

- At 2,000 hands an interval is still about ±60 bb/100. Telling two of our seats apart here would
  take many more hands. That is Slumbot's server time, so it needs the owner's yes first.
