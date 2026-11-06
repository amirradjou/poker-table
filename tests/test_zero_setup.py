"""Nothing to set up: a table from one word, and an experiment from a starter file."""

from __future__ import annotations

import re
import socket
import sys
from importlib.resources import files
from pathlib import Path

import pytest

from poker_table.cli import free_port, main
from poker_table.experiments import load_experiment, run_experiment

STARTER = files("poker_table").joinpath("experiment.example.toml").read_text(encoding="utf-8")


@pytest.fixture
def no_server(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stands in for the server and the browser, and records what they were asked to do."""
    calls: dict = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: calls.update(kw, app=app))
    monkeypatch.setattr("webbrowser.open", lambda url: calls.update(opened=url))
    return calls


def test_watch_opens_a_live_table_with_nothing_to_set_up(no_server: dict, capsys) -> None:
    assert main(["watch", "-n", "2", "--pace", "0"]) == 0
    assert no_server["host"] == "127.0.0.1"
    assert no_server["opened"] == f"http://127.0.0.1:{no_server['port']}/"
    printed = capsys.readouterr().out
    assert "tag, rock, maniac, station, random" in printed and "appending to" in printed
    hands = Path(re.search(r"appending to (\S+)", printed).group(1))
    assert hands.parent.name.startswith("poker-table-") and hands.parent.stat().st_mode & 0o77 == 0


def test_watch_deals_new_decks_each_time_unless_told_otherwise(no_server, capsys, monkeypatch):
    seeds = []
    real = __import__("poker_table.web.live", fromlist=["LiveSession"]).LiveSession

    def record(agents, config, *args, **kwargs):
        seeds.append(config.seed)
        return real(agents, config, *args, **kwargs)

    monkeypatch.setattr("poker_table.web.live.LiveSession", record)
    for _ in range(2):
        assert main(["watch", "-n", "1", "--pace", "0", "--no-open"]) == 0
    assert main(["watch", "-n", "1", "--pace", "0", "--no-open", "--seed", "7"]) == 0
    assert seeds[0] != seeds[1] and seeds[2] == 7
    assert "opened" not in no_server


def test_a_taken_port_is_stepped_over() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert free_port(port) > port


def test_no_free_port_says_how_to_choose_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self, address):
        raise OSError("taken")

    monkeypatch.setattr(socket.socket, "bind", refuse)
    with pytest.raises(ValueError, match="--port"):
        free_port()


def test_the_bare_command_opens_a_table_at_a_terminal(no_server, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("poker_table.cli.cmd_watch", lambda args, out: no_server.update(ran=args))
    main([])
    assert no_server["ran"].command == "watch"


def test_the_bare_command_in_a_script_prints_help_rather_than_serving(no_server, capsys) -> None:
    assert main([]) == 2
    assert "usage: poker-table" in capsys.readouterr().err
    assert "port" not in no_server


# ----- the starter file --------------------------------------------------------------------


def test_init_writes_a_starter_that_runs_free(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert main(["experiment", "init"]) == 0
    assert "experiment run league.toml" in capsys.readouterr().out
    outcome = run_experiment("league.toml", "results", hands=10, log=lambda _: None)
    assert outcome.hands == 10
    assert (tmp_path / "results" / "my-league" / "leaderboard.md").exists()


def test_init_never_overwrites_a_file(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    Path("league.toml").write_text("mine")
    assert main(["experiment", "init"]) != 0
    assert "already exists" in capsys.readouterr().err
    assert Path("league.toml").read_text() == "mine"
    assert main(["experiment", "init", "other.toml"]) == 0


def test_init_will_not_write_through_a_link(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("target").write_text("mine")
    Path("league.toml").symlink_to("target")
    assert main(["experiment", "init"]) != 0
    assert Path("target").read_text() == "mine"


def test_a_missing_file_points_at_init(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["experiment", "run", "experiments/local-league.toml"]) != 0
    assert "poker-table experiment init" in capsys.readouterr().err


def test_every_setting_shows_up_in_the_starter() -> None:
    from poker_table.experiments import _KEYS

    missing = [k for k in _KEYS if not re.search(rf"^(# )?{k} = ", STARTER, re.MULTILINE)]
    assert not missing


@pytest.mark.parametrize(
    "seats",
    re.findall(r"^# (seats = .+)$", STARTER, re.MULTILINE),
    ids=["local", "claude", "another-provider"],
)
def test_the_seats_it_suggests_are_real(seats: str, tmp_path: Path) -> None:
    from poker_table.agents.registry import make_agents

    path = tmp_path / "x.toml"
    path.write_text(re.sub(r"^seats = .+$", seats, STARTER, flags=re.MULTILINE))
    make_agents(list(load_experiment(path).seats), seed=0)


def test_every_commented_setting_is_one_the_file_format_accepts(tmp_path: Path) -> None:
    lines = []
    for line in STARTER.splitlines():
        match = re.match(r"# ((?!seats)\w+ = .+)$", line)
        lines.append(match.group(1) if match else line)
    path = tmp_path / "all.toml"
    path.write_text("\n".join(lines))
    experiment = load_experiment(path)
    assert experiment.budget_usd == 1.0 and experiment.tables == 1 and experiment.coach is True
    assert experiment.small_blind == 1 and experiment.big_blind == 2 and experiment.stack == 200
