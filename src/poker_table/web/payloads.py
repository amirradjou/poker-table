"""What every GET the viewer makes returns, worked out from a file of hands.

The server's routes (``web/app.py``), the static export (``web/export.py``) and the browser demo
(``web/demo_play.py``) all answer from here, so the three send the same JSON. Nothing in this
module needs the web stack: the demo runs it in the visitor's browser, where there is none.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from poker_table.cards import Card
from poker_table.coach.drills import DrillLog, DrillSession, spots_from
from poker_table.coach.facts import Fact, replay, tag_hands
from poker_table.coach.report import Report, build_report
from poker_table.engine import EventKind
from poker_table.history import HandHistory, filter_by_date, read_jsonl, weeks_of
from poker_table.league import Standing, standings_of
from poker_table.margins import CAVEAT, all_in_adjusted, all_in_note, calls, seat_margins
from poker_table.stats import compute_stats, leaderboard
from poker_table.web.commentary import LONG_SHOT
from poker_table.web.live import ODDS_SAMPLES, Broadcast

# How much of a story each kind of commentary line makes, for ranking hands.
STORY = {
    "long_shot": 5,
    "folded_best": 3,
    "turnaround": 3,
    "comeback": 3,
    "dead": 1,
    "shove": 1,
    "all_in_call": 1,
}
RARITY = 200  # a point per half a percent, so a long shot's odds outweigh the rest of the hand
HIGHLIGHT_BATCH = 50  # the most hands one request works out, at about 150 ms a hand
NEWEST_TO_SCAN = 2000  # beyond this a file would take minutes, for hands nobody is looking for


class HandStore:
    """Hand histories loaded from a JSONL file; ``reload()`` picks up appended hands.

    With ``view``, each hand is kept only as ``view`` turns it, so nothing this store answers
    can hold more than that: a shared table gives each reader a store of their own. Without
    ``broadcast`` it never works out the win chances and commentary, which a table with people
    at it does not send.
    """

    def __init__(
        self,
        path: Path,
        view: Callable[[HandHistory], HandHistory] | None = None,
        *,
        broadcast: bool = True,
    ) -> None:
        self.path = path
        self.view = view
        self.broadcast = broadcast
        self.hands: list[HandHistory] = []
        self.by_id: dict[str, HandHistory] = {}
        self._reports: dict[tuple[str, int, int, str, str], Report] = {}
        self._drills: dict[tuple[str, int], DrillSession] = {}
        self._odds: dict[str, dict[str, Any]] = {}
        self._worth: dict[tuple[str, int, str], dict[int, float] | None] = {}
        self._highlights: dict[tuple[str, int, str], dict[str, Any]] = {}
        self.reload()

    def reload(self) -> int:
        hands = list(read_jsonl(self.path)) if self.path.exists() else []
        self.hands = [self.view(h) for h in hands] if self.view is not None else hands
        self.by_id = {h.hand_id: h for h in self.hands}
        return len(self.hands)

    def players(self) -> list[str]:
        seen: dict[str, None] = {}
        for h in self.hands:
            for p in h.players:
                seen.setdefault(p.name, None)
        return list(seen)

    def report(self, player: str, samples: int, since: str = "", until: str = "") -> Report:
        """The coach report for ``player`` (optionally one period), cached until hands arrive."""
        key = (player, len(self.hands), samples, since, until)
        if key not in self._reports:
            hands = filter_by_date(self.hands, since or None, until or None)
            facts = tag_hands(hands, player, samples=samples)
            self._reports[key] = build_report(hands, player, facts)
        return self._reports[key]

    def drill(self, player: str, samples: int = 200) -> DrillSession:
        """The drill sitting for ``player``; spots come from the (cached) full report."""
        key = (player, len(self.hands))
        if key not in self._drills:
            report = self.report(player, samples)
            spots = spots_from(self.hands, report.facts)
            log = DrillLog.load(self.path.with_suffix(f".{player}.drills.json"))
            self._drills[key] = DrillSession(spots, log)
        return self._drills[key]

    def odds(self, hand_id: str) -> dict[str, Any]:
        """The per-decision probabilities for one hand, computed once and kept."""
        if hand_id not in self._odds:
            self._odds[hand_id] = odds_payload(self.by_id[hand_id], broadcast=self.broadcast)
        return self._odds[hand_id]

    def worth(self, history: HandHistory) -> dict[int, float] | None:
        """One hand's all-in adjusted result, kept: a live table only pays for its new hands. Two
        runs appended to one file can repeat a hand id, so the seed and time are in the key."""
        key = (history.hand_id, history.seed, history.played_at)
        if key not in self._worth:
            self._worth[key] = all_in_adjusted(history)
        return self._worth[key]

    def highlight(self, history: HandHistory) -> dict[str, Any]:
        """One hand as the filters see it, worked out once: it costs about 150 ms. Two runs
        appended to one file can repeat a hand id, so the seed and time are in the key."""
        key = (history.hand_id, history.seed, history.played_at)
        if key not in self._highlights:
            broadcast = _broadcast(history) if self.broadcast else None
            self._highlights[key] = highlight_of(history, broadcast.lines if broadcast else [])
        return self._highlights[key]

    def step_of(self, fact: Fact) -> int:
        """Index of the fact's decision among the hand's viewer steps (state just before it)."""
        history = self.by_id.get(fact.hand_id)
        if history is None:
            return 0
        for index, step in enumerate(steps_for(history)):
            if (
                step["kind"] == EventKind.ACTION.value
                and step["seat"] == fact.seat
                and step["street"] == fact.street
                and step["action"] == fact.action
            ):
                return index
        return 0


# ----- payloads ---------------------------------------------------------------------------
#
# Every GET the viewer makes is built here rather than inside a route, so that `serve --export`
# can write the same JSON to disk and the static site is answered by identical bytes.


def session_payload(store: HandStore) -> dict[str, Any]:
    heroes = {h.hero for h in store.hands if h.hero}
    return {
        "file": store.path.name,
        "hands": len(store.hands),
        "players": store.players(),
        "hero": heroes.pop() if len(heroes) == 1 else None,
        "weeks": weeks_of(store.hands),
        "highlights": True,
        "odds": True,
    } | _tournament_payload(store.hands)


def _tournament_payload(hands: Sequence[HandHistory]) -> dict[str, Any]:
    """The last tournament's standing, and every tournament's when a file holds several."""
    standings = tournament_standings(hands)
    if not standings:
        return {}
    return {"tournament": standings[-1]} | (
        {"tournaments": standings} if len(standings) > 1 else {}
    )


def tournament_standings(hands: Sequence[HandHistory]) -> list[dict[str, Any]]:
    """Where each freezeout in a file stands after its last hand: the level, who is left with
    how many chips, and who went out in what place and in which hand. One for each tournament
    id an experiment wrote, and none for a file of cash hands."""
    return [_standing(standing) for standing in standings_of(hands)]


def tournament_standing(hands: Sequence[HandHistory]) -> dict[str, Any] | None:
    """The standing of the last tournament in a file. None for a file of cash hands."""
    standings = tournament_standings(hands)
    return standings[-1] if standings else None


def _standing(standing: Standing) -> dict[str, Any]:
    last = standing.hands[-1]
    over = standing.complete
    rows = [
        {"name": name, "place": 1 if over else None, "chips": chips, "out_in": None}
        for name, chips in standing.left
    ] + [
        {"name": name, "place": place, "chips": 0, "out_in": hand_id}
        for name, (place, hand_id) in sorted(standing.gone.items(), key=lambda item: item[1][0])
    ]
    return ({"id": standing.number} if standing.number is not None else {}) | {
        "level": last.tournament.get("level"),
        "blinds": f"{last.small_blind}/{last.big_blind}" + (f"+{last.ante}" if last.ante else ""),
        "left": len(standing.left),
        "over": over,
        "hands": len(standing.hands),
        "standings": rows,
    }


def hands_payload(store: HandStore, offset: int = 0, limit: int = 100, ids: str = "") -> dict:
    if ids:  # a chosen subset, e.g. the example hands of one leak, in the order given
        chosen = [store.by_id[i] for i in ids.split(",") if i in store.by_id]
        return {"total": len(chosen), "hands": [summarize(h) for h in chosen]}
    newest_first = list(reversed(store.hands))
    page = newest_first[offset : offset + limit]
    return {"total": len(store.hands), "hands": [summarize(h) for h in page]}


def highlight_of(history: HandHistory, lines: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """A hand's summary, and the story its commentary told: which kinds of moment it had, how
    much of a story that makes, and the line that sums it up."""
    kinds = sorted({line["kind"] for line in lines})
    headline = max(lines, key=lambda line: STORY[line["kind"]], default=None)
    # A 5% shot coming in beats a 20% one, whatever else happened in the hand.
    rarity = sum(RARITY * (LONG_SHOT - line["share"]) for line in lines if "share" in line)
    return summarize(history) | {
        "kinds": kinds,
        "score": round(sum(STORY[kind] for kind in kinds) + rarity, 2),
        "line": headline["text"] if headline else "",
    }


def highlights_payload(store: HandStore, offset: int = 0, limit: int = 10) -> dict[str, Any]:
    """A page of the newest hands, tagged. In pages because tagging is slow: the page asks for
    one at a time, shows how far it has got and can stop. ``next`` is None at the end."""
    newest = list(reversed(store.hands))[:NEWEST_TO_SCAN]
    page = newest[offset : offset + max(1, min(limit, HIGHLIGHT_BATCH))]
    after = offset + len(page)
    return {
        "total": len(newest),
        "next": after if after < len(newest) else None,
        "hands": [store.highlight(history) for history in page],
    }


def hand_payload(history: HandHistory) -> dict[str, Any]:
    return history.to_dict() | {"steps": steps_for(history)}


def bankroll_payload(store: HandStore) -> dict[str, Any]:
    """Cumulative chips won per player after each hand, in seating order of first appearance."""
    totals: dict[str, int] = {}
    series: dict[str, list[int]] = {}
    hand_ids: list[str] = []
    for history in store.hands:
        hand_ids.append(history.hand_id)
        for player in history.players:
            totals[player.name] = totals.get(player.name, 0) + player.net
        for name in totals:
            series.setdefault(name, [0] * (len(hand_ids) - 1)).append(totals[name])
    return {
        "hands": hand_ids,
        "big_blind": store.hands[0].big_blind if store.hands else 0,
        "series": [{"name": name, "values": values} for name, values in series.items()],
    }


def stats_payload(store: HandStore) -> dict[str, Any]:
    rows = [s.as_row() for s in leaderboard(compute_stats(store.hands))]
    for row in rows:
        for key, value in row.items():
            if value == float("inf"):
                row[key] = None
    return {"hands": len(store.hands), "rows": rows}


def margins_payload(store: HandStore) -> dict[str, Any]:
    """Each seat's error bars. Apart from the stats because a big file takes a while: the page
    draws the leaderboard first and fills these in when they arrive."""
    margins = sorted(
        seat_margins(store.hands, store.worth).values(),
        key=lambda m: m.bb_per_100,
        reverse=True,
    )
    return {
        "hands": len(store.hands),
        "rows": [
            {
                "name": m.name,
                "adjusted": m.adjusted_bb_per_100,
                "margin": m.adjusted_margin,
                "clear": m.clear_of_zero,
            }
            for m in margins
        ],
        "calls": calls(margins),
        "note": f"{all_in_note(store.hands)} {CAVEAT}" if store.hands else "",
    }


def coach_payload(
    store: HandStore, player: str, samples: int = 200, since: str = "", until: str = ""
) -> dict[str, Any]:
    report = store.report(player, max(20, min(samples, 2000)), since, until)
    data = report.to_dict()
    data.pop("facts")
    data["since"], data["until"] = since, until
    for leak, source in zip(data["leaks"], report.leaks, strict=True):
        for example, fact in zip(leak["examples"], source.examples, strict=True):
            example["step"] = store.step_of(fact)
    return data


def odds_payload(
    history: HandHistory, *, samples: int = ODDS_SAMPLES, broadcast: bool = True
) -> dict[str, Any]:
    """The probability calculation at every decision of one hand, both ways round.

    ``blind`` is what the seat itself could work out — its cards against the hands still in,
    unseen. ``known`` is what a replay can also say, because the file has everyone's cards:
    the same spot against the actual holdings, enumerated where that is cheap. A hand
    imported from a site keeps ``known`` empty for the seats whose cards were never shown, and
    a seat whose own cards the file does not hold has no spot: the replay deals it stand-ins,
    and their numbers would be about cards nobody held.
    """
    from poker_table.odds import calculate, chance_row

    holes = {p.seat: [Card.parse(c) for c in p.hole] for p in history.players}
    steps = [i for i, step in enumerate(steps_for(history)) if step["kind"] == "action"]
    spots: list[dict[str, Any]] = []
    try:
        decisions = list(replay(history))
    except ValueError:  # a hand that will not replay (an import we cannot rebuild)
        return {"hand_id": history.hand_id, "spots": [], "table": [], "commentary": []}
    for index, (view, action) in enumerate(decisions):
        if index >= len(steps):
            break
        if len(holes.get(view.seat, [])) != 2:
            continue
        others = [p.seat for p in view.players if p.seat != view.seat and not p.folded]
        price = {"pot": view.pot, "to_call": view.to_call}
        blind = calculate(view.hole, view.board, max(1, len(others)), samples=samples, **price)
        shown = [tuple(holes[seat]) for seat in others if len(holes.get(seat, [])) == 2]
        known = (
            calculate(view.hole, view.board, shown, samples=samples, **price)
            if shown and len(shown) == len(others)
            else None
        )
        spots.append(
            {
                "step": steps[index],
                "seat": view.seat,
                "name": view.name,
                "street": view.street.value,
                "action": str(action),
                "pot": view.pot,
                "to_call": view.to_call,
                "pot_odds": round(blind.pot_odds, 4),
                "required": round(blind.required_equity, 4),
                "ratio": blind.ratio,
                "ev_call": round(blind.ev_call, 2),
                "blind": chance_row(blind),
                "known": chance_row(known) if known is not None else None,
            }
        )
    table = _broadcast(history) if broadcast else None
    return {
        "hand_id": history.hand_id,
        "spots": spots,
        "table": table.rows if table else [],
        "commentary": table.lines if table else [],
    }


def _broadcast(history: HandHistory) -> Broadcast | None:
    """The hand's win chances and commentary. None when any seat's cards are unknown: an imported
    hand only knows the cards that were shown, and a broadcast number against a guess would be
    worse than none.
    """
    holes: dict[int, tuple[Card, Card]] = {}
    for p in history.players:
        if len(p.hole) != 2:
            return None
        a, b = (Card.parse(c) for c in p.hole)
        holes[p.seat] = (a, b)
    broadcast = Broadcast(holes, {p.seat: p.name for p in history.players})
    for index, step in enumerate(steps_for(history)):
        broadcast.take(index + 1, step)
    return broadcast


def summarize(history: HandHistory) -> dict[str, Any]:
    winners = [history.players[s].name for s in history.winners()]
    return {
        "hand_id": history.hand_id,
        "board": history.board,
        "pot": sum(p["amount"] for p in history.pots),
        "winners": winners,
        "showdown": bool(history.showdown),
        "all_in": any(action.all_in for action in history.actions()),
        "players": [{"name": p.name, "net": p.net} for p in history.players],
    } | _tournament_of(history)


def _tournament_of(history: HandHistory) -> dict[str, Any]:
    """Who went out in a freezeout's hand, and which tournament it is when a file has several."""
    if not history.tournament:
        return {}
    number = history.tournament.get("id")
    return {"out": history.tournament.get("out", {})} | (
        {} if number is None else {"tournament": number}
    )


def steps_for(history: HandHistory) -> list[dict[str, Any]]:
    """Events worth stepping through, with each action's decision trace joined in."""
    names = {p.seat: p.name for p in history.players}
    decisions = iter(history.decisions)
    steps: list[dict[str, Any]] = []
    for e in history.events:
        if e.kind in (EventKind.HAND_START.value, EventKind.DEAL_HOLE.value):
            continue
        step: dict[str, Any] = {
            "kind": e.kind,
            "street": e.street,
            "seat": e.seat,
            "name": names.get(e.seat) if e.seat is not None else None,
            "action": e.action,
            "amount": e.amount,
            "cards": e.cards,
            "text": e.text,
            "all_in": e.all_in,
        }
        if e.kind == EventKind.ACTION.value:
            trace = next(decisions, None)
            if trace is not None:
                step |= {
                    "reasoning": trace.reasoning,
                    "table_talk": trace.table_talk,
                    "illegal": trace.illegal,
                    "requested": trace.requested,
                    "meta": trace.meta,
                }
        steps.append(step)
    return steps
