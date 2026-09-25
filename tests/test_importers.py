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
    assert [h.hand_id for h in result.hands] == [
        "245000000001",
        "245000000002",
        "245000000003",
    ]
    assert result.skipped == []
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


def test_an_ante_hand_imports_with_the_ante_as_dead_money() -> None:
    hand = import_text(CASH).hands[2]
    assert hand.ante == 2  # $0.02 in cents
    antes = [e for e in hand.events if e.kind == "post_ante"]
    assert [(e.seat, e.amount) for e in antes] == [(0, 2), (1, 2), (2, 2), (3, 2)]
    # the ante buys no part of the blind: everyone folds to the big blind, who is returned
    # only the uncalled 5 of the blind, and collects the four antes with it
    assert [(e.seat, e.amount) for e in hand.events if e.kind == "return_uncalled"] == [(1, 5)]
    assert hand.payouts == {1: 18} and hand.players[1].net == 11
    assert [p.net for p in hand.players] == [-7, 11, -2, -2]
    assert "posts the ante 2" in hand.render()


def test_tournament_hand_uses_whole_chips() -> None:
    result = import_text(TOURNEY)
    # the second hand is a big blind ante, which the engine cannot post seat for seat
    assert result.skipped == [("245000000011", "uneven antes: hero posted 0 of 600")]
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
    assert report.hands == 4 and report.net == 215 + 440 + 11 + 1525
    stats = compute_stats(hands)
    assert stats["hero"].hands == 4 and stats["hero"].vpip == 0.75  # the ante hand folded to him
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
    assert "pokerstars_cash.txt: 3 hands, 0 skipped" in text
    assert "pokerstars_tournament.txt: 1 hands, 1 skipped" in text
    assert "skipped #245000000011: uneven antes: hero posted 0 of 600" in text
    assert "4 hands written" in text and "--player hero" in text
    buf = io.StringIO()
    assert main(["coach", str(out), "--player", "hero", "--samples", "40"], out=buf) == 0
    assert "Coach report for hero — 4 hands" in buf.getvalue()
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


def test_imported_hands_carry_their_hero_and_the_cli_uses_it(tmp_path: Path) -> None:
    import io

    from poker_table.cli import main, resolve_player

    cash = import_text(CASH).hands
    assert all(h.hero == "hero" for h in cash)
    assert HandHistory.from_json(cash[0].to_json()).hero == "hero"
    assert resolve_player(cash, None, Path("x")) == "hero"
    assert resolve_player(cash, "villain1", Path("x")) == "villain1"
    gg = import_text((FIXTURES / "ggpoker_cash.txt").read_text()).hands
    with pytest.raises(ValueError, match="several heroes"):
        resolve_player(cash + gg, None, Path("x"))
    with pytest.raises(ValueError, match="no seat named"):
        resolve_player(cash, "nobody", Path("x"))
    out = tmp_path / "h.jsonl"
    main(["import", str(FIXTURES / "pokerstars_cash.txt"), "-o", str(out)], out=io.StringIO())
    buf = io.StringIO()
    assert main(["coach", str(out), "--samples", "30"], out=buf) == 0
    assert "Coach report for hero" in buf.getvalue()


def test_888_dialect() -> None:
    text = (FIXTURES / "888_cash.txt").read_text()
    assert detect_format(text) == "888"
    result = import_text(text)
    assert [h.hand_id for h in result.hands] == ["1122334455", "1122334456"]
    assert result.skipped == [] and result.hero == "hero"
    first, second = result.hands
    assert (first.small_blind, first.big_blind) == (1, 2)
    assert first.played_at == "2026-09-10T20:11:22+00:00"
    assert [p.name for p in first.players][:3] == ["villain1", "hero", "villain3"]
    assert first.players[1].position == "CO" and first.button == 2
    assert first.players[1].hole == ["Ah", "Kd"] and first.hero == "hero"
    # "raises [$X]" is what the player added: villain1 to 6, hero to 20, villain1 calls 14
    assert [(e.seat, e.action, e.amount) for e in first.actions()][1:3] == [
        (0, "raise 6", 6),
        (1, "raise 20", 20),
    ]
    assert [e for e in first.actions() if e.action == "call"][0].amount == 14
    # no "uncalled bet" line in the file, so the builder returned hero's 50 on the turn
    returned = [e for e in first.events if e.kind == "return_uncalled"]
    assert [(e.seat, e.amount) for e in returned] == [(1, 50)]
    assert first.payouts == {1: 87}
    assert first.players[1].net == 87 - 20 - 24  # rake-adjusted
    assert first.players[0].net == -(20 + 24)
    # second hand: the small blind's re-raise adds on top of the posted blind
    hero_raise = [e for e in second.actions() if e.seat == 1 and e.action.startswith("raise")]
    assert [e.action for e in hero_raise] == ["raise 6", "raise 60"]
    sb = [e for e in second.actions() if e.seat == 4 and e.action.startswith("raise")]
    assert sb[0].action == "raise 20"  # posted 1, added 19
    assert second.showdown[1] == "three of a kind, queens"
    assert second.showdown[4] == "a pair of aces"
    assert second.players[1].hole == ["Qs", "Qd"] and second.players[4].hole == ["Ac", "As"]
    # hero shoved 123 into villain5's last 78: the 45 nobody could call comes back
    assert [(e.seat, e.amount) for e in second.events if e.kind == "return_uncalled"] == [(1, 45)]
    assert second.players[1].net == 385 - 198 and second.players[4].net == -198
    for hand in result.hands:
        assert [(v.seat, str(a)) for v, a in replay(hand)] == [
            (e.seat, e.action) for e in hand.actions()
        ]
    facts = tag_hands(result.hands, "hero", samples=40)
    assert facts and all(f.hand_id in ("1122334455", "1122334456") for f in facts)


def test_888_antes_are_dead_money_too() -> None:
    text = (FIXTURES / "888_cash.txt").read_text()
    anted = text.replace(
        "villain4 posts small blind [$0.01]",
        "\n".join(f"villain{i} posts ante [$0.01]" for i in (1, 3, 4, 5, 6))
        + "\nhero posts ante [$0.01]\nvillain4 posts small blind [$0.01]",
        1,
    )
    plain, with_antes = import_text(text).hands[0], import_text(anted).hands[0]
    assert with_antes.ante == 1 and plain.ante == 0
    assert [e.amount for e in with_antes.events if e.kind == "post_ante"] == [1] * 6
    # the ante is not part of what anyone called, so the inferred uncalled bet is unchanged
    returned = [(e.seat, e.amount) for e in with_antes.events if e.kind == "return_uncalled"]
    assert returned == [(1, 50)]
    assert [p.net for p in with_antes.players] == [p.net - 1 for p in plain.players]
