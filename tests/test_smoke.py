import io
import re
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


def test_play_tournament_prints_finishing_places(tmp_path: Path) -> None:
    out = tmp_path / "freezeout.jsonl"
    code, text = run(
        "play",
        "--tournament",
        "--seats",
        "tag,rock,maniac,station",
        "--seed",
        "7",
        "--level-hands",
        "12",
        "-o",
        str(out),
    )
    assert code == 0
    assert "freezeout · 200 chips each · levels 1/2 to 800/1600+200, 12 hands each" in text
    assert "place  name" in text and "won with 800" in text
    standings = re.findall(r"^\s+(\d+)\s+(\w+)\s+(\d+)\s", text, re.M)
    assert [place for place, _, _ in standings] == ["1", "2", "3", "4"]
    assert "bb/100" in text  # the usual leaderboard is printed too
    played = re.search(r"^(\d+) hands?, 200 chips", text, re.M)
    assert played is not None and out.read_text().count("\n") == int(played.group(1))


def test_play_tournament_with_a_hand_limit_and_a_custom_schedule() -> None:
    code, text = run(
        "play", "--tournament", "--seats", "tag,station", "-n", "3", "--levels", "5/10+2", "-q"
    )
    assert code == 0
    assert "3 hands, 200 chips each, reached level 1 (5/10+2)" in text
    assert "no winner" in text and "still in with" in text


def test_play_with_an_ante() -> None:
    code, text = run("play", "-n", "12", "--seats", "tag,station", "--ante", "1", "--show", "-q")
    assert code == 0
    assert "NLHE 1/2 ante 1" in text and text.count("posts the ante 1") == 24


def test_a_bad_level_schedule_is_a_clean_error() -> None:
    code, _ = run("play", "--tournament", "--levels", "1-2")
    assert code == 2


def test_odds_with_the_other_hands_known() -> None:
    code, text = run(
        "odds", "9h 8d", "-b", "Ts 7c 2d Kh", "--vs", "As Ac", "--pot", "60", "--call", "20"
    )
    assert code == 0
    assert "9h 8d on the turn (Ts 7c 2d Kh) against As Ac" in text
    assert "Equity 18.2% (exact, 44 run-outs)" in text
    assert "Outs to the best hand: 8" in text
    assert "pot odds 3.0:1" in text and "less equity than the price asks" in text


def test_odds_without_them() -> None:
    code, text = run("odds", "Ah Ad", "--vs", "1", "--samples", "4000")
    assert code == 0 and "against 1 unknown hand" in text
    equity = float(text.split("Equity ")[1].split("%")[0])
    assert 83 < equity < 87  # aces against one random hand: about 85%
    assert "4000 samples, ±" in text

    mixed = run("odds", "Ah Ad", "--vs", "Ks Kd,?", "--samples", "2000")[1]
    assert "against Ks Kd, an unknown hand" in mixed


def test_odds_refuses_nonsense() -> None:
    assert run("odds", "Ah Ah")[0] == 2  # the same card twice
    assert run("odds", "Ah Kd", "--vs", "Qs")[0] == 2  # one card is not a hand
    assert run("odds", "Ah Kd", "--vs", "0")[0] == 2
