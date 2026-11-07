"""FastAPI app serving hand histories from a JSONL file to the single-page viewer."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from poker_table.cards import Card
from poker_table.coach.drills import DrillLog, DrillSession, spots_from
from poker_table.coach.facts import Fact, replay, tag_hands
from poker_table.coach.report import Report, build_report
from poker_table.engine import ActionType, EventKind
from poker_table.history import HandHistory, filter_by_date, parse_action, read_jsonl, weeks_of
from poker_table.margins import CAVEAT, all_in_adjusted, all_in_note, calls, seat_margins
from poker_table.stats import compute_stats, leaderboard
from poker_table.web.commentary import LONG_SHOT
from poker_table.web.guard import TableGuard
from poker_table.web.live import (
    MAX_PACE,
    ODDS_SAMPLES,
    Broadcast,
    LiveSession,
    live_view_payload,
)

STATIC = Path(__file__).parent / "static"

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
    """Hand histories loaded from a JSONL file; ``reload()`` picks up appended hands."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.hands: list[HandHistory] = []
        self.by_id: dict[str, HandHistory] = {}
        self._reports: dict[tuple[str, int, int, str, str], Report] = {}
        self._drills: dict[tuple[str, int], DrillSession] = {}
        self._odds: dict[str, dict[str, Any]] = {}
        self._worth: dict[tuple[str, int], dict[int, float] | None] = {}
        self._highlights: dict[tuple[str, int], dict[str, Any]] = {}
        self.reload()

    def reload(self) -> int:
        self.hands = list(read_jsonl(self.path)) if self.path.exists() else []
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
            self._odds[hand_id] = odds_payload(self.by_id[hand_id])
        return self._odds[hand_id]

    def worth(self, history: HandHistory) -> dict[int, float] | None:
        """One hand's all-in adjusted result, kept: a live table only pays for its new hands. Two
        runs appended to one file can repeat a hand id, so the deck's seed is part of the key."""
        key = (history.hand_id, history.seed)
        if key not in self._worth:
            self._worth[key] = all_in_adjusted(history)
        return self._worth[key]

    def highlight(self, history: HandHistory) -> dict[str, Any]:
        """One hand as the filters see it, worked out once: it costs about 150 ms. Two runs
        appended to one file can repeat a hand id, so the deck's seed is part of the key."""
        key = (history.hand_id, history.seed)
        if key not in self._highlights:
            broadcast = _broadcast(history)
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


