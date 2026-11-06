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
    leaderboard.md    who won, with an error bar, and what it cost
    coach/<seat>.md   the coach's report on each model seat

A stopped run keeps every finished hand, and ``--resume`` carries on with the same decks. The
report is built from those files alone, so ``experiment report DIR`` can rebuild it at any time.

Running an experiment file can run code: its ``agents`` may name a file with a ``python`` kind
(see ``agents/custom.py``), so only run files you trust. Rebuilding a report never loads agents
and never writes through a link in the folder, so that part is safe on someone else's results.
"""

from __future__ import annotations

import json
import math
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
from poker_table.agents.base import Agent
from poker_table.agents.custom import load_agent_files
from poker_table.agents.human import HumanAgent
from poker_table.agents.llm import LLMAgent
from poker_table.agents.ollama import is_local
from poker_table.agents.registry import check_seats_ready, make_agents
from poker_table.history import HandHistory, read_jsonl, write_jsonl
from poker_table.league import LeagueConfig, parse_blinds, run_league, show_hand, spent_usd
from poker_table.stats import compute_stats, format_table

# A seat failing this often is not being measured: it is folding on its fallback.
MAX_FAILURES = 10

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # a folder name: never "..", never a path
_REQUIRED = object()
_KEYS = frozenset(
    "name description seats hands seed blinds ante stack budget_usd tables agents coach".split()
)


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

    @property
    def game(self) -> tuple[Any, ...]:
        """What must match for two runs to be the same league dealt from the same decks."""
        return (
            self.seats,
            self.seed,
            self.small_blind,
            self.big_blind,
            self.ante,
            self.stack,
            self.tables,
        )

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
    hands = _take(data, "hands", int, path)
    tables = _take(data, "tables", int, path, 1)
    if hands < 1 or tables < 1 or tables > hands:
        raise ValueError(f"{path}: needs at least one hand, one table, and a hand for each table")
    small, big = parse_blinds(_take(data, "blinds", str, path, "1/2"))
    budget = _take(data, "budget_usd", (int, float), path, None)
    if budget is not None and budget <= 0:
        raise ValueError(f"{path}: budget_usd must be above zero")
    files = _take(data, "agents", list, path, [])
    return Experiment(
        name=name,
        seats=tuple(seats),
        hands=hands,
        description=_take(data, "description", str, path, ""),
        seed=_take(data, "seed", int, path, 0),
        small_blind=small,
        big_blind=big,
        ante=_take(data, "ante", int, path, 0),
        stack=_take(data, "stack", int, path, 200),
        budget_usd=float(budget) if budget is not None else None,
        tables=tables,
        agents=tuple((path.parent / str(file)).resolve() for file in files),
        coach=_take(data, "coach", bool, path, True),
    )


def _check_seats(experiment: Experiment, agents: Sequence[Agent]) -> None:
    """Refuse anything that would waste a night or spend money without a limit."""
    check_seats_ready(agents)
    for agent in agents:
        if isinstance(agent, HumanAgent):
            raise ValueError(f"seat {agent.name!r} is a human, and a results run has nobody at it")
    paid = [a for a in agents if isinstance(a, LLMAgent) and not is_local(a.model)]
    if paid and experiment.budget_usd is None:
        raise ValueError(
            f"seat {paid[0].name!r} calls {paid[0].model}, which costs money: "
            "set budget_usd so the run has a limit"
        )
    # Listing a model is free, and it fails on a missing key or a wrong model name, either of
    # which would otherwise be found out by folding every hand.
    for agent in paid:
        try:
            agent.client.models.retrieve(agent.model)
        except Exception as exc:  # noqa: BLE001 - any failure here means the seat cannot play
            raise ValueError(
                f"seat {agent.name!r} cannot use {agent.model}: {type(exc).__name__}: {exc}"
            ) from None


def _failing(agent: Agent) -> bool:
    usage = getattr(agent, "usage", None)
    errors = getattr(usage, "errors", 0)
    return errors >= MAX_FAILURES and errors * 2 >= getattr(usage, "calls", 0)


def _catch_up(agents: Sequence[Agent], earlier: Iterable[HandHistory]) -> None:
    """Show a resumed table's seats what they saw before, so they keep their reads and notes."""
    for history in earlier:
        show_hand(agents, history)
        for decision in history.decisions:
            note = decision.meta.get("note")
            agent = agents[decision.seat]
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


def run_experiment(
    path: Path | str,
    out: Path | str = "results",
    *,
    hands: int | None = None,
    resume: bool = False,
    log: Callable[[str], None] = print,
) -> Outcome:
    """Play the experiment in ``path`` into ``out/<name>/`` and write its report."""
    path = Path(path)
    experiment = load_experiment(path)
    if hands is not None:
        experiment = replace(experiment, hands=hands)
        if hands < experiment.tables:
            raise ValueError(f"--hands {hands} leaves a table with no hands")
    custom = load_agent_files(experiment.agents)
    tables = [
        make_agents(experiment.seats, seed=experiment.table_seed(table), custom=custom)
        for table in range(experiment.tables)
    ]
    _check_seats(experiment, tables[0])

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
            raise ValueError(f"{folder} was played with a different league, so it cannot resume")
        earlier = list(read_jsonl(hands_file))
        previous_seconds = _read_run(folder).get("seconds", 0.0)
    folder.mkdir(parents=True, exist_ok=True)
    if path.resolve() != (folder / "experiment.toml").resolve():
        _write_text(folder / "experiment.toml", path.read_text(encoding="utf-8"))

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

    def on_hand(history: HandHistory) -> None:
        nonlocal played
        with lock:
            write_jsonl(hands_file, [history], append=True)
            played += 1
            if played % every == 0:
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
    seconds = previous_seconds + time.monotonic() - started
    spent = earlier_cost + spent_usd(everyone)
    _write_json(
        folder / "run.json",
        {
            "name": experiment.name,
            "planned_hands": experiment.hands,
            "hands": played,
            "stopped": stopped,
            "tables": experiment.tables,
            "seconds": round(seconds, 1),
            "spent_usd": round(spent, 6),
            "last_started": started_at.isoformat(timespec="seconds"),
            "last_finished": datetime.now(UTC).isoformat(timespec="seconds"),
            "version": __version__,
            "commit": _commit(),
        },
    )
    if played:
        write_report(folder)
    if crash is not None:
        raise RuntimeError(f"a table failed after {played} hands: {crash}") from crash
    return Outcome(folder, played, experiment.hands, stopped, spent, seconds)


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


