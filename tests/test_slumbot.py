"""The Slumbot benchmark, offline: a stand-in server replays answers the real API gave.

Nothing here calls slumbot.com. The recorded hands in ``fixtures/slumbot`` are real exchanges
with its API, the token taken out. A few hands are made by hand to its documented format, and
say so.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from poker_table.agents.base import Decision, SeatView
from poker_table.agents.scripted import CallingStation
from poker_table.engine import Action
from poker_table.margins import all_in_adjusted
from poker_table.slumbot import (
    BIG,
    SMALL,
    THEIRS,
    Api,
    Move,
    Refused,
    action_for,
    parse_line,
    play_hand,
)

ROOT = Path(__file__).resolve().parent.parent
RECORDED = json.loads((ROOT / "tests" / "fixtures" / "slumbot" / "probes.json").read_text())
SECRET = "tok-5ec4e7-never-on-disk"


def recorded(name: str) -> list[dict[str, Any]]:
    """A recorded hand without the API's refusals in it: our client never sends those."""
    return [copy.deepcopy(e) for e in RECORDED[name] if "error_msg" not in e["response"]]


# Made to the API's documented format ("An all-in can contain streets with no action").
ALL_IN_CALLED = [
    {
        "endpoint": "new_hand",
        "request": {},
        "response": {
            "old_action": "",
            "action": "",
            "client_pos": 1,
            "hole_cards": ["As", "Ad"],
            "board": [],
        },
    },
    {
        "endpoint": "act",
        "request": {"incr": "b20000"},
        "response": {
            "old_action": "",
            "action": "b20000c///",
            "client_pos": 1,
            "hole_cards": ["As", "Ad"],
            "board": ["2h", "7s", "9c", "Jd", "3c"],
            "bot_hole_cards": ["Kc", "Kd"],
            "winnings": 20000,
            "won_pot": 40000,
            "baseline_winnings": 0,
        },
    },
]

# Made the same way: Slumbot limps from the button and our seat, wanting to fold, may check.
LIMP_THEN_BET = [
    {
        "endpoint": "new_hand",
        "request": {},
        "response": {
            "old_action": "",
            "action": "c",
            "client_pos": 0,
            "hole_cards": ["7d", "2c"],
            "board": [],
        },
    },
    {
        "endpoint": "act",
        "request": {"incr": "k"},
        "response": {
            "old_action": "c",
            "action": "ck/",
            "client_pos": 0,
            "hole_cards": ["7d", "2c"],
            "board": ["Ah", "Kh", "Qs"],
        },
    },
    {
        "endpoint": "act",
        "request": {"incr": "k"},
        "response": {
            "old_action": "ck/",
            "action": "ck/kb100",
            "client_pos": 0,
            "hole_cards": ["7d", "2c"],
            "board": ["Ah", "Kh", "Qs"],
        },
    },
    {
        "endpoint": "act",
        "request": {"incr": "f"},
        "response": {
            "old_action": "ck/kb100",
            "action": "ck/kb100f",
            "client_pos": 0,
            "hole_cards": ["7d", "2c"],
            "board": ["Ah", "Kh", "Qs"],
            "bot_hole_cards": ["Jh", "Th"],
            "winnings": -100,
            "won_pot": -300,
            "baseline_winnings": 0,
        },
    },
]