def odds_payload(history: HandHistory, *, samples: int = ODDS_SAMPLES) -> dict[str, Any]:
    """The probability calculation at every decision of one hand, both ways round.

    ``blind`` is what the seat itself could work out — its cards against the hands still in,
    unseen. ``known`` is what a replay can also say, because the file has everyone's cards:
    the same spot against the actual holdings, enumerated where that is cheap. A hand
    imported from a site keeps ``known`` empty for the seats whose cards were never shown.
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
    broadcast = _broadcast(history)
    return {
        "hand_id": history.hand_id,
        "spots": spots,
        "table": broadcast.rows if broadcast else [],
        "commentary": broadcast.lines if broadcast else [],
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
    }


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


class ActRequest(BaseModel):
    action: str
    table_talk: str = ""


class NarrateRequest(BaseModel):
    player: str  # no model here: the page must not choose what the owner's key pays for


class PaceRequest(BaseModel):
    pace: float


class DrillAnswer(BaseModel):
    player: str
    key: str
    action: str  # "fold", "check", "call", "bet 12", "raise 30"


def create_app(
    path: Path | str,
    live: LiveSession | None = None,
    *,
    allowed_hosts: Iterable[str] | None = None,
) -> FastAPI:
    store = HandStore(Path(path))
    app = FastAPI(title="poker-table", docs_url=None, redoc_url=None)
    app.add_middleware(TableGuard, allowed_hosts=allowed_hosts)
    app.state.store = store
    app.state.live = live

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/session")
    def session() -> dict[str, Any]:
        store.reload()
        return session_payload(store)

    @app.get("/api/hands")
    def hands(offset: int = 0, limit: int = 100, ids: str = "") -> dict[str, Any]:
        return hands_payload(store, offset, limit, ids)

    @app.get("/api/hands/{hand_id}")
    def hand(hand_id: str) -> dict[str, Any]:
        history = store.by_id.get(hand_id)
        if history is None:
            raise HTTPException(404, f"no hand {hand_id!r}")
        return hand_payload(history)

    @app.get("/api/odds/{hand_id}")
    def odds(hand_id: str) -> dict[str, Any]:
        history = store.by_id.get(hand_id)
        if history is None:
            raise HTTPException(404, f"no hand {hand_id!r}")
        return store.odds(hand_id)

    @app.get("/api/live")
    def live_status() -> dict[str, Any]:
        if live is None:
            return {"live": False}
        return live.status()

    @app.get("/api/events")
    def events() -> StreamingResponse:
        if live is None:
            raise HTTPException(404, "not a live session")
        return StreamingResponse(
            live.sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/live/pace")
    def set_pace(body: PaceRequest) -> dict[str, Any]:
        if live is None:
            raise HTTPException(404, "not a live session")
        live.pace = min(max(body.pace, 0.0), MAX_PACE)
        return {"pace": live.pace}

    @app.post("/api/act")
    def act(body: ActRequest) -> dict[str, Any]:
        human = live.human if live is not None else None
        if human is None:
            raise HTTPException(404, "no human seat at this table")
        try:
            action = human.submit(body.action, body.table_talk)
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, f"bad action: {exc}") from None
        return {"ok": True, "action": str(action)}

    @app.get("/api/coach")
    def coach(player: str, samples: int = 200, since: str = "", until: str = "") -> dict[str, Any]:
        if player not in store.players():
            raise HTTPException(404, f"no seat named {player!r}")
        try:
            return coach_payload(store, player, samples, since, until)
        except ValueError as exc:  # a malformed date
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/coach/narrate")
    def coach_narrate(body: NarrateRequest) -> dict[str, Any]:
        from poker_table.agents.llm import DEFAULT_MODEL, explain_model_error
        from poker_table.coach.narrate import narrate

        if body.player not in store.players():
            raise HTTPException(404, f"no seat named {body.player!r}")
        report = store.report(body.player, 200)
        try:
            narration = narrate(report, model=DEFAULT_MODEL)
        except Exception as exc:  # noqa: BLE001 - no key, network down, model error: tell the page
            raise HTTPException(503, explain_model_error(exc, DEFAULT_MODEL)) from None
        return {
            "notes": [
                {
                    "tag": n.tag,
                    "title": n.title,
                    "note": n.note,
                    "cited_hands": list(n.cited_hands),
                    "one_thing": n.one_thing,
                }
                for n in narration.notes
            ],
            "focus": narration.focus,
            "dropped": narration.dropped,
            "model": narration.model,
            "cost_usd": round(narration.cost_usd, 4),
        }

    @app.get("/api/drill/next")
    def drill_next(player: str, restart: bool = False) -> dict[str, Any]:
        if player not in store.players():
            raise HTTPException(404, f"no seat named {player!r}")
        session = store.drill(player)
        if restart:
            session.restart()
        spot = session.next()
        out: dict[str, Any] = {"progress": session.progress(), "spot": None}
        if spot is not None:
            out["spot"] = live_view_payload(spot.view) | {
                "key": spot.key,
                "tag": spot.fact.tag,
                "hand_id": spot.fact.hand_id,
            }
        return out

    @app.post("/api/drill/answer")
    def drill_answer(body: DrillAnswer) -> dict[str, Any]:
        if body.player not in store.players():
            raise HTTPException(404, f"no seat named {body.player!r}")
        session = store.drill(body.player)
        try:
            action = parse_action(body.action)
        except ValueError as exc:
            raise HTTPException(400, f"bad action: {exc}") from None
        try:
            verdict = session.answer(body.key, ActionType(action.type))
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from None
        return verdict | {"progress": session.progress()}

    @app.get("/api/bankroll")
    def bankroll() -> dict[str, Any]:
        return bankroll_payload(store)

    @app.get("/api/highlights")
    def highlights(offset: int = 0, limit: int = 10) -> dict[str, Any]:
        if offset == 0:
            store.reload()
        return highlights_payload(store, offset, limit)

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        return stats_payload(store)

    @app.get("/api/margins")
    def margins() -> dict[str, Any]:
        return margins_payload(store)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
