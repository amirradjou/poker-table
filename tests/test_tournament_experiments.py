"""Freezeouts in an experiment file: the settings, the run, resuming one half played, the report."""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from poker_table.agents.llm import LLMAgent
from poker_table.agents.registry import make_agents
from poker_table.cli import main
from poker_table.experiments import _restore_notes, load_experiment, run_experiment
from poker_table.history import DecisionTrace, read_jsonl
from poker_table.league import run_tournament, standings_of
from poker_table.margins import Finish, finish_calls, t_margin, wilson

BOTS = '["tag", "rock", "maniac", "station", "random"]'
QUICK = f"seats = {BOTS}\ntournaments = 3\nlevel_hands = 5\nseed = 4"

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

    def __init__(self, name, seed=0, after=12):
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


def write_experiment(folder: Path, body: str, name: str = "fz") -> Path:
    path = folder / f"{name}.toml"
    path.write_text(f'name = "{name}"\ncoach = false\n{body}\n', encoding="utf-8")
    return path


def custom_seats(folder: Path) -> str:
    (folder / "seats.py").write_text(SEATS, encoding="utf-8")
    (folder / "agents.toml").write_text(AGENTS, encoding="utf-8")
    return 'agents = ["agents.toml"]\n'


def hands_of(folder: Path) -> list:
    return list(read_jsonl(folder / "hands.jsonl"))


def as_written(history) -> dict:
    """A hand as the file holds it, less the clock, which a second run cannot repeat."""
    data = json.loads(history.to_json())
    del data["played_at"]
    for decision in data["decisions"]:
        del decision["latency_ms"]
    return data


def quiet(_: str) -> None:
    pass


# ----- the file ----------------------------------------------------------------------------


def test_a_file_of_tournaments_reads_with_its_defaults(tmp_path: Path) -> None:
    experiment = load_experiment(write_experiment(tmp_path, f"seats = {BOTS}\ntournaments = 40"))
    assert experiment.tournaments == 40 and experiment.hands == 0
    assert (experiment.level_hands, experiment.max_hands, experiment.stack) == (20, 5000, 200)
    assert str(experiment.levels[0]) == "1/2" and str(experiment.levels[-1]) == "800/1600+200"
    custom = load_experiment(
        write_experiment(tmp_path, f'seats = {BOTS}\ntournaments = 2\nlevels = "2/4, 5/10+1"')
    )
    assert [str(level) for level in custom.levels] == ["2/4", "5/10+1"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (f"seats = {BOTS}\nhands = 10\ntournaments = 5", "not both"),
        (f'seats = {BOTS}\ntournaments = 5\nblinds = "1/2"', "drop blinds"),
        (f"seats = {BOTS}\ntournaments = 5\nante = 1", "drop ante"),
        (f"seats = {BOTS}\ntournaments = 5\ntables = 2", "one table"),
        (f"seats = {BOTS}\nhands = 10\nlevel_hands = 5", "level_hands is for tournaments"),
        (f"seats = {BOTS}\ntournaments = 0", "1 or more"),
        (f"seats = {BOTS}\ntournaments = 3\nmax_hands = 0", "1 or more"),
        (f'seats = {BOTS}\ntournaments = 3\nlevels = "1-2"', "levels: blinds must look"),
        (f"seats = {BOTS}", "hands is required"),
    ],
)
def test_a_file_that_mixes_or_misses_its_settings_is_refused(tmp_path, body, message) -> None:
    with pytest.raises(ValueError, match=message):
        load_experiment(write_experiment(tmp_path, body))


# ----- the run -----------------------------------------------------------------------------


