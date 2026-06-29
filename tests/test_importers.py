from pathlib import Path

import pytest

from poker_table.coach.facts import replay, tag_hands
from poker_table.coach.importers import ImportError_, detect_format, import_text
from poker_table.coach.importers.pokerstars import split_hands
from poker_table.coach.report import build_report
from poker_table.history import HandHistory
from poker_table.stats import compute_stats

FIXTURES = Path(__file__).parent / "fixtures"
CASH = (FIXTURES / "pokerstars_cash.txt").read_text()
TOURNEY = (FIXTURES / "pokerstars_tournament.txt").read_text()


def test_detect_and_split() -> None:
    assert detect_format(CASH) == "pokerstars"
    assert detect_format("nothing to see") is None
    assert len(list(split_hands(CASH))) == 3
    with pytest.raises(ImportError_, match="unrecognised"):
        import_text("Full Tilt Poker Game #1: ...")


def test_cash_hands_import_with_cents_as_chips() -> None:
    result = import_text(CASH)
    assert [h.hand_id for h in result.hands] == ["245000000001", "245000000002"]
    assert result.skipped == [("245000000003", "unsupported post: the ante")]
    assert result.hero == "hero"
    first = result.hands[0]
    assert (first.small_blind, first.big_blind) == (5, 10)
    assert [p.name for p in first.players] == [
        "villain1",
        "hero",
        "villain3",
        "villain4",
        "villain6",
    ]
    assert first.button == 2 and first.players[1].position == "CO"
    assert first.players[1].hole == ["Ah", "Kd"] and first.players[0].hole == []
    assert first.players[1].stack == 1235
    assert first.board == ["2c", "7d", "Kh", "9s"]
    assert [(e.seat, e.action, e.amount) for e in first.actions()][:2] == [
        (0, "raise 30", 30),
        (1, "raise 100", 100),
    ]
    assert first.payouts == {1: 435}
    assert first.players[1].net == 435 - 220  # rake makes it less than the others' losses
    assert sum(p.net for p in first.players) == -20  # the rake
    assert [e.kind for e in first.events][-1] == "hand_end"
    assert first.played_at == "2026-09-11T00:11:22+00:00"  # 20:11:22 ET (EDT) in UTC
    assert result.hands[1].played is not None


def test_showdown_side_pot_and_mucked_cards() -> None:
    second = import_text(CASH).hands[1]
    hero, btn, sb = second.players[1], second.players[3], second.players[4]
    assert hero.hole == ["7s", "2h"] and sb.hole == ["As", "Ac"]
    assert btn.hole == ["Kh", "Qh"]  # mucked, recovered from the summary
    assert second.payouts == {1: 1340, 4: 580}
    assert second.showdown[1] == "two pair, sevens and twos"
    assert second.showdown[4] == "a pair of aces"
    assert second.showdown[3] == "a pair of queens"  # mucked at showdown, but we know the cards
    assert hero.net == 1340 - 900 and btn.net == -900 and sb.net == 580 - 200
    all_in = [e for e in second.actions() if e.all_in]
    assert [(e.seat, e.action) for e in all_in] == [(4, "raise 200")]


def test_tournament_hand_uses_whole_chips() -> None:
    result = import_text(TOURNEY)
    assert not result.skipped
    hand = result.hands[0]
    assert (hand.small_blind, hand.big_blind) == (25, 50)
    assert hand.players[1].name == "hero" and hand.players[1].stack == 2210
    assert hand.board == ["8d", "7s", "2d", "Kc", "6h"]
    assert (
        hand.showdown[1] == "straight, ten high" and hand.showdown[4] == "three of a kind, eights"
    )
    assert hand.players[1].net == 3025 - 1500  # 150 preflop + 2060 - 710 uncalled
    uncalled = [e for e in hand.events if e.kind == "return_uncalled"]
    assert [(e.seat, e.amount) for e in uncalled] == [(1, 710)]


