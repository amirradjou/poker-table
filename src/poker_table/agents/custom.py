"""Your own seats, defined in a TOML file: an LLM persona or a tuned bot needs no code.

    [agents.shark]
    description = "Patient, then ruthless"
    type = "llm"
    model = "claude-sonnet-5"          # or "ollama/qwen2.5:7b" for a local model
    extends = "nerd"                   # start from a built-in personality (optional)
    prompt = "You are THE SHARK. ..."  # replaces the personality's prompt
    effort = "medium"
    auto_fold_below = 6                # false switches the preflop gate off
    talkative = false
    aids = ["odds", "ranges", "reads"] # what it is handed; all five when left out

    [agents.nit]
    type = "bot"
    base = "tag"                       # tag | rock | maniac | station | random
    tightness = 3
    aggression = 0.5

    [agents.bluffer]
    type = "python"
    class = "my_bots.py:Bluffer"       # a file beside this one, or "package.module:Class"
    options = { rate = 0.4 }           # keyword arguments for the class

Seat them like any built-in kind: ``--agents agents.toml --seats shark,nit,alice:bluffer``;
an LLM kind takes another model per seat with ``shark@ollama/qwen2.5:7b``. A ``python``
kind runs the code it names, so only load files you trust.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import re
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poker_table.agents.base import Agent
from poker_table.agents.llm import DEFAULT_MODEL, LLMAgent
from poker_table.agents.ollama import OllamaClient, is_local
from poker_table.agents.personalities import (
    AIDS,
    PERSONALITIES,
    Personality,
    check_aids,
)
from poker_table.agents.scripted import CallingStation, Maniac, RandomAgent, TightAggressive

EFFORTS = ("low", "medium", "high", "xhigh", "max")
RESERVED = {"llm", "laya", "human", "random", "station", "tag", "rock", "maniac"}
_KEY = re.compile(r"^[a-z][a-z0-9_-]*$")

Build = Callable[[str, int, str | None], Agent]  # (seat name, seed, model override)


@dataclass(frozen=True, slots=True)
class CustomKind:
    """One ``[agents.<key>]`` table: what it is, and how to seat it."""

    key: str
    type: str  # llm | bot | python
    description: str
    build: Build
    personality: Personality | None = None  # for llm kinds

    def make(self, name: str, seed: int = 0, model: str | None = None) -> Agent:
        if model and self.type != "llm":
            raise ValueError(f"{self.key!r} is a {self.type} kind; only llm kinds take @model")
        return self.build(name, seed, model)


class AgentFileError(ValueError):
    pass


def load_agents(path: Path | str) -> dict[str, CustomKind]:
    """Read every ``[agents.<key>]`` table from a TOML file, checking each field."""
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AgentFileError(f"{path}: {exc}") from None
    tables = data.get("agents")
    if not isinstance(tables, dict) or not tables:
        raise AgentFileError(f"{path}: no [agents.<name>] tables")
    extra = sorted(set(data) - {"agents"})
    if extra:
        raise AgentFileError(f"{path}: unknown top-level key(s) {', '.join(extra)}")
    kinds = {}
    for key, table in tables.items():
        where = f"{path}: [agents.{key}]"
        if not _KEY.match(key):
            raise AgentFileError(f"{where}: names are lower-case letters, digits, - and _")
        if key in RESERVED or key in PERSONALITIES:
            raise AgentFileError(f"{where}: {key!r} is a built-in kind; pick another name")
        if not isinstance(table, dict):
            raise AgentFileError(f"{where}: expected a table")
        kinds[key] = _parse(key, dict(table), path.parent, where)
    return kinds


def load_agent_files(paths) -> dict[str, CustomKind]:
    kinds: dict[str, CustomKind] = {}
    for path in paths or ():
        for key, kind in load_agents(path).items():
            if key in kinds:
                raise AgentFileError(f"{path}: {key!r} is already defined in another file")
            kinds[key] = kind
    return kinds


def _parse(key: str, table: dict[str, Any], base_dir: Path, where: str) -> CustomKind:
    kind = table.pop("type", None)
    description = _take(table, "description", str, where, "")
    match kind:
        case "llm":
            return _llm(key, table, description, where)
        case "bot":
            return _bot(key, table, description, where)
        case "python":
            return _python(key, table, description, base_dir, where)
    raise AgentFileError(f'{where}: type must be "llm", "bot" or "python", not {kind!r}')


def _take(table: dict[str, Any], field: str, types, where: str, default: Any = None) -> Any:
    if field not in table:
        return default
    value = table.pop(field)
    if isinstance(value, bool) and bool not in (types if isinstance(types, tuple) else (types,)):
        raise AgentFileError(f"{where}: {field} must not be true/false")
    if not isinstance(value, types):
        want = types.__name__ if isinstance(types, type) else " or ".join(t.__name__ for t in types)
        raise AgentFileError(f"{where}: {field} must be a {want}, not {value!r}")
    return value


def _no_leftovers(table: dict[str, Any], where: str, allowed: tuple[str, ...]) -> None:
    if table:
        raise AgentFileError(
            f"{where}: unknown field(s) {', '.join(sorted(table))} "
            f"(this type takes: {', '.join(allowed)})"
        )


# ----- llm ----------------------------------------------------------------------------------

LLM_FIELDS = (
    "description",
    "model",
    "extends",
    "prompt",
    "effort",
    "auto_fold_below",
    "talkative",
    "aids",
    "max_tokens",
    "options",
)


def _llm(key: str, table: dict[str, Any], description: str, where: str) -> CustomKind:
    model = _take(table, "model", str, where, DEFAULT_MODEL)
    parent_key = _take(table, "extends", str, where)
    parent = None
    if parent_key is not None:
        if parent_key not in PERSONALITIES:
            raise AgentFileError(
                f"{where}: extends {parent_key!r}, but the built-in personalities are "
                + ", ".join(sorted(PERSONALITIES))
            )
        parent = PERSONALITIES[parent_key]
    prompt = _take(table, "prompt", str, where, parent.prompt if parent else None)
    if not prompt:
        raise AgentFileError(f"{where}: an llm kind needs a prompt (or extends a personality)")
    effort = _take(table, "effort", str, where, parent.effort if parent else "medium")
    if effort not in EFFORTS:
        raise AgentFileError(f"{where}: effort must be one of {', '.join(EFFORTS)}")
    gate = table.pop("auto_fold_below", parent.auto_fold_below if parent else 5)
    if gate is False:
        gate = None
    elif isinstance(gate, bool) or not isinstance(gate, (int, float, type(None))):
        raise AgentFileError(f"{where}: auto_fold_below is a Chen score, or false for no gate")
    talkative = _take(table, "talkative", bool, where, parent.talkative if parent else True)
    aids = _take(table, "aids", list, where, list(parent.aids if parent else AIDS))
    try:
        aids = tuple(a for a in AIDS if a in check_aids(aids))
    except ValueError as exc:
        raise AgentFileError(f"{where}: {exc}") from None
    max_tokens = _take(table, "max_tokens", int, where, 8000)
    options = _take(table, "options", dict, where)
    _no_leftovers(table, where, LLM_FIELDS)
    if options is not None and not is_local(model):
        raise AgentFileError(f'{where}: options apply to local models (model = "ollama/...")')
    personality = Personality(
        key,
        prompt,
        effort=effort,
        auto_fold_below=gate,
        talkative=talkative,
        aids=aids,
        description=description,
    )

    def build(name: str, seed: int, override: str | None) -> Agent:
        chosen = override or model
        client = OllamaClient(options=options) if is_local(chosen) and options else None
        return LLMAgent(name, personality, model=chosen, client=client, max_tokens=max_tokens)

    summary = description or f"LLM persona on {model}"
    return CustomKind(key, "llm", summary, build, personality)


# ----- bot ----------------------------------------------------------------------------------

BOTS: dict[str, tuple[str, ...]] = {
    "tag": ("tightness", "aggression"),
    "rock": ("tightness", "aggression"),
    "maniac": ("raise_rate",),
    "station": (),
    "random": (),
}


def _bot(key: str, table: dict[str, Any], description: str, where: str) -> CustomKind:
    base = _take(table, "base", str, where)
    if base not in BOTS:
        raise AgentFileError(f"{where}: base must be one of {', '.join(BOTS)}, not {base!r}")
    params = {p: _take(table, p, (int, float), where) for p in BOTS[base] if p in table}
    _no_leftovers(table, where, ("description", "base", *BOTS[base]))

    def build(name: str, seed: int, _model: str | None) -> Agent:
        agent: Any
        if base in ("tag", "rock"):
            defaults = {"tightness": 2.0, "aggression": 0.3} if base == "rock" else {}
            agent = TightAggressive(name, seed=seed, kind=key, **{**defaults, **params})
        elif base == "maniac":
            agent = Maniac(name, seed=seed, **params)
        elif base == "station":
            agent = CallingStation(name)
        else:
            agent = RandomAgent(name, seed=seed)
        agent.kind = key
        return agent

    tuned = ", ".join(f"{k} {v:g}" for k, v in params.items())
    summary = description or f"{base} bot" + (f" ({tuned})" if tuned else "")
    return CustomKind(key, "bot", summary, build)


# ----- python -------------------------------------------------------------------------------


def _python(
    key: str, table: dict[str, Any], description: str, base_dir: Path, where: str
) -> CustomKind:
    target = _take(table, "class", str, where)
    if not target or ":" not in target:
        raise AgentFileError(f'{where}: class = "file.py:ClassName" or "module:ClassName"')
    options = _take(table, "options", dict, where, {})
    _no_leftovers(table, where, ("description", "class", "options"))
    cls = _import_class(target, base_dir, where)
    takes_seed = "seed" in inspect.signature(cls).parameters

    def build(name: str, seed: int, _model: str | None) -> Agent:
        kwargs = {**options, **({"seed": seed} if takes_seed and "seed" not in options else {})}
        try:
            agent = cls(name, **kwargs)
        except TypeError as exc:
            raise AgentFileError(f"{where}: {target}(name, **options) failed: {exc}") from None
        if not callable(getattr(agent, "act", None)):
            raise AgentFileError(f"{where}: {target} has no act(view) method")
        if not getattr(agent, "kind", ""):
            agent.kind = key
        return agent

    summary = description or f"Python agent {target}"
    return CustomKind(key, "python", summary, build)


def _import_class(target: str, base_dir: Path, where: str) -> type:
    source, _, name = target.rpartition(":")
    try:
        if source.endswith(".py"):
            file = (base_dir / source).resolve()
            module_name = f"poker_table_custom.{file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, file)
            if spec is None or spec.loader is None or not file.exists():
                raise AgentFileError(f"{where}: no Python file {file}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        else:
            module = importlib.import_module(source)
    except AgentFileError:
        raise
    except Exception as exc:  # noqa: BLE001 - the user's module failed to import; say how
        raise AgentFileError(f"{where}: importing {source} failed: {exc!r}") from None
    cls = getattr(module, name, None)
    if not isinstance(cls, type):
        raise AgentFileError(f"{where}: {source} has no class {name!r}")
    return cls


def describe_kinds(custom: Mapping[str, CustomKind] | None = None) -> list[tuple[str, str]]:
    """(kind, one line) for every kind a seat spec may name, built-in first."""
    from poker_table.agents.registry import BUILT_IN

    rows = list(BUILT_IN.items())
    rows += [(f"llm:{k}", p.description) for k, p in PERSONALITIES.items()]
    rows += [(k, c.description) for k, c in (custom or {}).items()]
    return rows
