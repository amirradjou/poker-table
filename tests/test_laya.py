import pytest

from poker_table.agents import LayaAgent, make_view
from poker_table.agents.laya import (
    QUESTION_ID,
    build_question,
    compact_state,
    option_actions,
    read_choice,
)
from poker_table.agents.registry import available_kinds, make_agent, make_agents
from poker_table.cards import Deck, parse_cards
from poker_table.engine import Action, Hand, Player
from poker_table.history import HandHistory
from poker_table.league import LeagueConfig, run_league
from poker_table.table import new_hand, play_hand


class FakeLaya:
    """Answers with a fixed option (or a canned result), recording what it was asked."""

    def __init__(self, option=None, *, confidence=0.9, result=None, error=None):
        self.option = option
        self.confidence = confidence
        self.result = result
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def predict(self, state, questions):
        self.calls.append((state, questions))
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        criteria = questions[QUESTION_ID]["criteria"]
        option = self.option if self.option in criteria else next(iter(criteria))
        rest = (1 - self.confidence) / max(1, len(criteria) - 1)
        return {
            "model": "fake",
            "answers": {
                QUESTION_ID: {
                    "type": "choice",
                    "choice": option,
                    "probabilities": {
                        k: (self.confidence if k == option else rest) for k in criteria
                    },
                    "confidence": self.confidence,
                    "answer_confidence": self.confidence,
                    "action": {"act_probability": 1.0},
                }
            },
            "usage": {"input_tokens": 120, "output_tokens": 0},
        }


def stacked(hole: dict[int, str], board: str = "", *, button: int = 0) -> Deck:
    n = len(hole)
    order = [(button + 1 + i) % n for i in range(n)]
    first = [parse_cards(hole[i])[0] for i in order]
    second = [parse_cards(hole[i])[1] for i in order]
    return Deck.stacked(first + second + parse_cards(board))


def hand_with(hole: dict[int, str], board: str = "", stacks=None) -> Hand:
    n = len(hole)
    players = [Player(f"p{i}", (stacks or [200] * n)[i]) for i in range(n)]
    return Hand(players, button=0, small_blind=1, big_blind=2, seed=0, deck=stacked(hole, board))


def test_state_is_compact_and_shows_only_our_cards() -> None:
    hand = hand_with({0: "Ah Kd", 1: "7c 2d", 2: "Qs Js"}, "8s 7d 2c")
    hand.apply(Action.raise_to(6))
    hand.apply(Action.fold())
    hand.apply(Action.call())
    hand.apply(Action.check())
    view = make_view(hand, 0)
    state = compact_state(view)
    assert "Ah Kd" in state and "BTN" in state
    assert "8s 7d 2c" in state and "Pot 13" in state
    assert "preflop: you raise 6" in state and "p1 fold" in state
    for card in ("7c", "2d", "Qs", "Js"):  # never another seat's hole cards
        assert card not in state
    assert len(state) < 600 and state.count("\n") < 10  # Laya's context is a few hundred tokens


def test_options_are_the_legal_actions_and_nothing_else() -> None:
    hand = hand_with({0: "Ah Kd", 1: "7c 2d", 2: "Qs Js"})
    options = option_actions(make_view(hand, 0))
    # pot 3, call 2: the min raise 4 (half the pot rounds to it too), pot-sized 7, and the shove
    assert set(options) == {"fold", "call 2", "raise to 4", "raise to 7", "raise to 200"}
    assert options["fold"] == Action.fold() and options["call 2"] == Action.call()
    assert options["raise to 200"] == Action.raise_to(200)
    # a spot where checking is free: no call option, and bets are named "bet"
    hand.apply(Action.call())
    hand.apply(Action.call())
    hand.apply(Action.check())
    flop = option_actions(make_view(hand, 1))
    assert "check" in flop and not any(k.startswith("call") for k in flop)
    assert all(k == "fold" or k == "check" or k.startswith("bet ") for k in flop)


def test_question_is_a_positive_choice_over_the_options() -> None:
    hand = hand_with({0: "Ah Kd", 1: "7c 2d", 2: "Qs Js"})
    options, question = build_question(make_view(hand, 0))
    q = question[QUESTION_ID]
    assert q["type"] == "choice" and set(q["criteria"]) == set(options)
    assert all(text and "not " not in text.lower() for text in q["criteria"].values())
    assert len(q["criteria"]) <= 20


def test_seat_plays_the_option_the_model_picks() -> None:
    client = FakeLaya("raise to 7", confidence=0.71)
    agent = LayaAgent("laya", client=client)
    hand = hand_with({0: "Ah Kd", 1: "7c 2d", 2: "Qs Js"})
    decision = agent.act(make_view(hand, 0))
    assert decision.action == Action.raise_to(7)
    assert decision.meta["cost_usd"] == 0.0 and decision.meta["output_tokens"] == 0
    assert (
        decision.meta["confidence"] == 0.71 and decision.meta["probabilities"]["raise to 7"] == 0.71
    )
    assert "raise to 7" in decision.reasoning
    assert agent.usage.decisions == 1 and agent.usage.gated == 0
    assert agent.usage.avg_latency_ms is not None and agent.usage.avg_confidence == 0.71
    state, questions = client.calls[0]
    assert "Ah Kd" in state and QUESTION_ID in questions


