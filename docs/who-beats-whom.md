# Who beats whom

Every bot against every other, heads-up: 66 matches of 2,000 hands, each from seed
2026, at blinds 1/2 with 200 chips topped up every hand (100 big blinds). A cell is the row's
all-in adjusted bb/100 against the column, with a 95% interval. A win is in bold only when its
interval is wholly above zero; any other cell is too close to call. Heads-up is zero-sum, so
each match fills two cells, one the other's mirror. "Beats" and "loses to" count clear cells only.

| | vs cfr:blueprint | vs trapper | vs tag | vs calculator | vs exploiter | vs lag | vs fish | vs tilter | vs rock | vs maniac | vs station | vs random | beats | loses to |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cfr:blueprint | · | **+40 ±39** | **+45 ±40** | **+116 ±86** | **+44 ±40** | +19 ±40 | +53 ±54 | **+82 ±54** | **+60 ±20** | +117 ±128 | **+187 ±72** | **+165 ±86** | 8 | 0 |
| trapper | -40 ±39 | · | +21 ±32 | +67 ±78 | +21 ±32 | +3 ±28 | **+53 ±50** | +5 ±37 | +7 ±24 | **+226 ±94** | **+172 ±37** | **+124 ±57** | 4 | 1 |
| tag | -45 ±40 | -21 ±32 | · | **+80 ±79** | -19 ±31 | -11 ±34 | +50 ±54 | +4 ±35 | +6 ±20 | **+238 ±98** | **+191 ±43** | **+78 ±55** | 4 | 1 |
| calculator | -116 ±86 | -67 ±78 | -80 ±79 | · | -58 ±101 | -39 ±103 | +43 ±49 | **+229 ±118** | -3 ±38 | **+1556 ±195** | **+252 ±40** | **+915 ±201** | 4 | 2 |
| exploiter | -44 ±40 | -21 ±32 | +19 ±31 | +58 ±101 | · | -10 ±36 | +50 ±54 | +4 ±35 | +15 ±26 | **+546 ±132** | **+423 ±85** | **+70 ±54** | 3 | 1 |
| lag | -19 ±40 | -3 ±28 | +11 ±34 | +39 ±103 | +10 ±36 | · | +50 ±67 | +11 ±28 | +16 ±29 | +7 ±87 | **+506 ±90** | **+105 ±59** | 2 | 0 |
| fish | -53 ±54 | -53 ±50 | -50 ±54 | -43 ±49 | -50 ±54 | -50 ±67 | · | -7 ±64 | +3 ±21 | **+495 ±122** | **+57 ±13** | **+402 ±133** | 3 | 1 |
| tilter | -82 ±54 | -5 ±37 | -4 ±35 | -229 ±118 | -4 ±35 | -11 ±28 | +7 ±64 | · | +14 ±24 | **+282 ±116** | **+225 ±45** | **+140 ±65** | 3 | 2 |
| rock | -60 ±20 | -7 ±24 | -6 ±20 | +3 ±38 | -15 ±26 | -16 ±29 | -3 ±21 | -14 ±24 | · | **+65 ±58** | +1 ±8 | **+126 ±58** | 2 | 1 |
| maniac | -117 ±128 | -226 ±94 | -238 ±98 | -1556 ±195 | -546 ±132 | -7 ±87 | -495 ±122 | -282 ±116 | -65 ±58 | · | +2 ±138 | **+1721 ±195** | 1 | 7 |
| station | -187 ±72 | -172 ±37 | -191 ±43 | -252 ±40 | -423 ±85 | -506 ±90 | -57 ±13 | -225 ±45 | -1 ±8 | -2 ±138 | · | -239 ±292 | 0 | 8 |
| random | -165 ±86 | -124 ±57 | -78 ±55 | -915 ±201 | -70 ±54 | -105 ±59 | -402 ±133 | -140 ±65 | -126 ±58 | -1721 ±195 | +239 ±292 | · | 0 | 10 |

Rows are ordered by clear wins less clear losses, then by their average cell. The order says
nothing where two rows' cells overlap. It is one seed and one match a pair, so a cell near the
edge of its interval could fall the other way on another seed. `nash:pushfold` is left out: at
100 big blinds it hands every spot to tag, so its row would be tag's again.

Rerun it (12 minutes here):

```sh
uv run python scripts/who_beats_whom.py --out docs/who-beats-whom.md
```
