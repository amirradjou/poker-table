"""Build agents from short specs like ``tag``, ``alice:maniac`` or ``llm:nerd@model``.

Custom kinds loaded from an agents file (``agents/custom.py``) are passed as ``custom`` and
seated by their name, exactly like the built-in kinds.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from poker_table.agents.base import Agent
from poker_table.agents.human import HumanAgent
from poker_table.agents.laya import DEFAULT_MODEL as LAYA_MODEL
from poker_table.agents.laya import LayaAgent
from poker_table.agents.llm import DEFAULT_MODEL, LLMAgent
from poker_table.agents.personalities import PERSONALITIES
from poker_table.agents.scripted import CallingStation, Maniac, RandomAgent, TightAggressive

if TYPE_CHECKING:
    from poker_table.agents.custom import CustomKind

Factory = Callable[[str, int], Agent]
Custom = Mapping[str, "CustomKind"]

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

# One line per built-in kind, for `poker-table agents`.
BUILT_IN: dict[str, str] = {
    "tag": "tight-aggressive regular: Chen-formula preflop, made-hand rules after the flop",
    "rock": "a tag that waits longer and bets less",
    "maniac": "raises most of the time and almost never folds",
    "station": "calls everything, never raises",
    "random": "any legal action, chosen at random",
    "human": "you, at the terminal (or in the browser with serve --live)",
    "laya": "the open Laya decision model (pip install 'poker-table[laya]')",
}


def available_kinds(custom: Custom | None = None) -> list[str]:
    return (
        sorted(SCRIPTED)
        + ["laya"]
        + [f"llm:{p}" for p in sorted(PERSONALITIES)]
        + sorted(custom or {})
    )


def _build(kind: str, name: str, seed: int, custom: Custom) -> Agent:
    base, _, model = kind.partition("@")
    if base in custom:
        return custom[base].make(name, seed, model or None)
    if kind in SCRIPTED:
        return SCRIPTED[kind](name, seed)
    if kind.startswith("llm:"):
        personality, _, model = kind[4:].partition("@")
        if personality in custom and custom[personality].type == "llm":
            return custom[personality].make(name, seed, model or None)
        return LLMAgent(name, personality, model=model or DEFAULT_MODEL)
    if kind == "laya" or kind.startswith("laya@"):
        # laya@checkpoint, optionally laya@checkpoint#subfolder (the repo bundles several)
        _, _, spec = kind.partition("@")
        model, _, subfolder = spec.partition("#")
        return LayaAgent(name, model=model or LAYA_MODEL, subfolder=subfolder or None, seed=seed)
    raise ValueError(
        f"unknown agent kind {kind!r}; choose from {', '.join(available_kinds(custom))}"
    )


def _default_name(kind: str) -> str:
    if kind.startswith("llm:"):
        return kind[4:].partition("@")[0]
    return "laya" if kind.startswith("laya") else kind.partition("@")[0]


def _is_kind(word: str, custom: Custom) -> bool:
    word = word.lower()
    return (
        word in SCRIPTED
        or word == "llm"
        or word.startswith("laya")
        or word.partition("@")[0] in custom
    )


def make_agent(
    spec: str, *, seed: int = 0, taken: Sequence[str] = (), custom: Custom | None = None
) -> Agent:
    """Build one agent from a spec.

    Specs: ``tag`` · ``alice:tag`` · ``llm:nerd`` · ``bob:llm:maniac@claude-sonnet-5`` ·
    ``llm:rock@ollama/qwen2.5:7b`` · a custom kind: ``shark``, ``ann:shark@claude-haiku-4-5``.
    Unnamed agents are named after their kind, numbered when it repeats.
    """
    custom = custom or {}
    parts = [p.strip() for p in spec.split(":")]
    # "llm:..." is the kind's own prefix, never a seat called llm
    named = len(parts) > 1 and parts[0].lower() != "llm" and _is_kind(parts[1], custom)
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
    return _build(kind, name, seed, custom)


def make_agents(
    specs: Sequence[str], *, seed: int = 0, custom: Custom | None = None
) -> list[Agent]:
    agents: list[Agent] = []
    for i, spec in enumerate(specs):
        agents.append(
            make_agent(spec, seed=seed * 1000 + i, taken=[a.name for a in agents], custom=custom)
        )
    return agents
