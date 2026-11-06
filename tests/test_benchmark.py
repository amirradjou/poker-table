"""The fixed test, and the check that a results folder is what it says it is."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from poker_table.benchmark import (
    ENTRANT,
    HANDS,
    OPPONENTS,
    SEED,
    benchmark_file,
    benchmark_problems,
    entry_of,
    folder_name,
    render_leaderboard,
)
from poker_table.cli import main
from poker_table.experiments import load_experiment, run_experiment
from poker_table.verify import verify_folder

ROOT = Path(__file__).parent.parent
LEAGUE = ROOT / "docs" / "results" / "local-league"
STATIONS = '["a:station", "b:station", "c:tag"]'


@pytest.fixture
def played(tmp_path: Path) -> Path:
    """A finished run with some betting in it, ready to be tampered with."""
    path = tmp_path / "x.toml"
    path.write_text(f'name = "x"\ncoach = false\nseats = {STATIONS}\nhands = 12\nseed = 4\n')
    return run_experiment(path, tmp_path / "out", log=lambda _: None).folder


def edit_hands(folder: Path, change) -> None:
    path = folder / "hands.jsonl"
    hands = [json.loads(line) for line in path.read_text().splitlines()]
    hands = change(hands) or hands
    path.write_text("".join(json.dumps(h) + "\n" for h in hands))


# ----- the check ---------------------------------------------------------------------------


def test_the_published_league_checks_out() -> None:
    assert verify_folder(LEAGUE) == []


def test_a_fresh_run_checks_out(played: Path) -> None:
    assert verify_folder(played) == []


def test_a_run_on_several_tables_checks_out(tmp_path: Path) -> None:
    path = tmp_path / "x.toml"
    path.write_text(f'name = "x"\ncoach = false\nseats = {STATIONS}\nhands = 9\ntables = 2\n')
    assert verify_folder(run_experiment(path, tmp_path / "o", log=lambda _: None).folder) == []


def _improve_a_result(hands):
    hands[3]["players"][0]["net"] += 500


def _change_the_cards(hands):
    a, b = hands[2]["players"][0]["hole"], hands[2]["players"][1]["hole"]
    hands[2]["players"][0]["hole"], hands[2]["players"][1]["hole"] = b, a


def _change_the_board(hands):
    with_board = next(h for h in hands if h["board"])
    with_board["board"][0] = "As" if with_board["board"][0] != "As" else "Ks"


def _pay_someone_else(hands):
    paid = next(h for h in hands if h["payouts"])
    winner, amount = next(iter(paid["payouts"].items()))
    other = str((int(winner) + 1) % len(paid["players"]))
    paid["payouts"] = {other: amount}


def _change_the_deck(hands):
    hands[1]["seed"] += 1


def _move_the_button(hands):
    hands[1]["button"] = (hands[1]["button"] + 1) % len(hands[1]["players"])


def _start_with_more_chips(hands):
    hands[1]["players"][0]["stack"] += 100


def _drop_a_hand(hands):
    del hands[4]


def _reorder_hands(hands):
    hands[1], hands[2] = hands[2], hands[1]


def _make_a_move_illegal(hands):
    decision = next(h for h in hands if h["decisions"])["decisions"][0]
    decision["applied"] = "raise 999999"


def _swap_who_acted(hands):
    decisions = next(h for h in hands if len(h["decisions"]) > 1)["decisions"]
    decisions[0]["seat"] = (decisions[0]["seat"] + 1) % 3


def _cut_a_hand_short(hands):
    next(h for h in hands if len(h["decisions"]) > 1)["decisions"].pop()


def _change_the_stakes(hands):
    hands[0]["big_blind"] = 4


@pytest.mark.parametrize(
    "tamper",
    [
        _improve_a_result,
        _change_the_cards,
        _change_the_board,
        _pay_someone_else,
        _change_the_deck,
        _move_the_button,
        _start_with_more_chips,
        _drop_a_hand,
        _reorder_hands,
        _make_a_move_illegal,
        _swap_who_acted,
        _cut_a_hand_short,
        _change_the_stakes,
    ],
    ids=lambda f: f.__name__.strip("_"),
)
def test_a_doctored_file_is_caught(played: Path, tamper) -> None:
    edit_hands(played, tamper)
    assert verify_folder(played)


def test_a_run_cut_short_cannot_claim_it_finished(played: Path) -> None:
    run = json.loads((played / "run.json").read_text())
    run["planned_hands"] = 500
    (played / "run.json").write_text(json.dumps(run))
    assert any("it says it finished" in p for p in verify_folder(played))


def test_a_count_that_does_not_match_is_caught(played: Path) -> None:
    run = json.loads((played / "run.json").read_text())
    run["hands"] = 500
    (played / "run.json").write_text(json.dumps(run))
    assert any("run.json says 500 hands" in p for p in verify_folder(played))


def test_changing_the_league_after_the_fact_is_caught(played: Path) -> None:
    path = played / "experiment.toml"
    path.write_text(path.read_text().replace("seed = 4", "seed = 5"))
    assert verify_folder(played)


def test_a_folder_missing_its_files_says_so(tmp_path: Path) -> None:
    assert "has no hands.jsonl, experiment.toml, run.json" in verify_folder(tmp_path)[0]


def test_the_command_says_what_it_can_and_cannot_show(played: Path, capsys) -> None:
    assert main(["verify", str(played)]) == 0
    printed = capsys.readouterr().out
    assert "checks out" in printed and "cannot show who made the decisions" in printed
    edit_hands(played, _improve_a_result)
    assert main(["verify", str(played)]) == 1
    assert ": 1 problem\n" in capsys.readouterr().out


# ----- the benchmark -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seat", "name"),
    [
        ("tag", "bench-tag"),
        ("llm:nerd@groq/some-model", "bench-nerd-groq-some-model"),
        ("llm:nerd@openrouter/Meta/Llama-X:free", "bench-nerd-openrouter-Meta-Llama-X-free"),
        ("llm:nerd@ollama/qwen2.5:3b", "bench-nerd-ollama-qwen2.5-3b"),
    ],
)
def test_a_folder_name_is_safe_whatever_the_model_is_called(tmp_path, seat, name) -> None:
    assert folder_name(seat) == name
    path = tmp_path / "x.toml"
    path.write_text(benchmark_file(seat))
    experiment = load_experiment(path)
    assert experiment.name == name and experiment.seats[0] == f"{ENTRANT}:{seat}"
    assert tuple(experiment.seats[1:]) == OPPONENTS


@pytest.fixture
def benched(tmp_path: Path, capsys) -> Path:
    assert main(["benchmark", "random", "-o", str(tmp_path)]) == 0
    capsys.readouterr()
    return tmp_path / "bench-random"


def test_every_entrant_gets_the_same_cards_in_the_same_seat(tmp_path: Path, capsys) -> None:
    decks = {}
    for seat in ("random", "station"):
        assert main(["benchmark", seat, "-o", str(tmp_path)]) == 0
        lines = (tmp_path / f"bench-{seat}" / "hands.jsonl").read_text().splitlines()
        hands = [json.loads(line) for line in lines]
        decks[seat] = [(h["seed"], h["players"][0]["hole"], h["players"][0]["name"]) for h in hands]
    assert decks["random"] == decks["station"]


def test_a_benchmark_passes_its_own_checks_and_makes_a_row(benched: Path) -> None:
    assert benchmark_problems(benched) == []
    entry = entry_of(benched)
    assert entry.entrant == "random" and entry.margin > 0 and entry.adjusted < 0
    assert entry.dollars_per_hand is None and entry.seconds_per_call is None


def test_only_the_real_benchmark_gets_a_row(benched: Path, tmp_path: Path) -> None:
    def changed(old: str, new: str) -> Path:
        copy = tmp_path / "copy"
        shutil.rmtree(copy, ignore_errors=True)
        shutil.copytree(benched, copy)
        path = copy / "experiment.toml"
        assert old in path.read_text()
        path.write_text(path.read_text().replace(old, new))
        return copy

    easier = changed('"rock", "maniac"', '"random", "maniac"')
    assert any("not the benchmark's" in p for p in benchmark_problems(easier))
    other_decks = changed(f"seed = {SEED}", "seed = 7")
    assert benchmark_problems(other_decks)
    fewer = changed(f"hands = {HANDS}", "hands = 50")
    assert any(f"not {HANDS} hands" in p for p in benchmark_problems(fewer))


def test_a_run_that_did_not_finish_gets_no_row(benched: Path) -> None:
    run = json.loads((benched / "run.json").read_text())
    run["stopped"] = "budget"
    (benched / "run.json").write_text(json.dumps(run))
    assert any("did not finish" in p for p in benchmark_problems(benched))


def test_a_paid_entrant_needs_a_budget(tmp_path: Path, capsys) -> None:
    argv = ["benchmark", "llm:nerd@claude-haiku-5-5", "-o", str(tmp_path)]
    assert main(argv) != 0
    assert "set budget_usd" in capsys.readouterr().err
    assert "budget_usd = 2.5" in benchmark_file("llm:nerd@claude-haiku-5-5", 2.5)


def test_a_benchmark_can_start_with_a_trial(tmp_path: Path, capsys) -> None:
    assert main(["benchmark", "random", "-o", str(tmp_path), "--trial", "5"]) == 0
    printed = capsys.readouterr().out
    assert f"5 of {HANDS} hands took" in printed and "To carry on" in printed
    assert main(["benchmark", "random", "-o", str(tmp_path), "--resume"]) == 0
    assert benchmark_problems(tmp_path / "bench-random") == []


def test_the_leaderboard_ranks_and_tells_people_how_to_read_it(benched, tmp_path, capsys) -> None:
    assert main(["benchmark", "tag", "-o", str(tmp_path)]) == 0
    text = render_leaderboard([benched, tmp_path / "bench-tag"])
    assert text.index("| tag |") < text.index("| random |")
    assert "Read the intervals before the order" in text
    assert "cannot show who made the decisions" in text
    assert "poker-table benchmark" in text


def test_the_published_leaderboard_is_built_from_the_published_folders() -> None:
    folders = sorted((ROOT / "docs" / "results").glob("bench-*"))
    assert folders
    for folder in folders:
        assert benchmark_problems(folder) == [], folder.name
    assert (ROOT / "docs" / "leaderboard.md").read_text() == render_leaderboard(folders)