class StandIn:
    """Answers like Slumbot's API by replaying recorded hands, and checks what it is sent.

    A request without a token starts a session in the big blind, as Slumbot's does, and the
    position flips every hand. Each new hand is the next recorded one for that position.
    ``first=SMALL`` starts sessions on the button instead, which Slumbot's never do: it saves a
    test of one button hand from playing a big-blind hand first.
    """

    def __init__(self, big=(), small=(), *, cycle: bool = False, first: int = BIG) -> None:
        self.pools: dict[int, list[list[dict[str, Any]]]] = {BIG: list(big), SMALL: list(small)}
        self.cycle = cycle
        self.requests: list[dict[str, Any]] = []
        self.statuses: list[int] = []  # answered in turn, before anything is replayed
        self.down_after: int | None = None  # new hands served before every answer is a 503
        self.hands = 0
        self.sessions = 0
        self.first = first
        self.position = first
        self.token = ""
        self.script: list[dict[str, Any]] = []
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def reply(self, status: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                endpoint = self.path.rsplit("/", 1)[-1]
                stand_in.requests.append(
                    {"endpoint": endpoint, "body": dict(body), "headers": dict(self.headers)}
                )
                status, answer = stand_in.answer(endpoint, body)
                self.reply(status, answer)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/slumbot/api/"

    def answer(self, endpoint: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.statuses:
            return self.statuses.pop(0), {"error": "busy"}
        if self.down_after is not None and self.hands >= self.down_after:
            return 503, {"error": "down"}
        token = body.pop("token", None)
        fresh = token is None
        if fresh:
            if endpoint != "new_hand":
                return 200, {"error_msg": "stand-in: no token"}
            self.sessions += 1
            self.position = self.first
            self.token = f"{SECRET}-{self.sessions}"
        elif token != self.token:
            return 200, {"error_msg": "stand-in: not this session's token"}
        if endpoint == "new_hand":
            pool = self.pools[self.position]
            if not pool:
                return 200, {"error_msg": "stand-in: no recorded hand left"}
            hand = pool.pop(0)
            if self.cycle:
                pool.append(hand)
            self.position = 1 - self.position
            self.script = copy.deepcopy(hand)
            self.hands += 1
        expected = self.script.pop(0) if self.script else None
        if expected is None or expected["endpoint"] != endpoint or expected["request"] != body:
            return 200, {"error_msg": f"stand-in: not the recorded request: {endpoint} {body}"}
        response = dict(expected["response"])
        if fresh:
            response["token"] = self.token
        return 200, response

    @property
    def sent(self) -> list[str]:
        return [r["body"].get("incr", "new_hand") for r in self.requests]


@pytest.fixture
def serve():
    started: list[StandIn] = []

    def start(*args, **kwargs) -> StandIn:
        stand_in = StandIn(*args, **kwargs)
        threading.Thread(target=stand_in.server.serve_forever, args=(0.05,), daemon=True).start()
        started.append(stand_in)
        return stand_in

    yield start
    for stand_in in started:
        stand_in.server.shutdown()
        stand_in.server.server_close()


def api_for(stand_in: StandIn, sleeps: list[float] | None = None) -> Api:
    return Api(stand_in.url, interval=0, sleep=(sleeps if sleeps is not None else []).append)


class Folder:
    """Folds every time it is asked, and remembers what it was shown."""

    kind = "folder"

    def __init__(self, name: str = "folder") -> None:
        self.name = name
        self.views: list[SeatView] = []
        self.observed: list[str] = []

    def act(self, view: SeatView) -> Decision:
        self.views.append(view)
        return Decision(Action.fold(), reasoning="not this one")

    def observe(self, history) -> None:
        self.observed.append(history.hand_id)


class Shover:
    kind = "shover"
    name = "shover"

    def act(self, view: SeatView) -> Decision:
        legal = view.legal
        if legal.can_raise:
            return Decision(Action.raise_to(legal.max_raise_to))
        return Decision(Action.call() if legal.can_call else Action.check())


class Spy:
    """Plays like a calling station and keeps every view it was given."""

    kind = "station"
    name = "station"

    def __init__(self) -> None:
        self.station = CallingStation()
        self.views: list[SeatView] = []

    def act(self, view: SeatView) -> Decision:
        self.views.append(view)
        return self.station.act(view)


# ----- the action strings ------------------------------------------------------------------


def test_a_recorded_hand_reads_street_by_street() -> None:
    line = parse_line("b200c/kb200c/kk/kk")
    assert [str(m) for m in line.moves] == ["b200", "c", "k", "b200", "c", "k", "k", "k", "k"]
    assert [m.street for m in line.moves] == [0, 0, 1, 1, 1, 2, 2, 3, 3]
    # Preflop the small blind acts first; after it, the big blind.
    assert [m.position for m in line.moves[:4]] == [SMALL, BIG, BIG, SMALL]
    assert line.ended == "showdown" and line.to_act is None
    assert line.put_in == (400, 400) and line.board_size == 5


@pytest.mark.parametrize(
    ("text", "street", "to_act", "put_in"),
    [
        ("", 0, SMALL, (100, 50)),
        ("b200", 0, BIG, (100, 200)),
        ("b200c/", 1, BIG, (200, 200)),
        ("b200c", 1, BIG, (200, 200)),  # a street the API closed without its slash
        ("ck/", 1, BIG, (100, 100)),  # a limp, checked through
        ("ck/b100", 1, SMALL, (200, 100)),
        ("b300b900c/kb600", 1, BIG, (900, 1500)),
    ],
)
def test_where_a_hand_stands(text: str, street: int, to_act: int, put_in: tuple[int, int]) -> None:
    line = parse_line(text)
    assert (line.street, line.to_act, line.ended, line.put_in) == (street, to_act, "", put_in)


@pytest.mark.parametrize("text", ["b20000c///", "b20000c", "b200b20000c///", "b200c/b19800c//"])
def test_an_all_in_has_empty_streets_with_or_without_their_slashes(text: str) -> None:
    line = parse_line(text)
    assert line.ended == "showdown" and line.to_act is None
    assert line.put_in == (20000, 20000) and line.board_size == 5


def test_a_short_all_in_is_legal_and_ends_the_raising() -> None:
    # 19,000 more than the 1,000 bet is a full raise; 20,000 after a raise to 15,000 is short of
    # the 14,000 due, but all in, so it stands, and all the other seat can do is call or fold.
    assert parse_line("b1000b15000b20000c").ended == "showdown"
    with pytest.raises(Refused):
        parse_line("b1000b15000b20000b20000")


def test_folds_end_the_hand() -> None:
    assert parse_line("f").ended == "fold" and parse_line("f").put_in == (100, 50)
    assert parse_line("b200b20000f").put_in == (20000, 200)


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("k", "a check facing a bet"),
        ("cc", "a call with nothing to call"),
        ("ck/f", "a fold with nothing to call"),
        ("b150", "less than the 100 due"),
        ("b200b250", "less than the 100 due"),
        ("b200c/kb99", "less than the 100 due"),
        ("b20001", "it can go to 20000"),
        ("b20000c/", "some of the streets left have a slash"),
        ("b200c//", "before the street's betting was over"),
        ("f/", "after the hand ended in a fold"),
        ("ck/kk/kk/kk/", "more than four streets"),
        ("b", "cannot read"),
        ("x", "cannot read"),
        ("ff", "a move after the betting was over"),
    ],
)
def test_what_the_api_rules_out_is_refused(text: str, why: str) -> None:
    with pytest.raises(Refused, match=why):
        parse_line(text)


