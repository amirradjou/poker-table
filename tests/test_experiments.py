import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from poker_table.agents.llm import LLMAgent
from poker_table.cli import main
from poker_table.experiments import (
    Experiment,
    load_experiment,
    margin_of_error,
    run_experiment,
    seat_results,
    write_report,
)
from poker_table.history import read_jsonl
from poker_table.web.app import create_app
from poker_table.web.export import export_site

BOTS = '["tag", "rock", "maniac", "station"]'
STATIONS = '["a:station", "b:station", "c:station"]'

# Scripted seats that behave like model seats: one charges for each decision, one hits Ctrl-C.
SEATS = """
from types import SimpleNamespace
from poker_table.agents.base import Decision
from poker_table.engine import Action


class Costly:
    kind = "costly"

    def __init__(self, name, seed=0):
        self.name = name
        self.usage = SimpleNamespace(cost_usd=0.0, calls=0, errors=0)

    def act(self, view):
        self.usage.cost_usd += 0.01
        self.usage.calls += 1
        action = Action.check() if view.legal.can_check else Action.call()
        return Decision(action, reasoning="pricey", meta={"model": "fake", "cost_usd": 0.01})


class Interrupter:
    kind = "interrupter"

    def __init__(self, name, seed=0, after=5):
        self.name = name
        self.left = after

    def act(self, view):
        self.left -= 1
        if self.left < 0:
            raise KeyboardInterrupt
        return Decision(Action.check() if view.legal.can_check else Action.fold())
"""

AGENTS = """
[agents.costly]
type = "python"
class = "seats.py:Costly"

[agents.interrupter]
type = "python"
class = "seats.py:Interrupter"
"""


def write_experiment(folder: Path, body: str, name: str = "trial") -> Path:
    path = folder / f"{name}.toml"
    path.write_text(f'name = "{name}"\ncoach = false\n{body}\n', encoding="utf-8")
    return path


def custom_seats(folder: Path) -> str:
    (folder / "seats.py").write_text(SEATS, encoding="utf-8")
    (folder / "agents.toml").write_text(AGENTS, encoding="utf-8")
    return 'agents = ["agents.toml"]\n'


def fingerprint(history) -> tuple:
    return (
        history.hand_id,
        history.seed,
        history.board,
        [e.action for e in history.actions()],
        [p.net for p in history.players],
    )


def hands_of(folder: Path) -> list:
    return list(read_jsonl(folder / "hands.jsonl"))


# ----- the file ----------------------------------------------------------------------------


