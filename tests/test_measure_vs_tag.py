"""scripts/measure_vs_tag.py: the paired comparison every new seat is measured with."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "measure_vs_tag.py"


@pytest.fixture(scope="module")
def measure():
    spec = importlib.util.spec_from_file_location("measure_vs_tag", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["measure_vs_tag"] = module
    spec.loader.exec_module(module)
    return module


def test_the_control_against_itself_differs_by_exactly_zero(measure) -> None:
    p = measure.paired("tag", ("station",), hands=60, seed=3)
    assert p.difference == 0.0 and p.margin == 0.0
    assert p.seat.adjusted_bb_per_100 == p.control.adjusted_bb_per_100


def test_the_two_runs_share_their_deals_so_the_pairing_is_tighter(measure) -> None:
    p = measure.paired("maniac", ("station",), hands=200, seed=3)
    assert p.difference != 0.0
    # The same hand ids were dealt from the same decks, and the paired interval is narrower
    # than the two arms' intervals put side by side.
    assert p.margin is not None and p.seat.margin is not None and p.control.margin is not None
    assert p.margin < p.seat.margin + p.control.margin


def test_five_handed_runs_the_benchmarks_bots_and_counts_methods(measure) -> None:
    p = measure.paired("nash:pushfold", ("station",), hands=40, seed=1)
    assert p.seat.methods  # nash:pushfold records how each decision was made
    assert set(p.seat.methods) <= {"own", "translated", "fallback"}
    assert measure.methods(p.control) == "-"  # tag records none
    five = measure.paired("tag", measure.OPPONENTS, hands=30, seed=1)
    assert five.difference == 0.0
