"""The viewer in a real browser: what app.js does, which the API tests cannot see.

These need the ``browser`` dependency group and a Chromium for Playwright:

    uv sync --group browser && uv run playwright install chromium

Without them the tests are skipped, unless ``POKER_TABLE_BROWSER_TESTS=required`` (as in CI),
where a missing browser is a failure instead of a quiet skip.
"""

from __future__ import annotations

import os
import re
import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from starlette.staticfiles import StaticFiles

from poker_table.agents.registry import make_agents
from poker_table.agents.scripted import CallingStation, TightAggressive
from poker_table.cli import main
from poker_table.history import read_jsonl
from poker_table.league import LeagueConfig, TournamentConfig
from poker_table.web.app import create_app
from poker_table.web.export import export_site
from poker_table.web.live import LiveSession, WebHumanAgent

REQUIRED = os.environ.get("POKER_TABLE_BROWSER_TESTS") == "required"

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Page, expect, sync_playwright
except ImportError:
    if REQUIRED:
        raise
    pytest.skip("needs the browser group: uv sync --group browser", allow_module_level=True)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except PlaywrightError as exc:
            if REQUIRED:
                raise
            pytest.skip(f"no Chromium for Playwright ({exc.message.splitlines()[0]})")
        yield chromium
        chromium.close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def serve() -> Iterator[Callable[[Any], str]]:
    """Serve an ASGI app on a free local port for the length of a test; returns its URL."""
    servers: list[tuple[uvicorn.Server, threading.Thread]] = []

    def start(app: Any) -> str:
        port = _free_port()
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=1
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        servers.append((server, thread))
        end = time.time() + 10
        while not server.started:
            assert time.time() < end, "the server did not start"
            time.sleep(0.02)
        return f"http://127.0.0.1:{port}"

    yield start
    for server, thread in servers:
        server.should_exit = True
        thread.join(10)


def _watched(browser: Any, **context: Any) -> Iterator[Page]:
    """A fresh page that fails the test on any script error, console error or failed request."""
    ctx = browser.new_context(**context)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"script error: {exc}"))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.on(
        "response",
        lambda r: errors.append(f"{r.status} for {r.url}") if r.status >= 400 else None,
    )
    page.set_default_timeout(15_000)
    expect.set_options(timeout=15_000)
    yield page
    ctx.close()
    assert errors == []


# Both depend on ``serve`` so they close before the servers stop: a live page whose server has
# gone logs a connection error that is not the viewer's fault.
@pytest.fixture
def page(browser: Any, serve: Callable[[Any], str]) -> Iterator[Page]:
    yield from _watched(browser, viewport={"width": 1280, "height": 900})


@pytest.fixture
def phone(browser: Any, serve: Callable[[Any], str]) -> Iterator[Page]:
    """A small phone: 360 by 740, touch, two pixels to the point."""
    yield from _watched(
        browser,
        viewport={"width": 360, "height": 740},
        device_scale_factor=2,
        is_mobile=True,
        has_touch=True,
    )