def test_each_tournament_has_its_own_seats_decks_and_number(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, QUICK)
    outcome = run_experiment(path, tmp_path / "out", log=quiet)
    assert outcome.stopped == "finished" and outcome.tournaments == 3 and outcome.planned == 3
    hands = hands_of(outcome.folder)
    assert outcome.hands == len(hands)
    experiment = load_experiment(path)
    orders = []
    for number in (1, 2, 3):
        mine = [h for h in hands if h.tournament["id"] == number]
        assert [h.hand_id for h in mine] == [f"f{number}-{n}" for n in range(1, len(mine) + 1)]
        # each one is the freezeout run_tournament plays alone from the seed and the number
        agents = make_agents(experiment.seats, seed=experiment.tournament_seed(number))
        seated = [agents[i] for i in experiment.seating(number)]
        alone = run_tournament(seated, experiment.tournament_config(number))
        assert [as_written(h) for h in mine] == [as_written(h) for h in alone.histories]
        orders.append([p.name for p in mine[0].players])
        assert orders[-1] == [experiment.seats[i] for i in experiment.seating(number)]
    # the seats are drawn for each one: over many, every seat starts on the button about as often
    drawn = [tuple(experiment.seating(number)) for number in range(1, 2001)]
    assert len(set(drawn)) == 120  # every order of five seats turns up
    buttons = [order[0] for order in drawn]
    assert all(330 < buttons.count(seat) < 470 for seat in range(5))
    run = json.loads((outcome.folder / "run.json").read_text())
    assert (run["planned_tournaments"], run["tournaments"], run["hands"]) == (3, 3, len(hands))
    assert "planned_hands" not in run


def cuts(hands: list) -> list[int]:
    """Lines to cut a hands file at: before and after the first hand, hands where a seat went
    out and the ones either side of them, the last hand of a tournament and the one before it."""
    points = {0, 1, len(hands) - 1}
    for line, hand in enumerate(hands):
        if hand.tournament["out"]:
            points |= {line, line + 1, line + 2}
    return sorted(p for p in points if 0 <= p < len(hands))


def test_a_freezeout_carried_on_from_any_hand_plays_out_as_an_unbroken_run(tmp_path) -> None:
    path = write_experiment(tmp_path, QUICK)
    whole = run_experiment(path, tmp_path / "whole", log=quiet)
    hands = hands_of(whole.folder)
    expected = [as_written(h) for h in hands]
    points = cuts(hands)
    ends = [
        i + 1
        for i, h in enumerate(hands[:-1])
        if h.tournament["id"] != hands[i + 1].tournament["id"]
    ]
    assert set(ends) <= set(points) and len(points) > 10  # between tournaments too
    for cut in points:
        split = tmp_path / f"cut{cut}" / "fz"
        shutil.copytree(whole.folder, split)
        lines = (whole.folder / "hands.jsonl").read_text().splitlines(keepends=True)
        (split / "hands.jsonl").write_text("".join(lines[:cut]))
        resumed = run_experiment(path, split.parent, resume=True, log=quiet)
        assert resumed.stopped == "finished" and resumed.tournaments == 3
        assert [as_written(h) for h in hands_of(split)] == expected, f"cut after line {cut}"


def test_a_budget_stop_cuts_a_tournament_off_and_resuming_finishes_it(tmp_path: Path) -> None:
    agents = custom_seats(tmp_path)
    body = f'{agents}seats = ["costly", "tag", "station"]\ntournaments = 2\nlevel_hands = 5'
    path = write_experiment(tmp_path, f"{body}\nbudget_usd = 0.3")
    outcome = run_experiment(path, tmp_path / "out", log=quiet)
    assert outcome.stopped == "budget" and outcome.tournaments == 0
    report = (outcome.folder / "leaderboard.md").read_text()
    assert "reached its budget" in report
    assert "Cut off: tournament 1 was partway through" in report
    assert "No tournament is over yet" in report

    path.write_text(path.read_text().replace("budget_usd = 0.3", "budget_usd = 100"))
    resumed = run_experiment(path, tmp_path / "out", resume=True, log=quiet)
    assert resumed.stopped == "finished" and resumed.tournaments == 2
    unbroken = run_experiment(path, tmp_path / "unbroken", log=quiet)
    assert [as_written(h) for h in hands_of(resumed.folder)] == [
        as_written(h) for h in hands_of(unbroken.folder)
    ]
    assert resumed.spent_usd == pytest.approx(unbroken.spent_usd)
    assert "Cut off" not in (resumed.folder / "leaderboard.md").read_text()