def test_a_move_our_engine_does_not_allow_is_refused() -> None:
    from poker_table.engine import LegalActions

    legal = LegalActions(
        can_check=False, call_amount=50, min_raise_to=200, max_raise_to=20000, current_bet=100
    )
    assert action_for(Move(0, SMALL, "b", 300), legal) == Action.raise_to(300)
    with pytest.raises(Refused, match="does not allow"):
        action_for(Move(0, SMALL, "b", 150), legal)
    with pytest.raises(Refused, match="does not allow"):
        action_for(Move(0, SMALL, "k"), legal)


# ----- one hand on the stand-in ------------------------------------------------------------


def test_a_recorded_showdown_is_played_on_our_engine(serve) -> None:
    stand_in = serve([recorded("check_call_to_showdown")])
    played = play_hand(CallingStation(), api_for(stand_in), "1")
    assert not played.refused, played.refused
    assert stand_in.sent == ["new_hand", "c", "k", "c", "k", "k"]
    history = played.history
    assert history is not None and played.position == BIG and played.winnings == -400
    assert history.players[0].net == -400 and history.players[1].net == 400
    assert history.players[0].hole == ["Kc", "7h"] and history.players[1].hole == ["Kd", "6s"]
    assert history.board == ["Ah", "Ac", "6d", "Td", "3d"]
    assert history.button == 1 and history.big_blind == 100 and history.players[0].stack == 20000
    assert set(history.showdown) == {0, 1}
    assert [d.applied for d in history.decisions] == [
        "raise 200",
        "call",
        "check",
        "bet 200",
        "call",
        "check",
        "check",
        "check",
        "check",
    ]
    assert len(played.exchanges) == 6 and played.baseline == 0