def test_a_file_reads_with_its_defaults(tmp_path: Path) -> None:
    path = tmp_path / "x.toml"
    path.write_text(
        'name = "x"\nseats = ["tag", "rock"]\nhands = 10\nblinds = "2/5"\nbudget_usd = 3\n',
        encoding="utf-8",
    )
    experiment = load_experiment(path)
    assert experiment.seats == ("tag", "rock") and experiment.hands == 10
    assert (experiment.small_blind, experiment.big_blind) == (2, 5)
    assert experiment.budget_usd == 3.0 and experiment.tables == 1 and experiment.stack == 200
    assert experiment.coach is True


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('seats = ["tag", "rock"]', "hands is required"),
        ("hands = 5", "seats is required"),
        ('seats = ["tag"]\nhands = 5', "at least two"),
        ('seats = ["tag", "rock"]\nhands = 0', "at least one hand"),
        ('seats = ["tag", "rock"]\nhands = 2\ntables = 3', "a hand for each table"),
        ('seats = ["tag", "rock"]\nhands = true', "wrong type"),
        ('seats = ["tag", "rock"]\nhands = 5\nbudget_usd = 0', "above zero"),
        # a misspelt limit must not quietly mean no limit
        ('seats = ["tag", "rock"]\nhands = 5\nbudget = 5.0', "unknown key"),
    ],
)
def test_a_bad_file_is_refused(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_experiment(write_experiment(tmp_path, body))


@pytest.mark.parametrize("name", ["..", ".", "../up", "a/b", ".hidden", "-x", "trial\\n", ""])
def test_a_name_cannot_leave_the_results_folder(tmp_path: Path, name: str) -> None:
    path = tmp_path / "x.toml"
    path.write_text(f'name = "{name}"\nseats = ["tag", "rock"]\nhands = 5\n', encoding="utf-8")
    with pytest.raises(ValueError, match="folder"):
        load_experiment(path)


def test_rebuilding_a_report_never_loads_the_agents_it_names(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, f"seats = {BOTS}\nhands = 4")
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    saved = outcome.folder / "experiment.toml"
    saved.write_text(saved.read_text() + 'agents = ["boom.toml"]\n', encoding="utf-8")
    (outcome.folder / "boom.toml").write_text(
        '[agents.boom]\ntype = "python"\nclass = "boom.py:Boom"\n', encoding="utf-8"
    )
    marker = tmp_path / "ran"
    (outcome.folder / "boom.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8"
    )
    assert write_report(outcome.folder).exists()
    assert not marker.exists()


def test_a_link_in_a_results_folder_cannot_overwrite_what_it_points_at(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, f"seats = {BOTS}\nhands = 4")
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    precious = tmp_path / "bashrc"
    precious.write_text("mine", encoding="utf-8")
    report = outcome.folder / "leaderboard.md"
    report.unlink()
    report.symlink_to(precious)

    write_report(outcome.folder)
    assert precious.read_text() == "mine"
    assert not report.is_symlink() and report.read_text().startswith("# trial")


def test_a_leftover_temporary_link_is_not_written_through(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, f"seats = {BOTS}\nhands = 4")
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    precious = tmp_path / "bashrc"
    precious.write_text("mine", encoding="utf-8")
    (outcome.folder / ".leaderboard.md.tmp").symlink_to(precious)

    write_report(outcome.folder)
    assert precious.read_text() == "mine"


def test_a_linked_coach_folder_is_refused(tmp_path: Path, monkeypatch) -> None:
    use_client(monkeypatch, Client())
    path = tmp_path / "paid.toml"
    path.write_text(
        'name = "paid"\nseats = ["llm:nerd@claude-sonnet-5-5", "tag"]\nhands = 4\nbudget_usd = 5\n',
        encoding="utf-8",
    )
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    coach = outcome.folder / "coach"
    (coach / "nerd.md").unlink()
    coach.rmdir()
    coach.symlink_to(elsewhere)

    with pytest.raises(ValueError, match="is a link"):
        write_report(outcome.folder)
    assert list(elsewhere.iterdir()) == []


def test_resuming_will_not_append_hands_through_a_link(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, f"seats = {BOTS}\nhands = 4")
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    precious = tmp_path / "elsewhere.jsonl"
    hands = outcome.folder / "hands.jsonl"
    precious.write_text(hands.read_text(), encoding="utf-8")
    before = precious.read_text()
    hands.unlink()
    hands.symlink_to(precious)

    with pytest.raises(ValueError, match="is a link"):
        run_experiment(path, tmp_path / "out", hands=8, resume=True, log=lambda _: None)
    assert precious.read_text() == before


# ----- the seats ---------------------------------------------------------------------------


def test_a_human_seat_is_refused(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, 'seats = ["human", "tag"]\nhands = 5')
    with pytest.raises(ValueError, match="human"):
        run_experiment(path, tmp_path / "out")


def test_a_paid_seat_needs_a_budget(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, 'seats = ["llm:nerd@claude-sonnet-5-5", "tag"]\nhands = 5')
    with pytest.raises(ValueError, match="set budget_usd"):
        run_experiment(path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


class Messages:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def create(self, **request):
        self.calls += 1
        if self.fail:
            raise ConnectionError("no route")
        reply = {"action": "fold", "amount": 0, "table_talk": "", "reasoning": "tight"}
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(reply))],
            stop_reason="end_turn",
            model=request["model"],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        )


class Client:
    def __init__(self, *, known: bool = True, fail: bool = False) -> None:
        self.messages = Messages(fail)
        self.known = known
        self.models = self

    def retrieve(self, model: str):
        if not self.known:
            raise PermissionError("no credentials")
        return SimpleNamespace(id=model)


