import json
from types import SimpleNamespace

import pytest

from poker_table.agents import LLMAgent
from poker_table.agents.base import Decision
from poker_table.agents.registry import make_agents
from poker_table.engine import Action, ActionType
from poker_table.evals import build_questions, format_scores, majority_rate, score
from poker_table.league import LeagueConfig, run_league


@pytest.fixture(scope="module")
def questions():
    bots = make_agents(["tag", "rock", "station", "maniac", "random"], seed=3)
    histories = run_league(bots, LeagueConfig(hands=80, seed=3)).histories
    players = ["tag", "rock", "station", "maniac", "random"]
    return build_questions(histories, players, count=40, samples=60, seed=3)


class Oracle:
    """Answers with the first accepted action: what a perfect student would do."""

    name = "oracle"

    def __init__(self, questions):
        self.answers = {id(q.view): q for q in questions}

    def act(self, view):
        q = self.answers[id(view)]
        kind = sorted(q.accepted, key=lambda a: a.value)[0]
        legal = view.legal
        if kind in (ActionType.BET, ActionType.RAISE):
            return Decision(Action(kind, legal.min_raise_to))
        return Decision(Action(kind))


class Folder:
    name = "folder"

    def act(self, view):
        return Decision(Action.check() if view.legal.can_check else Action.fold())


def test_half_the_questions_are_mistakes_and_half_are_fine(questions) -> None:
    assert len(questions) == 40
    assert sum(q.mistake for q in questions) == 20
    assert all(q.accepted for q in questions)
    assert len({q.key for q in questions}) == len(questions)
    # a mistake's accepted answer is never what was played
    for q in questions:
        played = Action(ActionType(q.played.split(" ")[0]), 1)
        assert q.accepts(played) != q.mistake


def test_a_perfect_student_scores_everything_and_a_folder_scores_the_fold_share(questions):
    perfect = score(Oracle(questions), questions)
    assert perfect.rate == 1.0 and perfect.fixed == perfect.mistakes == 20
    folder = score(Folder(), questions)
    share = sum(q.accepts(Action.fold()) or q.accepts(Action.check()) for q in questions)
    assert folder.agreed <= share
    answer, rate = majority_rate(questions)
    assert rate >= max(
        sum(q.accepts(Action(k, 1)) for q in questions) / len(questions)
        for k in (ActionType.FOLD, ActionType.CALL, ActionType.RAISE)
    )
    table = format_scores([perfect, folder], questions)
    assert "oracle" in table and "100%" in table and f"always {answer}" in table


def test_a_bet_and_a_raise_are_the_same_answer() -> None:
    from poker_table.evals import Question

    q = Question("k", None, frozenset({ActionType.RAISE}), "open", False, "raise 6")
    assert q.accepts(Action.bet(10)) and q.accepts(Action.raise_to(10))
    assert not q.accepts(Action.call())


def test_the_same_questions_every_time(questions) -> None:
    bots = make_agents(["tag", "rock", "station", "maniac", "random"], seed=3)
    histories = run_league(bots, LeagueConfig(hands=80, seed=3)).histories
    again = build_questions(
        histories, ["tag", "rock", "station", "maniac", "random"], count=40, samples=60, seed=3
    )
    assert [q.key for q in again] == [q.key for q in questions]


class FakeMessages:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = {"action": "fold", "amount": 0, "table_talk": "", "reasoning": "", "note": "x"}
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(reply))],
            stop_reason="end_turn",
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=10,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


def test_an_llm_seat_answers_each_question_fresh_and_its_cost_is_counted(questions) -> None:
    client = SimpleNamespace(messages=FakeMessages())
    seat = LLMAgent("llm", "maniac", client=client)
    result = score(seat, questions[:6])
    assert result.asked == 6 and result.cost_usd > 0
    for request in client.messages.requests:
        assert "Your notes from earlier hands" not in request["messages"][0]["content"]


def test_eval_on_the_command_line(tmp_path) -> None:
    import io

    from poker_table.cli import main

    out = io.StringIO()
    path = tmp_path / "scores.json"
    code = main(
        ["eval", "--seats", "tag,station", "-n", "20", "--hands", "60", "--json", str(path)],
        out=out,
    )
    text = out.getvalue()
    assert code == 0 and "20 questions (10 spots the coach calls mistakes" in text
    assert "tag " in text and "station " in text and "always " in text
    data = json.loads(path.read_text())
    assert [s["seat"] for s in data["scores"]] == ["tag", "station"]
    assert len(data["answers"]) == 40


def test_eval_takes_aids_for_llm_seats_and_refuses_humans(capsys) -> None:
    import io

    from poker_table.cli import main

    code = main(["eval", "--seats", "me:human", "-n", "4", "--hands", "30"], out=io.StringIO())
    assert code == 2 and "drill" in capsys.readouterr().err
    code = main(
        ["eval", "--seats", "llm:rock", "--aids", "telepathy", "-n", "4", "--hands", "30"],
        out=io.StringIO(),
    )
    assert code == 2 and "unknown aid telepathy" in capsys.readouterr().err
