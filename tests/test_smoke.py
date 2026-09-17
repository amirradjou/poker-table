import io
from pathlib import Path

import pytest

import poker_table
from poker_table.cli import main


def test_version_is_set() -> None:
    assert poker_table.__version__


def test_cli_requires_a_command() -> None:
    with pytest.raises(SystemExit):
        main([])


def run(*argv: str) -> tuple[int, str]:
    buffer = io.StringIO()
    code = main(list(argv), out=buffer)
    return code, buffer.getvalue()


def test_play_prints_a_leaderboard_and_writes_jsonl(tmp_path: Path) -> None:
    out = tmp_path / "hands.jsonl"
    code, text = run(
        "play", "-n", "30", "--seats", "tag,station,maniac", "--seed", "5", "-o", str(out)
    )
    assert code == 0
    assert "30 hands · blinds 1/2" in text
    assert text.count("\n") >= 5
    assert "station" in text and "bb/100" in text
    assert out.read_text().count("\n") == 30


def test_play_show_prints_every_hand() -> None:
    code, text = run("play", "-n", "3", "--seats", "tag,station", "--show", "-q")
    assert code == 0
    assert text.count("Hand #") == 3
    assert "thinks:" in text


def test_play_carry_reports_rebuys() -> None:
    code, text = run("play", "-n", "50", "--seats", "maniac,station", "--carry", "--stack", "20")
    assert code == 0
    assert "rebuys:" in text


def test_stats_and_replay_read_the_file_back(tmp_path: Path) -> None:
    out = tmp_path / "hands.jsonl"
    run("play", "-n", "10", "--seats", "tag,station", "-o", str(out), "-q")
    code, text = run("stats", str(out))
    assert code == 0 and text.startswith("10 hands from")
    code, text = run("replay", str(out), "--hand", "3", "-r")
    assert code == 0 and text.count("Hand #") == 1 and "Hand #3 " in text
    code, text = run("replay", str(out), "--last", "2")
    assert code == 0 and text.count("Hand #") == 2 and "thinks:" not in text
    code, _ = run("replay", str(out), "--hand", "999")
    assert code == 1


def test_bad_seat_kind_is_a_clean_error() -> None:
    code, _ = run("play", "--seats", "wizard")
    assert code == 2


def test_bad_blinds_is_a_clean_error() -> None:
    code, _ = run("play", "--blinds", "2")
    assert code == 2
