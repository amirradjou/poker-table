"""Build agents from short specs like ``tag``, ``alice:maniac`` or ``rock``."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from poker_table.agents.base import Agent
from poker_table.agents.scripted import CallingStation, Maniac, RandomAgent, TightAggressive

Factory = Callable[[str, int], Agent]

SCRIPTED: dict[str, Factory] = {
    "random": lambda name, seed: RandomAgent(name, seed=seed),
    "station": lambda name, seed: CallingStation(name),
    "tag": lambda name, seed: TightAggressive(name, seed=seed),
    "rock": lambda name, seed: TightAggressive(name, tightness=2, aggression=0.3, seed=seed),
    "maniac": lambda name, seed: Maniac(name, seed=seed),
}


def available_kinds() -> list[str]:
    return sorted(SCRIPTED)


def make_agent(spec: str, *, seed: int = 0, taken: Sequence[str] = ()) -> Agent:
    """``spec`` is ``kind`` or ``name:kind``. Unnamed agents get the kind as their name,
    numbered when the same kind sits down twice."""
    name, sep, kind = spec.partition(":")
    if not sep:
        kind, name = spec, spec
    kind = kind.strip().lower()
    name = name.strip()
    if kind not in SCRIPTED:
        raise ValueError(f"unknown agent kind {kind!r}; choose from {', '.join(available_kinds())}")
    if not sep:
        base, counter = name, 2
        while name in taken:
            name = f"{base}{counter}"
            counter += 1
    elif name in taken:
        raise ValueError(f"duplicate seat name {name!r}")
    return SCRIPTED[kind](name, seed)


def make_agents(specs: Sequence[str], *, seed: int = 0) -> list[Agent]:
    agents: list[Agent] = []
    for i, spec in enumerate(specs):
        agents.append(make_agent(spec, seed=seed * 1000 + i, taken=[a.name for a in agents]))
    return agents
