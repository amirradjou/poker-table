import pytest

from poker_table.agents import LLMAgent, TightAggressive
from poker_table.agents.custom import AgentFileError, load_agent_files, load_agents
from poker_table.agents.ollama import OllamaClient
from poker_table.agents.registry import available_kinds, make_agents
from poker_table.league import LeagueConfig, run_league

AGENTS = """
[agents.shark]
description = "Patient, then ruthless"
type = "llm"
model = "claude-sonnet-5"
prompt = "You are THE SHARK. You wait, then you strike."
effort = "high"
auto_fold_below = 6
talkative = false
aids = ["odds", "ranges"]

[agents.calc]
type = "llm"
extends = "nerd"
model = "ollama/qwen2.5:7b"
options = { temperature = 0.2 }

[agents.nit]
type = "bot"
base = "tag"
tightness = 3
aggression = 0.4

[agents.wild]
type = "bot"
base = "maniac"
raise_rate = 0.95

[agents.bluffer]
type = "python"
class = "my_bots.py:Bluffer"
options = { rate = 0.5 }
"""

BOT = '''
import random

from poker_table.agents.base import Decision
from poker_table.engine import Action


class Bluffer:
    """Bets the pot when checked to, folds to any bet — some of the time."""

    def __init__(self, name, rate=0.3, seed=0):
        self.name = name
        self.rate = rate
        self.rng = random.Random(seed)

    def act(self, view):
        legal = view.legal
        if legal.can_check and legal.can_raise and self.rng.random() < self.rate:
            amount = min(max(view.pot, legal.min_raise_to), legal.max_raise_to)
            return Decision(Action.bet(amount) if legal.is_bet else Action.raise_to(amount))
        return Decision(Action.check() if legal.can_check else Action.fold())
'''


@pytest.fixture
def agents_file(tmp_path):
    (tmp_path / "my_bots.py").write_text(BOT)
    path = tmp_path / "agents.toml"
    path.write_text(AGENTS)
    return path


def test_an_llm_persona_needs_no_code(agents_file) -> None:
    kinds = load_agents(agents_file)
    (shark,) = make_agents(["shark"], custom=kinds)
    assert isinstance(shark, LLMAgent) and shark.name == "shark" and shark.kind == "llm:shark"
    assert shark.model == "claude-sonnet-5" and shark.personality.effort == "high"
    assert shark.personality.auto_fold_below == 6 and not shark.personality.talkative
    assert shark.aids == {"odds", "ranges"} and shark.reads is None
    assert "You are THE SHARK" in shark.system_prompt
    assert "How strong players play" not in shark.system_prompt  # no playbook asked for
    assert kinds["shark"].description == "Patient, then ruthless"


def test_a_persona_can_start_from_a_built_in_and_run_locally(agents_file) -> None:
    (calc,) = make_agents(["calc"], custom=load_agents(agents_file))
    assert "THE MATH NERD" in calc.system_prompt and calc.personality.effort == "high"
    assert calc.model == "ollama/qwen2.5:7b"
    assert isinstance(calc.client, OllamaClient) and calc.client.options["temperature"] == 0.2


def test_one_seat_can_take_another_model(agents_file) -> None:
    kinds = load_agents(agents_file)
    a, b, c = make_agents(
        ["shark@ollama/qwen2.5:3b", "ann:shark@claude-haiku-4-5", "llm:shark@claude-opus-5"],
        custom=kinds,
    )
    assert (a.name, a.model) == ("shark", "ollama/qwen2.5:3b")
    assert (b.name, b.model) == ("ann", "claude-haiku-4-5")
    assert (c.name, c.model) == ("shark2", "claude-opus-5")
    with pytest.raises(ValueError, match="only llm kinds take @model"):
        make_agents(["nit@claude-opus-5"], custom=kinds)


