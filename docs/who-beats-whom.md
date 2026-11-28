# Who beats whom

Every bot against every other, heads-up: 66 matches of 2,000 hands, each from seed
2026, at blinds 1/2 with 200 chips topped up every hand (100 big blinds). A cell is the row's
all-in adjusted bb/100 against the column, with a 95% interval. A win is in bold only when its
interval is wholly above zero; any other cell is too close to call. Heads-up is zero-sum, so
each match fills two cells, one the other's mirror. "Beats" and "loses to" count clear cells only.

| | vs cfr:blueprint | vs tag | vs calculator | vs exploiter | vs trapper | vs fish | vs lag | vs tilter | vs rock | vs maniac | vs station | vs random | beats | loses to |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cfr:blueprint | · | +42 ±43 | +80 ±94 | +42 ±43 | **+43 ±41** | +34 ±60 | **+44 ±41** | **+108 ±58** | **+42 ±17** | **+174 ±131** | **+198 ±76** | **+134 ±90** | 7 | 0 |
| tag | -42 ±43 | · | **+80 ±79** | -19 ±31 | -21 ±32 | +50 ±54 | -11 ±34 | +4 ±35 | +6 ±20 | **+238 ±98** | **+191 ±43** | **+78 ±55** | 4 | 0 |
| calculator | -80 ±94 | -80 ±79 | · | -58 ±101 | -67 ±78 | +43 ±49 | -39 ±103 | **+229 ±118** | -3 ±38 | **+1556 ±195** | **+252 ±40** | **+915 ±201** | 4 | 1 |
| exploiter | -42 ±43 | +19 ±31 | +58 ±101 | · | -21 ±32 | +50 ±54 | -10 ±36 | +4 ±35 | +15 ±26 | **+546 ±132** | **+423 ±85** | **+70 ±54** | 3 | 0 |
| trapper | -43 ±41 | +21 ±32 | +67 ±78 | +21 ±32 | · | **+53 ±50** | +3 ±28 | +5 ±37 | +7 ±24 | **+226 ±94** | **+172 ±37** | **+124 ±57** | 4 | 1 |
| fish | -34 ±60 | -50 ±54 | -43 ±49 | -50 ±54 | -53 ±50 | · | -50 ±67 | -7 ±64 | +3 ±21 | **+495 ±122** | **+57 ±13** | **+402 ±133** | 3 | 1 |
| lag | -44 ±41 | +11 ±34 | +39 ±103 | +10 ±36 | -3 ±28 | +50 ±67 | · | +11 ±28 | +16 ±29 | +7 ±87 | **+506 ±90** | **+105 ±59** | 2 | 1 |
| tilter | -108 ±58 | -4 ±35 | -229 ±118 | -4 ±35 | -5 ±37 | +7 ±64 | -11 ±28 | · | +14 ±24 | **+282 ±116** | **+225 ±45** | **+140 ±65** | 3 | 2 |
| rock | -42 ±17 | -6 ±20 | +3 ±38 | -15 ±26 | -7 ±24 | -3 ±21 | -16 ±29 | -14 ±24 | · | **+65 ±58** | +1 ±8 | **+126 ±58** | 2 | 1 |
| maniac | -174 ±131 | -238 ±98 | -1556 ±195 | -546 ±132 | -226 ±94 | -495 ±122 | -7 ±87 | -282 ±116 | -65 ±58 | · | +2 ±138 | **+1721 ±195** | 1 | 8 |
| station | -198 ±76 | -191 ±43 | -252 ±40 | -423 ±85 | -172 ±37 | -57 ±13 | -506 ±90 | -225 ±45 | -1 ±8 | -2 ±138 | · | -239 ±292 | 0 | 8 |
| random | -134 ±90 | -78 ±55 | -915 ±201 | -70 ±54 | -124 ±57 | -402 ±133 | -105 ±59 | -140 ±65 | -126 ±58 | -1721 ±195 | +239 ±292 | · | 0 | 10 |

Rows are ordered by clear wins less clear losses, then by their average cell. The order says
nothing where two rows' cells overlap. It is one seed and one match a pair, so a cell near the
edge of its interval could fall the other way on another seed. `nash:pushfold` is left out: at
100 big blinds it hands every spot to tag, so its row would be tag's again.

Rerun it (11 minutes here):

```sh
uv run python scripts/who_beats_whom.py --out docs/who-beats-whom.md
```