def test_our_seat_never_sees_slumbots_cards_before_the_hand_ends(serve) -> None:
    stand_in = serve([recorded("check_call_to_showdown")])
    spy = Spy()
    played = play_hand(spy, api_for(stand_in), "1")
    assert not played.refused and len(spy.views) == 5
    theirs = {"Kd", "6s"}
    for view in spy.views:
        seen = {str(c) for c in view.hole} | {str(c) for c in view.board}
        seen |= {str(c) for e in view.events for c in e.cards}
        assert not seen & theirs
        assert all(e.seat != THEIRS for e in view.events if e.kind.value == "deal_hole")
        assert "Kd" not in view.describe() and "6s" not in view.describe()
    # The seat saw each street's cards as the API showed them, and the right legal actions.
    assert [len(v.board) for v in spy.views] == [0, 3, 3, 4, 5]
    assert spy.views[0].legal.call_amount == 100 and spy.views[0].legal.min_raise_to == 300


def test_slumbot_folding_inside_new_hand_needs_no_decision(serve) -> None:
    folder = Folder()
    played = play_hand(folder, api_for(serve([recorded("slumbot_folds_in_new_hand")])), "1")
    assert not played.refused and not folder.views
    assert played.winnings == 50 and played.history.players[0].net == 50


def test_an_all_in_with_empty_streets_runs_out_the_board(serve) -> None:
    played = play_hand(Shover(), api_for(serve([], [ALL_IN_CALLED], first=SMALL)), "1")
    assert not played.refused, played.refused
    history = played.history
    assert history.board == ["2h", "7s", "9c", "Jd", "3c"] and history.players[0].net == 20000
    adjusted = all_in_adjusted(history)  # both hands are known, so the luck can be taken out
    assert adjusted is not None and 12000 < adjusted[0] < 13500  # aces win about 82%


class Replayer:
    """Sends a recorded hand's moves again, written as our engine's actions."""

    name = kind = "replayer"

    def __init__(self, hand: list[dict[str, Any]]) -> None:
        self.moves = [e["request"]["incr"] for e in hand if e["endpoint"] == "act"]

    def act(self, view: SeatView) -> Decision:
        move = self.moves.pop(0)
        plain = {"k": Action.check(), "c": Action.call(), "f": Action.fold()}
        return Decision(plain.get(move) or Action.raise_to(int(move[1:])))


def test_a_real_all_in_on_the_turn_runs_out_its_empty_river(serve) -> None:
    hand = recorded("turn_all_in_called")  # exploiter's hand 942 against Slumbot
    assert hand[-1]["response"]["action"].endswith("kb5400b14600c/")
    stand_in = serve([], [hand], first=SMALL)
    played = play_hand(Replayer(hand), api_for(stand_in), "942")
    assert not played.refused, played.refused
    history = played.history
    assert history.board == ["9s", "6d", "3s", "7c", "9c"] and history.players[0].net == 20000
    assert history.actions()[-1].action == "call"
    # Aces against two pair with a card to come: about 18% to win, so all-in adjusted
    # scores the hand near -12,700 rather than the +20,000 the river gave.
    adjusted = all_in_adjusted(history)
    assert adjusted is not None and -14500 < adjusted[0] < -11000


def test_a_shove_is_sent_as_the_street_total(serve) -> None:
    stand_in = serve([recorded("bad_bet_then_shove")])
    played = play_hand(Shover(), api_for(stand_in), "1")
    assert not played.refused and stand_in.sent == ["new_hand", "b20000"]
    assert played.history.players[0].net == 200


def test_a_fold_with_a_free_check_is_sent_as_a_check(serve) -> None:
    stand_in = serve([LIMP_THEN_BET])
    played = play_hand(Folder(), api_for(stand_in), "1")
    assert not played.refused, played.refused
    assert stand_in.sent == ["new_hand", "k", "k", "f"]
    first = played.history.decisions[1]
    assert first.applied == "check" and first.meta["asked"] == "fold"
    assert played.history.players[0].net == -100


def test_an_illegal_move_of_ours_gets_the_tables_fallback_and_counts(serve) -> None:
    class Checker:
        name = kind = "checker"

        def act(self, view: SeatView) -> Decision:
            return Decision(Action.check())  # facing the big blind, so it may not check

    stand_in = serve([], [recorded("fold_the_button")], first=SMALL)
    played = play_hand(Checker(), api_for(stand_in), "1")
    assert not played.refused and stand_in.sent == ["new_hand", "f"]
    trace = played.history.decisions[0]
    assert trace.illegal and trace.requested == "check" and trace.applied == "fold"