def test_imported_hands_replay_on_the_engine_and_coach_the_hero() -> None:
    hands = import_text(CASH).hands + import_text(TOURNEY).hands
    for hand in hands:
        steps = list(replay(hand))
        assert [(v.seat, str(a)) for v, a in steps] == [(e.seat, e.action) for e in hand.actions()]
    facts = tag_hands(hands, "hero", samples=60)
    tags = [(f.hand_id[-1], f.tag, f.ok) for f in facts]
    assert ("1", "three_bet", True) in tags  # AKo 3-bet
    assert ("2", "limp", False) in tags  # 72o limp
    report = build_report(hands, "hero", facts)
    assert report.hands == 3 and report.net == 215 + 440 + 1525
    stats = compute_stats(hands)
    assert stats["hero"].hands == 3 and stats["hero"].vpip == 1.0
    assert stats["villain4"].bluffs == 0  # cards known from the muck line
    assert HandHistory.from_json(hands[0].to_json()) == hands[0]
    assert "Dealt to hero [Ah Kd]" in hands[0].render()


def test_blind_mismatch_is_skipped_not_crashed() -> None:
    broken = CASH.split("\n\n")[0].replace(
        "villain4: posts small blind $0.05", "villain1: posts small blind $0.05"
    )
    result = import_text(broken)
    assert result.hands == [] and "blinds do not match" in result.skipped[0][1]


def test_cli_import_then_coach(tmp_path: Path) -> None:
    import io

    from poker_table.cli import main

    out = tmp_path / "real.jsonl"
    buf = io.StringIO()
    code = main(
        [
            "import",
            str(FIXTURES / "pokerstars_cash.txt"),
            str(FIXTURES / "pokerstars_tournament.txt"),
            "-o",
            str(out),
        ],
        out=buf,
    )
    assert code == 0
    text = buf.getvalue()
    assert "pokerstars_cash.txt: 2 hands, 1 skipped" in text
    assert "skipped #245000000003: unsupported post: the ante" in text
    assert "3 hands written" in text and "--player hero" in text
    buf = io.StringIO()
    assert main(["coach", str(out), "--player", "hero", "--samples", "40"], out=buf) == 0
    assert "Coach report for hero — 3 hands" in buf.getvalue()
    buf = io.StringIO()
    assert main(["stats", str(out)], out=buf) == 0 and "hero" in buf.getvalue()
    buf = io.StringIO()
    assert main(["replay", str(out), "--hand", "245000000002"], out=buf) == 0
    assert "hero: shows [7s 2h] (two pair, sevens and twos)" in buf.getvalue()


def test_ggpoker_dialect() -> None:
    text = (FIXTURES / "ggpoker_cash.txt").read_text()
    assert detect_format(text) == "ggpoker"
    result = import_text(text)
    assert [h.hand_id for h in result.hands] == ["HD1234567890"]
    assert result.skipped == [("HD1234567891", "run it twice")]
    assert result.hero == "Hero"
    hand = result.hands[0]
    assert (hand.small_blind, hand.big_blind) == (5, 10)
    hero = hand.players[1]
    assert hero.name == "Hero" and hero.stack == 1000 and hero.hole == ["Kc", "Kd"]
    assert hand.players[5].hole == ["As", "9s"]  # revealed by its own "Dealt to" line
    assert hand.players[5].stack == 490
    assert hero.net == 945 - 490 and hand.players[5].net == -490
    assert hand.played_at == "2026-09-12T21:45:10+00:00"
    assert hand.showdown[1] == "a pair of kings"
    assert [(v.seat, str(a)) for v, a in replay(hand)] == [
        (e.seat, e.action) for e in hand.actions()
    ]
    facts = tag_hands([hand], "Hero", samples=40)
    assert [(f.tag, f.ok) for f in facts] == [("three_bet", True), ("vs_three_bet", True)]
