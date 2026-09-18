"""Build agents from short specs like ``tag``, ``alice:maniac`` or ``rock``."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from poker_table.agents.base import Agent
from poker_table.agents.human import HumanAgent
from poker_table.agents.llm import DEFAULT_MODEL, LLMAgent
from poker_table.agents.personalities import PERSONALITIES
from poker_table.agents.scripted import CallingStation, Maniac, RandomAgent, TightAggressive

Factory = Callable[[str, int], Agent]

SCRIPTED: dict[str, Factory] = {
    "random": lambda name, seed: RandomAgent(name, seed=seed),
    "station": lambda name, seed: CallingStation(name),
    "tag": lambda name, seed: TightAggressive(name, seed=seed),
    "rock": lambda name, seed: TightAggressive(
        name, tightness=2, aggression=0.3, seed=seed, kind="rock"
    ),
    "maniac": lambda name, seed: Maniac(name, seed=seed),
    "human": lambda name, seed: HumanAgent(name),
}


def available_kinds() -> list[str]:
    return sorted(SCRIPTED) + [f"llm:{p}" for p in sorted(PERSONALITIES)]


def _build(kind: str, name: str, seed: int) -> Agent:
    if kind in SCRIPTED:
        return SCRIPTED[kind](name, seed)
    if kind.startswith("llm:"):
        personality, _, model = kind[4:].partition("@")
        return LLMAgent(name, personality, model=model or DEFAULT_MODEL)
    raise ValueError(f"unknown agent kind {kind!r}; choose from {', '.join(available_kinds())}")


def _default_name(kind: str) -> str:
    return kind[4:].partition("@")[0] if kind.startswith("llm:") else kind


def make_agent(spec: str, *, seed: int = 0, taken: Sequence[str] = ()) -> Agent:
    """Build one agent from a spec.

    Specs: ``tag`` · ``alice:tag`` · ``llm:nerd`` · ``bob:llm:maniac@claude-sonnet-5``.
    Unnamed agents are named after their kind, numbered when it repeats.
    """
    parts = [p.strip() for p in spec.split(":")]
    named = len(parts) > 1 and (parts[1].lower() in SCRIPTED or parts[1].lower() == "llm")
    name = parts[0] if named else ""
    kind = ":".join(parts[1:] if named else parts).lower()
    if not kind:
        raise ValueError(f"empty agent kind in {spec!r}")
    if not named:
        base = _default_name(kind)
        name, counter = base, 2
        while name in taken:
            name = f"{base}{counter}"
            counter += 1
    elif name in taken:
        raise ValueError(f"duplicate seat name {name!r}")
    return _build(kind, name, seed)


def make_agents(specs: Sequence[str], *, seed: int = 0) -> list[Agent]:
    agents: list[Agent] = []
    for i, spec in enumerate(specs):
        agents.append(make_agent(spec, seed=seed * 1000 + i, taken=[a.name for a in agents]))
    return agents
