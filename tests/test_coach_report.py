import json
from pathlib import Path

import pytest

from poker_table.agents import CallingStation, Maniac, RandomAgent, TightAggressive
from poker_table.cli import main
from poker_table.coach.facts import tag_hands
from poker_table.coach.report import build_report
from poker_table.league import LeagueConfig, run_league


def session(hands=60):
    agents = [
        TightAggressive("tag"),
        Maniac("maniac", seed=1),
        CallingStation("station"),
        RandomAgent("random", seed=2),
    ]
    return run_league(agents, LeagueConfig(hands=hands, seed=7)).histories


def test_report_finds_the_station_and_the_maniac_leaks() -> None:
    histories = session()
    station = build_report(histories, "station", tag_hands(histories, "station", samples=60))
    assert station.hands == 60 and station.decisions > 0
    tags = {leak.tag for leak in station.leaks}
    assert "limp" in tags or "call_open" in tags or "call_vs_bet" in tags
    text = station.render()
    assert text.startswith("Coach report for station — 60 hands")
    assert "Recurring leaks" in text and "#" in text
    maniac = build_report(histories, "maniac", tag_hands(histories, "maniac", samples=60))
    assert any(leak.tag in ("open", "three_bet") for leak in maniac.leaks)
    assert any("bluffs:" in o for o in maniac.observations)


def test_report_is_ranked_and_serialisable() -> None:
    histories = session(40)
    report = build_report(histories, "random", tag_hands(histories, "random", samples=40))
    scores = [lk.leaks * lk.rate for lk in report.leaks]
    assert scores == sorted(scores, reverse=True)
    for leak in report.leaks:
        assert 0 < leak.leaks <= leak.opportunities
        assert leak.examples and all(f.ok is False for f in leak.examples)
        assert sum(n for n, _ in leak.by_position.values()) == leak.leaks
    data = json.loads(json.dumps(report.to_dict()))
    assert data["player"] == "random" and data["leaks"][0]["examples"][0]["hand_id"]
    assert data["net"] == sum(p.net for h in histories for p in h.players if p.name == "random")


def test_empty_and_unknown_players() -> None:
    report = build_report([], "ghost", [])
    assert report.hands == 0 and report.leaks == [] and "No leaks" in report.render()
    assert report.bb_per_100 == 0.0


def test_cli_coach(tmp_path: Path) -> None:
    out = tmp_path / "h.jsonl"
    assert main(["play", "-n", "30", "--seats", "tag,station,maniac", "-o", str(out), "-q"]) == 0
    import io

    buf = io.StringIO()
    code = main(
        [
            "coach",
            str(out),
            "--player",
            "station",
            "--samples",
            "40",
            "--json",
            str(tmp_path / "r.json"),
        ],
        out=buf,
    )
    assert code == 0
    assert "Coach report for station — 30 hands" in buf.getvalue()
    assert (tmp_path / "r.json").exists()
    code = main(["coach", str(out), "--player", "nobody"], out=io.StringIO())
    assert code == 2


def test_trend_compares_the_two_halves() -> None:
    histories = session(80)
    report = build_report(histories, "maniac", tag_hands(histories, "maniac", samples=30))
    assert report.trend
    assert all("→" in t and t.endswith(")") for t in report.trend)
    assert "Trend" in report.render()
    short = build_report(histories[:20], "maniac", tag_hands(histories[:20], "maniac", samples=30))
    assert short.trend == []


def test_weekly_trend_when_hands_span_weeks() -> None:
    from dataclasses import replace

    histories = session(90)
    stamped = []
    for i, h in enumerate(histories):
        week = i // 30  # three weeks of thirty hands
        stamped.append(replace(h, played_at=f"2026-09-{1 + 7 * week:02d}T12:00:00+00:00"))
    report = build_report(stamped, "maniac", tag_hands(stamped, "maniac", samples=30))
    assert report.trend
    assert any("2026-W36" in t and "2026-W38" in t for t in report.trend)
    assert all("→" in t and "W3" in t for t in report.trend)
    assert "Trend" in report.render()
    # hands without any date fall back to halves
    undated = [replace(h, played_at="") for h in stamped]
    plain = build_report(undated, "maniac", tag_hands(undated, "maniac", samples=30))
    assert plain.trend and all("first half" in t for t in plain.trend)


def test_report_names_the_villains_you_leak_against() -> None:
    histories = session(80)
    report = build_report(histories, "station", tag_hands(histories, "station", samples=30))
    assert report.opponents, "a station leaks against everyone"
    names = [o.name for o in report.opponents]
    assert "station" not in names and set(names) <= {"tag", "maniac", "random"}
    top = report.opponents[0]
    assert top.leaks <= top.decisions and top.worst_count >= 1 and top.hands == 80
    assert "vs " in top.describe() and "VPIP" in top.describe()
    data = report.to_dict()
    assert data["opponents"][0]["name"] == top.name and data["opponents"][0]["text"]
    assert "Against whom" in report.render()
    # facts remember who was in the pot
    facts = report.facts
    assert all(isinstance(f.opponents, tuple) for f in facts)
    assert any(len(f.opponents) >= 2 for f in facts)
    assert isinstance(facts[0].to_dict()["opponents"], list)


def test_margins_change_the_verdicts() -> None:
    from poker_table.coach.facts import DEFAULT_MARGINS, Margins

    assert Margins.parse("") == DEFAULT_MARGINS
    custom = Margins.parse("call=0.2, fold=0.0, strong=0.5")
    assert (custom.call, custom.fold, custom.strong) == (0.2, 0.0, 0.5)
    assert custom.bluff_share == DEFAULT_MARGINS.bluff_share
    with pytest.raises(ValueError, match="unknown margin"):
        Margins.parse("nope=1")
    histories = session(60)
    strict = tag_hands(histories, "station", samples=40, margins=Margins(call=0.0))
    lenient = tag_hands(histories, "station", samples=40, margins=Margins(call=0.5))
    bad_strict = sum(f.ok is False for f in strict if f.tag == "call_vs_bet")
    bad_lenient = sum(f.ok is False for f in lenient if f.tag == "call_vs_bet")
    assert bad_strict > bad_lenient


def test_played_at_is_stamped_and_filterable(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from poker_table.cli import filter_by_date

    histories = session(5)
    assert all(h.played is not None and h.played.tzinfo is not None for h in histories)
    assert all(h.played.year == datetime.now(UTC).year for h in histories)
    today = datetime.now(UTC).date().isoformat()
    assert len(filter_by_date(histories, today, None)) == 5
    assert filter_by_date(histories, None, today) == []
    assert filter_by_date(histories, None, None) == histories