def margin_of_error(big_blinds_per_hand: Sequence[float]) -> float | None:
    """Half the width of a 95% interval on bb/100, from one seat's result in each hand."""
    if len(big_blinds_per_hand) < 2:
        return None
    error = statistics.stdev(big_blinds_per_hand) / math.sqrt(len(big_blinds_per_hand))
    return 1.96 * error * 100


@dataclass(frozen=True, slots=True)
class SeatResult:
    name: str
    plays_as: str
    hands: int
    net: int
    bb_per_100: float
    margin: float | None
    decisions: int
    model_calls: int
    seconds_per_call: float | None
    cost_usd: float
    illegal: int
    failed: int

    @property
    def clear_of_zero(self) -> bool:
        return self.margin is not None and abs(self.bb_per_100) > self.margin


def seat_results(histories: Sequence[HandHistory]) -> list[SeatResult]:
    """One row per seat, best first. Hands are pooled across tables."""
    nets: dict[str, list[float]] = defaultdict(list)
    kinds: dict[str, str] = {}
    models: dict[str, str] = {}
    latencies: dict[str, list[float]] = defaultdict(list)
    failed: dict[str, int] = defaultdict(int)
    for history in histories:
        names = {p.seat: p.name for p in history.players}
        for player in history.players:
            nets[player.name].append(player.net / history.big_blind)
            kinds.setdefault(player.name, player.kind)
        for decision in history.decisions:
            name = names[decision.seat]
            if decision.meta.get("model"):
                models.setdefault(name, str(decision.meta["model"]))
            if "cost_usd" in decision.meta:
                latencies[name].append(decision.latency_ms / 1000)
            failed[name] += bool(decision.meta.get("failed"))
    rows = []
    for name, stats in compute_stats(histories).items():
        model = models.get(name)
        rows.append(
            SeatResult(
                name=name,
                plays_as=f"{kinds[name]} on {model}" if model else kinds[name],
                hands=stats.hands,
                net=stats.net,
                bb_per_100=stats.bb_per_100 or 0.0,
                margin=margin_of_error(nets[name]),
                decisions=stats.decisions,
                model_calls=stats.model_calls,
                seconds_per_call=(statistics.fmean(latencies[name]) if latencies[name] else None),
                cost_usd=stats.cost_usd,
                illegal=stats.illegal,
                failed=failed[name],
            )
        )
    return sorted(rows, key=lambda row: row.bb_per_100, reverse=True)


def _duration(seconds: float) -> str:
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
}


def render_report(
    experiment: Experiment, histories: Sequence[HandHistory], run: dict[str, Any]
) -> str:
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
        "| seat | plays as | bb/100 | ±95% | net chips | model calls | s/call | $/hand "
        "| illegal | failed |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        margin = f"{row.margin:.0f}" if row.margin is not None else "-"
        seconds = f"{row.seconds_per_call:.1f}" if row.seconds_per_call is not None else "-"
        per_hand = f"{row.cost_usd / row.hands:.4f}" if row.model_calls else "-"
        lines.append(
            f"| {row.name} | {row.plays_as} | {row.bb_per_100:+.0f} | {margin} | {row.net:+d} "
            f"| {row.model_calls} | {seconds} | {per_hand} | {row.illegal} | {row.failed} |"
        )
    clear = [row.name for row in rows if row.clear_of_zero]
    unclear = [row.name for row in rows if not row.clear_of_zero]
    lines += [""]
    if clear:
        lines.append(f"Clear of break-even: {', '.join(clear)}.")
    if unclear:
        lines.append(f"Too close to break-even to call: {', '.join(unclear)}.")
    lines += [
        "",
        "The interval treats hands as independent and looks at one seat at a time, so with many "
        "seats one can clear zero by luck.",
        "",
        "## How they played",
        "",
        "```",
        format_table(compute_stats(histories)),
        "```",
    ]
    coached = [row.name for row in rows if experiment.coach and row.model_calls]
    if coached:
        lines += ["", "## What the coach says", ""]
        lines += [f"- [{name}](coach/{_file_name(name)}.md)" for name in coached]
    return "\n".join(lines) + "\n"


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
        for row in seat_results(histories):
            if row.model_calls:
                facts = tag_hands(histories, row.name, samples=200)
                text = build_report(histories, row.name, facts).render(top=5)
                coach = folder / "coach" / f"{_file_name(row.name)}.md"
                _write_text(coach, f"# {row.name}\n\n```\n{text}\n```\n")
    return report
