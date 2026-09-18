"""Per-player exploitability stats over a set of hand histories.

Definitions follow the usual tracker conventions:

- VPIP: hands where the player voluntarily put chips in preflop (call/bet/raise; blinds and
  a big-blind check do not count).
- PFR: hands where the player raised preflop.
- 3-bet: raised when facing exactly one preflop raise; the opportunity is having faced it.
- Fold to 3-bet: folded after opening and facing a 3-bet.
- AF (aggression factor): postflop (bets + raises) / calls.
- WTSD: went to showdown given saw the flop.  W$SD: won at showdown given went to showdown.
- Bluff rate: postflop bets/raises made with no pair and no draw, over all postflop bets/raises.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from poker_table.agents.strength import MadeHand, classify, has_flush_draw, has_open_ender
from poker_table.cards import Card
from poker_table.engine import ActionType, EventKind, Street
from poker_table.history import HandHistory, board_cards, hole_cards, parse_action

_AGGRESSIVE = {ActionType.BET, ActionType.RAISE}


@dataclass(slots=True)
class PlayerStats:
    name: str
    hands: int = 0
    net: int = 0
    big_blinds: float = 0.0  # net measured in big blinds, summed per hand
    vpip_hands: int = 0
    pfr_hands: int = 0
    three_bet_opps: int = 0
    three_bets: int = 0
    fold_to_three_bet_opps: int = 0
    fold_to_three_bets: int = 0
    postflop_bets_raises: int = 0
    postflop_calls: int = 0
    postflop_folds: int = 0
    bluffs: int = 0
    saw_flop: int = 0
    showdowns: int = 0
    showdown_wins: int = 0
    decisions: int = 0
    illegal: int = 0
    latency_ms_total: float = 0.0
    talks: int = 0
    model_calls: int = 0
    cost_usd: float = 0.0

    # -- derived rates (None when there was no opportunity) --

    @property
    def bb_per_100(self) -> float | None:
        return None if self.hands == 0 else 100 * self.big_blinds / self.hands

    @property
    def vpip(self) -> float | None:
        return _rate(self.vpip_hands, self.hands)

    @property
    def pfr(self) -> float | None:
        return _rate(self.pfr_hands, self.hands)

    @property
    def three_bet(self) -> float | None:
        return _rate(self.three_bets, self.three_bet_opps)

    @property
    def fold_to_three_bet(self) -> float | None:
        return _rate(self.fold_to_three_bets, self.fold_to_three_bet_opps)

    @property
    def aggression_factor(self) -> float | None:
        if self.postflop_calls == 0:
            return None if self.postflop_bets_raises == 0 else float("inf")
        return self.postflop_bets_raises / self.postflop_calls

    @property
    def wtsd(self) -> float | None:
        return _rate(self.showdowns, self.saw_flop)

    @property
    def won_at_showdown(self) -> float | None:
        return _rate(self.showdown_wins, self.showdowns)

    @property
    def bluff_rate(self) -> float | None:
        return _rate(self.bluffs, self.postflop_bets_raises)

    @property
    def illegal_rate(self) -> float | None:
        return _rate(self.illegal, self.decisions)

    @property
    def avg_latency_ms(self) -> float | None:
        return None if self.decisions == 0 else self.latency_ms_total / self.decisions

    @property
    def cost_per_hand(self) -> float | None:
        return None if self.hands == 0 or self.model_calls == 0 else self.cost_usd / self.hands

    def as_row(self) -> dict[str, object]:
        return {
            "name": self.name,
            "hands": self.hands,
            "net": self.net,
            "bb/100": self.bb_per_100,
            "vpip": self.vpip,
            "pfr": self.pfr,
            "3bet": self.three_bet,
            "f3b": self.fold_to_three_bet,
            "af": self.aggression_factor,
            "wtsd": self.wtsd,
            "w$sd": self.won_at_showdown,
            "bluff": self.bluff_rate,
            "illegal": self.illegal_rate,
            "ms": self.avg_latency_ms,
            "$/hand": self.cost_per_hand,
        }


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def compute_stats(histories: Iterable[HandHistory]) -> dict[str, PlayerStats]:
    """Aggregate stats per player name across ``histories``."""
    stats: dict[str, PlayerStats] = {}
    for history in histories:
        _add_hand(stats, history)
    return stats


def _add_hand(stats: dict[str, PlayerStats], history: HandHistory) -> None:
    by_seat = {p.seat: stats.setdefault(p.name, PlayerStats(p.name)) for p in history.players}
    for p in history.players:
        s = by_seat[p.seat]
        s.hands += 1
        s.net += p.net
        s.big_blinds += p.net / history.big_blind

    board = board_cards(history)
    holes: dict[int, tuple[Card, Card]] = {
        p.seat: hole_cards(history, p.seat) for p in history.players
    }
    raises = 0  # preflop raises so far
    opener: int | None = None
    vpip: set[int] = set()
    pfr: set[int] = set()
    saw_flop: set[int] = set()
    folded: set[int] = set()
    street_len = {Street.FLOP.value: 3, Street.TURN.value: 4, Street.RIVER.value: 5}

    for e in history.events:
        if e.kind == EventKind.STREET.value and e.street == Street.FLOP.value:
            saw_flop = {p.seat for p in history.players if p.seat not in folded}
        if e.kind != EventKind.ACTION.value or e.seat is None or e.action is None:
            continue
        seat = e.seat
        s = by_seat[seat]
        action = parse_action(e.action)
        aggressive = action.type in _AGGRESSIVE
        if e.street == Street.PREFLOP.value:
            if raises == 1 and seat != opener:
                s.three_bet_opps += 1
                s.three_bets += aggressive
            if raises == 2 and seat == opener:
                s.fold_to_three_bet_opps += 1
                s.fold_to_three_bets += action.type is ActionType.FOLD
            if aggressive:
                raises += 1
                opener = seat if opener is None else opener
                pfr.add(seat)
                vpip.add(seat)
            elif action.type is ActionType.CALL:
                vpip.add(seat)
        else:
            if aggressive:
                s.postflop_bets_raises += 1
                if _is_air(holes[seat], board[: street_len[e.street]]):
                    s.bluffs += 1
            elif action.type is ActionType.CALL:
                s.postflop_calls += 1
            elif action.type is ActionType.FOLD:
                s.postflop_folds += 1
        if action.type is ActionType.FOLD:
            folded.add(seat)

    for seat in vpip:
        by_seat[seat].vpip_hands += 1
    for seat in pfr:
        by_seat[seat].pfr_hands += 1
    for seat in saw_flop:
        by_seat[seat].saw_flop += 1
    for seat in history.showdown:
        by_seat[seat].showdowns += 1
        if history.payouts.get(seat, 0) > 0:
            by_seat[seat].showdown_wins += 1
    for d in history.decisions:
        s = by_seat[d.seat]
        s.decisions += 1
        s.illegal += d.illegal
        s.latency_ms_total += d.latency_ms
        s.talks += bool(d.table_talk)
        if "cost_usd" in d.meta:
            s.model_calls += 1
            s.cost_usd += float(d.meta["cost_usd"])


def _is_air(hole: tuple[Card, Card], board: list[Card]) -> bool:
    return (
        classify(hole, board) is MadeHand.NOTHING
        and not has_flush_draw(hole, board)
        and not has_open_ender(hole, board)
    )


def leaderboard(stats: dict[str, PlayerStats]) -> list[PlayerStats]:
    return sorted(stats.values(), key=lambda s: s.net, reverse=True)


def format_table(stats: dict[str, PlayerStats]) -> str:
    """A fixed-width leaderboard for the terminal."""
    rows = [s.as_row() for s in leaderboard(stats)]
    if not rows:
        return "(no hands)"
    columns = list(rows[0])
    percent = {"vpip", "pfr", "3bet", "f3b", "wtsd", "w$sd", "bluff", "illegal"}

    def cell(key: str, value: object) -> str:
        if value is None:
            return "-"
        if key in percent:
            return f"{float(value) * 100:.0f}%"
        if key in ("bb/100", "af", "ms"):
            return "inf" if value == float("inf") else f"{float(value):.1f}"
        if key == "$/hand":
            return f"{float(value):.4f}"
        return str(value)

    table = [columns] + [[cell(k, r[k]) for k in columns] for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(columns))]

    def line(row: list[str]) -> str:
        cells = [
            c.ljust(w) if i == 0 else c.rjust(w)
            for i, (c, w) in enumerate(zip(row, widths, strict=True))
        ]
        return "  ".join(cells).rstrip()

    return "\n".join(line(row) for row in table)
