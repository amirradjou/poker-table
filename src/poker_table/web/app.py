"""FastAPI app serving hand histories from a JSONL file to the single-page viewer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from poker_table.engine import EventKind
from poker_table.history import HandHistory, read_jsonl
from poker_table.stats import compute_stats, leaderboard
from poker_table.web.live import LiveSession

STATIC = Path(__file__).parent / "static"


class HandStore:
    """Hand histories loaded from a JSONL file; ``reload()`` picks up appended hands."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.hands: list[HandHistory] = []
        self.by_id: dict[str, HandHistory] = {}
        self.reload()

    def reload(self) -> int:
        self.hands = list(read_jsonl(self.path)) if self.path.exists() else []
        self.by_id = {h.hand_id: h for h in self.hands}
        return len(self.hands)


def summarize(history: HandHistory) -> dict[str, Any]:
    winners = [history.players[s].name for s in history.winners()]
    return {
        "hand_id": history.hand_id,
        "board": history.board,
        "pot": sum(p["amount"] for p in history.pots),
        "winners": winners,
        "showdown": bool(history.showdown),
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


def create_app(path: Path | str, live: LiveSession | None = None) -> FastAPI:
    store = HandStore(Path(path))
    app = FastAPI(title="poker-table", docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.live = live

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/session")
    def session() -> dict[str, Any]:
        store.reload()
        return {"file": store.path.name, "hands": len(store.hands)}

    @app.get("/api/hands")
    def hands(offset: int = 0, limit: int = 100) -> dict[str, Any]:
        newest_first = list(reversed(store.hands))
        page = newest_first[offset : offset + limit]
        return {"total": len(store.hands), "hands": [summarize(h) for h in page]}

    @app.get("/api/hands/{hand_id}")
    def hand(hand_id: str) -> dict[str, Any]:
        history = store.by_id.get(hand_id)
        if history is None:
            raise HTTPException(404, f"no hand {hand_id!r}")
        return history.to_dict() | {"steps": steps_for(history)}

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

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        rows = [s.as_row() for s in leaderboard(compute_stats(store.hands))]
        for row in rows:
            for key, value in row.items():
                if value == float("inf"):
                    row[key] = None
        return {"hands": len(store.hands), "rows": rows}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
