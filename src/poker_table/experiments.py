"""A league described in a file: one command, one results directory, nothing to remember.

    name = "four-personalities"
    seats = ["llm:nerd", "llm:maniac", "tag", "station"]
    hands = 200
    seed = 2026
    budget_usd = 5.0
    tables = 4

``poker-table experiment run FILE`` plays it and writes ``results/<name>/``:

    experiment.toml   the file it was run from
    hands.jsonl       every hand, with every seat's reasoning
    run.json          what ran, when, for how long, and why it stopped
    leaderboard.md    who won, with error bars, and what it cost
    coach/<seat>.md   the coach's report on each model seat

A stopped run keeps every finished hand, and ``--resume`` carries on with the same decks. The
report is built from those files alone, so ``experiment report DIR`` can rebuild it at any time.

Running an experiment file can run code: its ``agents`` may name a file with a ``python`` kind
(see ``agents/custom.py``), so only run files you trust. Rebuilding a report never loads agents
and never writes through a link in the folder, so that part is safe on someone else's results.
"""

from __future__ import annotations

import json
import os
import random
import re
import statistics
import subprocess
import threading
import time
import tomllib
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from poker_table import __version__
from poker_table.agents.base import Agent, TableShape, seating_notes
from poker_table.agents.compat import is_priced
from poker_table.agents.custom import load_agent_files
from poker_table.agents.human import HumanAgent
from poker_table.agents.llm import LLMAgent
from poker_table.agents.ollama import is_local
from poker_table.agents.registry import check_seats_ready, make_agents
from poker_table.cards import Card
from poker_table.history import HandHistory, read_jsonl, write_jsonl
from poker_table.honesty import honesty
from poker_table.league import (
    DEFAULT_SCHEDULE,
    LeagueConfig,
    Level,
    Standing,
    TournamentConfig,
    parse_blinds,
    parse_levels,
    run_league,
    run_tournament,
    show_hand,
    spent_usd,
    standings_of,
)
from poker_table.margins import CAVEAT, Finish, all_in_note, calls, finish_calls, seat_margins
from poker_table.stats import compute_stats, format_table

# A seat failing this often is not being measured: it is folding on its fallback.
MAX_FAILURES = 10

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # a folder name: never "..", never a path
_REQUIRED = object()
_KEYS = frozenset(
    "name description seats hands seed blinds ante stack budget_usd tables agents coach "
    "tournaments levels level_hands max_hands".split()
)
_CASH_KEYS = ("hands", "blinds", "ante")
_TOURNAMENT_KEYS = ("levels", "level_hands", "max_hands")


@dataclass(frozen=True, slots=True)
class Experiment:
    name: str
    seats: tuple[str, ...]
    hands: int
    description: str = ""
    seed: int = 0
    small_blind: int = 1
    big_blind: int = 2
    ante: int = 0
    stack: int = 200
    budget_usd: float | None = None
    tables: int = 1
    agents: tuple[Path, ...] = ()
    coach: bool = True
    # Freezeouts to play instead of cash hands (hands is then 0). Each is seeded on its own.
    tournaments: int = 0
    levels: tuple[Level, ...] = DEFAULT_SCHEDULE
    level_hands: int = 20
    max_hands: int = 5000  # a tournament still going after this many hands ranks by chips

    @property
    def game(self) -> tuple[Any, ...]:
        """What must match for two runs to be the same league dealt from the same decks."""
        if self.tournaments:  # not their number: raising it and resuming plays more
            game = (self.seats, self.seed, self.stack, self.levels, self.level_hands)
            return ("freezeout", *game, self.max_hands)
        return (
            self.seats,
            self.seed,
            self.small_blind,
            self.big_blind,
            self.ante,
            self.stack,
            self.tables,
        )

    def shape(self, seats: int) -> TableShape:
        """The first hand's table, as a seat built for one kind of table needs to know it."""
        if self.tournaments:
            first = self.levels[0]
            return TableShape(seats, first.small_blind, first.big_blind, first.ante, self.stack)
        return TableShape(seats, self.small_blind, self.big_blind, self.ante, self.stack)

    # ----- freezeouts: tournament ``number`` counts from 1 ------------------------------------

    def tournament_seed(self, number: int) -> int:
        """Each tournament has its own seed for its decks and its bots, so it repeats alone."""
        return random.Random(f"{self.seed}/tournament/{number}").randrange(2**63)

    def seating(self, number: int) -> list[int]:
        """The seats of a tournament as drawn for it: which of ``seats`` sits where, the first
        one on the button. A fixed order would give every tournament the same seat bias."""
        order = list(range(len(self.seats)))
        random.Random(f"{self.seed}/seats/{number}").shuffle(order)
        return order

    def tournament_config(self, number: int) -> TournamentConfig:
        return TournamentConfig(
            starting_stack=self.stack,
            schedule=self.levels,
            hands_per_level=self.level_hands,
            max_hands=self.max_hands,
            seed=self.tournament_seed(number),
            hand_prefix=f"f{number}-",
            number=number,
        )

    def tournament_of(self, history: HandHistory) -> int:
        return int(history.tournament["id"])

    def hands_at(self, table: int) -> int:
        share, extra = divmod(self.hands, self.tables)
        return share + (1 if table < extra else 0)

    def table_seed(self, table: int) -> int:
        """A single table deals what ``poker-table play --seed`` deals; more get their own decks."""
        if self.tables == 1:
            return self.seed
        return random.Random(f"{self.seed}/{table}").randrange(2**63)

    def hand_prefix(self, table: int) -> str:
        return "" if self.tables == 1 else f"t{table + 1}-"

    def table_of(self, history: HandHistory) -> int:
        return 0 if self.tables == 1 else int(history.hand_id.partition("-")[0][1:]) - 1


