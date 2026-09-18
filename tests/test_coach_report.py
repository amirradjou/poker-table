import json
from pathlib import Path

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