def test_ctrl_c_partway_keeps_the_hands_and_says_which_tournament_was_cut_off(
    tmp_path: Path, capsys
) -> None:
    agents = custom_seats(tmp_path)
    body = f'{agents}seats = ["interrupter", "tag", "station"]\ntournaments = 3\nlevel_hands = 5'
    code = main(["experiment", "run", str(write_experiment(tmp_path, body)), "-o", str(tmp_path)])
    assert code == 130
    run = json.loads((tmp_path / "fz" / "run.json").read_text())
    assert run["stopped"] == "interrupted" and run["hands"] == len(hands_of(tmp_path / "fz"))
    assert "Cut off: tournament" in (tmp_path / "fz" / "leaderboard.md").read_text()
    assert "interrupted" in capsys.readouterr().out


def test_a_tournament_at_the_hand_limit_counts_by_chips_and_says_it_is_unfinished(tmp_path):
    path = write_experiment(tmp_path, f"seats = {BOTS}\ntournaments = 2\nmax_hands = 6")
    outcome = run_experiment(path, tmp_path / "out", log=quiet)
    assert outcome.stopped == "finished" and outcome.tournaments == 2
    hands = hands_of(outcome.folder)
    assert len(hands) == 12
    report = (outcome.folder / "leaderboard.md").read_text()
    assert "Unfinished: tournament 1 and 2 reached the 6-hand limit" in report
    for standing in standings_of(hands):
        chips = dict(standing.left)
        places = standing.places()
        ranked = sorted(chips, key=lambda name: places[name])
        assert [chips[name] for name in ranked] == sorted(chips.values(), reverse=True)


# ----- the report --------------------------------------------------------------------------


def test_the_report_ranks_by_finish_with_intervals_and_rebuilds_the_same(tmp_path) -> None:
    path = write_experiment(tmp_path, f"seats = {BOTS}\ntournaments = 12\nlevel_hands = 5\nseed=1")
    outcome = run_experiment(path, tmp_path / "out", log=quiet)
    report = outcome.folder / "leaderboard.md"
    text = report.read_text()
    assert text.startswith("# fz\n") and "12 freezeouts of 5 seats with 200 chips each" in text
    assert "| average finish | ±95% | wins | share won | 95% interval |" in text
    assert "bb/100" not in text and "all-in adj." not in text
    assert "chance is an average finish of 3.00 and 20% of the wins" in text
    assert "t interval" in text and "Wilson" in text
    assert "Better than chance:" in text and "Too close to" in text

    # the places in the report are the ones the tournaments gave, averaged
    hands = hands_of(outcome.folder)
    places: dict[str, list[int]] = {}
    for standing in standings_of(hands):
        for name, place in standing.places().items():
            places.setdefault(name, []).append(place)
    best = min(places, key=lambda name: sum(places[name]))
    row = next(line for line in text.splitlines() if line.startswith(f"| {best} |"))
    assert f"| {sum(places[best]) / 12:.2f} |" in row

    report.unlink()
    assert main(["experiment", "report", str(outcome.folder)]) == 0
    assert report.read_text() == text


def test_the_intervals_are_a_t_interval_and_a_wilson_score_interval() -> None:
    # t(0.975, 4 degrees of freedom) is 2.776; the sample standard deviation of 1..5 is 1.581
    assert t_margin([1, 2, 3, 4, 5]) == pytest.approx(2.776 * 1.5811 / 5**0.5, abs=1e-3)
    assert t_margin([2]) is None
    low, high = wilson(0, 10)
    assert low == 0 and high == pytest.approx(0.2775, abs=1e-3)  # never zero wide at 0 of 10
    assert wilson(5, 10) == pytest.approx((0.2366, 0.7634), abs=1e-3)