def _take(
    data: dict[str, Any],
    key: str,
    kinds: type | tuple[type, ...],
    where: Path,
    default: Any = _REQUIRED,
) -> Any:
    if key not in data:
        if default is _REQUIRED:
            raise ValueError(f"{where}: {key} is required")
        return default
    value = data[key]
    # TOML's true is a Python int, and hands = true should not mean one hand
    if not isinstance(value, kinds) or (isinstance(value, bool) and kinds is not bool):
        raise ValueError(f"{where}: {key} has the wrong type: {value!r}")
    return value


def load_experiment(path: Path | str) -> Experiment:
    """Read and check an experiment file. Anything it does not know is an error, because a
    misspelt ``budget_usd`` should not quietly run all night with no limit."""
    path = Path(path)
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    unknown = set(data) - _KEYS
    if unknown:
        raise ValueError(f"{path}: unknown key(s): {', '.join(sorted(unknown))}")

    name = _take(data, "name", str, path)
    if not _NAME.fullmatch(name):
        raise ValueError(
            f"{path}: name becomes a folder, so start it with a letter or digit "
            "and keep the rest to letters, digits, . _ -"
        )
    seats = _take(data, "seats", list, path)
    if len(seats) < 2 or not all(isinstance(seat, str) for seat in seats):
        raise ValueError(f"{path}: seats needs at least two kinds, as strings")
    budget = _take(data, "budget_usd", (int, float), path, None)
    if budget is not None and budget <= 0:
        raise ValueError(f"{path}: budget_usd must be above zero")
    files = _take(data, "agents", list, path, [])
    experiment = Experiment(
        name=name,
        seats=tuple(seats),
        hands=0,
        description=_take(data, "description", str, path, ""),
        seed=_take(data, "seed", int, path, 0),
        stack=_take(data, "stack", int, path, 200),
        budget_usd=float(budget) if budget is not None else None,
        agents=tuple((path.parent / str(file)).resolve() for file in files),
        coach=_take(data, "coach", bool, path, True),
    )
    if "tournaments" in data:
        return _freezeouts(experiment, data, path)
    extra = [key for key in _TOURNAMENT_KEYS if key in data]
    if extra:
        raise ValueError(f"{path}: {extra[0]} is for tournaments: add tournaments = N, or drop it")
    if "hands" not in data:
        raise ValueError(f"{path}: hands is required, or tournaments for a run of freezeouts")
    hands = _take(data, "hands", int, path)
    tables = _take(data, "tables", int, path, 1)
    if hands < 1 or tables < 1 or tables > hands:
        raise ValueError(f"{path}: needs at least one hand, one table, and a hand for each table")
    small, big = parse_blinds(_take(data, "blinds", str, path, "1/2"))
    return replace(
        experiment,
        hands=hands,
        small_blind=small,
        big_blind=big,
        ante=_take(data, "ante", int, path, 0),
        tables=tables,
    )


def _freezeouts(experiment: Experiment, data: dict[str, Any], path: Path) -> Experiment:
    """The settings of a file that plays tournaments instead of cash hands."""
    cash = [key for key in _CASH_KEYS if key in data]
    if "hands" in cash:
        raise ValueError(
            f"{path}: set hands for a cash league or tournaments for freezeouts, not both"
        )
    if cash:
        raise ValueError(
            f"{path}: a tournament takes its blinds and antes from levels: drop {cash[0]}"
        )
    if _take(data, "tables", int, path, 1) != 1:
        raise ValueError(
            f"{path}: tournaments play on one table, one after another: drop tables "
            "(docs/experiments.md says why)"
        )
    tournaments = _take(data, "tournaments", int, path)
    level_hands = _take(data, "level_hands", int, path, 20)
    max_hands = _take(data, "max_hands", int, path, 5000)
    if min(tournaments, level_hands, max_hands, experiment.stack) < 1:
        raise ValueError(f"{path}: tournaments, level_hands, max_hands and stack must be 1 or more")
    levels = DEFAULT_SCHEDULE
    if "levels" in data:
        try:
            levels = parse_levels(_take(data, "levels", str, path))
        except ValueError as exc:
            raise ValueError(f"{path}: levels: {exc}") from None
    return replace(
        experiment,
        tournaments=tournaments,
        levels=levels,
        level_hands=level_hands,
        max_hands=max_hands,
    )