def test_tuned_bots_and_python_bots_take_their_seats(agents_file) -> None:
    kinds = load_agents(agents_file)
    nit, wild, bluffer, named = make_agents(["nit", "wild", "bluffer", "bo:bluffer"], custom=kinds)
    assert isinstance(nit, TightAggressive) and nit.tightness == 3 and nit.aggression == 0.4
    assert nit.kind == "nit" and wild.raise_rate == 0.95 and wild.kind == "wild"
    assert type(bluffer).__name__ == "Bluffer" and bluffer.rate == 0.5
    assert bluffer.kind == "bluffer" and named.name == "bo"
    assert "bluffer" in available_kinds(kinds) and "tag" in available_kinds(kinds)
    result = run_league([nit, wild, bluffer], LeagueConfig(hands=30, seed=2))
    assert {p.kind for p in result.histories[0].players} == {"nit", "wild", "bluffer"}
    assert sum(result.bankrolls.values()) == 0


def test_python_bots_get_their_own_seed(agents_file) -> None:
    kinds = load_agents(agents_file)
    first = make_agents(["bluffer", "bluffer"], seed=1, custom=kinds)
    assert [a.name for a in first] == ["bluffer", "bluffer2"]
    assert first[0].rng.random() != first[1].rng.random()


def test_two_files_combine_and_a_name_is_defined_once(tmp_path, agents_file) -> None:
    other = tmp_path / "more.toml"
    other.write_text('[agents.fish]\ntype = "bot"\nbase = "station"\n')
    kinds = load_agent_files([agents_file, other])
    assert {"shark", "fish"} <= set(kinds)
    with pytest.raises(AgentFileError, match="already defined"):
        load_agent_files([agents_file, agents_file])


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ('[agents.x]\ntype = "llm"\n', "needs a prompt"),
        ('[agents.x]\ntype = "llm"\nextends = "shark"\n', "built-in personalities are"),
        ('[agents.x]\ntype = "llm"\nprompt = "p"\neffort = "huge"\n', "effort must be one of"),
        ('[agents.x]\ntype = "llm"\nprompt = "p"\naids = ["x-ray"]\n', "unknown aid x-ray"),
        ('[agents.x]\ntype = "llm"\nprompt = "p"\nmodle = "a"\n', "unknown field.*modle"),
        ('[agents.x]\ntype = "llm"\nprompt = "p"\noptions = {a = 1}\n', "apply to local models"),
        ('[agents.x]\ntype = "llm"\nprompt = "p"\nauto_fold_below = "5"\n', "Chen score"),
        ('[agents.x]\ntype = "bot"\nbase = "shark"\n', "base must be one of"),
        ('[agents.x]\ntype = "bot"\nbase = "station"\ntightness = 2\n', "unknown field"),
        ('[agents.x]\ntype = "bot"\nbase = "tag"\ntightness = "loose"\n', "must be a int or"),
        ('[agents.x]\ntype = "robot"\n', "type must be"),
        ('[agents.tag]\ntype = "bot"\nbase = "tag"\n', "built-in kind"),
        ('[agents.nerd]\ntype = "bot"\nbase = "tag"\n', "built-in kind"),
        ('[agents."Big Fish"]\ntype = "bot"\nbase = "tag"\n', "lower-case"),
        ('[agents.x]\ntype = "python"\nclass = "nope.py:Bot"\n', "no Python file"),
        ('[agents.x]\ntype = "python"\nclass = "Bot"\n', "file.py:ClassName"),
        ('[agents.x]\ntype = "python"\nclass = "json:Nope"\n', "has no class 'Nope'"),
        ("[table]\nx = 1\n", "no \\[agents"),
        ("[agents.x\n", "agents.toml"),
    ],
)
def test_mistakes_in_the_file_are_named(tmp_path, toml: str, message: str) -> None:
    path = tmp_path / "agents.toml"
    path.write_text(toml)
    with pytest.raises(AgentFileError, match=message):
        load_agents(path)


def test_a_python_class_without_act_is_refused_when_seated(tmp_path) -> None:
    (tmp_path / "lazy.py").write_text("class Lazy:\n    def __init__(self, name):\n        pass\n")
    path = tmp_path / "agents.toml"
    path.write_text('[agents.lazy]\ntype = "python"\nclass = "lazy.py:Lazy"\n')
    with pytest.raises(AgentFileError, match="no act"):
        make_agents(["lazy"], custom=load_agents(path))