@pytest.fixture(scope="module")
def hands(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("browser") / "hands.jsonl"
    seats = "tag,station,maniac"
    assert main(["play", "-n", "12", "--seats", seats, "--seed", "7", "-o", str(path), "-q"]) == 0
    return path


def test_a_replay_steps_through_a_hand(page: Page, serve, hands: Path) -> None:
    page.goto(serve(create_app(hands)))
    expect(page.locator("#hand-list li")).to_have_count(12)
    expect(page.locator("#file")).to_contain_text("12 hands from")

    page.locator("#hand-list button").first.click()
    expect(page.locator("#replay")).to_be_visible()
    expect(page.locator("#table .seat")).to_have_count(3)
    expect(page.locator("#toggle-odds")).to_be_visible()
    names = page.locator("#table .seat .plate .name").all_inner_texts()
    assert sorted(names) == ["maniac", "station", "tag"]

    scrub = page.locator("#scrub")
    expect(scrub).to_have_value("0")
    page.locator("#next").click()
    expect(scrub).to_have_value("1")
    page.locator("#last").click()
    expect(page.locator("#street")).to_have_text("Hand over")
    expect(page.locator("#table .seat.winner").first).to_be_visible()
    page.keyboard.press("Home")
    expect(scrub).to_have_value("0")


def test_the_leaderboard_fills_in_its_error_bars(page: Page, serve, hands: Path) -> None:
    page.goto(serve(create_app(hands)))
    page.locator("#tab-board").click()
    expect(page.locator("#board-view")).to_be_visible()
    expect(page.locator("#board-title")).to_have_text("12 hands, sorted by chips won")
    expect(page.locator("#board-table tbody tr")).to_have_count(3)
    for cell in page.locator("#board-table td.margin").all():
        expect(cell).to_have_text(re.compile(r"^(±\d+|–)$"))  # "…" until the margins arrive
    expect(page.locator("#board-call")).not_to_be_empty()


def test_a_filter_shows_the_hands_the_server_says_match(page: Page, serve, hands: Path) -> None:
    url = serve(create_app(hands))
    page.goto(url)
    expect(page.locator("#hand-list li")).to_have_count(12)
    summaries = page.request.get(f"{url}/api/hands?limit=100").json()["hands"]
    all_in = {h["hand_id"] for h in summaries if h["all_in"]}
    page.locator("#filter").select_option("all_in")
    expect(page.locator("#hand-list button[data-id]")).to_have_count(len(all_in))
    shown = page.locator("#hand-list button[data-id]").evaluate_all(
        "bs => bs.map(b => b.dataset.id)"
    )
    assert set(shown) == all_in

    page.locator("#filter").select_option("")
    expect(page.locator("#hand-list button[data-id]")).to_have_count(12)


def test_a_spectator_sees_a_live_table_with_everyones_chances(page: Page, serve, tmp_path) -> None:
    path = tmp_path / "live.jsonl"
    players = [TightAggressive("tag"), CallingStation("station")]
    session = LiveSession(players, LeagueConfig(hands=3, seed=4, buy_in=100), path, pace=0.4)
    url = serve(create_app(path, session))
    session.start()
    page.goto(url)
    expect(page.locator("#live")).to_contain_text("live: hand")
    expect(page.locator("#table .seat .equity").first).to_be_visible()  # the broadcast strip
    session.pace = 0.0
    expect(page.locator("#live")).to_have_text("session over, 3 hands", timeout=30_000)
    expect(page.locator("#hand-list li")).to_have_count(3)


def test_a_person_plays_from_the_browser_and_sees_only_their_own_cards(
    page: Page, serve, tmp_path
) -> None:
    path = tmp_path / "live.jsonl"
    players = [WebHumanAgent("me"), CallingStation("station")]
    session = LiveSession(players, LeagueConfig(hands=2, seed=2, buy_in=100), path)
    url = serve(create_app(path, session))
    session.start()
    page.goto(url)
    for played in range(2):
        expect(page.locator("#live")).to_have_text("waiting for you")
        expect(page.locator("#action-bar")).to_be_visible()
        expect(page.locator("#your-odds")).to_contain_text("You have")
        me, them = (
            page.locator('#table .seat[data-seat="0"]'),
            page.locator('#table .seat[data-seat="1"]'),
        )
        expect(me.locator(".card:not(.back)")).to_have_count(2)
        expect(them.locator(".card.back")).to_have_count(2)
        expect(page.locator("#table .equity")).to_have_count(
            0
        )  # nobody's chances at a player's table
        page.locator("#act-fold").click()
        expect(page.locator("#hand-list li")).to_have_count(played + 1)
    expect(page.locator("#live")).to_have_text("session over, 2 hands")
    assert session.hands_played == 2


def test_the_published_demo_works_with_no_server_behind_it(
    page: Page, serve, hands: Path, tmp_path
) -> None:
    site = tmp_path / "site"
    export_site(hands, site, samples=20)
    page.goto(serve(StaticFiles(directory=site, html=True)) + "/")
    expect(page.locator("#replay")).to_be_visible()  # it opens on a hand worth watching
    expect(page.locator("#table .seat")).to_have_count(3)
    expect(page.locator("#tab-drill")).to_be_hidden()
    expect(page.locator("#live")).to_be_hidden()
    expect(page.locator("#toggle-odds")).to_be_hidden()  # exported without --export-odds
    page.locator("#tab-board").click()
    expect(page.locator("#board-table tbody tr")).to_have_count(3)
    expect(page.locator("#board-call")).not_to_be_empty()


def _box(page: Page, selector: str) -> dict[str, float]:
    return page.locator(selector).first.bounding_box()


def _overflowing(page: Page) -> list[str]:
    """Whatever sticks out past the sides of the screen, which a phone would cut off."""
    return page.evaluate(
        """() => {
          const width = document.documentElement.clientWidth;
          const out = [];
          for (const el of document.querySelectorAll("#table .seat .plate, #action-bar *")) {
            const r = el.getBoundingClientRect();
            if (r.width && (r.left < 0 || r.right > width)) out.push(el.className || el.id);
          }
          if (document.documentElement.scrollWidth > width) out.push("the page scrolls sideways");
          return out;
        }"""
    )


def test_a_phone_fits_the_table_and_gives_the_leaderboard_the_screen(
    phone: Page, serve, tmp_path
) -> None:
    path = tmp_path / "five.jsonl"
    seats = "tag,station,maniac,rock,random"
    assert main(["play", "-n", "6", "--seats", seats, "--seed", "11", "-o", str(path), "-q"]) == 0
    phone.goto(serve(create_app(path)))
    phone.locator("#hand-list button").first.tap()
    expect(phone.locator("#table .seat")).to_have_count(5)
    phone.keyboard.press("End")
    expect(phone.locator("#street")).to_have_text("Hand over")
    assert _box(phone, "#table .seat .plate")["width"] <= 120  # the phone's plates, not the desk's
    assert _overflowing(phone) == []

    phone.locator("#tab-board").tap()
    expect(phone.locator("#board-table tbody tr")).to_have_count(5)
    # it used to get the hand list's short row and scroll inside it
    assert _box(phone, "#board-view")["height"] > 740 * 0.6


def test_a_phone_player_sees_the_board_and_the_buttons_at_once(
    phone: Page, serve, tmp_path
) -> None:
    path = tmp_path / "live.jsonl"
    bots = [CallingStation("station"), TightAggressive("tag"), CallingStation("fish")]
    session = LiveSession(
        [WebHumanAgent("me"), *bots], LeagueConfig(hands=1, seed=3, buy_in=100), path
    )
    url = serve(create_app(path, session))
    session.start()
    phone.goto(url)
    expect(phone.locator("#action-bar")).to_be_visible()
    expect(phone.locator("#act-quick button")).to_have_count(4)
    phone.wait_for_function(  # both on screen together, once the bar has scrolled into view
        """() => {
          const board = document.querySelector("#board").getBoundingClientRect();
          const bar = document.querySelector("#action-bar").getBoundingClientRect();
          return board.top >= 0 && bar.bottom <= innerHeight;
        }"""
    )
    assert _overflowing(phone) == []
    phone.locator("#act-fold").tap()
    expect(phone.locator("#live")).to_have_text("session over, 1 hands")


def test_a_freezeout_shows_its_levels_bust_outs_and_standings(page: Page, serve, tmp_path) -> None:
    path = tmp_path / "freezeout.jsonl"
    session = LiveSession(
        make_agents(["tag", "station", "maniac", "rock", "random"], seed=3),
        LeagueConfig(hands=1, seed=3),
        path,
        tournament=TournamentConfig(seed=3, hands_per_level=10),
    )
    url = serve(create_app(path, session))
    session.start()
    page.goto(url)
    expect(page.locator("#live")).to_contain_text("tournament over:", timeout=60_000)
    winner = page.locator("#live").inner_text().split(": ")[1].split(" wins")[0]

    page.locator("#tab-board").click()
    expect(page.locator("#board-title")).to_contain_text("Tournament over after")
    expect(page.locator("#standings tbody tr")).to_have_count(5)
    first = page.locator("#standings tbody tr").first.locator("td")
    expect(first.nth(0)).to_have_text("1st")
    expect(first.nth(1)).to_have_text(winner)

    bust = next(h for h in read_jsonl(path) if h.tournament["out"])
    name, place = next(iter(bust.tournament["out"].items()))
    page.locator("#tab-hands").click()
    page.evaluate(f"openHand({bust.hand_id!r}, {{at: 'end'}})")
    expect(page.locator("#hand-sub")).to_contain_text(f"Level {bust.tournament['level']}: blinds")
    expect(page.locator("#summary p.out")).to_contain_text(f"{name} is out in")