@pytest.mark.parametrize("hole", [["2c", "2d"], ["2h", "2s"], ["2d", "2h"]])
def test_any_hole_cards_of_ours_leave_slumbot_two_stand_ins(serve, hole: list[str]) -> None:
    # A pair of deuces once took two of the three stand-in cards and crashed a run.
    hand = recorded("fold_the_button")
    for exchange in hand:
        exchange["response"]["hole_cards"] = hole
    played = play_hand(Folder(), api_for(serve([], [hand], first=SMALL)), "1")
    assert not played.refused, played.refused
    assert played.history.players[0].hole == hole


def test_a_bug_of_ours_keeps_the_hand_on_file_and_stops(
    serve, monkeypatch: pytest.MonkeyPatch
) -> None:
    import poker_table.slumbot as slumbot

    def broken(*args, **kwargs):
        raise IndexError("a bug")

    monkeypatch.setattr(slumbot, "_FedDeck", broken)
    played = play_hand(Folder(), api_for(serve([recorded("check_call_to_showdown")])), "1")
    assert played.stop and played.refused == "our client failed: IndexError: a bug"
    assert len(played.exchanges) == 1


def test_chips_that_do_not_add_up_refuse_the_hand_and_stop_the_run(serve) -> None:
    hand = recorded("check_call_to_showdown")
    hand[-1]["response"]["winnings"] = -300
    played = play_hand(CallingStation(), api_for(serve([hand])), "1")
    assert played.history is None and played.stop
    assert "chips do not add up" in played.refused and len(played.exchanges) == 6


def test_an_illegal_move_from_slumbot_is_refused_and_recorded(serve) -> None:
    hand = recorded("check_call_to_showdown")
    hand[2]["response"]["action"] = "b200c/kb99"
    played = play_hand(CallingStation(), api_for(serve([hand])), "1")
    assert played.history is None and not played.stop
    assert "less than the 100 due" in played.refused and len(played.exchanges) == 3


def test_a_rewritten_history_or_changed_board_is_refused(serve) -> None:
    hand = recorded("check_call_to_showdown")
    hand[3]["response"]["board"] = ["Ah", "Ac", "5d", "Td"]
    played = play_hand(CallingStation(), api_for(serve([hand])), "1")
    assert "changed board cards" in played.refused
    hand = recorded("check_call_to_showdown")
    hand[2]["response"]["old_action"] = "b300"
    played = play_hand(CallingStation(), api_for(serve([hand])), "1")
    assert "before it" in played.refused


def test_an_api_refusal_refuses_the_hand(serve) -> None:
    hand = recorded("fold_the_button")
    hand[1]["response"] = {"old_action": "", "error_msg": "Illegal fold"}
    played = play_hand(Folder(), api_for(serve([], [hand], first=SMALL)), "1")
    assert played.refused == "the API refused act f: Illegal fold"


def test_slumbots_cards_go_in_the_record_even_after_a_fold(serve) -> None:
    played = play_hand(
        Folder(), api_for(serve([], [recorded("fold_the_button")], first=SMALL)), "1"
    )
    assert played.history.players[1].hole == ["Td", "8d"]  # the API shows them on a fold too
    assert played.history.showdown == {}


# ----- the HTTP side -----------------------------------------------------------------------


def test_requests_are_plain_json_with_pythons_own_user_agent_and_the_latest_token(serve) -> None:
    stand_in = serve([recorded("check_call_to_showdown")], [recorded("fold_the_button")])
    api = api_for(stand_in)
    play_hand(CallingStation(), api, "1")
    play_hand(Folder(), api, "2")
    first, *rest = stand_in.requests
    assert first["body"] == {}  # the first request of a session has no token
    assert all(r["body"]["token"] == f"{SECRET}-1" for r in rest)
    for r in stand_in.requests:
        assert r["headers"]["Content-Type"] == "application/json"
        assert r["headers"]["User-Agent"].startswith("Python-urllib/")
        # Nothing about this repo, its owner or the machine: the headers urllib sends by itself.
        assert set(r["headers"]) == {
            "Accept-Encoding",
            "Connection",
            "Content-Length",
            "Content-Type",
            "Host",
            "User-Agent",
        }
    assert api.requests == 8