def test_low_confidence_falls_back_to_the_chart() -> None:
    hand = hand_with({0: "7c 2h", 1: "Ah Kd", 2: "Qs Js"})  # junk on the button
    agent = LayaAgent("laya", client=FakeLaya("raise to 200", confidence=0.1), confidence=0.5)
    decision = agent.act(make_view(hand, 0))
    assert decision.action == Action.fold()  # the chart folds 72o, not the model's shove
    assert decision.meta["gated"] is True and decision.meta["confidence"] == 0.1
    assert "chart instead" in decision.reasoning and "Chen" in decision.reasoning
    assert agent.usage.gated == 1 and agent.usage.decisions == 0


def test_errors_and_off_menu_answers_degrade_to_the_chart() -> None:
    hand = hand_with({0: "Ah Kd", 1: "7c 2d", 2: "Qs Js"})
    view = make_view(hand, 0)
    broken = LayaAgent("laya", client=FakeLaya(error=RuntimeError("no weights")))
    decision = broken.act(view)
    assert decision.action.type.value in ("fold", "call", "raise")  # whatever the chart says
    assert "laya error" in decision.reasoning and broken.usage.errors == 1

    off_menu = LayaAgent(
        "laya", client=FakeLaya(result={"answers": {QUESTION_ID: {"choice": "shove"}}})
    )
    decision = off_menu.act(view)
    assert "not an option" in decision.reasoning and off_menu.usage.errors == 1

    empty = LayaAgent("laya", client=FakeLaya(result={"answers": {}}))
    assert "laya error" in empty.act(view).reasoning


def test_read_choice_prefers_the_calibrated_confidence() -> None:
    result = {
        "answers": {
            QUESTION_ID: {
                "choice": "call 12",
                "probabilities": {"fold": 0.18, "call 12": 0.82},
                "confidence": 0.51,
                "answer_confidence": 0.82,
                "action": {"act_probability": 1.0},
            }
        },
        "usage": {"input_tokens": 136, "output_tokens": 0},
    }
    choice = read_choice(result)
    assert choice.option == "call 12" and choice.input_tokens == 136
    assert choice.confidence == 0.51 and choice.answer_confidence == 0.82
    with pytest.raises(ValueError, match="no 'action' choice"):
        read_choice({"answers": {}})


def test_registry_seats_laya_without_loading_a_model() -> None:
    agents = make_agents(["laya", "ada:laya", "laya@convaiinnovations/laya#typed-decisions"])
    assert [a.name for a in agents] == ["laya", "ada", "laya2"]
    assert all(isinstance(a, LayaAgent) for a in agents)
    assert agents[2].subfolder == "typed-decisions"
    assert all(a.kind == "laya" for a in agents)
    assert "laya" in available_kinds()
    with pytest.raises(ValueError, match="unknown agent kind"):
        make_agent("laya-ish")


def test_a_laya_seat_never_acts_illegally_over_a_session() -> None:
    agents = [
        LayaAgent("laya", client=FakeLaya("fold", confidence=0.95)),
        *make_agents(["tag", "station"], seed=3),
    ]
    result = run_league(agents, LeagueConfig(hands=25, seed=7))
    facts = [d for h in result.histories for d in h.decisions if h.players[d.seat].name == "laya"]
    assert facts and not any(d.illegal for d in facts)
    assert all(d.meta.get("cost_usd") == 0.0 for d in facts)
    # the kind reaches the history, so the viewer can draw it and stats can group by it
    assert result.histories[0].players[0].kind == "laya"


def test_the_seat_is_free_and_fast_on_the_leaderboard() -> None:
    from poker_table.stats import compute_stats

    hand, seated = new_hand(
        [LayaAgent("laya", client=FakeLaya("call 1", confidence=0.8)), *make_agents(["station"])],
        [100, 100],
        button=0,
        small_blind=1,
        big_blind=2,
        seed=4,
    )
    history = HandHistory.from_played(play_hand(hand, seated))
    stats = compute_stats([history])["laya"]
    assert stats.model_calls >= 1 and stats.cost_usd == 0.0
    assert stats.cost_per_hand == 0.0 and stats.illegal_rate == 0.0


def test_play_reports_how_much_of_a_laya_seat_was_really_the_model() -> None:
    from poker_table.agents.scripted import CallingStation as Station
    from poker_table.cli import model_seat_notes

    sure = LayaAgent("sure", client=FakeLaya("fold", confidence=0.9))
    unsure = LayaAgent("unsure", client=FakeLaya("fold", confidence=0.01), confidence=0.5)
    for seat in (sure, unsure):
        for _ in range(3):
            hand = hand_with({0: "Ah Kd", 1: "7c 2d", 2: "Qs Js"})
            seat.act(make_view(hand, 0))
    notes = model_seat_notes([sure, unsure, Station("s")])
    assert len(notes) == 2
    assert "sure: 3/3 decisions were the model's" in notes[0]
    assert "unsure: 0/3 decisions were the model's" in notes[1]
    assert "handed to the chart below 50% confidence" in notes[1]
    assert model_seat_notes([Station("s")]) == []
