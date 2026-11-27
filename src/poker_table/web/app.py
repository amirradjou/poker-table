"""FastAPI app serving hand histories from a JSONL file to the single-page viewer."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from poker_table.engine import ActionType
from poker_table.history import HandHistory, parse_action, public_view
from poker_table.web.guard import TableGuard
from poker_table.web.live import MAX_PACE, LiveSession, WebHumanAgent, live_view_payload
from poker_table.web.payloads import (
    HandStore,
    bankroll_payload,
    coach_payload,
    hand_payload,
    hands_payload,
    highlights_payload,
    margins_payload,
    session_payload,
    stats_payload,
)

STATIC = Path(__file__).parent / "static"


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


def seat_view(history: HandHistory, name: str) -> HandHistory:
    """A finished hand as one person at a shared table may see it: the public view (the board,
    every action and table talk, the cards turned over at showdown) plus their own cards and
    reasoning. With ``name`` empty, as someone with no seat sees it. The deck's seed goes too,
    because it deals the whole hand again, folded cards and all."""
    return replace(public_view(history, name), seed=0, hero=name)


@dataclass(frozen=True, slots=True)
class Reader:
    """Who a request is from: a person at the live table, or nobody, and the hands they may read.

    At a shared table ``store`` holds only :func:`seat_view` of each hand, so whatever a route
    works out from it cannot hold another seat's hidden cards or reasoning.
    """

    person: WebHumanAgent | None
    store: HandStore
    shared: bool = False

    @property
    def name(self) -> str | None:
        return self.person.name if self.person is not None else None

    def own_seat_only(self, player: str) -> None:
        if self.shared and player != self.name:
            raise HTTPException(403, "at a shared table the coach reads only your own seat")


def _reader(request: Request, seat: str = "") -> Reader:
    """The reader a request's seat link names; ``create_app`` keeps the table it looks in."""
    return request.app.state.reader(seat)


Who = Annotated[Reader, Depends(_reader)]


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
    shared = live is not None and live.shared
    seat_stores: dict[str, HandStore] = {}

    def reader(seat: str = "") -> Reader:
        """The seat link's token names the reader. A page with no token is the one person at a
        table for one, as it always was, and nobody at a shared table."""
        if seat:
            person = live.seat_for(seat) if live is not None else None
            if person is None:
                raise HTTPException(403, "this seat link is not one of this table's")
        else:
            person = live.person(None) if live is not None else None
        if not shared:
            return Reader(person, store)
        name = person.name if person is not None else ""
        if name not in seat_stores:
            # Even a hand shown down by everyone gets no broadcast: a shared table never sends one.
            seat_stores[name] = HandStore(
                store.path, view=lambda h: seat_view(h, name), broadcast=False
            )
        return Reader(person, seat_stores[name], shared=True)

    app.state.reader = reader

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/session")
    def session(who: Who) -> dict[str, Any]:
        who.store.reload()
        payload = session_payload(who.store)
        if who.shared:  # most hands hide someone's folded cards, which the stories need
            payload |= {"shared": True, "you": who.name, "highlights": False}
        return payload

    @app.get("/api/hands")
    def hands(who: Who, offset: int = 0, limit: int = 100, ids: str = "") -> dict[str, Any]:
        return hands_payload(who.store, offset, limit, ids)

    @app.get("/api/hands/{hand_id}")
    def hand(hand_id: str, who: Who) -> dict[str, Any]:
        history = who.store.by_id.get(hand_id)
        if history is None:
            raise HTTPException(404, f"no hand {hand_id!r}")
        payload = hand_payload(history)
        if who.shared:
            payload.pop("seed")  # nought in a seat's view, so the page should not claim one
        return payload

    @app.get("/api/odds/{hand_id}")
    def odds(hand_id: str, who: Who) -> dict[str, Any]:
        history = who.store.by_id.get(hand_id)
        if history is None:
            raise HTTPException(404, f"no hand {hand_id!r}")
        return who.store.odds(hand_id)

    @app.get("/api/live")
    def live_status(who: Who) -> dict[str, Any]:
        if live is None:
            return {"live": False}
        return live.status(who.name)

    @app.get("/api/events")
    def events(who: Who) -> StreamingResponse:
        if live is None:
            raise HTTPException(404, "not a live session")
        return StreamingResponse(
            live.sse(who.name),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/live/pace")
    def set_pace(body: PaceRequest, who: Who) -> dict[str, Any]:
        if live is None:
            raise HTTPException(404, "not a live session")
        live.pace = min(max(body.pace, 0.0), MAX_PACE)
        return {"pace": live.pace}

    @app.post("/api/act")
    def act(body: ActRequest, who: Who) -> dict[str, Any]:
        if who.person is None:
            if shared:
                raise HTTPException(403, "this page has no seat: open your own seat link to play")
            raise HTTPException(404, "no human seat at this table")
        try:
            action = who.person.submit(body.action, body.table_talk)
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, f"bad action: {exc}") from None
        return {"ok": True, "action": str(action)}

    @app.get("/api/coach")
    def coach(
        player: str, who: Who, samples: int = 200, since: str = "", until: str = ""
    ) -> dict[str, Any]:
        who.own_seat_only(player)
        if player not in who.store.players():
            raise HTTPException(404, f"no seat named {player!r}")
        try:
            return coach_payload(who.store, player, samples, since, until)
        except ValueError as exc:  # a malformed date
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/coach/narrate")
    def coach_narrate(body: NarrateRequest, who: Who) -> dict[str, Any]:
        from poker_table.agents.llm import DEFAULT_MODEL, explain_model_error
        from poker_table.coach.narrate import narrate

        if who.shared:  # a friend's link must not spend the host's key
            raise HTTPException(
                403,
                "Claude's notes run on the host's key, so a shared table does not offer them: "
                "`poker-table coach FILE --narrate` gives them afterwards",
            )
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
    def drill_next(player: str, who: Who, restart: bool = False) -> dict[str, Any]:
        who.own_seat_only(player)
        if player not in who.store.players():
            raise HTTPException(404, f"no seat named {player!r}")
        session = who.store.drill(player)
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
    def drill_answer(body: DrillAnswer, who: Who) -> dict[str, Any]:
        who.own_seat_only(body.player)
        if body.player not in who.store.players():
            raise HTTPException(404, f"no seat named {body.player!r}")
        session = who.store.drill(body.player)
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
    def bankroll(who: Who) -> dict[str, Any]:
        return bankroll_payload(who.store)

    @app.get("/api/highlights")
    def highlights(who: Who, offset: int = 0, limit: int = 10) -> dict[str, Any]:
        if offset == 0:
            who.store.reload()
        return highlights_payload(who.store, offset, limit)

    @app.get("/api/stats")
    def stats(who: Who) -> dict[str, Any]:
        return stats_payload(who.store)

    @app.get("/api/margins")
    def margins(who: Who) -> dict[str, Any]:
        return margins_payload(who.store)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