def test_the_token_is_never_in_the_record(serve) -> None:
    api = api_for(serve([recorded("check_call_to_showdown")]))
    played = play_hand(CallingStation(), api, "1")
    text = json.dumps(played.record(1)) + played.history.to_json()
    assert SECRET not in text and "token" not in text


def test_requests_are_spaced_at_least_a_quarter_second_apart(serve) -> None:
    stand_in = serve([recorded("check_call_to_showdown")])
    now = [0.0]
    sleeps: list[float] = []
    api = Api(stand_in.url, sleep=sleeps.append, clock=lambda: now[0])
    play_hand(CallingStation(), api, "1")
    assert sleeps == [0.25] * 5  # the clock never moved, so every request after the first waited


def test_busy_answers_are_retried_with_a_growing_back_off(serve) -> None:
    stand_in = serve([recorded("check_call_to_showdown")])
    stand_in.statuses = [429, 503, 429]
    sleeps: list[float] = []
    played = play_hand(CallingStation(), api_for(stand_in, sleeps), "1")
    assert not played.refused and sleeps == [2.0, 4.0, 8.0]
    assert [e["status"] for e in played.exchanges[:4]] == [429, 503, 429, 200]


def test_an_api_that_stays_down_stops_the_run(serve) -> None:
    stand_in = serve([recorded("check_call_to_showdown")])
    stand_in.down_after = 0
    sleeps: list[float] = []
    played = play_hand(CallingStation(), api_for(stand_in, sleeps), "1")
    assert played.stop and played.refused.startswith("the run stopped: new_hand failed 6 times")
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 32.0] and len(stand_in.requests) == 6


def test_a_lost_answer_to_a_move_is_never_resent(serve) -> None:
    stand_in = serve([recorded("check_call_to_showdown")])
    stand_in.down_after = 1  # the new hand is dealt, then every answer is a 503
    played = play_hand(CallingStation(), api_for(stand_in), "1")
    assert "was not resent" in played.refused and not played.stop
    assert stand_in.sent == ["new_hand", "c"]


# ----- the script: runs, resume, the report ------------------------------------------------


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location(
        "bench_slumbot", ROOT / "scripts" / "bench_slumbot.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_slumbot"] = module
    spec.loader.exec_module(module)
    return module


def folding_stand_in(serve) -> StandIn:
    """Recorded hands a folding seat replays every time: Slumbot folds its button, or ours is
    folded."""
    return serve(
        [recorded("slumbot_folds_in_new_hand")],
        [recorded("fold_the_button"), recorded("fold_the_button_again")],
        cycle=True,
    )


@pytest.fixture
def folder_seat(bench, monkeypatch: pytest.MonkeyPatch) -> list[Folder]:
    made: list[Folder] = []

    def make(spec: str, *, seed: int, taken) -> Folder:
        made.append(Folder())
        return made[-1]

    monkeypatch.setattr(bench, "make_agent", make)
    return made


def test_an_odd_number_of_hands_is_refused(bench, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="must be even"):
        bench.run("tag", 3, tmp_path / "out", api=Api("http://127.0.0.1:9/"))


def test_a_seat_built_for_another_depth_is_refused(bench, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="stakes are fixed"):
        bench.run("cfr:blueprint", 2, tmp_path / "out", api=Api("http://127.0.0.1:9/"))


