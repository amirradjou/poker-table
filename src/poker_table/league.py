"""Play many hands between the same agents: rotating button, seeded decks, bankrolls."""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from poker_table.agents.base import Agent
from poker_table.history import HandHistory
from poker_table.stats import PlayerStats, compute_stats
from poker_table.table import new_hand, play_hand


@dataclass(frozen=True, slots=True)
class LeagueConfig:
    hands: int
    small_blind: int = 1
    big_blind: int = 2
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
    keep_histories: bool = True,
) -> LeagueResult:
    """Play ``config.hands`` hands. ``on_hand`` is called after each one (for streaming to disk)."""
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
            seed=seeds.randrange(2**63),
            hand_id=str(n + 1),
        )
        history = HandHistory.from_played(play_hand(hand, seated))
        for player in history.players:
            result.bankrolls[player.name] += player.net
        stacks = [s.stack for s in hand.seats]
        if keep_histories:
            result.histories.append(history)
        if on_hand is not None:
            on_hand(history)
    return result