def use_client(monkeypatch, client: Client) -> None:
    monkeypatch.setattr(LLMAgent, "client", property(lambda _: client))


def test_a_paid_seat_that_cannot_connect_is_found_before_the_first_hand(
    tmp_path: Path, monkeypatch
) -> None:
    client = Client(known=False)
    use_client(monkeypatch, client)
    path = write_experiment(
        tmp_path, 'seats = ["llm:nerd@claude-sonnet-5-5", "tag"]\nhands = 5\nbudget_usd = 1'
    )
    with pytest.raises(ValueError, match="cannot use claude-sonnet-5-5.*no credentials"):
        run_experiment(path, tmp_path / "out")
    assert client.messages.calls == 0


def test_a_model_seat_gets_a_cost_a_price_and_a_coach_report(tmp_path: Path, monkeypatch) -> None:
    use_client(monkeypatch, Client())
    path = tmp_path / "paid.toml"
    path.write_text(
        'name = "paid"\nseats = ["llm:nerd@claude-sonnet-5-5", "tag"]\nhands = 6\n'
        "budget_usd = 5\nseed = 3\n",
        encoding="utf-8",
    )
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    assert outcome.stopped == "finished" and outcome.hands == 6 and outcome.spent_usd > 0
    assert (outcome.folder / "experiment.toml").read_text() == path.read_text()
    run = json.loads((outcome.folder / "run.json").read_text())
    assert run["stopped"] == "finished" and run["spent_usd"] == pytest.approx(outcome.spent_usd)

    report = (outcome.folder / "leaderboard.md").read_text()
    assert "llm:nerd on claude-sonnet-5-5" in report
    assert "[nerd](coach/nerd.md)" in report and "tag.md" not in report
    assert (outcome.folder / "coach" / "nerd.md").exists()
    assert not (outcome.folder / "coach" / "tag.md").exists()
    nerd = next(r for r in seat_results(hands_of(outcome.folder)) if r.name == "nerd")
    assert nerd.model_calls > 0 and nerd.cost_usd > 0 and nerd.failed == 0


def test_a_seat_that_keeps_failing_stops_the_run_rather_than_folding_all_night(
    tmp_path: Path, monkeypatch
) -> None:
    use_client(monkeypatch, Client(fail=True))
    path = write_experiment(
        tmp_path, 'seats = ["llm:maniac@claude-sonnet-5-5", "station"]\nhands = 500\nbudget_usd = 1'
    )
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    assert outcome.stopped == "errors" and outcome.hands < 500
    report = (outcome.folder / "leaderboard.md").read_text()
    assert "kept failing" in report
    row = next(r for r in seat_results(hands_of(outcome.folder)) if r.name == "maniac")
    assert row.failed >= 10 and row.model_calls == 0


# ----- the run -----------------------------------------------------------------------------


def test_one_table_deals_what_the_play_command_deals(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, f"seats = {BOTS}\nhands = 30\nseed = 5")
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    assert outcome.stopped == "finished" and outcome.hands == 30
    played = tmp_path / "played.jsonl"
    seats = "tag,rock,maniac,station"
    assert main(["play", "-n", "30", "--seed", "5", "--seats", seats, "-o", str(played), "-q"]) == 0
    assert [fingerprint(h) for h in hands_of(outcome.folder)] == [
        fingerprint(h) for h in read_jsonl(played)
    ]


def test_tables_share_one_file_and_each_repeats_exactly(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, f"seats = {BOTS}\nhands = 32\nseed = 5\ntables = 3")
    first = run_experiment(path, tmp_path / "one", log=lambda _: None)
    again = run_experiment(path, tmp_path / "two", log=lambda _: None)
    hands = hands_of(first.folder)
    ids = [h.hand_id for h in hands]
    assert len(ids) == 32 and len(set(ids)) == 32
    for table, count in ((1, 11), (2, 11), (3, 10)):
        assert sum(i.startswith(f"t{table}-") for i in ids) == count
    assert all(sum(p.net for p in h.players) == 0 for h in hands)
    # finishing order varies between runs; each table's own hands do not
    assert sorted(map(fingerprint, hands)) == sorted(map(fingerprint, hands_of(again.folder)))
    assert len({h.seed for h in hands}) == 32  # the tables are not dealing the same decks


