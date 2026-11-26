"""The heads-up track: one match against each bot, each folder an ordinary results folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from poker_table.benchmark import (
    HEADS_UP_HANDS,
    OPPONENTS,
    SEED,
    heads_up_file,
    heads_up_name,
    heads_up_problems,
    render_heads_up_leaderboard,
)
from poker_table.cli import main
from poker_table.experiments import load_experiment
from poker_table.verify import verify_folder

ROOT = Path(__file__).parent.parent


def matches(where: Path) -> list[Path]:
    return sorted(f for f in where.glob("hu-*") if f.is_dir())  # not the .toml files beside them


def test_a_match_is_named_for_the_entrant_and_the_bot(tmp_path: Path) -> None:
    assert heads_up_name("cfr:blueprint", "tag") == "hu-cfr-blueprint-vs-tag"
    assert (
        heads_up_name("llm:nerd@ollama/qwen2.5:3b", "rock") == "hu-nerd-ollama-qwen2.5-3b-vs-rock"
    )
    path = tmp_path / "m.toml"
    path.write_text(heads_up_file("nash:pushfold", "maniac"))
    match = load_experiment(path)
    assert match.seats == ("entrant:nash:pushfold", "maniac")
    assert (match.hands, match.seed, match.stack) == (HEADS_UP_HANDS, SEED, 200)


@pytest.fixture(scope="module")
def track(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("track")
    assert main(["benchmark", "random", "--heads-up", "-o", str(out)]) == 0
    assert main(["benchmark", "rock", "--heads-up", "-o", str(out)]) == 0
    return out


def test_benchmark_heads_up_plays_one_verifiable_folder_per_bot(track: Path) -> None:
    for seat in ("random", "rock"):
        for opponent in OPPONENTS:
            folder = track / heads_up_name(seat, opponent)
            assert verify_folder(folder) == []
            assert heads_up_problems(folder) == []
    assert main(["verify", *map(str, matches(track))]) == 0


def test_the_heads_up_leaderboard_pools_the_four_matches(track: Path) -> None:
    text = render_heads_up_leaderboard(matches(track))
    assert text.index("| rock |") < text.index("| random |")
    assert "vs tag | vs rock | vs maniac | vs station" in text
    assert f"{HEADS_UP_HANDS * len(OPPONENTS):,} hands in all" in text
    assert "cannot show who made the decisions" in text


def test_a_missing_match_keeps_an_entrant_off(track: Path) -> None:
    folders = [f for f in matches(track) if f.name != "hu-rock-vs-tag"]
    with pytest.raises(ValueError, match="rock has no heads-up match against tag"):
        render_heads_up_leaderboard(folders)


def test_a_five_handed_folder_is_not_a_heads_up_match(tmp_path: Path) -> None:
    assert main(["benchmark", "random", "-o", str(tmp_path)]) == 0
    problems = heads_up_problems(tmp_path / "bench-random")
    assert any("not a heads-up match" in p for p in problems)


def test_the_published_heads_up_leaderboard_is_built_from_the_published_folders() -> None:
    folders = matches(ROOT / "docs" / "results")
    assert len(folders) % len(OPPONENTS) == 0 and folders
    for folder in folders:
        assert heads_up_problems(folder) == [], folder.name
    page = ROOT / "docs" / "leaderboard-heads-up.md"
    assert page.read_text() == render_heads_up_leaderboard(folders)