def test_a_few_tournaments_call_nobody_and_overlapping_neighbours_are_too_close() -> None:
    assert finish_calls([Finish("a", (1, 2)), Finish("b", (2, 1))], 2) == [
        "Too few tournaments to call anyone: that takes at least 10."
    ]
    sure = Finish("sure", (1,) * 9 + (2,))
    near = Finish("near", (1, 2) * 5)
    far = Finish("far", (3,) * 9 + (2,))
    lines = finish_calls([sure, near, far], 3)
    assert lines == [
        "Better than chance: sure, near.",
        "Worse than chance: far.",
        "Too close to call against the seat below: sure and near.",
    ]


# ----- the command -------------------------------------------------------------------------


def test_hands_means_cash_hands_and_is_refused_for_a_file_of_tournaments(tmp_path, capsys):
    path = write_experiment(tmp_path, QUICK)
    out = tmp_path / "out"
    assert main(["experiment", "run", str(path), "-o", str(out), "--hands", "5"]) != 0
    assert "change tournaments = N" in capsys.readouterr().err
    assert not out.exists()


def test_a_trial_plays_whole_tournaments_then_says_what_the_rest_would_take(tmp_path, capsys):
    path = write_experiment(tmp_path, QUICK)
    out = tmp_path / "out"
    assert main(["experiment", "run", str(path), "-o", str(out), "--trial", "1"]) == 0
    printed = capsys.readouterr().out
    assert "1 of 3 tournaments took" in printed and "for free" in printed
    assert f"To carry on: poker-table experiment run {path} --resume" in printed
    trial = hands_of(out / "fz")
    assert {h.tournament["id"] for h in trial} == {1}
    assert standings_of(trial)[0].complete

    assert main(["experiment", "run", str(path), "-o", str(out), "--resume"]) == 0
    whole = run_experiment(path, tmp_path / "whole", log=quiet)
    assert [as_written(h) for h in hands_of(out / "fz")] == [
        as_written(h) for h in hands_of(whole.folder)
    ]


def test_resuming_refuses_other_tournaments(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, QUICK)
    run_experiment(path, tmp_path / "out", tournaments=1, log=quiet)
    path.write_text(path.read_text().replace("level_hands = 5", "level_hands = 6"))
    with pytest.raises(ValueError, match="other tournaments"):
        run_experiment(path, tmp_path / "out", resume=True, log=quiet)
    # more tournaments is the same run, carried on
    path.write_text(path.read_text().replace("level_hands = 6", "level_hands = 5"))
    more = run_experiment(path, tmp_path / "out", resume=True, log=quiet)
    assert more.tournaments == 3


def test_a_model_seat_gets_its_notes_back_by_name_as_seats_go_out() -> None:
    nerd = make_agents(["llm:nerd@ollama/none"])[0]
    assert isinstance(nerd, LLMAgent)
    bots = make_agents(["tag", "station", "rock"], seed=2)
    hand = run_tournament(bots, replace_config()).histories[-1]
    # rename the last seat of a late hand to the model seat, and give it a note to restore
    seat = len(hand.players) - 1
    players = [replace(p, name="nerd") if p.seat == seat else p for p in hand.players]
    note = DecisionTrace(seat, "preflop", "fold", "fold", False, "", "", 1.0, {"note": "shoves"})
    history = replace(hand, players=players, decisions=[note])
    _restore_notes([*bots, nerd], [history])
    assert list(nerd.notes) == [f"hand {history.hand_id}: shoves"]


def replace_config():
    from poker_table.league import TournamentConfig

    return replace(TournamentConfig(seed=2), hands_per_level=5)