def test_the_viewer_and_the_coach_read_a_file_from_several_tables(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, f"seats = {BOTS}\nhands = 12\nseed = 5\ntables = 2")
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    source = outcome.folder / "hands.jsonl"
    client = TestClient(create_app(source))
    assert client.get("/api/session").json()["hands"] == 12
    assert client.get("/api/hands/t2-3").json()["hand_id"] == "t2-3"
    assert client.get("/api/stats").status_code == 200
    export_site(source, tmp_path / "site", players=["tag"])
    assert (tmp_path / "site" / "data" / "hands" / "t2-3.json").exists()
    assert main(["coach", str(source), "-p", "tag", "--samples", "30"]) == 0


def test_a_finished_folder_is_not_overwritten(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, f"seats = {BOTS}\nhands = 4")
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    before = (outcome.folder / "hands.jsonl").read_bytes()
    with pytest.raises(ValueError, match="--resume"):
        run_experiment(path, tmp_path / "out", log=lambda _: None)
    assert (outcome.folder / "hands.jsonl").read_bytes() == before


def test_resuming_deals_the_hands_an_unbroken_run_would_have(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, f"seats = {STATIONS}\nhands = 20\nseed = 9\ntables = 2")
    whole = run_experiment(path, tmp_path / "whole", log=lambda _: None)
    run_experiment(path, tmp_path / "split", hands=8, log=lambda _: None)
    resumed = run_experiment(path, tmp_path / "split", resume=True, log=lambda _: None)
    assert resumed.hands == 20 and resumed.stopped == "finished"
    assert sorted(map(fingerprint, hands_of(resumed.folder))) == sorted(
        map(fingerprint, hands_of(whole.folder))
    )
    assert json.loads((resumed.folder / "run.json").read_text())["planned_hands"] == 20


def test_a_resumed_seat_gets_back_what_it_had_seen_and_written(monkeypatch) -> None:
    from poker_table.agents.registry import make_agents
    from poker_table.experiments import _catch_up
    from poker_table.history import DecisionTrace, HandHistory
    from poker_table.league import LeagueConfig, run_league

    monkeypatch.setattr(LLMAgent, "client", property(lambda _: Client()))
    first = run_league(
        make_agents(["llm:nerd@ollama/none", "station"]), LeagueConfig(hands=3)
    ).histories
    noted = DecisionTrace(0, "preflop", "fold", "fold", False, "", "", 1.0, {"note": "calls a lot"})
    with_note = HandHistory.from_json(first[0].to_json())
    object.__setattr__(with_note, "decisions", (*with_note.decisions, noted))

    seat, station = make_agents(["llm:nerd@ollama/none", "station"])
    assert isinstance(seat, LLMAgent) and seat.reads is not None
    _catch_up([seat, station], [with_note, *first[1:]])
    assert list(seat.notes)[-1] == f"hand {with_note.hand_id}: calls a lot"
    assert len(seat.reads.hands) == 3


def test_resuming_refuses_a_different_league(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, f"seats = {BOTS}\nhands = 4\nseed = 1")
    run_experiment(path, tmp_path / "out", log=lambda _: None)
    changed = write_experiment(tmp_path, f"seats = {BOTS}\nhands = 8\nseed = 2")
    with pytest.raises(ValueError, match="different league"):
        run_experiment(changed, tmp_path / "out", resume=True, log=lambda _: None)


def test_the_budget_is_shared_across_tables_and_resuming_remembers_it(tmp_path: Path) -> None:
    agents = custom_seats(tmp_path)
    path = write_experiment(
        tmp_path,
        f'{agents}seats = ["costly", "station"]\nhands = 400\ntables = 4\nbudget_usd = 0.2',
    )
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    assert outcome.stopped == "budget" and outcome.hands < 400
    # each of the four tables may finish the hand it is in, and no more than that
    assert 0.2 <= outcome.spent_usd < 0.2 + 4 * 0.05
    assert "reached its budget" in (outcome.folder / "leaderboard.md").read_text()

    again = run_experiment(path, tmp_path / "out", resume=True, log=lambda _: None)
    assert again.hands == outcome.hands  # already at the limit, so nothing more is dealt
    assert again.spent_usd == pytest.approx(outcome.spent_usd)


