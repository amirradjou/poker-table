import io
from datetime import date
from pathlib import Path

import pytest

from poker_table.agents import CallingStation, Maniac, TightAggressive
from poker_table.cli import main
from poker_table.coach.drills import DrillLog, accepted_actions, run_drill, spots_from
from poker_table.coach.facts import tag_hands
from poker_table.engine import ActionType
from poker_table.league import LeagueConfig, run_league


def maniac_spots():
    agents = [TightAggressive("tag"), Maniac("maniac", seed=1), CallingStation("station")]
    histories = run_league(agents, LeagueConfig(hands=30, seed=5)).histories
    facts = tag_hands(histories, "maniac", samples=40)
    return histories, facts, spots_from(histories, facts)


def test_spots_rebuild_the_flagged_decisions() -> None:
    histories, facts, spots = maniac_spots()
    flagged = [f for f in facts if f.ok is False]
    assert spots and len(spots) <= len(flagged)
    for spot in spots:
        assert spot.view.seat == spot.fact.seat
        assert spot.view.street.value == spot.fact.street
        assert spot.accepted
        assert spot.view.legal.describe()  # the view is at a real decision point
    open_spot = next(s for s in spots if s.fact.tag == "open")
    assert open_spot.accepted == {ActionType.FOLD}
    assert accepted_actions(open_spot.fact, open_spot.view) == open_spot.accepted


def test_leitner_log_moves_boxes_and_due_dates(tmp_path: Path) -> None:
    log = DrillLog.load(tmp_path / "d.json")
    today = date(2026, 9, 18)
    assert log.due("k", today)
    e = log.record("k", True, today)
    assert e["box"] == 1 and e["due"] == "2026-09-19"
    assert not log.due("k", today) and log.due("k", date(2026, 9, 19))
    e = log.record("k", True, date(2026, 9, 19))
    assert e["box"] == 2 and e["due"] == "2026-09-22"
    e = log.record("k", False, date(2026, 9, 22))
    assert e["box"] == 0 and e["due"] == "2026-09-22" and e["attempts"] == 3 and e["correct"] == 2
    log.save()
    again = DrillLog.load(tmp_path / "d.json")
    assert again.spots == log.spots


def test_run_drill_grades_and_orders_failed_spots_first(tmp_path: Path) -> None:
    _, _, spots = maniac_spots()
    log = DrillLog.load(tmp_path / "d.json")
    today = date(2026, 9, 18)
    answers = iter(["zzz", "f"] + ["f"] * 20)  # a bad command first, then fold everything
    shown: list[str] = []
    result = run_drill(
        spots, log, count=3, today=today, input_fn=lambda p: next(answers), output_fn=shown.append
    )
    assert result.asked == 3
    assert any("unknown command" in s for s in shown)
    assert any("Right." in s or "Not this time" in s for s in shown)
    assert (tmp_path / "d.json").exists()
    # A wrong answer drops the spot to box 0 and makes it due again today, ahead of new spots.
    wrong = [k for k in result.keys if log.spots[k]["box"] == 0]
    if wrong:
        assert log.order(spots, today)[0].key == wrong[0]
    # Correct ones are not due today any more.
    right = [k for k in result.keys if log.spots[k]["box"] == 1]
    assert all(not log.due(k, today) for k in right)


def test_run_drill_with_nothing_due_and_eof(tmp_path: Path) -> None:
    _, _, spots = maniac_spots()
    shown: list[str] = []
    empty = run_drill([], DrillLog.load(tmp_path / "d.json"), output_fn=shown.append)
    assert empty.asked == 0 and "Nothing is due" in shown[0]

    def closed(prompt: str) -> str:
        raise EOFError

    result = run_drill(
        spots, DrillLog.load(tmp_path / "e.json"), input_fn=closed, output_fn=shown.append
    )
    assert result.asked == 0 and (tmp_path / "e.json").exists()


def test_drill_session_hands_out_spots_once_per_sitting(tmp_path: Path) -> None:
    from poker_table.coach.drills import DrillSession

    _, _, spots = maniac_spots()
    log = DrillLog.load(tmp_path / "d.json")
    session = DrillSession(spots, log, today=date(2026, 9, 18))
    first = session.next()
    assert first is not None and session.current is first
    verdict = session.answer(first.key, ActionType.FOLD)
    assert verdict["correct"] == (ActionType.FOLD in first.accepted)
    assert verdict["accepted"] == sorted(a.value for a in first.accepted)
    assert verdict["at_the_table"] == first.fact.action and verdict["score"]["asked"] == 1
    assert (tmp_path / "d.json").exists()
    second = session.next()
    assert second is not None and second.key != first.key  # not the same spot again, even if wrong
    with pytest.raises(LookupError, match="already answered"):
        session.answer(first.key, ActionType.FOLD)
    with pytest.raises(LookupError, match="unknown spot"):
        session.answer("nope", ActionType.FOLD)
    p = session.progress()
    still_due = 0 if verdict["correct"] else 1  # a wrong answer is due again today
    assert p["spots"] == len(spots) and p["asked"] == 1
    assert p["remaining"] == p["due"] - still_due
    assert sum(p["boxes"]) == len(spots)
    session.restart()
    assert session.progress()["asked"] == 0 and session.next() is not None


def test_drill_api(tmp_path: Path) -> None:
    import io

    from fastapi.testclient import TestClient

    from poker_table.web.app import create_app

    out = tmp_path / "h.jsonl"
    assert (
        main(
            ["play", "-n", "30", "--seats", "tag,station,maniac", "-o", str(out), "-q"],
            out=io.StringIO(),
        )
        == 0
    )
    client = TestClient(create_app(out))
    first = client.get("/api/drill/next?player=maniac").json()
    assert first["spot"] is not None and first["progress"]["remaining"] >= 1
    spot = first["spot"]
    assert {"key", "tag", "legal", "players", "steps", "seat"} <= set(spot)
    assert spot["players"][spot["seat"]]["hole"][0] is not None
    assert all(
        p["hole"] == [None, None] for i, p in enumerate(spot["players"]) if i != spot["seat"]
    )
    assert "accepted" not in spot  # the answer is not in the question
    verdict = client.post(
        "/api/drill/answer", json={"player": "maniac", "key": spot["key"], "action": "fold"}
    ).json()
    assert {"correct", "accepted", "detail", "box", "due", "score", "progress"} <= set(verdict)
    assert (tmp_path / "h.maniac.drills.json").exists()
    again = client.post(
        "/api/drill/answer", json={"player": "maniac", "key": spot["key"], "action": "fold"}
    )
    assert again.status_code == 409
    assert (
        client.post(
            "/api/drill/answer", json={"player": "maniac", "key": spot["key"], "action": "shove"}
        ).status_code
        == 400
    )
    assert client.get("/api/drill/next?player=nobody").status_code == 404
    second = client.get("/api/drill/next?player=maniac").json()
    assert second["spot"] is None or second["spot"]["key"] != spot["key"]
    fresh = client.get("/api/drill/next?player=maniac&restart=1").json()
    assert fresh["progress"]["asked"] == 0


def test_cli_drill(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "h.jsonl"
    assert main(["play", "-n", "30", "--seats", "tag,station,maniac", "-o", str(out), "-q"]) == 0
    answers = iter(["f"] * 50)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    buf = io.StringIO()
    assert (
        main(["drill", str(out), "--player", "maniac", "-n", "2", "--samples", "30"], out=buf) == 0
    )
    assert "Spot 1 of 2" in buf.getvalue() and "Score" in buf.getvalue()
    assert (tmp_path / "h.maniac.drills.json").exists()