def _check_seats(
    experiment: Experiment, agents: Sequence[Agent], log: Callable[[str], None] = print
) -> None:
    """Refuse anything that would waste a night or spend money without a limit."""
    for agent in agents:
        if isinstance(agent, HumanAgent):
            raise ValueError(f"seat {agent.name!r} is a human, and a results run has nobody at it")
    paid = [a for a in agents if isinstance(a, LLMAgent) and not is_local(a.model)]
    priced = [a for a in paid if is_priced(a.model)]
    if priced and experiment.budget_usd is None:
        raise ValueError(
            f"seat {priced[0].name!r} calls {priced[0].model}, which costs money: "
            "set budget_usd so the run has a limit"
        )
    for agent in paid:
        if not is_priced(agent.model):
            log(
                f"{agent.name} on {agent.model}: this table does not know that provider's prices, "
                "so its cost reads $0 and budget_usd cannot limit it."
            )
    shape = experiment.shape(len(agents))
    check_seats_ready(agents, shape)
    for note in seating_notes(agents, shape):
        log(note)


def _failing(agent: Agent) -> bool:
    usage = getattr(agent, "usage", None)
    errors = getattr(usage, "errors", 0)
    return errors >= MAX_FAILURES and errors * 2 >= getattr(usage, "calls", 0)


def _catch_up(agents: Sequence[Agent], earlier: Iterable[HandHistory]) -> None:
    """Show a resumed table's seats what they saw before, so they keep their reads and notes."""
    earlier = list(earlier)
    for history in earlier:
        show_hand(agents, history)
    _restore_notes(agents, earlier)


def _restore_notes(agents: Sequence[Agent], earlier: Iterable[HandHistory]) -> None:
    """Give a resumed model seat back the notes it wrote. Seats are found by name, because in a
    freezeout a seat's number in a hand changes as the seats before it go out."""
    by_name = {agent.name: agent for agent in agents}
    for history in earlier:
        for decision in history.decisions:
            note = decision.meta.get("note")
            agent = by_name.get(history.players[decision.seat].name)
            if note and isinstance(agent, LLMAgent):
                agent.notes.append(f"hand {history.hand_id}: {note}")


def _cost_of(histories: Iterable[HandHistory]) -> float:
    return sum(float(d.meta.get("cost_usd", 0.0)) for h in histories for d in h.decisions)


def _commit() -> str:
    """The commit a run came from, when it runs from a checkout rather than an install."""
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return ""
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", "src"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{head}+changes" if dirty else head


@dataclass(frozen=True, slots=True)
class Outcome:
    folder: Path
    hands: int
    planned: int
    stopped: str  # finished | budget | errors | interrupted
    spent_usd: float
    seconds: float
    tournaments: int | None = None  # a freezeout run: how many are over (planned counts these)