def test_a_run_writes_every_hand_and_counts_each_position_equally(
    bench, serve, folder_seat, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    out = tmp_path / "folder"
    assert (
        bench.run("folder", 4, out, api=api_for(folding_stand_in(serve)), say=lambda _: None) == 0
    )
    records, histories = bench.read_folder(out)
    assert [r["client_pos"] for r in records] == [BIG, SMALL, BIG, SMALL]
    assert bench.counted(records, histories) == ["1", "2", "3", "4"]
    run = json.loads((out / "run.json").read_text())
    assert run["runs"][0]["stopped"] == "finished" and run["runs"][0]["requests"] == 6
    assert sorted(p.name for p in out.iterdir()) == ["api-001.jsonl", "hands-001.jsonl", "run.json"]
    every_file = "".join(p.read_text() for p in out.iterdir())
    assert SECRET not in every_file and "token" not in every_file
    summary = bench.summarise(out)
    # Two hands at +50 (Slumbot folds its button) and two at -50 (ours is folded).
    assert summary.hands == 4 and summary.bb_per_100 == 0.0
    assert summary.baseline == pytest.approx((0 + 1150 + 0 + 150) / 4)
    assert "| folder | 4 | +0" in capsys.readouterr().out


def test_a_full_folder_needs_resume(bench, serve, folder_seat, tmp_path: Path) -> None:
    out = tmp_path / "folder"
    bench.run("folder", 2, out, api=api_for(folding_stand_in(serve)), say=lambda _: None)
    with pytest.raises(SystemExit, match="--resume"):
        bench.run("folder", 4, out, api=api_for(folding_stand_in(serve)), say=lambda _: None)


def test_resume_carries_on_and_keeps_the_positions_even(
    bench, serve, folder_seat, tmp_path: Path
) -> None:
    out = tmp_path / "folder"
    first = folding_stand_in(serve)
    first.down_after = 3  # big blind, button, big blind, then the API goes away
    assert bench.run("folder", 4, out, api=api_for(first), say=lambda _: None) == 1
    records, histories = bench.read_folder(out)
    assert [r["client_pos"] for r in records] == [BIG, SMALL, BIG, None]
    assert records[-1]["refused"].startswith("the run stopped") and len(histories) == 3
    assert len(bench.counted(records, histories)) == 2  # the third hand waits for a button

    # A new process starts a new session, in the big blind again: that hand is one too many
    # from the big blind, so it is kept and left out, and the button after it is counted.
    second = folding_stand_in(serve)
    assert bench.run("folder", 4, out, resume=True, api=api_for(second), say=lambda _: None) == 0
    records, histories = bench.read_folder(out)
    assert [r["hand"] for r in records] == ["1", "2", "3", "4", "5", "6"]
    assert [r["client_pos"] for r in records] == [BIG, SMALL, BIG, None, BIG, SMALL]
    assert bench.counted(records, histories) == ["1", "2", "3", "6"]
    # The seat that learns was shown every hand it had played before it went on.
    assert folder_seat[1].observed == ["1", "2", "3", "5", "6"]
    run = json.loads((out / "run.json").read_text())
    assert [r["stopped"].split(":")[0] for r in run["runs"]] == [
        "stopped after hand 4",
        "finished",
    ]


def test_hand_files_roll_over_before_they_reach_the_limit(
    bench, serve, folder_seat, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bench, "MAX_BYTES", 5000)
    out = tmp_path / "folder"
    bench.run("folder", 6, out, api=api_for(folding_stand_in(serve)), say=lambda _: None)
    hand_files = sorted(out.glob("hands-*.jsonl"))
    assert len(hand_files) > 1 and all(p.stat().st_size <= 5000 for p in hand_files)
    records, histories = bench.read_folder(out)
    assert len(histories) == 6 and len(records) == 6


def test_the_report_says_how_a_seat_that_adapts_decided(bench) -> None:
    from dataclasses import replace

    plain = bench.Summary(
        seat="exploiter",
        hands=2,
        played=2,
        refused=0,
        bb_per_100=0.0,
        margin=None,
        adjusted=0.0,
        adjusted_margin=None,
        all_in=0,
        baseline=None,
        baseline_margin=None,
        requests=4,
        minutes=0.1,
        played_on="2026-11-27",
        methods={"-": 5},
        rules={},
    )
    assert "| 0 | - | 0 |" in bench.render([plain])  # a seat that records no method
    adapts = replace(plain, methods={"tag": 97, "adjusted": 3}, rules={"call_down": 2, "x": 1})
    table = bench.render([adapts])
    assert "| tag 97%, adjusted 3% |" in table
    assert table.endswith("exploiter: 3 of 100 decisions changed by a read (call_down 2, x 1).")
    few = replace(adapts, methods={"tag": 2578, "adjusted": 6})
    assert "| tag 99.8%, adjusted 0.2% |" in bench.render([few])  # never "adjusted 0%"


def test_the_report_reads_a_folder_back(bench, serve, folder_seat, tmp_path: Path) -> None:
    out = tmp_path / "folder"
    bench.run("folder", 2, out, api=api_for(folding_stand_in(serve)), say=lambda _: None)
    table = bench.render([bench.summarise(out)])
    assert table.splitlines()[0].startswith("| our seat | hands | bb/100 | all-in adj.")
    assert "| folder | 2 |" in table
