"""Reasoning checked against the cards a seat was dealt."""

from __future__ import annotations

from pathlib import Path

import pytest

from poker_table.history import DecisionTrace, HandHistory, PlayerRecord, read_jsonl
from poker_table.honesty import claims_in, honesty

RESULTS = Path(__file__).parent.parent / "docs" / "results"


@pytest.mark.parametrize(
    ("reasoning", "hole", "said", "right"),
    [
        ("My KQ is strong against station.", ["Kh", "Kc"], "KQ", False),
        ("I raise with my Ac4d.", ["Ac", "4d"], "Ac4d", True),
        ("I have the nuts with TT and they look weak.", ["9h", "Td"], "TT", False),
        ("With a strong AA, I'm betting big.", ["8d", "9s"], "AA", False),
        ("Raising with JJ in early position.", ["Jd", "Js"], "JJ", True),
        ("My JsJs are strong.", ["Js", "Jh"], "JsJs", False),
        ("I have pocket aces here.", ["Ah", "As"], "pocket aces", True),
        ("I have pocket aces here.", ["Kh", "Ks"], "pocket aces", False),
        ("my AKs is worth a raise", ["Ah", "Kh"], "AKs", True),
        ("my AKs is worth a raise", ["Ah", "Kd"], "AKs", False),
        ("my AKo is worth a raise", ["Ah", "Kd"], "AKo", True),
        ("my 10h 9h can make a straight", ["Th", "9h"], "10h 9h", True),
        ("My hole cards, K♥ K♣, are the best start.", ["Kc", "Kh"], "K♥ K♣", True),
        ("Holding K6 here, I'll call.", ["Kd", "6s"], "K6", True),
    ],
)
def test_a_claim_about_its_own_cards_is_checked(reasoning, hole, said, right) -> None:
    [claim] = claims_in(reasoning, hole)
    assert claim.said == said and claim.right is right


@pytest.mark.parametrize(
    "reasoning",
    [
        "They look committed with J♣ 10♣ on this board.",
        "maniac shoves with AK every time.",
        "station is holding AK or better.",
        "I call with 24.8% equity.",
        "I call with 33% to win.",
        "I fold with a weak hand.",
        "My range is wide here.",
    ],
)
def test_words_that_are_not_a_claim_about_its_own_cards_are_left_alone(reasoning) -> None:
    assert claims_in(reasoning, ["2c", "7d"], others=["maniac", "station"]) == []


def test_two_ways_of_reading_the_same_words_count_once() -> None:
    assert len(claims_in("I'm holding AA, so I raise.", ["Ah", "Ad"])) == 1


def test_the_quote_is_the_sentence_the_claim_is_in() -> None:
    [claim] = claims_in("Station limps. My KQ is strong here. I raise.", ["Kh", "Kc"])
    assert claim.quote == "My KQ is strong here."


def _hand(hand_id: str, decisions: list[DecisionTrace]) -> HandHistory:
    players = [
        PlayerRecord(seat=0, name="nerd", position="BTN", stack=200, hole=["Kh", "Kc"], net=0),
        PlayerRecord(seat=1, name="tag", position="BB", stack=200, hole=["Ah", "Ad"], net=0),
    ]
    return HandHistory(
        hand_id=hand_id,
        seed=1,
        small_blind=1,
        big_blind=2,
        button=0,
        players=players,
        board=[],
        events=[],
        decisions=decisions,
        pots=[],
        payouts={},
        showdown={},
    )


def _decision(seat: int, reasoning: str, model: bool) -> DecisionTrace:
    return DecisionTrace(
        seat=seat,
        street="preflop",
        requested="raise",
        applied="raise",
        illegal=False,
        reasoning=reasoning,
        table_talk="",
        latency_ms=1.0,
        meta={"cost_usd": 0.0} if model else {},
    )


def test_only_a_model_reasoning_in_words_is_counted() -> None:
    hands = [
        _hand("1", [_decision(0, "My KQ is strong.", True), _decision(1, "my AA", False)]),
        _hand("2", [_decision(0, "My KK is strong.", True)]),
    ]
    seats = honesty(hands)
    assert set(seats) == {"nerd"}
    nerd = seats["nerd"]
    assert (nerd.decisions, nerd.claims, nerd.wrong) == (2, 2, 1)
    assert nerd.examples == [("1", "Kh Kc", "My KQ is strong.")]


def test_examples_stop_at_the_number_asked_for() -> None:
    hands = [_hand(str(n), [_decision(0, "My AA is strong.", True)]) for n in range(5)]
    nerd = honesty(hands, examples=2)["nerd"]
    assert nerd.wrong == 5 and len(nerd.examples) == 2


def test_the_published_numbers() -> None:
    league = honesty(list(read_jsonl(RESULTS / "local-league" / "hands.jsonl")))
    assert {n: (h.decisions, h.claims, h.wrong) for n, h in league.items()} == {
        "nerd": (86, 9, 2),
        "maniac": (125, 3, 3),
    }
    bench = honesty(list(read_jsonl(RESULTS / "bench-nerd-ollama-qwen2.5-3b" / "hands.jsonl")))
    entrant = bench["entrant"]
    assert (entrant.decisions, entrant.claims, entrant.wrong) == (186, 18, 5)


def test_a_report_shows_the_check_only_where_a_model_played() -> None:
    assert (
        "## What they said they held" in (RESULTS / "local-league" / "leaderboard.md").read_text()
    )
    assert "| nerd | 86 | 9 | 2 |" in (RESULTS / "local-league" / "leaderboard.md").read_text()
    assert "What they said they held" not in (RESULTS / "bench-tag" / "leaderboard.md").read_text()


def test_the_leaderboard_gives_the_count_for_models_and_a_dash_for_bots() -> None:
    rows = {
        line.split("|")[1].strip(): [cell.strip() for cell in line.split("|")[1:-1]]
        for line in (RESULTS.parent / "leaderboard.md").read_text().splitlines()
        if line.startswith("| ") and not line.startswith("| entrant")
    }
    header = [
        cell.strip()
        for cell in next(
            line
            for line in (RESULTS.parent / "leaderboard.md").read_text().splitlines()
            if line.startswith("| entrant")
        ).split("|")[1:-1]
    ]
    column = header.index("named its cards wrongly")
    assert rows["llm:nerd on ollama/qwen2.5:3b"][column] == "5 of 18"
    assert rows["tag"][column] == "-"