def run_experiment(
    path: Path | str,
    out: Path | str = "results",
    *,
    hands: int | None = None,
    tournaments: int | None = None,
    resume: bool = False,
    log: Callable[[str], None] = print,
) -> Outcome:
    """Play the experiment in ``path`` into ``out/<name>/`` and write its report.

    ``hands`` replaces a cash league's hands, and ``tournaments`` a freezeout file's count."""
    path = Path(path)
    experiment = load_experiment(path)
    if experiment.tournaments:
        if hands is not None:
            raise ValueError(
                "--hands counts cash hands, and this file plays tournaments: "
                "change tournaments = N in it instead"
            )
        if tournaments is not None:
            experiment = replace(experiment, tournaments=tournaments)
        return _run_tournaments(path, experiment, out, resume, log)
    if tournaments is not None:
        raise ValueError(f"{path} plays a cash league, not tournaments")
    if hands is not None:
        experiment = replace(experiment, hands=hands)
        if hands < experiment.tables:
            raise ValueError(f"--hands {hands} leaves a table with no hands")
    custom = load_agent_files(experiment.agents)
    tables = [
        make_agents(experiment.seats, seed=experiment.table_seed(table), custom=custom)
        for table in range(experiment.tables)
    ]
    _check_seats(experiment, tables[0], log)
    folder, hands_file, earlier, previous_seconds = _open_folder(path, experiment, out, resume)

    by_table: dict[int, list[HandHistory]] = defaultdict(list)
    for history in earlier:
        by_table[experiment.table_of(history)].append(history)
    for table, agents in enumerate(tables):
        _catch_up(agents, by_table[table])

    everyone = [agent for agents in tables for agent in agents]
    earlier_cost = _cost_of(earlier)
    started_at = datetime.now(UTC)
    started = time.monotonic()
    lock = threading.Lock()
    stopping = threading.Event()
    stopped = "finished"
    played = len(earlier)
    every = max(1, experiment.hands // 20)
    commit = _commit()

    def should_stop() -> bool:
        nonlocal stopped
        budget = experiment.budget_usd
        if stopping.is_set():
            return True
        if budget is not None and earlier_cost + spent_usd(everyone) >= budget:
            stopped = "budget"
            stopping.set()
        elif any(_failing(agent) for agent in everyone):
            stopped = "errors"
            stopping.set()
        return stopping.is_set()

    def seconds_so_far() -> float:
        return previous_seconds + time.monotonic() - started

    def save_run(state: str) -> None:
        _write_json(
            folder / "run.json",
            {
                "name": experiment.name,
                "planned_hands": experiment.hands,
                "hands": played,
                "stopped": state,
                "tables": experiment.tables,
                "seconds": round(seconds_so_far(), 1),
                "spent_usd": round(earlier_cost + spent_usd(everyone), 6),
                "last_started": started_at.isoformat(timespec="seconds"),
                "last_saved": datetime.now(UTC).isoformat(timespec="seconds"),
                "version": __version__,
                "commit": commit,
            },
        )

    def on_hand(history: HandHistory) -> None:
        nonlocal played
        with lock:
            write_jsonl(hands_file, [history], append=True)
            played += 1
            if played % every == 0:
                save_run(
                    "running"
                )  # so a run that is killed outright still knows its time and cost
                minutes = (time.monotonic() - started) / 60
                cost = earlier_cost + spent_usd(everyone)
                log(f"... {played}/{experiment.hands} hands, ${cost:.4f}, {minutes:.1f} min")

    def play_table(table: int) -> None:
        config = LeagueConfig(
            hands=experiment.hands_at(table),
            small_blind=experiment.small_blind,
            big_blind=experiment.big_blind,
            ante=experiment.ante,
            buy_in=experiment.stack,
            seed=experiment.table_seed(table),
            first_hand=len(by_table[table]),
            hand_prefix=experiment.hand_prefix(table),
        )
        run_league(tables[table], config, on_hand=on_hand, stop=should_stop, keep_histories=False)

    crash: Exception | None = None
    if not should_stop():  # a resumed run may already be at its budget
        with ThreadPoolExecutor(max_workers=experiment.tables) as pool:
            futures = [pool.submit(play_table, table) for table in range(experiment.tables)]
            try:
                for future in futures:
                    future.result()
            except KeyboardInterrupt:
                stopped = "interrupted"
                stopping.set()
                log("stopping: finishing the hands in progress, then writing the report")
            except Exception as exc:  # noqa: BLE001 - keep what was played, then raise it
                crash = exc
                stopped = "errors"
                stopping.set()

    if played >= experiment.hands:
        stopped = "finished"
    seconds = seconds_so_far()
    spent = earlier_cost + spent_usd(everyone)
    save_run(stopped)
    if played:
        write_report(folder)
    if crash is not None:
        raise RuntimeError(f"a table failed after {played} hands: {crash}") from crash
    return Outcome(folder, played, experiment.hands, stopped, spent, seconds)


def _open_folder(
    path: Path, experiment: Experiment, out: Path | str, resume: bool
) -> tuple[Path, Path, list[HandHistory], float]:
    """The results folder, and the hands and seconds a resumed run already has. It will not
    write through a link, add to a folder without ``resume``, or resume another league."""
    folder = Path(out) / experiment.name
    hands_file = folder / "hands.jsonl"
    earlier: list[HandHistory] = []
    previous_seconds = 0.0
    if hands_file.is_symlink():
        raise ValueError(f"{hands_file} is a link, and hands are only written to a plain file")
    if hands_file.exists() and hands_file.stat().st_size > 0:
        if not resume:
            raise ValueError(
                f"{folder} already holds hands: pass --resume to carry on, "
                "or remove the folder to start again"
            )
        saved = load_experiment(folder / "experiment.toml")
        if saved.game != experiment.game:
            other = "other tournaments" if experiment.tournaments else "a different league"
            raise ValueError(f"{folder} was played with {other}, so it cannot resume")
        earlier = list(read_jsonl(hands_file))
        previous_seconds = _read_run(folder).get("seconds", 0.0)
    folder.mkdir(parents=True, exist_ok=True)
    if path.resolve() != (folder / "experiment.toml").resolve():
        _write_text(folder / "experiment.toml", path.read_text(encoding="utf-8"))
    return folder, hands_file, earlier, previous_seconds


def _over(experiment: Experiment, hands: Sequence[HandHistory]) -> bool:
    """Nothing is left to play in a tournament: one seat has every chip, or the limit is up."""
    if not hands:
        return False
    last = hands[-1].tournament
    return last["left"] - len(last["out"]) <= 1 or len(hands) >= experiment.max_hands


def _run_tournaments(
    path: Path, experiment: Experiment, out: Path | str, resume: bool, log: Callable[[str], None]
) -> Outcome:
    """Play the file's freezeouts one after another, each with its own seats and decks.

    Fresh seats for each tournament keep each one repeatable on its own, and the tournaments
    independent, which is what the intervals in the report assume. A model seat's reads and
    notes therefore last one tournament.
    """
    custom = load_agent_files(experiment.agents)

    def seats_for(number: int) -> list[Agent]:
        seed = experiment.tournament_seed(number)
        agents = make_agents(experiment.seats, seed=seed, custom=custom)
        return [agents[i] for i in experiment.seating(number)]

    first = seats_for(1)
    _check_seats(experiment, first, log)
    folder, hands_file, earlier, previous_seconds = _open_folder(path, experiment, out, resume)
    by_number: dict[int, list[HandHistory]] = defaultdict(list)
    for history in earlier:
        by_number[experiment.tournament_of(history)].append(history)

    everyone: list[Agent] = list(first)  # every seat built so far, for the budget
    earlier_cost = _cost_of(earlier)
    started_at = datetime.now(UTC)
    started = time.monotonic()
    lock = threading.Lock()
    stopping = threading.Event()
    stopped = "finished"
    played = len(earlier)
    over = sum(_over(experiment, hands) for hands in by_number.values())
    every = max(1, experiment.tournaments // 20)
    commit = _commit()

    def should_stop() -> bool:
        nonlocal stopped
        budget = experiment.budget_usd
        if stopping.is_set():
            return True
        if budget is not None and earlier_cost + spent_usd(everyone) >= budget:
            stopped = "budget"
            stopping.set()
        elif any(_failing(agent) for agent in everyone):
            stopped = "errors"
            stopping.set()
        return stopping.is_set()

    def seconds_so_far() -> float:
        return previous_seconds + time.monotonic() - started

    def save_run(state: str) -> None:
        _write_json(
            folder / "run.json",
            {
                "name": experiment.name,
                "planned_tournaments": experiment.tournaments,
                "tournaments": over,
                "hands": played,
                "stopped": state,
                "tables": 1,
                "seconds": round(seconds_so_far(), 1),
                "spent_usd": round(earlier_cost + spent_usd(everyone), 6),
                "last_started": started_at.isoformat(timespec="seconds"),
                "last_saved": datetime.now(UTC).isoformat(timespec="seconds"),
                "version": __version__,
                "commit": commit,
            },
        )

    def on_hand(history: HandHistory) -> None:
        nonlocal played
        with lock:
            write_jsonl(hands_file, [history], append=True)
            played += 1
            if played % 20 == 0:
                save_run("running")  # so a run that is killed outright still knows its time

    def play_all() -> None:
        nonlocal over
        for number in range(1, experiment.tournaments + 1):
            so_far = by_number[number]
            if _over(experiment, so_far):
                continue
            if should_stop():
                return
            seats = first if number == 1 else seats_for(number)
            if seats is not first:
                everyone.extend(seats)
            _restore_notes(seats, so_far)  # replaying the hands shows them to the seats again
            result = run_tournament(
                seats,
                experiment.tournament_config(number),
                resume_from=so_far,
                on_hand=on_hand,
                keep_histories=False,
                stop=should_stop,
            )
            for name, hand_id in result.differed.items():
                log(
                    f"note: {name} did not decide as before in hand {hand_id}, so the rest of "
                    f"tournament {number} need not repeat a run that was never stopped"
                )
            if not (result.complete or result.hands >= experiment.max_hands):
                return  # stopped partway: --resume finishes it
            with lock:
                over += 1
                save_run("running")
            if over % every == 0:
                minutes = (time.monotonic() - started) / 60
                cost = earlier_cost + spent_usd(everyone)
                log(
                    f"... {over}/{experiment.tournaments} tournaments, {played} hands, "
                    f"${cost:.4f}, {minutes:.1f} min"
                )

    crash: Exception | None = None
    if not should_stop():  # a resumed run may already be at its budget
        # In a thread of its own, as the cash tables are, so that Ctrl-C lands between hands
        # and never halfway through writing one.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(play_all)
            try:
                future.result()
            except KeyboardInterrupt:
                stopped = "interrupted"
                stopping.set()
                log("stopping: finishing the hand in progress, then writing the report")
            except Exception as exc:  # noqa: BLE001 - keep what was played, then raise it
                crash = exc
                stopped = "errors"
                stopping.set()

    if over >= experiment.tournaments:
        stopped = "finished"
    seconds = seconds_so_far()
    spent = earlier_cost + spent_usd(everyone)
    save_run(stopped)
    if played:
        write_report(folder)
    if crash is not None:
        raise RuntimeError(f"a tournament failed after {played} hands: {crash}") from crash
    return Outcome(
        folder, played, experiment.tournaments, stopped, spent, seconds, tournaments=over
    )


@dataclass(frozen=True, slots=True)
class Projection:
    """What the rest of a run should take, going by the hands played so far."""

    hands_done: int
    hands_left: int
    spent_usd: float
    seconds: float
    budget_usd: float | None
    priced: bool = True  # False when a seat's provider does not say what it charges
    unit: str = "hands"  # or "tournaments": what was counted, for a run of freezeouts

    @property
    def seconds_left(self) -> float:
        return self.seconds / self.hands_done * self.hands_left

    @property
    def cost_left_usd(self) -> float:
        return self.spent_usd / self.hands_done * self.hands_left

    @property
    def total_usd(self) -> float:
        return self.spent_usd + self.cost_left_usd

    @property
    def stops_after(self) -> int | None:
        """Roughly where the budget would end the run, if it would."""
        if self.budget_usd is None or self.total_usd <= self.budget_usd:
            return None
        return int(self.budget_usd / (self.spent_usd / self.hands_done))

    def describe(self) -> str:
        planned = self.hands_done + self.hands_left
        trial = f"{self.hands_done} of {planned} {self.unit} took {_duration(self.seconds)}"
        rest = f"The rest should take about {_duration(self.seconds_left)}"
        if not self.priced:
            return (
                f"{trial}. {rest}. This table does not know what these models cost, "
                "so check your provider's account."
            )
        if self.spent_usd == 0:
            return f"{trial}, for free. {rest}, for free."
        lines = [
            f"{trial} and cost ${self.spent_usd:.4f}. {rest} and cost about "
            f"${self.cost_left_usd:.2f}, so about ${self.total_usd:.2f} in all.",
            f"That is a rough guess from a few {self.unit}, and it tends to run high, because a "
            "seat's first calls cost the most.",
        ]
        if self.stops_after is not None:
            lines.append(
                f"It is over your budget of ${self.budget_usd:.2f}, so the run would stop after "
                f"about {self.stops_after} {self.unit}."
            )
        return "\n".join(lines)


def unpriced_models(folder: Path) -> list[str]:
    """Models in a results folder that this table cannot put a price on."""
    models = {
        str(decision.meta["model"])
        for history in read_jsonl(folder / "hands.jsonl")
        for decision in history.decisions
        if decision.meta.get("model")
    }
    return sorted(model for model in models if not is_priced(model))


def failed_calls(folder: Path) -> int:
    """Model calls that could not be used in a results folder so far."""
    histories = read_jsonl(folder / "hands.jsonl")
    return sum(bool(d.meta.get("failed")) for history in histories for d in history.decisions)


def _write_text(path: Path, text: str) -> None:
    """Write a file in a results folder without following a link planted there: the folder may
    have come from someone else, and leaderboard.md could point at ~/.bashrc. Replacing a link
    swaps the link itself, and leaves what it pointed at alone."""
    if path.parent.is_symlink():
        raise ValueError(f"{path.parent} is a link, and reports are only written to a plain folder")
    path.parent.mkdir(exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    with temporary.open("x", encoding="utf-8") as fh:  # "x" will not open a link left in the way
        fh.write(text)
    os.replace(temporary, path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, indent=1) + "\n")


def _read_run(folder: Path) -> dict[str, Any]:
    run = folder / "run.json"
    return json.loads(run.read_text(encoding="utf-8")) if run.exists() else {}


# ----- the report --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeatResult:
    name: str
    plays_as: str
    hands: int
    net: int
    bb_per_100: float
    margin: float | None
    adjusted_bb_per_100: float
    adjusted_margin: float | None
    decisions: int
    model_calls: int
    seconds_per_call: float | None
    cost_usd: float
    illegal: int
    failed: int

    clear_of_zero: bool


@dataclass(frozen=True, slots=True)
class SeatUsage:
    """What a seat is and what it cost: the columns a cash and a freezeout report share."""

    plays_as: str
    decisions: int
    model_calls: int
    seconds_per_call: float | None
    cost_usd: float
    illegal: int
    failed: int


def seat_usage(histories: Sequence[HandHistory]) -> dict[str, SeatUsage]:
    kinds: dict[str, str] = {}
    models: dict[str, str] = {}
    latencies: dict[str, list[float]] = defaultdict(list)
    failed: dict[str, int] = defaultdict(int)
    for history in histories:
        names = {p.seat: p.name for p in history.players}
        for player in history.players:
            kinds.setdefault(player.name, player.kind)
        for decision in history.decisions:
            name = names[decision.seat]
            if decision.meta.get("model"):
                models.setdefault(name, str(decision.meta["model"]))
            if "cost_usd" in decision.meta:
                latencies[name].append(decision.latency_ms / 1000)
            failed[name] += bool(decision.meta.get("failed"))
    usage = {}
    for name, stats in compute_stats(histories).items():
        model = models.get(name)
        usage[name] = SeatUsage(
            plays_as=f"{kinds[name]} on {model}" if model else kinds[name],
            decisions=stats.decisions,
            model_calls=stats.model_calls,
            seconds_per_call=(statistics.fmean(latencies[name]) if latencies[name] else None),
            cost_usd=stats.cost_usd,
            illegal=stats.illegal,
            failed=failed[name],
        )
    return usage


def seat_results(histories: Sequence[HandHistory]) -> list[SeatResult]:
    """One row per seat, best first. Hands are pooled across tables."""
    margins = seat_margins(histories)
    usage = seat_usage(histories)
    rows = []
    for name, stats in compute_stats(histories).items():
        seat = usage[name]
        rows.append(
            SeatResult(
                name=name,
                plays_as=seat.plays_as,
                hands=stats.hands,
                net=stats.net,
                bb_per_100=stats.bb_per_100 or 0.0,
                margin=margins[name].margin,
                adjusted_bb_per_100=margins[name].adjusted_bb_per_100,
                adjusted_margin=margins[name].adjusted_margin,
                clear_of_zero=margins[name].clear_of_zero,
                decisions=seat.decisions,
                model_calls=seat.model_calls,
                seconds_per_call=seat.seconds_per_call,
                cost_usd=seat.cost_usd,
                illegal=seat.illegal,
                failed=seat.failed,
            )
        )
    return sorted(rows, key=lambda row: row.bb_per_100, reverse=True)


def _duration(seconds: float) -> str:
    if seconds < 1.5:
        return "a second or less"
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes} minutes"
    return f"{minutes // 60} h {minutes % 60} min"


_STOPPED = {
    "budget": "It stopped early because it reached its budget.",
    "errors": "It stopped early because a seat kept failing, so read the failed column first.",
    "interrupted": "It was interrupted before the end.",
    "running": "It was cut off before the end, so the time and cost may run a little short.",
}


def render_report(
    experiment: Experiment, histories: Sequence[HandHistory], run: dict[str, Any]
) -> str:
    if experiment.tournaments:
        return render_freezeouts(experiment, histories, run)
    rows = seat_results(histories)
    stakes = f"blinds {experiment.small_blind}/{experiment.big_blind}"
    if experiment.ante:
        stakes += f", ante {experiment.ante}"
    if len(histories) < run.get("planned_hands", len(histories)):
        played = f"{len(histories)} of {run['planned_hands']} hands"
    else:
        played = f"{len(histories)} hands"
    tables = f", on {experiment.tables} tables" if experiment.tables > 1 else ""
    lines = [f"# {experiment.name}", ""]
    if experiment.description:
        lines += [experiment.description, ""]
    lines += [
        f"{played} at {stakes}, {experiment.stack} chips topped up every hand, "
        f"seed {experiment.seed}{tables}. It took {_duration(run.get('seconds', 0.0))} "
        f"and ${run.get('spent_usd', _cost_of(histories)):.4f}.",
    ]
    if run.get("stopped") in _STOPPED:
        lines += ["", _STOPPED[run["stopped"]]]
    lines += [
        "",
        "| seat | plays as | bb/100 | ±95% | all-in adj. | ±95% | net chips | model calls "
        "| s/call | $/hand | illegal | failed |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        margin = f"{row.margin:.0f}" if row.margin is not None else "-"
        adjusted = f"{row.adjusted_margin:.0f}" if row.adjusted_margin is not None else "-"
        seconds = f"{row.seconds_per_call:.1f}" if row.seconds_per_call is not None else "-"
        per_hand = f"{row.cost_usd / row.hands:.4f}" if row.model_calls else "-"
        lines.append(
            f"| {row.name} | {row.plays_as} | {row.bb_per_100:+.0f} | {margin} "
            f"| {row.adjusted_bb_per_100:+.0f} | {adjusted} | {row.net:+d} "
            f"| {row.model_calls} | {seconds} | {per_hand} | {row.illegal} | {row.failed} |"
        )
    unpriced = [
        row.name
        for row in rows
        if " on " in row.plays_as and not is_priced(row.plays_as.rsplit(" on ", 1)[1])
    ]
    if unpriced:
        lines += [
            "",
            f"Cost reads $0 for {', '.join(unpriced)}, because this table does not know that "
            "provider's prices. Check your provider's account.",
        ]
    lines += [
        "",
        all_in_note(histories),
        "",
        *calls(rows),
        "",
        CAVEAT,
        "",
        "## How they played",
        "",
        "```",
        format_table(compute_stats(histories)),
        "```",
    ]
    lines += _what_they_said_they_held(histories)
    coached = [row.name for row in rows if experiment.coach and row.model_calls]
    if coached:
        lines += ["", "## What the coach says", ""]
        lines += [f"- [{name}](coach/{_file_name(name)}.md)" for name in coached]
    return "\n".join(lines) + "\n"


def counted_tournaments(
    experiment: Experiment, histories: Sequence[HandHistory]
) -> tuple[list[Standing], list[Standing]]:
    """The tournaments a report counts, and the ones a stopped run left partway. A tournament
    counts once nothing is left to play in it: it has a winner, or it reached ``max_hands``."""
    over, partway = [], []
    for standing in standings_of(histories):
        done = standing.complete or len(standing.hands) >= experiment.max_hands
        (over if done else partway).append(standing)
    return over, partway


def _numbers(standings: Sequence[Standing]) -> str:
    ids = [str(s.number) for s in standings]
    return ids[0] if len(ids) == 1 else f"{', '.join(ids[:-1])} and {ids[-1]}"


def render_freezeouts(
    experiment: Experiment, histories: Sequence[HandHistory], run: dict[str, Any]
) -> str:
    """The report of a run of freezeouts: places, not chips. bb/100 means little when the big
    blind ends at 1,600, so the measure is where each seat finished, tournament by tournament."""
    over, partway = counted_tournaments(experiment, histories)
    usage = seat_usage(histories)
    places: dict[str, list[int]] = defaultdict(list)
    lasted: dict[str, list[int]] = defaultdict(list)
    for standing in over:
        dealt = standing.hands_dealt()
        for name, place in standing.places().items():
            places[name].append(place)
            lasted[name].append(dealt.get(name, 0))
    finishes = sorted(
        (Finish(name, tuple(seat_places)) for name, seat_places in places.items()),
        key=lambda finish: finish.average,
    )
    planned = run.get("planned_tournaments", experiment.tournaments)
    if len(over) < planned:
        count = f"{len(over)} of {planned} freezeouts"
    else:
        count = f"{len(over)} freezeout{'' if len(over) == 1 else 's'}"
    first, last = experiment.levels[0], experiment.levels[-1]
    levels = f"levels {first} to {last}" if len(experiment.levels) > 1 else f"blinds {first}"
    seats = len(experiment.seats)
    lines = [f"# {experiment.name}", ""]
    if experiment.description:
        lines += [experiment.description, ""]
    lines += [
        f"{count} of {seats} seats with {experiment.stack} chips each, {levels} at "
        f"{experiment.level_hands} hands a level, seed {experiment.seed}, the seats drawn again "
        f"for each one. {len(histories)} hands in all. It took "
        f"{_duration(run.get('seconds', 0.0))} and "
        f"${run.get('spent_usd', _cost_of(histories)):.4f}.",
    ]
    if run.get("stopped") in _STOPPED:
        lines += ["", _STOPPED[run["stopped"]]]
    if not finishes:
        lines += ["", "No tournament is over yet, so there is nothing to rank."]
    else:
        lines += [
            "",
            "| seat | plays as | average finish | ±95% | wins | share won | 95% interval "
            "| hands lasted | model calls | s/call | $/tournament | illegal | failed |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        sat = len(over) + len(partway)
        for finish in finishes:
            seat = usage[finish.name]
            margin = f"{finish.margin:.2f}" if finish.margin is not None else "-"
            low, high = finish.win_interval
            seconds = f"{seat.seconds_per_call:.1f}" if seat.seconds_per_call is not None else "-"
            per = f"{seat.cost_usd / sat:.4f}" if seat.model_calls else "-"
            lines.append(
                f"| {finish.name} | {seat.plays_as} | {finish.average:.2f} | {margin} "
                f"| {finish.wins} | {finish.wins / finish.tournaments:.0%} "
                f"| {low:.0%}–{high:.0%} | {statistics.fmean(lasted[finish.name]):.0f} "
                f"| {seat.model_calls} | {seconds} | {per} | {seat.illegal} | {seat.failed} |"
            )
        lines += [
            "",
            f"With {seats} seats, chance is an average finish of {(seats + 1) / 2:.2f} and "
            f"{1 / seats:.0%} of the wins. Each tournament is one sample, however long it ran: "
            "the average finish has a 95% t interval over the tournaments, and the share won a "
            "95% Wilson score interval. Hands lasted is how many hands a seat was dealt into.",
            "",
            *finish_calls(finishes, seats),
        ]
    limit = [s for s in over if not s.complete]
    if limit:
        lines += [
            "",
            f"Unfinished: tournament {_numbers(limit)} reached the {experiment.max_hands}-hand "
            "limit with more than one seat in. It counts, with the seats still in placed by "
            "their chips.",
        ]
    if partway:
        lines += [
            "",
            f"Cut off: tournament {_numbers(partway)} was partway through when the run stopped. "
            "It is not counted yet, and `--resume` plays it to the end.",
        ]
    unpriced = [
        name
        for name, seat in usage.items()
        if " on " in seat.plays_as and not is_priced(seat.plays_as.rsplit(" on ", 1)[1])
    ]
    if unpriced:
        lines += [
            "",
            f"Cost reads $0 for {', '.join(unpriced)}, because this table does not know that "
            "provider's prices. Check your provider's account.",
        ]
    lines += [
        "",
        "## How they played",
        "",
        "Over every hand of every tournament, so the blinds grow as it goes.",
        "",
        "```",
        format_table(compute_stats(histories), drop=("net", "bb/100")),
        "```",
    ]
    lines += _what_they_said_they_held(histories)
    coached = [name for name, seat in usage.items() if experiment.coach and seat.model_calls]
    if coached:
        lines += ["", "## What the coach says", ""]
        lines += [f"- [{name}](coach/{_file_name(name)}.md)" for name in coached]
    return "\n".join(lines) + "\n"


def _pretty(hole: str) -> str:
    return " ".join(Card.parse(card).pretty.replace("T", "10") for card in hole.split())


def _what_they_said_they_held(histories: Sequence[HandHistory]) -> list[str]:
    """Model seats' reasoning checked against their cards, if any seat reasoned in words."""
    seats = {name: h for name, h in honesty(histories).items() if h.decisions}
    if not seats:
        return []
    lines = [
        "",
        "## What they said they held",
        "",
        "Each model seat's reasoning, checked against the cards it was dealt. Only clear claims "
        "about its own two cards count, so this is a floor.",
        "",
        "| seat | decisions | named its own cards | wrongly |",
        "|---|---:|---:|---:|",
    ]
    lines += [f"| {n} | {h.decisions} | {h.claims} | {h.wrong} |" for n, h in seats.items()]
    examples = [
        f'- {name}, hand {hand}, holding {_pretty(held)}: "{quote}"'
        for name, h in seats.items()
        for hand, held, quote in h.examples
    ]
    return lines + ([""] + examples if examples else [])


def _file_name(seat: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", seat)


def write_report(folder: Path | str) -> Path:
    """Build ``leaderboard.md`` and the coach's reports from what is in ``folder``."""
    from poker_table.coach.facts import tag_hands
    from poker_table.coach.report import build_report

    folder = Path(folder)
    source = folder / "hands.jsonl"
    if not source.exists():
        raise ValueError(f"{folder} has no hands.jsonl")
    histories = list(read_jsonl(source))
    if not histories:
        raise ValueError(f"{source} has no hands in it yet")
    experiment = load_experiment(folder / "experiment.toml")
    run = _read_run(folder)
    report = folder / "leaderboard.md"
    _write_text(report, render_report(experiment, histories, run))
    if experiment.coach:
        for name, stats in compute_stats(histories).items():
            if stats.model_calls:
                facts = tag_hands(histories, name, samples=200)
                text = build_report(histories, name, facts).render(top=5)
                coach = folder / "coach" / f"{_file_name(name)}.md"
                _write_text(coach, f"# {name}\n\n```\n{text}\n```\n")
    return report
