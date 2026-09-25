"""Play many hands between the same agents.

Two shapes. :func:`run_league` is a cash game: every hand is dealt at the same blinds and
(by default) from the same buy-in, which keeps hands comparable — the right thing when the
question is "who plays better". :func:`run_tournament` is a freezeout: one buy-in each,
blinds and antes rising on a schedule, seats knocked out at zero chips, until one seat holds
every chip — the right thing when the question is "who survives".
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from poker_table.agents.base import Agent, SeatView
from poker_table.engine import Hand
from poker_table.history import HandHistory
from poker_table.stats import PlayerStats, compute_stats
from poker_table.table import DecisionRecord, new_hand, play_hand


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


@dataclass(slots=True)
class LeagueResult:
    config: LeagueConfig
    histories: list[HandHistory] = field(default_factory=list)
    bankrolls: dict[str, int] = field(default_factory=dict)
    rebuys: dict[str, int] = field(default_factory=dict)

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
) -> LeagueResult:
    """Play ``config.hands`` hands.

    ``on_hand`` is called after each finished hand (for streaming to disk); ``on_hand_start``,
    ``on_view`` and ``on_decision`` fire as a hand is dealt and played, for a live table.
    """
    if len(agents) < 2:
        raise ValueError("a league needs at least two agents")
    names = [a.name for a in agents]
    if len(set(names)) != len(names):
        raise ValueError("agent names must be unique")

    result = LeagueResult(config, bankrolls=dict.fromkeys(names, 0), rebuys=dict.fromkeys(names, 0))
    seeds = random.Random(config.seed)
    stacks = [config.buy_in] * len(agents)
    for n in range(config.hands):
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
            seed=seeds.randrange(2**63),
            hand_id=str(n + 1),
        )
        if on_hand_start is not None:
            on_hand_start(hand)
        played = play_hand(hand, seated, on_view=on_view, on_decision=on_decision)
        history = HandHistory.from_played(played)
        for player in history.players:
            result.bankrolls[player.name] += player.net
        stacks = [s.stack for s in hand.seats]
        if keep_histories:
            result.histories.append(history)
        if on_hand is not None:
            on_hand(history)
    return result


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
        if not self.complete:
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
    on_hand: Callable[[HandHistory], None] | None = None,
    on_hand_start: Callable[[Hand], None] | None = None,
    on_view: Callable[[SeatView], None] | None = None,
    on_decision: Callable[[DecisionRecord, Hand], None] | None = None,
    keep_histories: bool = True,
) -> TournamentResult:
    """Play a freezeout between ``agents`` until one of them holds every chip."""
    config = config if config is not None else TournamentConfig()
    if len(agents) < 2:
        raise ValueError("a tournament needs at least two agents")
    names = [a.name for a in agents]
    if len(set(names)) != len(names):
        raise ValueError("agent names must be unique")

    result = TournamentResult(config, hands_dealt=dict.fromkeys(names, 0))
    stacks = dict.fromkeys(names, config.starting_stack)
    seeds = random.Random(config.seed)
    live = list(range(len(agents)))  # seats still in it, in seating order
    button = 0  # a seat index; it moves on round the table even when that seat is out
    number = 0

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
            hand_id=str(number + 1),
        )
        for i in live:
            result.hands_dealt[names[i]] += 1
        if on_hand_start is not None:
            on_hand_start(hand)
        played = play_hand(hand, players, on_view=on_view, on_decision=on_decision)
        history = HandHistory.from_played(played)
        for seat, index in enumerate(live):
            stacks[names[index]] = hand.seats[seat].stack
        if keep_histories:
            result.histories.append(history)
        if on_hand is not None:
            on_hand(history)

        number += 1
        result.level = config.level_number(number - 1)
        # Everyone who lost their last chip is out. Several can bust in one hand; the one
        # that sat down with more chips outlasted the other, so it finishes higher.
        busted = sorted((i for i in live if stacks[names[i]] == 0), key=lambda i: before[i])
        survivors = len(live) - len(busted)
        for offset, index in enumerate(busted):
            result.finishes[names[index]] = survivors + len(busted) - offset
            result.out_at[names[index]] = result.level
        live = [i for i in live if stacks[names[i]] > 0]
        button = (button + 1) % len(agents)

    result.hands = number
    result.stacks = dict(stacks)
    result.complete = len(live) == 1
    # The winner, or — if the hand limit ran out first — the survivors by chip count.
    for place, index in enumerate(sorted(live, key=lambda i: -stacks[names[i]]), start=1):
        result.finishes[names[index]] = place
    return result
