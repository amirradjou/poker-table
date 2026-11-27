"""Play many hands between the same agents.

Two shapes. :func:`run_league` is a cash game: every hand is dealt at the same blinds and
(by default) from the same buy-in, which keeps hands comparable — the right thing when the
question is "who plays better". :func:`run_tournament` is a freezeout: one buy-in each,
blinds and antes rising on a schedule, seats knocked out at zero chips, until one seat holds
every chip — the right thing when the question is "who survives".
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace

from poker_table.agents.base import Agent, Decision, SeatView, TableShape, seating_problems
from poker_table.engine import Action, Hand
from poker_table.history import DecisionTrace, HandHistory, parse_action, public_view
from poker_table.stats import PlayerStats, compute_stats
from poker_table.table import DecisionRecord, PlayedHand, new_hand, play_hand


def spent_usd(agents: Iterable[Agent]) -> float:
    """What the seats' model calls have cost so far (scripted seats cost nothing)."""
    return sum(getattr(getattr(a, "usage", None), "cost_usd", 0.0) or 0.0 for a in agents)


def show_hand(agents: Iterable[Agent], history: HandHistory) -> None:
    """Show the hand just played to every seat that learns across hands (has ``observe``).

    Each one gets :func:`public_view` from its own chair — never folded cards or another
    seat's reasoning — so what a seat can learn is what a player at the table could.
    """
    for agent in agents:
        observe = getattr(agent, "observe", None)
        if callable(observe):
            observe(public_view(history, agent.name))


@dataclass(frozen=True, slots=True)
class LeagueConfig:
    hands: int
    small_blind: int = 1
    big_blind: int = 2
    ante: int = 0  # every seat posts this before the blinds
    buy_in: int = 200
    # top_up: every hand starts from ``buy_in`` (cash game with auto top-up), which keeps
    # every hand comparable. Otherwise stacks carry over and a busted seat rebuys.
    top_up: bool = True
    seed: int = 0
    # Stop dealing once the seats' model calls have cost this many dollars (None: no cap).
    # Checked between hands, so the hand that crosses the line is finished, not cut short.
    budget_usd: float | None = None
    # Start partway through: the first hands are skipped, but their decks are still drawn, so
    # the rest are dealt exactly as they would have been. Needs top_up: stacks are not stored.
    first_hand: int = 0
    # Goes in front of each hand's number, to keep ids apart when several tables share a file.
    hand_prefix: str = ""


@dataclass(slots=True)
class LeagueResult:
    config: LeagueConfig
    histories: list[HandHistory] = field(default_factory=list)
    bankrolls: dict[str, int] = field(default_factory=dict)
    rebuys: dict[str, int] = field(default_factory=dict)
    hands: int = 0  # hands actually played: fewer than config.hands when the budget ran out
    spent_usd: float = 0.0

    @property
    def over_budget(self) -> bool:
        budget = self.config.budget_usd
        return budget is not None and self.spent_usd >= budget

    @property
    def stats(self) -> dict[str, PlayerStats]:
        return compute_stats(self.histories)


