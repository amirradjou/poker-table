# Seats from the poker-AI research

Several papers say they solved poker, or beat professionals. Before any of them becomes a seat
here, this page checks each claim against the paper's own text. It also records what game the
result covers, what was released and under which licence, and what it cost to build. The last
column says what this table does with it. The seats it led to are in
[solver-seats.md](solver-seats.md). Everything was checked on 2026-11-26,
and anything that could not be checked says "not verified".

## What "solved" means

A strategy is an **equilibrium** when no opponent can beat it on average. Its **exploitability**
is how much a perfect opponent, one who knows the strategy, would win against it. Zero means an
exact equilibrium. In two-player poker an equilibrium cannot lose on average. With three or more
players there is no such guarantee, and the Pluribus paper says so.

Exploitability can only be computed exactly in games small enough to walk the whole tree. For
no-limit hold'em the papers use a *local best response* (LBR). LBR only gives a lower bound: it can
show that a strategy loses at least that much, but never that it can't be beaten.

As far as the sources go, the headline is:

- **Heads-up limit hold'em is "essentially weakly solved"** ([Cepheus, 2015](https://doi.org/10.1126/science.1259433)).
  The authors' definition: a lifetime of human play could not tell Cepheus apart from an exact
  equilibrium with high confidence. It is 0.986 mbb/g from one, under a thousandth of a big blind
  a hand ([IJCAI 2015](https://www.ijcai.org/Proceedings/15/Papers/097.pdf)).
- **No-limit hold'em is not solved.** No paper below claims it. Heads-up no-limit has "the number
  of decision points exceeding 10^160" ([DeepStack](https://arxiv.org/abs/1701.01724)). The no-limit
  claims are wins against professionals or bots, plus exploitability lower bounds.
- **More than two players:** Pluribus is "stronger than top human professionals in six-player
  no-limit Texas hold'em poker". Its stated goal is "not ... a specific game-theoretic solution
  concept but rather to create an AI that empirically consistently defeats human opponents"
  ([Science 2019](https://doi.org/10.1126/science.aay2400)).

## Three facts that decide most rows

- **Seats.** Apart from a three-player jam/fold paper for tournament endgames (row 19), every
  no-limit method below that was released or fits on a laptop is heads-up. Pluribus is the only
  multi-player result for full play, and no release of it was found. So for now research seats play at
  two-seat tables, and tables of three to nine seats get none.
- **Stacks.** The heads-up no-limit papers play 200 big blinds deep, with stacks reset every hand
  (the competition format described in the DeepStack paper). This table's cash games start every
  hand at 200 chips with blinds of 1/2, which is 100 big blinds. `--stack` changes the depth,
  `--carry` lets stacks carry over, and in [tournaments](tournaments.md) stacks get short as the
  blinds rise. A strategy built for 200 bb is a different strategy at 100 bb, so nothing trained at
  200 bb can be dropped in unchanged.
- **Compute.** One shared laptop: 12 cores, about 30 GB of memory, no GPU.

## The survey

Game: L = limit, NL = no-limit, HU = heads-up, bb = big blinds of stack. Win rates: mbb/g and
mbb/h are thousandths of a big blind per hand, and 1 bb/100 = 10 mbb a hand. Licences come from the
repository's licence file. Decision: **(a)** a wrapper around something released, **(b)** a small
reimplementation on our own abstraction, **(c)** a survey row only. The notes below give the reason
for each decision.

| # | Approach | Game | Claim, in the paper's words | Released, licence | Compute to build | Decision |
|---|---|---|---|---|---|---|
| 1 | [Cepheus](https://doi.org/10.1126/science.1259433), Bowling et al., Science 2015 | HU L | "essentially weakly solved" | [CFR+ code](https://poker.cs.ualberta.ca/cfr_plus.html), MIT-style licence file. Strategy: no download found | "900 core-year": 4,800 CPUs, 68 days | (c) limit |
| 2 | [DeepStack](https://doi.org/10.1126/science.aam6960) ([arXiv](https://arxiv.org/abs/1701.01724)), Moravčík et al., Science 2017 | HU NL, 200 bb | "defeated, with statistical significance, professional poker players" | [Leduc example only](https://github.com/lifrordi/DeepStack-Leduc), no licence file | 175+ core-years on 6,144 cores, plus half a GPU-year | (c) |
| 3 | [Libratus](https://doi.org/10.1126/science.aao1733) ([PDF](https://noambrown.github.io/papers/17-Science-Superhuman.pdf)), Brown & Sandholm, Science 2018 | HU NL, 200 bb | "defeated four top human specialist professionals" over 120,000 hands | nothing found | ~24 million core hours ([author's slides](https://www.cs.cmu.edu/~sandholm/cs15-888F26/Lecture_17_Libratus_and_subgame_solving.pdf)) | (b) `cfr:blueprint` |
| 4 | [Modicum](https://arxiv.org/abs/1805.08195) (depth-limited solving), Brown et al., NeurIPS 2018 | HU NL vs competition bots | "master-level", beats two prior top agents "using only a 4-core CPU and 16 GB of memory" | nothing found | 700 core hours, 16 GB | (b) later, owner approves |
| 5 | [Pluribus](https://doi.org/10.1126/science.aay2400) ([PDF](https://noambrown.github.io/papers/19-Science-Superhuman.pdf)), Brown & Sandholm, Science 2019 | 6-player NL, depth not verified | "stronger than top human professionals" | nothing found | 12,400 core hours (8 days, 64 cores), under 512 GB | (b) only if the owner approves a coarse multi-way build |
| 6 | [ReBeL](https://arxiv.org/abs/2007.13544), Brown et al., NeurIPS 2020 | HU NL, 200 bb | "superhuman performance in heads-up no-limit Texas hold'em" | [Liar's Dice only](https://github.com/facebookresearch/rebel), Apache-2.0 | up to 128 machines with 8 GPUs each | (c) |
| 7 | [Supremus](https://arxiv.org/abs/2007.10442), Zarick et al., arXiv 2020 | HU NL, 200 bb | "beats Slumbot by an extremely large margin": 176 ± 44 mbb/g | no code found | not given in hours; runs entirely on a GPU | (c) |
| 8 | [Student of Games](https://doi.org/10.1126/sciadv.adg3256) ([arXiv](https://arxiv.org/abs/2112.03178)), Schmid et al., Sci. Adv. 2023 | HU NL, 200 bb | "beats the strongest openly available agent" (Slumbot) by 7 ± 3 mbb a hand | no code found | TPU scale, "similar" to an AlphaZero run on 3,500 TPUv4 actors | (c) |
| 9 | [AlphaHoldem](https://doi.org/10.1609/aaai.v36i4.20394), Zhao et al., AAAI 2022 | HU NL, depth not verified | "defeats Slumbot and DeepStack" | hand data repo now gone (404). No official code found | 3 days on 8 GPUs and 64 cores | (c) |
| 10 | [DecisionHoldem](https://arxiv.org/abs/2201.11580), Zhou et al., arXiv 2022 | HU NL vs Slumbot (200 bb) | beats Slumbot "by more than 730 mbb/h" | [code](https://github.com/AI-Decision/DecisionHoldem), AGPL-3.0, plus a blueprint download link | ~4,000 core hours (48 cores, 3-4 days) | (c) licence |
| 11 | [Slumbot](https://www.slumbot.com/), Eric Jackson | HU NL, 200 bb | the 2018 version "won the Annual Computer Poker Competition" (its site) | [solver code](https://github.com/ericgjackson/slumbot2019), MIT, no strategy. A public API | ~250,000 core hours, 2 TB of memory (per the Modicum paper) | (c) as a seat. Its API is a [benchmark](slumbot.md) |
| 12 | [GTO Wizard Benchmark](https://arxiv.org/abs/2603.23660), Provost et al., arXiv 2026 | HU NL, 200 bb | GTO Wizard AI beat Slumbot by 19.4 ± 4.1 bb/100 | proprietary agent behind a public API | not given | (c) as a seat. API benchmark is the owner's call |
| 13 | [Deep CFR](https://arxiv.org/abs/1811.00164), Brown et al., ICML 2019, with [SD-CFR](https://arxiv.org/abs/1901.07621) | HU L (flop hold'em, limit hold'em) | "the first non-tabular variant of CFR to be successful in large games" | [Deep-CFR](https://github.com/EricSteinberger/Deep-CFR), MIT. [OpenSpiel](https://github.com/google-deepmind/open_spiel), Apache-2.0 | not given in hardware terms | (c) limit |
| 14 | [DREAM](https://arxiv.org/abs/2006.10410), Steinberger et al., arXiv 2020 | Leduc, HU L flop hold'em | "state-of-the-art performance among model-free algorithms" | [DREAM](https://github.com/EricSteinberger/DREAM), MIT | "multiple days of training" | (c) limit |
| 15 | [NFSP](https://arxiv.org/abs/1603.01121), Heinrich & Silver, arXiv 2016 | Leduc, HU L | "approached the performance of state-of-the-art, superhuman algorithms" | in OpenSpiel (Apache-2.0) and [RLCard](https://github.com/datamllab/rlcard) (MIT) | not given | (c) limit |
| 16 | [SpinGPT](https://arxiv.org/abs/2509.22387), Maugin & Cazenave, arXiv 2025 | 3-player Spin & Go; HU vs Slumbot | "matches the solver's actions in 78% of decisions"; 13.4 ± 12.9 BB/100 vs Slumbot | no release found | Llama-3.1-8B fine-tune, about 20 A100 GPU-hours | (c) |
| 17 | [PokerSkill](https://arxiv.org/abs/2605.30094), Li et al., arXiv 2026 | HU NL vs GTO Wizard (200 bb) | best model −57 ± 21 mbb a hand vs GTO Wizard, "outperforming the strong bot Slumbot" | [code](https://github.com/lbn187/PokerSkill), CC BY-NC 4.0 | no training; paid model calls | (c) licence |
| 18 | [Push/fold](https://doi.org/10.1145/1329125.1329357) ([archived PDF](https://web.archive.org/web/20240429153436/https://www.itu.dk/~trbj/papers/poker.pdf)), Miltersen & Sørensen, AAMAS 2007 | HU NL tournament, 8,000 chips at 300/600 | jam/fold strategy "within 1.4 percentage points" of optimal | nothing found | not given | (b) `nash:pushfold` |
| 19 | [3-player jam/fold](https://aamas.csc.liv.ac.uk/Proceedings/aamas08/proceedings/pdf/paper/AAMAS08_0313.pdf), Ganzfried & Sandholm, AAMAS 2008 | 3-player NL tournament | "approximate jam/fold equilibrium" | nothing found | 21 outer iterations, "each lasting several hours" | (c) for now |
| 20 | [OpenSpiel](https://arxiv.org/abs/1908.09453), Lanctot et al., arXiv 2019 | framework: hold'em for 2-10 players | none | Apache-2.0 | none | (c) not a seat |
| 21 | [PokerRL](https://github.com/EricSteinberger/PokerRL) (Steinberger), [RLCard](https://arxiv.org/abs/1910.04376) (Zha et al., 2019) | frameworks | none | MIT, MIT | none | (c) not seats |

## Notes per row

- **Cepheus (1).** Limit only. The [CFR+ page](https://poker.cs.ualberta.ca/cfr_plus.html) calls
  the code BSD, but the `LICENCE` file in the tarball is an MIT-style grant with a clause against
  using the authors' names in advertising. The Cepheus site it links to (poker.srv.ualberta.ca)
  refused connections, so whether you can still play or query the strategy is not verified.
- **DeepStack (2).** It beat 10 of the 11 players who finished 3,000 hands with statistical
  significance, and LBR "was unable to identify a positive lower bound" for its exploitability. The
  only code is a Leduc example: a toy game, and with no licence file, all rights reserved. The
  Supremus paper mentions released match logs. They were not found (deepstack.ai answers 404).
- **Libratus (3).** Three parts: a blueprint, subgame solving during play, and a self-improver.
  `cfr:blueprint` copies only the first, on a much coarser abstraction. The paper's own numbers show
  what a blueprint alone is worth: "By using only the raw blueprint strategy, Libratus lost to Baby
  Tartanian8 by 8 ± 15 mbb/game." The 24 million core hours come from the author's lecture slides,
  not the paper. The AlphaHoldem paper says "> 3 × 10^6" CPU hours.
- **Modicum (4).** The most laptop-sized result here: 700 core hours is about 58 hours on 12
  cores. But it plays at "an average of 20 seconds for an entire hand" on 4 cores, so a 1,000-hand
  benchmark takes about five and a half hours. A possible second step on top of `cfr:blueprint`,
  once the blueprint is measured.
- **Pluribus (5).** The only route to research seats at three to nine seats. It won 48 mbb/game
  (standard error 25) over 10,000 hands against five professionals, and 32 (standard error 15) with
  one professional against five copies. A full-size rebuild does not fit: under 512 GB of memory
  against 30 GB here, and 12,400 core hours is about 43 days of all 12 cores. The paper's bets run
  from $100 to $10,000, which suggests 100 bb, but the blinds and reset rule are in supplementary
  materials, which could not be opened here.
- **ReBeL (6).** The human result is one player over 7,500 hands: 165 ± 69 mbb/g, ± being one
  standard deviation. The poker code was held back on purpose: "we have decided not to release the
  code for poker".
- **Supremus (7).** It also reports that a DeepStack reimplementation "loses to the prior benchmark
  agent Slumbot by 63 ± 40 mbb/g".
- **Student of Games (8).** The poker claim is against a bot, Slumbot, not against people.
- **AlphaHoldem (9).** The "DeepStack" it beat is OpenStack, the authors' own reimplementation, by
  16.91 ± 22.34 mbb/h: the 95% interval includes zero. So does the result against professionals,
  10.27 ± 65.13. The abstract says 100,000 hands, the body 200,000. The Slumbot result,
  111.56 ± 16.06, is clear. An [unofficial reimplementation](https://github.com/bupticybee/AlphaNLHoldem)
  is AGPL-3.0.
- **DecisionHoldem (10).** The only no-limit project here that offers code and a trained blueprint.
  The README points to a Baidu Netdisk download for the blueprint, not opened here. AGPL-3.0 does
  not fit an MIT project.
- **Slumbot (11).** The code is MIT but ships no strategy, and building one costs far more than this
  laptop has. The [API](https://www.slumbot.com/) cannot be a seat: its server deals the cards (its
  responses carry your hole cards and the board), so it plays its own 200 bb match, not a hand at
  this table. So our seats go and play there instead, as a benchmark:
  [slumbot.md](slumbot.md) has how and the results. No terms of use were found on the site. The
  owner approved the runs on 2026-11-27, and they keep to one request at a time. The 2013 workshop
  paper ("Slumbot NL", cited by Student of Games) could not be located.
- **GTO Wizard Benchmark (12).** The API "handles game state management", so it probably deals its
  own hands too (not verified). PokerSkill's README passes a `GTO_WIZARD_API_KEY`, so it seems to
  need a key (not verified with GTO Wizard).
- **Deep CFR, DREAM, NFSP (13-15).** Limit and toy games only. The Deep-CFR repo is by Eric
  Steinberger, not by the Deep CFR paper's authors.
- **SpinGPT (16).** A three-player short-stack tournament format, a fine-tuned 8B model, and
  training data from one author's own professional hands. No release of data or weights was found.
  ±12.9 BB/100 against Slumbot is a wide interval.
- **PokerSkill (17).** A rule library that steers an LLM, close to this table's own
  [aids](agents.md#what-an-llm-seat-is-handed). Non-commercial licence. It and Slumbot were each
  measured against GTO Wizard, and the paper says "the Slumbot comparison is indirect".
- **Push/fold (18).** The paper's game has about 13 big blinds in play in total and optimises the
  chance of winning a heads-up tournament. `nash:pushfold` is our own computation of the same kind
  of strategy (all in or fold), heads-up and for short stacks only. The 1.4-point bound belongs to
  the paper's game, not to our seat. It also has charts for antes of 1/10 and 1/8 of the big
  blind, so it plays the short-stack end of our heads-up freezeouts, and its share of them won is
  measured in [solver-seats.md](solver-seats.md#nashpushfold).
- **Three-player jam/fold (19).** A possible later extension of `nash:pushfold`, if the owner wants
  three-handed endgames.
- **Frameworks (20-21).** Not seats. OpenSpiel's CFR on Kuhn or Leduc poker could cross-check our
  CFR code in a test.

## What this table offers

**Two research seats**, our own reimplementations. Both play at two-seat tables only and are
refused anywhere else. What each one is, how it was checked and how it measures is in
[solver-seats.md](solver-seats.md):

- `nash:pushfold`: a heads-up push/fold equilibrium we compute, for short stacks (row 18).
- `cfr:blueprint`: a CFR+ blueprint on our own coarse heads-up no-limit abstraction, in the
  style of the Libratus and Pluribus blueprints (rows 3 and 5), without their subgame solving.

Each is named for what it is and links its paper, never called "Libratus" or "Pluribus". Both are
measured like every other seat, with all-in adjusted bb/100 and intervals, on the
[heads-up track](leaderboard-heads-up.md) of the benchmark, because the main
[benchmark](leaderboard.md) is five-handed. Their exploitability within our abstraction is in
[solver-seats.md](solver-seats.md).

**Wrappers: none.** A wrapper has to play no-limit hold'em, ship a usable strategy (or one we can
train here), and carry a licence that fits MIT. Nothing passes all three. Slumbot's code is MIT but
has no strategy. DecisionHoldem has a strategy but is AGPL-3.0. PokerSkill is non-commercial.
DeepStack's code is Leduc only, with no licence.

**Survey rows only:** limit-only results (Cepheus, Deep CFR, DREAM, NFSP); nothing usable released
and far too big to rebuild here (DeepStack, ReBeL, Supremus, Student of Games, AlphaHoldem); licence
(DecisionHoldem, PokerSkill); other formats with no release (SpinGPT, and the three-player jam/fold
paper for now); and frameworks (OpenSpiel, PokerRL, RLCard).

**For the owner to decide:**

1. **Other people's servers.** Neither Slumbot's API nor GTO Wizard's can sit at this table. Either
   can host a run that sends one of our heads-up seats to play on their server, in their 200 bb
   format. Slumbot's was approved on 2026-11-27 and is measured in [slumbot.md](slumbot.md). GTO
   Wizard's still needs the owner's yes and a terms check, and it seems to need a key. It has not
   been called from this repo.
2. **Long training.** Modicum-style search on top of `cfr:blueprint` (row 4): about two and a half
   days of all 12 cores at Modicum's scale, then slow benchmark runs.
3. **More than two seats.** A Pluribus-style blueprint for three to six seats (row 5). Full size
   cannot run here. A much coarser one might, for a training time nobody has measured, and with no
   equilibrium guarantee to check it against. Without it, research seats stay at two-seat tables.
4. **Stack depth.** The published heads-up numbers are at 200 bb, and this table defaults to
   100 bb. A seat should be built for the depth it plays at. Comparing with a paper's number needs
   the 200 bb format (`--stack 400` at blinds of 1/2).

Trained strategy files too big for git go in release assets, as the plan says. The blueprint is
374 KB (version 2; version 1 was 128 KB), so it ships in the package.
