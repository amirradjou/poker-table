"""The demo's table, which runs in the visitor's browser: the CLI's hands, a step at a time."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from poker_table.agents.base import Decision
from poker_table.agents.human import HumanAgent
from poker_table.cli import main
from poker_table.history import HandHistory, parse_action, read_jsonl
from poker_table.web.demo_play import BOTS, DemoTable, command, seats_for
from poker_table.web.export import export_site

SRC = Path(__file__).parents[1] / "src"


def policy(k: int, legal: Any) -> str:
    """A fixed way to play the human seat, mixing raises, checks, calls and folds. ``legal`` is
    the engine's LegalActions or the page's copy of it (a dict with the same names)."""
    get = legal.get if isinstance(legal, dict) else lambda name: getattr(legal, name)
    if k % 4 == 1 and get("max_raise_to") > 0:
        return f"{'bet' if get('is_bet') else 'raise'} {get('min_raise_to')}"
    if get("can_check"):
        return "check"
    return "call" if get("call_amount") <= 6 else "fold"


def cli_hands(tmp_path: Path, monkeypatch, seats: list[str], seed: int, hands: int) -> list:
    """`poker-table play` with these seats, its human seat played by ``policy``."""
    turns = count()
    monkeypatch.setattr(
        HumanAgent,
        "act",
        lambda self, view: Decision(parse_action(policy(next(turns), view.legal)), "human"),
    )
    path = tmp_path / "cli.jsonl"
    args = ["play", "-n", str(hands), "--seed", str(seed), "--seats", ",".join(seats)]
    assert main([*args, "-o", str(path), "-q"]) == 0
    return list(read_jsonl(path))


def demo_hands(tmp_path: Path, seats: list[str], seed: int, hands: int) -> tuple[DemoTable, list]:
    """The demo's table with the same seats, the human seat answered from the page's turn."""
    table = DemoTable(seats, seed=seed, hands=hands, path=tmp_path / "demo.jsonl")
    turns = count()
    out = table.step()
    while out["next"] != "done":
        if out["next"] == "you":
            out = table.act(policy(next(turns), table.status()["turn"]["legal"]))
        else:
            out = table.step()
    return table, list(read_jsonl(table.session.path))


def dealt(history: HandHistory) -> dict[str, Any]:
    """A hand as dealt and played: everything but the clock, and the words a human seat records
    for itself (the terminal writes "human", the page "human (browser)")."""
    data = history.to_dict()
    data.pop("played_at")
    humans = {p.seat for p in history.players if p.kind == "human"}
    for decision in data["decisions"]:
        decision.pop("latency_ms")
        if decision["seat"] in humans:
            decision.pop("reasoning")
    return data


def test_the_demo_deals_and_plays_what_the_cli_does(tmp_path: Path, monkeypatch) -> None:
    seats = seats_for(len(BOTS))
    cli = cli_hands(tmp_path, monkeypatch, seats, seed=21, hands=12)
    _, demo = demo_hands(tmp_path, seats, seed=21, hands=12)
    assert [dealt(h) for h in demo] == [dealt(h) for h in cli]
    actions = {d.applied.split()[0] for h in demo for d in h.decisions if d.seat == 0}
    assert actions >= {"fold", "check", "call"} and actions & {"bet", "raise"}  # all were played


def test_an_exploiter_adapts_at_the_demo_table_as_it_does_in_the_cli(tmp_path, monkeypatch) -> None:
    # The demo deals one hand at a time; the seat must still be shown each one to read the table.
    seats = ["you:human", "exploiter", "station"]
    cli = cli_hands(tmp_path, monkeypatch, seats, seed=3, hands=40)
    _, demo = demo_hands(tmp_path, seats, seed=3, hands=40)
    assert [dealt(h) for h in demo] == [dealt(h) for h in cli]
    methods = {d.meta["method"] for h in demo for d in h.decisions if d.seat == 1}
    assert methods == {"tag", "adjusted"}


def test_a_table_of_bots_plays_out_as_the_cli_deals_it(tmp_path: Path, monkeypatch) -> None:
    seats = list(BOTS)
    cli = cli_hands(tmp_path, monkeypatch, seats, seed=5, hands=10)
    table = DemoTable(seats, seed=5, hands=10, path=tmp_path / "bots.jsonl")
    table.run_out()
    assert [dealt(h) for h in read_jsonl(table.session.path)] == [dealt(h) for h in cli]
    assert table.status()["finished"] and table.status()["hands_played"] == 10


def test_it_stops_at_your_turn_and_at_the_end_of_each_hand(tmp_path: Path) -> None:
    table = DemoTable(seats_for(2), seed=3, hands=2, path=tmp_path / "t.jsonl")
    hello = json.loads(table.page('{"op": "hello"}'))["body"]
    assert hello["type"] == "hello" and hello["live"] and hello["hands_total"] == 2
    assert hello["seats"] == ["you", "tag", "maniac"] and not hello["spectator"]
    seen: list[str] = []
    while True:
        out = table.step()
        seen += [e["type"] for e in out["events"]]
        if out["next"] == "you":
            # The page re-reads the table on a turn: it must hold that turn, and the hand so far.
            assert out["events"][-1] == {"type": "turn", "seat": 0, "hand_id": "1"}
            status = table.status()
            assert status["turn"]["players"][0]["hole"][0] is not None  # your own cards
            assert status["turn"]["players"][1]["hole"] == [None, None]  # nobody else's
            assert {tuple(p["hole"]) for p in status["current"]["players"]} == {(None, None)}
            assert status["turn"]["odds"]["blind"]["equity"] > 0
            assert table.step()["next"] == "you"  # nothing moves until you do
            out = table.act("fold")
            seen += [e["type"] for e in out["events"]]
        if out["next"] == "deal":
            break
    # ... and a hand's end is the last thing of its step, before anything of the next one.
    assert seen[-1] == "hand" and "hand_start" not in seen[1:]
    assert table.status()["current"] is None and table.status()["hands_played"] == 1
    assert table.step()["events"][0]["type"] == "hand_start"


def test_the_page_routes_answer_as_the_server_does(tmp_path: Path) -> None:
    table = DemoTable(seats_for(1), seed=8, hands=1, path=tmp_path / "t.jsonl")

    def page(op: str, **fields: Any) -> dict[str, Any]:
        return json.loads(table.page(json.dumps({"op": op} | fields)))

    act = {"path": "/api/act", "body": {"action": "call"}}
    while (out := page("step"))["next"] == "bot":
        pass
    assert out["next"] == "you"
    assert page("post", path="/api/act", body={"action": "shout"})["status"] == 400
    assert page("post", **act)["body"] == {"ok": True, "action": "call"}
    while page("step")["next"] != "done":
        if table.status()["turn"]:
            page("post", path="/api/act", body={"action": "fold"})
    assert page("post", **act) == {"status": 409, "body": {"detail": "it is not your turn"}}
    assert page("post", path="/api/live/pace", body={"pace": 99})["body"] == {"pace": 5.0}
    assert page("get", path="/api/live")["body"]["pace"] == 5.0

    session = page("get", path="/api/session")["body"]
    assert session["hands"] == 1 and session["file"] == "your session" and session["hero"] == "you"
    assert page("get", path="/api/hands?offset=0&limit=100")["body"]["total"] == 1
    hand = page("get", path="/api/hands/1")["body"]
    assert hand["hand_id"] == "1" and hand["steps"]
    assert page("get", path="/api/odds/1")["body"]["spots"]
    assert page("get", path="/api/hands/2")["status"] == 404
    assert [r["name"] for r in page("get", path="/api/stats")["body"]["rows"]] != []
    assert page("get", path="/api/margins")["body"]["hands"] == 1
    assert page("get", path="/api/bankroll")["body"]["hands"] == ["1"]
    assert page("get", path="/api/coach?player=you")["body"]["player"] == "you"
    assert page("get", path="/api/coach?player=nobody")["status"] == 404
    assert page("get", path="/api/drill/next?player=you")["status"] == 404  # no drills here


def test_seats_and_the_command_that_deals_the_same_hands() -> None:
    assert seats_for(1) == ["you:human", "tag"]
    assert seats_for(5) == ["you:human", *BOTS]
    with pytest.raises(ValueError):
        seats_for(6)
    assert (
        command(seats_for(3), 42)
        == "poker-table play --seed 42 --seats you:human,tag,maniac,station"
    )
    with pytest.raises(ValueError, match="one person"):
        DemoTable(["a:human", "b:human", "tag"], seed=1, path=Path("/nonexistent/x.jsonl"))


def test_an_export_with_a_wheel_has_a_table_to_play(tmp_path: Path) -> None:
    hands = tmp_path / "hands.jsonl"
    assert main(["play", "-n", "3", "--seats", "tag,station", "-o", str(hands), "-q"]) == 0
    wheel = tmp_path / "poker_table-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"PK" + bytes(98))
    out = io.StringIO()
    site = tmp_path / "site"
    args = ["serve", str(hands), "--export", str(site), "--export-coach", "tag"]
    assert main([*args, "--export-play", str(wheel)], out=out) == 0
    assert "a table to play the bots at" in out.getvalue()
    page = (site / "index.html").read_text()
    where = '{"url": "play/poker_table-0.2.0-py3-none-any.whl", "bytes": 100}'
    assert f"window.POKER_TABLE_PLAY_WHEEL = {where};" in page
    assert page.index("static/app.js") < page.index("static/play.js")  # it uses app.js's viewer
    assert "static/play.css" in page
    assert (site / "play" / wheel.name).read_bytes() == wheel.read_bytes()
    assert {p.name for p in (site / "static").iterdir()} >= {"play.js", "play-worker.js"}

    plain = tmp_path / "plain"
    export_site(hands, plain, players=[])
    assert "PLAY" not in (plain / "index.html").read_text()
    assert not (plain / "play").exists() and not (plain / "static" / "play.js").exists()
    with pytest.raises(ValueError, match="not a wheel"):
        export_site(hands, tmp_path / "bad", players=[], play_wheel=hands)


# What the browser has: the standard library and the wheel, installed without its dependencies.
ISOLATED = """
import sys
for name in ("anthropic", "fastapi", "uvicorn"):
    sys.modules[name] = None  # importing one now fails, as it would in the browser
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from poker_table.web import demo_play

seats = demo_play.seats_for(1) + sys.argv[3:]
table = demo_play.DemoTable(seats, seed=4, hands=2, path=Path(sys.argv[2]))
out = table.step()
while out["next"] != "done":
    out = table.act("call") if out["next"] == "you" else table.step()
for path in ("/api/session", "/api/hands/1", "/api/odds/1", "/api/stats", "/api/coach?player=you"):
    table.get(path)
web = [m for m, mod in sys.modules.items() if mod and m.split(".")[0] in ("fastapi", "pydantic")]
assert not web and "poker_table.web.app" not in sys.modules, web
print(table.status()["hands_played"])
"""


@pytest.mark.parametrize("more", [[], ["exploiter"]], ids=["default seats", "with exploiter"])
def test_the_demo_needs_nothing_but_the_standard_library(tmp_path: Path, more) -> None:
    # -S: no site-packages at all, so any import from outside the standard library fails.
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-c", ISOLATED, str(SRC), str(tmp_path / "t.jsonl"), *more],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "2"