def run_league(
    agents: Sequence[Agent],
    config: LeagueConfig,
    *,
    on_hand: Callable[[HandHistory], None] | None = None,
    on_hand_start: Callable[[Hand], None] | None = None,
    on_view: Callable[[SeatView], None] | None = None,
    on_decision: Callable[[DecisionRecord, Hand], None] | None = None,
    keep_histories: bool = True,
    stop: Callable[[], bool] | None = None,
) -> LeagueResult:
    """Play ``config.hands`` hands.

    ``on_hand`` is called after each finished hand (for streaming to disk); ``on_hand_start``,
    ``on_view`` and ``on_decision`` fire as a hand is dealt and played, for a live table.
    ``stop`` is asked after each hand, and a yes ends the league there.
    """
    if len(agents) < 2:
        raise ValueError("a league needs at least two agents")
    if config.first_hand and not config.top_up:
        raise ValueError("a league can only start partway through when stacks are topped up")
    names = [a.name for a in agents]
    if len(set(names)) != len(names):
        raise ValueError("agent names must be unique")
    shape = TableShape(
        len(agents), config.small_blind, config.big_blind, config.ante, config.buy_in
    )
    _refuse(seating_problems(agents, shape))

    result = LeagueResult(config, bankrolls=dict.fromkeys(names, 0), rebuys=dict.fromkeys(names, 0))
    seeds = random.Random(config.seed)
    stacks = [config.buy_in] * len(agents)
    for n in range(config.hands):
        deck_seed = seeds.randrange(2**63)
        if n < config.first_hand:
            continue
        if config.top_up:
            stacks = [config.buy_in] * len(agents)
        else:
            for i, stack in enumerate(stacks):
                if stack == 0:
                    stacks[i] = config.buy_in
                    result.rebuys[names[i]] += 1
        hand, seated = new_hand(
            agents,
            stacks,
            button=n % len(agents),
            small_blind=config.small_blind,
            big_blind=config.big_blind,
            ante=config.ante,
            seed=deck_seed,
            hand_id=f"{config.hand_prefix}{n + 1}",
        )
        if on_hand_start is not None:
            on_hand_start(hand)
        played = play_hand(hand, seated, on_view=on_view, on_decision=on_decision)
        history = HandHistory.from_played(played)
        show_hand(agents, history)
        for player in history.players:
            result.bankrolls[player.name] += player.net
        stacks = [s.stack for s in hand.seats]
        if keep_histories:
            result.histories.append(history)
        if on_hand is not None:
            on_hand(history)
        result.hands = n + 1
        result.spent_usd = spent_usd(agents)
        if result.over_budget or (stop is not None and stop()):
            break
    return result


def _refuse(problems: list[str]) -> None:
    """A seat that cannot play this table stops it before the first hand, never during it."""
    if problems:
        raise ValueError(problems[0])


def parse_blinds(text: str) -> tuple[int, int]:
    """``"1/2"`` -> small blind, big blind."""
    small, sep, big = text.partition("/")
    if not sep:
        raise ValueError("blinds must look like 1/2")
    return int(small), int(big)


# ----- tournaments -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Level:
    """One level of a blind schedule."""

    small_blind: int
    big_blind: int
    ante: int = 0

    def __str__(self) -> str:
        return f"{self.small_blind}/{self.big_blind}" + (f"+{self.ante}" if self.ante else "")


# Doubling roughly every level, with antes from level 4 — a turbo schedule, so a table of
# bots reaches a winner in a few hundred hands instead of tens of thousands.
DEFAULT_SCHEDULE = (
    Level(1, 2),
    Level(2, 4),
    Level(3, 6),
    Level(5, 10, 1),
    Level(10, 20, 2),
    Level(15, 30, 3),
    Level(25, 50, 5),
    Level(50, 100, 10),
    Level(100, 200, 25),
    Level(200, 400, 50),
    Level(400, 800, 100),
    Level(800, 1600, 200),
)


def parse_levels(text: str) -> tuple[Level, ...]:
    """``"1/2,2/4,5/10+1"`` -> a blind schedule; ``+n`` on a level is its ante."""
    levels = []
    for part in text.split(","):
        stakes, _, ante = part.strip().partition("+")
        small, big = parse_blinds(stakes)
        levels.append(Level(small, big, int(ante) if ante else 0))
    if not levels:
        raise ValueError("a schedule needs at least one level, e.g. 1/2")
    return tuple(levels)