def test_ctrl_c_keeps_the_finished_hands_and_writes_the_report(tmp_path: Path, capsys) -> None:
    agents = custom_seats(tmp_path)
    path = write_experiment(
        tmp_path, f'{agents}seats = ["interrupter", "station"]\nhands = 200\nseed = 1'
    )
    code = main(["experiment", "run", str(path), "-o", str(tmp_path / "out")])
    assert code == 130
    folder = tmp_path / "out" / "trial"
    played = hands_of(folder)
    assert 0 < len(played) < 200
    assert json.loads((folder / "run.json").read_text())["stopped"] == "interrupted"
    assert f"{len(played)} of 200 hands" in (folder / "leaderboard.md").read_text()
    assert "interrupted" in capsys.readouterr().out


# ----- the report --------------------------------------------------------------------------


def test_the_margin_is_a_95_percent_interval_on_bb_per_100() -> None:
    assert margin_of_error([2, -2, 2, -2]) == pytest.approx(226.3, abs=0.1)
    assert margin_of_error([5.0]) is None


def test_the_report_says_who_is_clear_of_break_even_and_rebuilds_the_same(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, 'seats = ["tag", "random"]\nhands = 3000\nseed = 2')
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    report = outcome.folder / "leaderboard.md"
    text = report.read_text()
    assert text.startswith("# trial\n")
    assert "3000 hands at blinds 1/2" in text and "seed 2" in text
    rows = seat_results(hands_of(outcome.folder))
    assert rows[0].name == "tag" and rows[0].clear_of_zero and rows[1].clear_of_zero
    assert "Clear of break-even: tag, random." in text

    report.unlink()
    assert main(["experiment", "report", str(outcome.folder)]) == 0
    assert report.read_text() == text


def test_a_short_run_is_not_allowed_to_sound_sure_of_itself(tmp_path: Path) -> None:
    # A tag does beat a random bot, and 300 hands heads-up still cannot show it: +173 +- 201.
    path = write_experiment(tmp_path, 'seats = ["tag", "random"]\nhands = 300\nseed = 2')
    outcome = run_experiment(path, tmp_path / "out", log=lambda _: None)
    rows = seat_results(hands_of(outcome.folder))
    assert rows[0].name == "tag" and rows[0].bb_per_100 > 0 and not rows[0].clear_of_zero
    text = (outcome.folder / "leaderboard.md").read_text()
    assert "Too close to break-even to call: tag, random." in text
    assert "Clear of break-even" not in text


def test_rebuilding_a_folder_with_no_hands_says_so(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no hands.jsonl"):
        write_report(tmp_path)


def test_the_hands_are_shared_out_across_the_tables() -> None:
    experiment = Experiment(name="x", seats=("a", "b"), hands=10, tables=3)
    assert [experiment.hands_at(table) for table in range(3)] == [4, 3, 3]


@pytest.mark.parametrize(
    "path", sorted((Path(__file__).parent.parent / "experiments").glob("*.toml")), ids=str
)
def test_the_experiments_that_ship_can_run(path: Path, monkeypatch) -> None:
    from poker_table.agents.registry import make_agents
    from poker_table.experiments import _check_seats

    use_client(monkeypatch, Client())
    monkeypatch.setattr(Client, "check", lambda self, model: None, raising=False)
    experiment = load_experiment(path)
    assert experiment.name == path.stem
    _check_seats(experiment, make_agents(experiment.seats, seed=experiment.seed))


def test_the_experiment_folder_has_the_examples_it_should() -> None:
    folder = Path(__file__).parent.parent / "experiments"
    assert {p.stem for p in folder.glob("*.toml")} >= {
        "four-personalities",
        "local-league",
        "per-model",
    }
