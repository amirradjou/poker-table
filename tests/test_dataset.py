import io
import json
from pathlib import Path

from poker_table.agents import CallingStation, Maniac, TightAggressive
from poker_table.agents.laya import build_question
from poker_table.cli import main
from poker_table.coach import dataset
from poker_table.coach.drills import accepted_actions, spots_from
from poker_table.coach.facts import tag_hands
from poker_table.league import LeagueConfig, run_league


def session(hands=40):
    agents = [TightAggressive("tag"), Maniac("maniac", seed=1), CallingStation("station")]
    return run_league(agents, LeagueConfig(hands=hands, seed=6)).histories


def test_coach_samples_are_labelled_with_what_was_right() -> None:
    histories = session()
    facts = tag_hands(histories, "maniac", samples=40)
    samples = list(dataset.from_coach(histories, facts))
    assert samples
    flagged = {(f.hand_id, f.street, f.seat) for f in facts if f.ok is False}
    assert {(s.hand_id, s.street, s.seat) for s in samples} <= flagged
    for s in samples:
        assert s.source == "coach" and s.answer in s.options
        assert s.state and s.instructions and 2 <= len(s.options) <= 20
        assert json.loads(s.to_json())["answer"] == s.answer
    # the label really is an accepted action for that spot
    spots = {s.fact.hand_id + s.fact.street: s for s in spots_from(histories, facts)}
    checked = 0
    for sample in samples:
        spot = spots.get(sample.hand_id + sample.street)
        if spot is None or spot.fact.seat != sample.seat:
            continue
        options = build_question(spot.view)[0]
        assert options[sample.answer].type in accepted_actions(spot.fact, spot.view)
        checked += 1
    assert checked, "expected at least one spot to line up"


def test_policy_samples_are_labelled_with_what_the_seat_did() -> None:
    histories = session()
    samples = list(dataset.from_policy(histories, "tag"))
    assert samples and all(s.source == "policy" and s.tag == "tag" for s in samples)
    assert all(s.answer in s.options for s in samples)
    # every decision the seat made shows up (sizes snap to the nearest offered option)
    decisions = sum(1 for h in histories for d in h.decisions if h.players[d.seat].name == "tag")
    assert len(samples) == decisions
    assert list(dataset.from_policy(histories, "nobody")) == []


def test_summary_and_jsonl_round_trip(tmp_path: Path) -> None:
    histories = session(20)
    samples = list(dataset.from_policy(histories, "station"))
    path = tmp_path / "d.jsonl"
    assert dataset.write_jsonl(path, samples) == len(samples)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(lines) == len(samples)
    assert {"state", "instructions", "options", "answer", "source", "hand_id"} <= set(lines[0])
    text = dataset.summarize(samples)
    assert f"{len(samples)} samples" in text and "policy" in text and "answers:" in text
    assert dataset.summarize([]) == "no samples"


def test_cli_dataset(tmp_path: Path) -> None:
    hands = tmp_path / "h.jsonl"
    assert (
        main(
            ["play", "-n", "25", "--seats", "tag,maniac,station", "-o", str(hands), "-q"],
            out=io.StringIO(),
        )
        == 0
    )
    out = tmp_path / "d.jsonl"
    buf = io.StringIO()
    code = main(
        [
            "dataset",
            str(hands),
            "-p",
            "maniac",
            "-o",
            str(out),
            "--source",
            "both",
            "--samples",
            "30",
        ],
        out=buf,
    )
    assert code == 0
    assert "maniac:" in buf.getvalue() and "samples" in buf.getvalue()
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines and {line["source"] for line in lines} == {"coach", "policy"}
    assert all(line["answer"] in line["options"] for line in lines)