@dataclass(frozen=True, slots=True)
class TournamentConfig:
    """A freezeout: one buy-in each, rising blinds, no rebuys, play until one seat has it all."""

    starting_stack: int = 200
    schedule: tuple[Level, ...] = DEFAULT_SCHEDULE
    hands_per_level: int = 20
    # A safety net: two seats that fold everything would otherwise pass the button forever
    # once the blinds stop growing.
    max_hands: int = 5000
    seed: int = 0
    budget_usd: float | None = None  # stop, unfinished, once model calls cost this much
    # Goes in front of each hand's number, to keep several tournaments apart in one file.
    hand_prefix: str = ""
    # Written into each hand's tournament record as its "id", when a file holds several.
    number: int | None = None

    def level_number(self, hand_number: int) -> int:
        """The 1-based level the ``hand_number``-th hand (0-based) is played at."""
        return min(hand_number // self.hands_per_level + 1, len(self.schedule))

    def level(self, hand_number: int) -> Level:
        return self.schedule[self.level_number(hand_number) - 1]


@dataclass(slots=True)
class TournamentResult:
    config: TournamentConfig
    hands: int = 0
    level: int = 1  # the level the last hand was played at
    complete: bool = False  # False when max_hands stopped it short of a winner
    histories: list[HandHistory] = field(default_factory=list)
    finishes: dict[str, int] = field(default_factory=dict)  # name -> place, 1 is the winner
    hands_dealt: dict[str, int] = field(default_factory=dict)  # hands each seat was dealt into
    out_at: dict[str, int] = field(default_factory=dict)  # name -> level it busted at
    stacks: dict[str, int] = field(default_factory=dict)  # chips at the end
    spent_usd: float = 0.0
    over_budget: bool = False
    # Resuming: a seat asked again about an earlier hand that answered otherwise than the record,
    # with the first hand it did so in. Its own random choices no longer follow the first run.
    differed: dict[str, str] = field(default_factory=dict)

    @property
    def winner(self) -> str | None:
        return next((name for name, place in self.finishes.items() if place == 1), None)

    @property
    def stats(self) -> dict[str, PlayerStats]:
        return compute_stats(self.histories)

    def standings(self) -> list[str]:
        """Seat names in finishing order, the winner first."""
        return sorted(self.finishes, key=lambda name: self.finishes[name])

    def render(self) -> str:
        plural = "" if self.hands == 1 else "s"
        head = (
            f"{self.hands} hand{plural}, {self.config.starting_stack} chips each, "
            f"reached level {self.level} ({self.config.schedule[self.level - 1]})"
        )
        if self.over_budget:
            head += f" — stopped at the ${self.config.budget_usd:.2f} budget, no winner"
        elif not self.complete:
            head += f" — stopped at the {self.config.max_hands}-hand limit, no winner"
        rows = [("place", "name", "hands", "out at")]
        for name in self.standings():
            level = self.out_at.get(name)
            chips = self.stacks.get(name, 0)
            if level:
                status = f"level {level}"
            elif self.complete:
                status = f"won with {chips}"
            else:
                status = f"still in with {chips}"
            rows.append(
                (str(self.finishes[name]), name, str(self.hands_dealt.get(name, 0)), status)
            )
        widths = [max(len(r[i]) for r in rows) for i in range(4)]
        lines = [
            "  ".join(
                cell.ljust(w) if i in (1, 3) else cell.rjust(w)
                for i, (cell, w) in enumerate(zip(row, widths, strict=True))
            ).rstrip()
            for row in rows
        ]
        return head + "\n" + "\n".join(lines)


def run_tournament(
    agents: Sequence[Agent],
    config: TournamentConfig | None = None,
    *,
    resume_from: Sequence[HandHistory] = (),
    on_hand: Callable[[HandHistory], None] | None = None,
    on_hand_start: Callable[[Hand], None] | None = None,
    on_view: Callable[[SeatView], None] | None = None,
    on_decision: Callable[[DecisionRecord, Hand], None] | None = None,
    keep_histories: bool = True,
    stop: Callable[[], bool] | None = None,
) -> TournamentResult:
    """Play a freezeout between ``agents`` until one of them holds every chip.

    ``resume_from`` carries on a tournament from the hands a file kept of it. Those hands are
    played again with the actions they record, so stacks, who is left, the button, the places
    and the decks all come out of the same code as before. A seat with no ``usage`` (a scripted
    bot) is asked again at each of its decisions, and its answer only compared with the record,
    so its own random choices move on as they did and the tournament carries on as if it had
    never stopped. A model seat is not asked: a call costs money and need not answer alike.

    ``stop`` is asked after each new hand, and a yes ends the tournament there, unfinished.
    """
    config = config if config is not None else TournamentConfig()
    if len(agents) < 2:
        raise ValueError("a tournament needs at least two agents")
    names = [a.name for a in agents]
    if len(set(names)) != len(names):
        raise ValueError("agent names must be unique")
    first = config.schedule[0]
    shape = TableShape(
        len(agents), first.small_blind, first.big_blind, first.ante, config.starting_stack
    )
    _refuse(seating_problems(agents, shape))

    result = TournamentResult(config, hands_dealt=dict.fromkeys(names, 0))
    stacks = dict.fromkeys(names, config.starting_stack)
    seeds = random.Random(config.seed)
    live = list(range(len(agents)))  # seats still in it, in seating order
    button = 0  # a seat index; it moves on round the table even when that seat is out
    number = 0
    earlier = list(resume_from)

    while len(live) > 1 and number < config.max_hands:
        while button not in live:
            button = (button + 1) % len(agents)
        level = config.level(number)
        seated = [agents[i] for i in live]
        before = {i: stacks[names[i]] for i in live}
        hand, players = new_hand(
            seated,
            [before[i] for i in live],
            button=live.index(button),
            small_blind=level.small_blind,
            big_blind=level.big_blind,
            ante=level.ante,
            seed=seeds.randrange(2**63),
            hand_id=f"{config.hand_prefix}{number + 1}",
        )
        record = earlier[number] if number < len(earlier) else None
        for i in live:
            result.hands_dealt[names[i]] += 1
        if record is None:
            if on_hand_start is not None:
                on_hand_start(hand)
            played = play_hand(hand, players, on_view=on_view, on_decision=on_decision)
        else:
            played = _play_again(hand, players, record, result.differed)
        for seat, index in enumerate(live):
            stacks[names[index]] = hand.seats[seat].stack

        result.level = config.level_number(number)
        number += 1
        # Everyone who lost their last chip is out. Several can bust in one hand; the one
        # that sat down with more chips outlasted the other, so it finishes higher.
        busted = sorted((i for i in live if stacks[names[i]] == 0), key=lambda i: before[i])
        survivors = len(live) - len(busted)
        out: dict[str, int] = {}
        for offset, index in enumerate(busted):
            out[names[index]] = survivors + len(busted) - offset
            result.finishes[names[index]] = out[names[index]]
            result.out_at[names[index]] = result.level
        where: dict[str, object] = {"level": result.level, "left": len(live), "out": out}
        if config.number is not None:
            where = {"id": config.number} | where
        history = replace(HandHistory.from_played(played), tournament=where)
        if record is not None:
            if _as_dealt(history) != _as_dealt(record):
                raise ValueError(_not_this_tournament(record))
            history = record  # keep what the first run wrote: its reasoning, times and costs
        show_hand(seated, history)
        if keep_histories:
            result.histories.append(history)
        live = [i for i in live if stacks[names[i]] > 0]
        button = (button + 1) % len(agents)
        if record is not None:
            continue
        if on_hand is not None:
            on_hand(history)
        result.spent_usd = spent_usd(agents)
        if config.budget_usd is not None and result.spent_usd >= config.budget_usd:
            result.over_budget = len(live) > 1
            break
        if stop is not None and len(live) > 1 and stop():
            break

    if number < len(earlier):
        raise ValueError(f"the tournament is over after {number} hands, and more were given")
    result.hands = number
    result.stacks = dict(stacks)
    result.complete = len(live) == 1
    result.spent_usd = spent_usd(agents)
    # The winner, or — if the hand limit ran out first — the survivors by chip count.
    for place, index in enumerate(sorted(live, key=lambda i: -stacks[names[i]]), start=1):
        result.finishes[names[index]] = place
    return result


def _not_this_tournament(record: HandHistory) -> str:
    return f"hand {record.hand_id} is not what this tournament deals there, so it cannot go on"


def _as_dealt(history: HandHistory) -> tuple[object, ...]:
    """What a hand played again must repeat: the deal, every action, the result and the
    tournament record. Times, reasoning and costs are the first run's own."""
    return (
        history.hand_id,
        history.seed,
        history.button,
        (history.small_blind, history.big_blind, history.ante),
        [(p.name, p.stack, p.hole, p.net) for p in history.players],
        history.board,
        dict(history.payouts),
        [(d.seat, d.applied) for d in history.decisions],
        history.tournament,
    )


class _Recorded:
    """A seat in a hand played again: it answers with what the record says it did.

    A seat with no ``usage`` is asked as well, and its answer only compared with the record,
    so that whatever it keeps between decisions (its random choices) moves on as it did. A
    record that does not fit the hand gets folds, and the check after the hand refuses it.
    """

    def __init__(
        self,
        agent: Agent,
        record: HandHistory,
        turns: Iterator[DecisionTrace],
        differed: dict[str, str],
    ) -> None:
        self.name = agent.name
        self.agent = agent
        self.record = record
        self.turns = turns
        self.differed = differed
        self.ask = getattr(agent, "usage", None) is None

    def act(self, view: SeatView) -> Decision:
        recorded = next(self.turns, None)
        if recorded is None or recorded.seat != view.seat:
            return Decision(Action.fold())
        if self.ask:
            try:
                answer = str(self.agent.act(view).action)
            except Exception:  # noqa: BLE001 - play_hand folds for a seat that crashes
                answer = "fold"
            if answer != recorded.requested:
                self.differed.setdefault(self.name, self.record.hand_id)
        return Decision(
            parse_action(recorded.applied),
            reasoning=recorded.reasoning,
            table_talk=recorded.table_talk,
            meta=dict(recorded.meta),
        )


def _play_again(
    hand: Hand, agents: dict[int, Agent], record: HandHistory, differed: dict[str, str]
) -> PlayedHand:
    turns = iter(record.decisions)
    seats: dict[int, Agent] = {
        seat: _Recorded(agent, record, turns, differed) for seat, agent in agents.items()
    }
    return play_hand(hand, seats)


@dataclass(frozen=True, slots=True)
class Standing:
    """Where a freezeout stands after the last of its hands in a file. It is read from the
    hands alone, so a report and the viewer need neither the seats nor the settings."""

    number: int | None  # the tournament's id, when a file holds several
    hands: list[HandHistory]
    left: list[tuple[str, int]]  # seats still in with their chips, most chips first
    gone: dict[str, tuple[int, str]]  # name -> (place it finished, hand it went out in)

    @property
    def complete(self) -> bool:
        return len(self.left) == 1

    def places(self) -> dict[str, int]:
        """Every seat's place: the ones still in rank by chips, as run_tournament ranks them."""
        places = {name: place for name, (place, _) in self.gone.items()}
        places |= {name: place for place, (name, _) in enumerate(self.left, start=1)}
        return places

    def hands_dealt(self) -> dict[str, int]:
        dealt: dict[str, int] = defaultdict(int)
        for history in self.hands:
            for player in history.players:
                dealt[player.name] += 1
        return dict(dealt)


def standings_of(histories: Iterable[HandHistory]) -> list[Standing]:
    """One standing for each tournament in a file, in the order of their ids. Cash hands are
    left out, and a file of one tournament written by ``play --tournament`` has no ids."""
    by_number: dict[int | None, list[HandHistory]] = defaultdict(list)
    for history in histories:
        if history.tournament:
            by_number[history.tournament.get("id")].append(history)
    standings = []
    for number in sorted(by_number, key=lambda n: -1 if n is None else n):
        hands = by_number[number]
        gone: dict[str, tuple[int, str]] = {}
        for history in hands:
            for name, place in history.tournament.get("out", {}).items():
                gone[name] = (place, history.hand_id)
        last = hands[-1]
        left = sorted(
            ((p.name, p.stack + p.net) for p in last.players if p.name not in gone),
            key=lambda row: -row[1],
        )
        standings.append(Standing(number, hands, left, gone))
    return standings
