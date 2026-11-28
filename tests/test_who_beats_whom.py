"""scripts/who_beats_whom.py: every bot against every other, heads-up."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "who_beats_whom.py"
KINDS = ["tag", "station", "maniac"]


@pytest.fixture(scope="module")
def grid():
    spec = importlib.util.spec_from_file_location("who_beats_whom", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["who_beats_whom"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cells(grid):
    return grid.play(KINDS, hands=120, seed=3)


def test_every_pair_plays_once_and_fills_two_mirrored_cells(grid, cells) -> None:
    assert [(c.first, c.second) for c in cells] == [
        ("tag", "station"),
        ("tag", "maniac"),
        ("station", "maniac"),
    ]
    assert all(c.hands == 120 for c in cells)
    got = grid.results(cells)
    for a in KINDS:
        for b in KINDS:
            if a != b:
                assert got[a, b] == (-got[b, a][0], got[b, a][1])


def test_a_match_is_the_same_hands_whenever_and_however_it_is_played(grid, cells) -> None:
    assert grid.match("tag", "station", hands=120, seed=3) == cells[0]
    assert grid.play(KINDS, hands=120, seed=3) == cells


def test_the_grid_counts_only_clear_cells_and_bolds_only_clear_wins(grid, cells) -> None:
    rows = {r.kind: r for r in grid.rows(KINDS, cells)}
    got = grid.results(cells)
    for k, r in rows.items():
        mine = [got[k, o] for o in KINDS if o != k]
        assert r.beats == sum(1 for v, m in mine if v > 0 and m is not None and v > m)
        assert r.loses_to == sum(1 for v, m in mine if v < 0 and m is not None and -v > m)
    assert sum(r.beats for r in rows.values()) == sum(r.loses_to for r in rows.values())
    assert grid.cell_text(50.0, 10.0) == "**+50 ±10**"
    assert grid.cell_text(5.0, 10.0) == "+5 ±10"  # too close to call: never bold
    assert grid.cell_text(-50.0, 10.0) == "-50 ±10"  # a clear loss is the mirror of a bold win
    table = grid.grid(KINDS, cells)
    assert table.count("·") == len(KINDS)
    assert len(table.splitlines()) == 2 + len(KINDS)


def test_a_seat_that_breaks_is_refused_not_scored(grid, monkeypatch) -> None:
    from poker_table.agents import scripted

    def broken(_self, _view):
        raise RuntimeError("boom")

    monkeypatch.setattr(scripted.CallingStation, "act", broken)
    with pytest.raises(SystemExit, match="station broke"):
        grid.match("tag", "station", hands=5, seed=1)
