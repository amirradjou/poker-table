"""The viewer in a real browser: what app.js does, which the API tests cannot see.

These need the ``browser`` dependency group and a Chromium for Playwright:

    uv sync --group browser && uv run playwright install chromium

``POKER_TABLE_BROWSER=firefox`` runs them in Firefox instead (after ``playwright install
firefox``); the phone tests need Chromium's mobile emulation and are skipped there.

Without them the tests are skipped, unless ``POKER_TABLE_BROWSER_TESTS=required`` (as in CI),
where a missing browser is a failure instead of a quiet skip.
"""

from __future__ import annotations

import functools
import os
import re
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from starlette.staticfiles import StaticFiles

from poker_table.agents.registry import make_agents
from poker_table.agents.scripted import CallingStation, TightAggressive
from poker_table.cli import main
from poker_table.experiments import run_experiment
from poker_table.history import read_jsonl
from poker_table.league import LeagueConfig, TournamentConfig, standings_of
from poker_table.web.app import create_app
from poker_table.web.export import export_site
from poker_table.web.live import LiveSession, WebHumanAgent
from poker_table.web.payloads import hand_payload

REQUIRED = os.environ.get("POKER_TABLE_BROWSER_TESTS") == "required"
ENGINE = os.environ.get("POKER_TABLE_BROWSER", "chromium")
ROOT = Path(__file__).parents[1]
# A folder for screenshots of the demo's table, to look at by eye; none are taken without it.
SHOTS = os.environ.get("POKER_TABLE_SHOTS")

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
            launched = getattr(playwright, ENGINE).launch()
        except PlaywrightError as exc:
            if REQUIRED:
                raise
            pytest.skip(f"no {ENGINE} for Playwright ({exc.message.splitlines()[0]})")
        yield launched
        launched.close()


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
    if ENGINE == "firefox":
        pytest.skip("Firefox has no mobile emulation in Playwright")
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


def test_an_exploiter_seat_says_what_it_is(page: Page, serve, tmp_path: Path) -> None:
    path = tmp_path / "exploiter.jsonl"
    seats = "exploiter,station"
    assert main(["play", "-n", "2", "--seats", seats, "--seed", "7", "-o", str(path), "-q"]) == 0
    page.goto(serve(create_app(path)))
    page.locator("#hand-list button").first.click()
    seat = page.locator("#table .seat", has_text="exploiter")
    expect(seat.locator(".pos")).to_contain_text("exploiter")
    label = seat.locator(".pos span[title]")
    expect(label).to_have_attribute(
        "title", "a tag that adapts to what it has seen of each opponent"
    )


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


# What the page hands the clipboard, kept where a test can read it in any browser.
CLIPBOARD = """
  window.copied = [];
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
    writeText: async (text) => { window.copied.push(text); },
  } });
"""


@pytest.mark.parametrize("where", ["served", "static"])
def test_a_link_opens_its_hand_at_its_step(page: Page, serve, hands: Path, tmp_path, where) -> None:
    if where == "served":
        url = serve(create_app(hands)) + "/"
    else:
        export_site(hands, tmp_path / "site", samples=20)
        url = serve(StaticFiles(directory=tmp_path / "site", html=True)) + "/"
    page.add_init_script(CLIPBOARD)
    page.goto(f"{url}#hand=5&step=3")
    expect(page.locator("#hand-title")).to_have_text("Hand #5")
    expect(page.locator("#scrub")).to_have_value("3")
    page.locator("#next").click()
    page.locator("#copy-link").click()
    expect(page.locator("#copy-link")).to_have_text("Link copied")
    assert page.evaluate("window.copied") == [f"{url}#hand=5&step=4"]
    expect(page).to_have_url(f"{url}#hand=5&step=4")  # the address follows the step too

    # Opening the copied link in a new tab shows the same hand at the same step.
    other = page.context.new_page()
    other.goto(f"{url}#hand=5&step=4")
    expect(other.locator("#hand-title")).to_have_text("Hand #5")
    expect(other.locator("#scrub")).to_have_value("4")
    other.close()

    # The older form still opens, and a link to a hand the file lacks opens the first listed,
    # with no failed request (the page fixture fails the test on any).
    page.goto(f"{url}#2")
    expect(page.locator("#hand-title")).to_have_text("Hand #2")
    page.goto(f"{url}#hand=no-such-hand&step=7")
    expect(page.locator("#hand-title")).to_have_text("Hand #12")  # the newest, at the top
    expect(page.locator("#scrub")).to_have_value("0")
    # A link pasted into the address bar of an open page opens without a reload.
    page.evaluate("location.hash = 'hand=7&step=2'")
    expect(page.locator("#hand-title")).to_have_text("Hand #7")
    expect(page.locator("#scrub")).to_have_value("2")


def test_a_link_leaves_out_the_seat_of_a_person_at_a_table_with_friends(
    page: Page, serve, tmp_path
) -> None:
    path = tmp_path / "friends.jsonl"
    path.touch()
    ann = WebHumanAgent("ann")
    session = LiveSession(
        [ann, WebHumanAgent("bob"), CallingStation("station")],
        LeagueConfig(hands=1, seed=2, buy_in=100),
        path,
        time_limit=0.01,
    )
    url = serve(create_app(path, session))
    session.start()
    session.join(30)
    page.add_init_script(CLIPBOARD)
    page.goto(f"{url}/?seat={ann.token}#hand=1&step=2")
    expect(page.locator("#hand-title")).to_have_text("Hand #1")
    page.locator("#copy-link").click()
    expect(page.locator("#copy-link")).to_have_text("Link copied")
    assert page.evaluate("window.copied") == [f"{url}/#hand=1&step=2"]


def test_a_picture_of_a_hand_downloads_as_a_png(page: Page, serve, hands: Path) -> None:
    page.goto(serve(create_app(hands)))
    # A hand shown down by some seats and not others, whose cards the file knows all the same.
    played = list(read_jsonl(hands))
    chosen = next(h for h in played if h.showdown and len(h.showdown) < len(h.players))
    page.evaluate(f"openHand({chosen.hand_id!r})")
    expect(page.locator("#hand-title")).to_have_text(f"Hand #{chosen.hand_id}")
    model = page.evaluate("pictureModel(hand)")
    assert model["id"] == chosen.hand_id and model["pot"] == sum(p["amount"] for p in chosen.pots)
    assert model["board"] == [str(c) for c in chosen.board]
    for seat in model["seats"]:
        player = chosen.players[seat["seat"]]
        shown = seat["seat"] in chosen.showdown
        assert seat["cards"] == ([str(c) for c in player.hole] if shown else [None, None])
        assert seat["kind"]["label"] and seat["net"] == player.net
    won = sum(w["amount"] for w in model["wins"])
    assert won == model["pot"] and {w["name"] for w in model["wins"]} <= {
        p.name for p in chosen.players if p.net > 0
    }

    with page.expect_download() as download:
        page.locator("#save-picture").click()
    saved = download.value
    assert saved.suggested_filename == f"poker-table-hand-{chosen.hand_id}.png"
    data = Path(saved.path()).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    assert (width, height) == (2400, 1600)


# Counts every sound the page makes: each AudioContext made, and each tone or noise started.
COUNT_SOUND = """
  window.sounds = { contexts: 0, started: 0 };
  const Real = window.AudioContext;
  window.AudioContext = class extends Real {
    constructor(...args) { super(...args); window.sounds.contexts += 1; }
    createOscillator() { return counted(super.createOscillator()); }
    createBufferSource() { return counted(super.createBufferSource()); }
  };
  function counted(node) {
    const start = node.start.bind(node);
    node.start = (...args) => { window.sounds.started += 1; return start(...args); };
    return node;
  }
"""


def test_sound_is_off_until_turned_on_and_remembered(page: Page, serve, hands: Path) -> None:
    page.add_init_script(COUNT_SOUND)
    page.goto(serve(create_app(hands)))
    page.locator("#hand-list button").first.click()
    toggle = page.locator("#toggle-sound")
    expect(toggle).to_have_attribute("aria-pressed", "false")
    for _ in range(4):
        page.locator("#next").click()
    page.locator("#last").click()
    expect(page.locator("#street")).to_have_text("Hand over")
    # Nothing before the switch: not a sound, not even an AudioContext.
    assert page.evaluate("window.sounds") == {"contexts": 0, "started": 0}
    assert page.evaluate("localStorage.getItem('poker-table.sound')") is None

    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    assert page.evaluate("localStorage.getItem('poker-table.sound')") == "on"
    started = page.evaluate("window.sounds.started")
    assert started > 0  # turning it on says so
    page.locator("#first").click()
    page.locator("#next").click()
    page.locator("#last").click()
    page.keyboard.press("Home")
    for _ in range(len(page.evaluate("hand.steps"))):
        page.keyboard.press("ArrowRight")  # every step of the hand: blinds, actions, the pot
    assert page.evaluate("window.sounds.started") > started
    assert page.evaluate("window.sounds.contexts") == 1

    page.reload()  # remembered, and still silent until the visitor touches the page
    expect(page.locator("#toggle-sound")).to_have_attribute("aria-pressed", "true")
    assert page.evaluate("window.sounds") == {"contexts": 0, "started": 0}
    page.locator("#toggle-sound").click()
    expect(page.locator("#toggle-sound")).to_have_attribute("aria-pressed", "false")
    assert page.evaluate("localStorage.getItem('poker-table.sound')") == "off"


def test_sound_cues_play_at_a_live_table(page: Page, serve, tmp_path) -> None:
    path = tmp_path / "live.jsonl"
    session = LiveSession(
        [TightAggressive("tag"), CallingStation("station")],
        LeagueConfig(hands=3, seed=4, buy_in=100),
        path,
        pace=0.3,
    )
    url = serve(create_app(path, session))
    page.add_init_script(COUNT_SOUND)
    page.add_init_script("localStorage.setItem('poker-table.sound', 'on')")
    session.start()
    page.goto(url)
    toggle = page.locator("#toggle-sound")
    expect(toggle).to_have_attribute("aria-pressed", "true")  # remembered from before
    toggle.click()  # off: a click is what lets a page make sound at all
    toggle.click()  # and on again
    before = page.evaluate("window.sounds.started")
    page.wait_for_function(f"window.sounds.started > {before}", timeout=20_000)  # the table's
    session.pace = 0.0
    expect(page.locator("#live")).to_have_text("session over, 3 hands", timeout=30_000)


def test_storage_that_throws_leaves_the_viewer_working(page: Page, serve, hands: Path) -> None:
    # A private window or blocked site data can make localStorage throw on every touch.
    page.add_init_script(
        "Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });"
    )
    page.goto(serve(create_app(hands)))
    page.locator("#hand-list button").first.click()
    page.locator("#toggle-sound").click()
    expect(page.locator("#toggle-sound")).to_have_attribute("aria-pressed", "true")
    page.locator("#next").click()
    expect(page.locator("#scrub")).to_have_value("1")


def _box(page: Page, selector: str) -> dict[str, float]:
    return page.locator(selector).first.bounding_box()


def _overflowing(page: Page) -> list[str]:
    """Whatever sticks out past the sides of the screen, which a phone would cut off."""
    return page.evaluate(
        """() => {
          const width = document.documentElement.clientWidth;
          const out = [];
          const parts = "#table .seat .plate, #action-bar *, .controls *, #play-bar *";
          for (const el of document.querySelectorAll(parts)) {
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
    # The blinds rise, so the chip table has no bb/100 and no margins, and says why.
    expect(page.locator("#board-table tbody tr")).to_have_count(5)
    assert "bb/100" not in page.locator("#board-table thead").inner_text()
    expect(page.locator("#board-table td.margin")).to_have_count(0)
    expect(page.locator("#board-note")).to_contain_text("no fixed unit")

    bust = next(h for h in read_jsonl(path) if h.tournament["out"])
    name, place = next(iter(bust.tournament["out"].items()))
    page.locator("#tab-hands").click()
    page.evaluate(f"openHand({bust.hand_id!r}, {{at: 'end'}})")
    expect(page.locator("#hand-sub")).to_contain_text(f"Level {bust.tournament['level']}: blinds")
    expect(page.locator("#summary p.out")).to_contain_text(f"{name} is out in")


def test_a_file_of_freezeouts_shows_one_tournament_at_a_time(page: Page, serve, tmp_path) -> None:
    experiment = tmp_path / "fz.toml"
    experiment.write_text(
        'name = "fz"\nseats = ["tag", "rock", "maniac", "station", "random"]\n'
        "tournaments = 3\nlevel_hands = 5\nseed = 4\ncoach = false\n"
    )
    path = run_experiment(experiment, tmp_path, log=lambda _: None).folder / "hands.jsonl"
    second = standings_of(read_jsonl(path))[1]
    url = serve(create_app(path))
    page.goto(url)

    page.locator("#tab-board").click()
    pick = page.locator("#standings-pick")
    expect(pick.locator("option")).to_have_count(3)
    expect(page.locator("#board-title")).to_contain_text("Tournament 1 of 3 over after")
    pick.select_option("2")
    expect(page.locator("#board-title")).to_have_text(
        f"Tournament 2 of 3 over after {len(second.hands)} hands: {second.left[0][0]} wins"
    )
    places = second.places()
    rows = page.locator("#standings tbody tr")
    expect(rows).to_have_count(5)
    for row in range(5):
        name = rows.nth(row).locator("td").nth(1).inner_text()
        expect(rows.nth(row).locator("td").first).to_have_text(f"{places[name]}{THS[places[name]]}")

    page.locator("#standings-hands").click()
    expect(page.locator("#hand-list .count")).to_have_text(
        f"Tournament 2: {len(second.hands)} hands"
    )
    listed = page.locator("#hand-list button[data-id]")
    expect(listed).to_have_count(len(second.hands))
    ids = listed.evaluate_all("buttons => buttons.map(b => b.dataset.id)")
    assert sorted(ids) == sorted(h.hand_id for h in second.hands)
    page.locator("#hand-list li.subset button", has_text="All hands").click()
    expect(page.locator("#hand-list button[data-id]")).to_have_count(100)  # the first page


THS = {1: "st", 2: "nd", 3: "rd", 4: "th", 5: "th"}


def _seat(page: Page, seat: int) -> Any:
    return page.locator(f'#table .seat[data-seat="{seat}"]')


def test_two_people_play_one_table_each_seeing_only_their_own_cards(
    page: Page, browser, serve, tmp_path
) -> None:
    path = tmp_path / "friends.jsonl"
    path.touch()
    people = [WebHumanAgent("ann"), WebHumanAgent("bob")]
    session = LiveSession(
        [*people, CallingStation("station")], LeagueConfig(hands=2, seed=2, buy_in=100), path
    )
    url = serve(create_app(path, session))
    second = _watched(browser, viewport={"width": 1280, "height": 900})  # a second browser
    pages = {"ann": page, "bob": next(second)}
    for person in people:
        pages[person.name].goto(f"{url}/?seat={person.token}")
    session.start()
    seat = {"ann": 0, "bob": 1}
    end = time.time() + 60
    while not session.finished:
        assert time.time() < end, "the table stopped"
        person = next((h for h in people if h.pending is not None), None)
        if person is None:
            time.sleep(0.02)
            continue
        other = "bob" if person.name == "ann" else "ann"
        mine, theirs = pages[person.name], pages[other]
        expect(mine.locator("#action-bar")).to_be_visible()
        expect(mine.locator("#live")).to_have_text(f"{person.name} · waiting for you")
        expect(_seat(mine, seat[person.name]).locator(".card:not(.back)")).to_have_count(2)
        expect(_seat(mine, seat[other]).locator(".card:not(.back)")).to_have_count(0)
        expect(mine.locator("#table .equity")).to_have_count(0)
        # the other person waits with their own cards in view and no buttons
        expect(theirs.locator("#action-bar")).to_be_hidden()
        expect(_seat(theirs, seat[other]).locator(".card:not(.back)")).to_have_count(2)
        expect(_seat(theirs, seat[person.name]).locator(".card:not(.back)")).to_have_count(0)
        turn = person.pending
        folds = person.name == "bob" and turn["hand_id"] == "1" and not turn["legal"]["can_check"]
        mine.locator("#act-fold" if folds else "#act-check").click()
        wait = time.time() + 10  # (the bar may already be back: the next turn can be theirs too)
        while person.pending is turn:
            assert time.time() < wait, "the answer did not arrive"
            time.sleep(0.01)
    session.join(10)
    first = next(read_jsonl(path))
    assert first.decisions[1].applied == "fold" and 1 not in first.showdown  # bob folded hand 1
    for name, other in (("ann", "bob"), ("bob", "ann")):
        seen = pages[name]
        expect(seen.locator("#live")).to_have_text(f"{name} · session over, 2 hands")
        # The page opens the last hand's replay by itself (the summary shows only in a replay):
        # wait for that, or it can land after the hand opened here.
        expect(seen.locator("#hand-title")).to_have_text("Hand #2")
        expect(seen.locator("#summary")).to_be_visible()
        seen.evaluate("openHand('1', {at: 'end'})")
        expect(seen.locator("#hand-title")).to_have_text("Hand #1")
        expect(_seat(seen, seat[name]).locator(".card:not(.back)")).to_have_count(2)  # their own
        if other == "bob":  # bob folded: nobody else sees his cards, nor anyone's reasoning
            expect(_seat(seen, seat["bob"]).locator(".card:not(.back)")).to_have_count(0)
        assert "I want to see what you have" not in seen.content()  # the station's reasoning
    next(second, None)  # close the second browser, failing on any error it saw


def test_a_phone_at_a_table_with_friends_fits_and_shows_the_clock(
    phone: Page, serve, tmp_path
) -> None:
    path = tmp_path / "friends.jsonl"
    path.touch()
    ann, bob = WebHumanAgent("ann"), WebHumanAgent("bob")
    bots = [CallingStation("station"), TightAggressive("tag")]
    session = LiveSession(
        [ann, bob, *bots], LeagueConfig(hands=1, seed=3, buy_in=100), path, time_limit=600
    )
    url = serve(create_app(path, session))
    session.start()
    end = time.time() + 30
    while ann.pending is None:  # bob plays from somewhere else
        assert time.time() < end
        if bob.pending is not None:
            bob.submit("check" if bob.pending["legal"]["can_check"] else "call")
        time.sleep(0.01)
    phone.goto(f"{url}/?seat={ann.token}")  # opened mid-hand, as after a reload: the seat is there
    expect(phone.locator("#action-bar")).to_be_visible()
    expect(_seat(phone, 0).locator(".card:not(.back)")).to_have_count(2)
    expect(_seat(phone, 1).locator(".card:not(.back)")).to_have_count(0)
    expect(phone.locator("#action-clock")).to_have_text(re.compile(r"^\d+ s left$"))
    expect(phone.locator("#live")).to_have_text("ann · waiting for you")
    phone.wait_for_function(  # both on screen together, once the bar has scrolled into view
        """() => {
          const board = document.querySelector("#board").getBoundingClientRect();
          const bar = document.querySelector("#action-bar").getBoundingClientRect();
          return board.top >= 0 && bar.bottom <= innerHeight;
        }"""
    )
    assert _box(phone, "#table .seat .plate")["width"] <= 120
    clock, lead = _box(phone, "#action-clock"), _box(phone, "#action-bar .lead")
    assert clock["x"] + clock["width"] <= lead["x"] + lead["width"] + 0.5  # inside the bar
    assert _overflowing(phone) == []
    for person in (ann, bob):
        person.time_limit = 0.01  # let the hand finish without anyone
    phone.locator("#act-fold").tap()
    expect(phone.locator("#live")).to_have_text("ann · session over, 1 hands")
    session.join(10)


# ----- playing the bots in the published demo, the table running in the browser (Pyodide) -----

LOAD = 180_000  # ms for Python to download from the CDN and start


class _Files(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # the test output stays readable
        pass


@pytest.fixture(scope="module")
def demo_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The demo as Pages gets it, built by its own script (from 12 hands, to be quick), served
    the way `python3 -m http.server -d site` serves it: plain files, no headers of its own."""
    site = tmp_path_factory.mktemp("demo") / "site"
    build = [str(ROOT / "scripts" / "build_demo.sh"), str(site), "12"]
    done = subprocess.run(build, cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Files, directory=site))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


def _shot(page: Page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def _sit_down(page: Page, url: str, *, bots: int, seed: int, pick: Sequence[str] = ()) -> list[Any]:
    """Open the demo, press Play, take the first ``bots`` bots, turn each bot in ``pick`` on or
    off, and deal; every request the page made, in order."""
    requests: list[Any] = []
    page.on("requestfinished", lambda request: requests.append(request))
    page.goto(f"{url}?seed={seed}")
    expect(page.locator("#replay")).to_be_visible()  # the replay opens as it always did
    page.wait_for_load_state("networkidle")
    first_paint = [r.url for r in requests if "jsdelivr" in r.url or r.url.endswith(".whl")]
    assert first_paint == []  # nothing of Python before Play is pressed
    play = page.locator("#play-open")
    expect(play).to_have_text(re.compile(r"^Play the bots · \d+\.\d MB$"))
    play.click()
    assert _overflowing(page) == []  # the picker fits a phone too
    page.locator(f'#play-bar [data-bots="{bots}"]').click()
    for name in pick:
        page.locator(f'#play-bar [data-bot="{name}"]').click()
    page.locator("#play-deal").click()
    expect(page.locator("#play-seated")).to_be_visible(timeout=LOAD)
    command = f"poker-table play --seed {seed} --seats you:human,"
    expect(page.locator("#play-command")).to_contain_text(command)
    expect(page.locator("#live")).to_be_visible()
    return requests


def _play_the_first_hand(page: Page, name: str) -> int:
    """Check or call at each turn until the first hand is over; the number of turns played."""
    turns = 0
    while True:
        page.wait_for_function(
            """() => !document.querySelector("#action-bar").classList.contains("hidden")
                     || document.querySelectorAll("#hand-list li").length > 0""",
            timeout=60_000,
        )
        if page.locator("#hand-list li").count():
            break
        expect(page.locator("#live")).to_have_text("waiting for you")
        expect(_seat(page, 0).locator(".card:not(.back)")).to_have_count(2)  # your cards
        expect(_seat(page, 1).locator(".card.back")).to_have_count(2)  # nobody else's
        expect(page.locator("#table .equity")).to_have_count(0)
        expect(page.locator("#your-odds")).to_contain_text("You have")
        assert _overflowing(page) == []
        if turns == 1:
            _shot(page, f"{name}-mid-hand")
        turns += 1
        page.locator("#act-check").click()
        expect(page.locator("#action-bar")).to_be_hidden()
    # The hand is listed, and opens as a replay at its end for a few seconds before the next deal.
    expect(page.locator("#hand-list li").first).to_contain_text("#1")
    expect(page.locator("#hand-title")).to_have_text("Hand #1")
    expect(page.locator("#street")).to_have_text("Hand over")
    expect(page.locator("#summary")).to_contain_text("you")
    _shot(page, f"{name}-end")
    expect(page.locator("#file")).to_have_text("1 hand from your session")
    assert _overflowing(page) == []
    return turns


def test_a_visitor_plays_the_bots_in_the_published_demo(page: Page, demo_url: str) -> None:
    requests = _sit_down(page, demo_url, bots=3, seed=4)
    assert page.locator("#table .seat").count() == 4
    assert _play_the_first_hand(page, "desktop") == 4  # seed 4 gives you four turns
    # The button says how big the download is: what this load really took, to 10%.
    sent = sum(
        r.sizes()["responseBodySize"]
        for r in requests
        if "cdn.jsdelivr.net/pyodide/" in r.url or r.url.endswith(".whl")
    )
    said = float(re.search(r"([\d.]+) MB", page.locator("#play-open").inner_text()).group(1))
    assert abs(sent / 1e6 - said) <= 0.1 * said, f"measured {sent} bytes"
    # Nothing but the site itself, its fonts and the pinned Pyodide: micropip stays off PyPI.
    pinned = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/"
    fonts = ("https://fonts.googleapis.com/", "https://fonts.gstatic.com/")
    assert [r.url for r in requests if not r.url.startswith((demo_url, pinned, *fonts))] == []
    # The table goes on: the next hand is dealt after a beat on the result.
    expect(page.locator("#hand-title")).to_have_text("Hand #2", timeout=30_000)


def test_a_phone_plays_the_bots_in_the_published_demo(phone: Page, demo_url: str) -> None:
    _sit_down(phone, demo_url, bots=5, seed=2)
    assert phone.locator("#table .seat").count() == 6
    assert _play_the_first_hand(phone, "phone") == 4
    assert _box(phone, "#table .seat .plate")["width"] <= 120


def test_a_visitor_picks_which_bots_sit_down(page: Page, demo_url: str) -> None:
    # The first one, then the exploiter on and back off and on again: you, tag and exploiter.
    _sit_down(page, demo_url, bots=1, seed=6, pick=["exploiter", "exploiter", "exploiter"])
    expect(page.locator("#play-command")).to_have_text(
        "poker-table play --seed 6 --seats you:human,tag,exploiter"
    )
    expect(page.locator("#play-who")).to_have_text("You and 2 bots: tag, exploiter.")
    expect(page.locator("#table .seat")).to_have_count(3)
    names = page.locator("#table .seat .plate .name").all_inner_texts()
    assert sorted(names) == ["exploiter", "tag", "you"]
    expect(page.locator("#table .seat", has_text="exploiter").locator(".pos")).to_contain_text(
        "exploiter"
    )


def test_bots_in_the_browser_deal_and_play_what_the_cli_does(
    page: Page, demo_url: str, tmp_path: Path
) -> None:
    seats, seed, hands = ["tag", "maniac", "station", "rock", "random"], 31, 8
    path = tmp_path / "cli.jsonl"
    args = ["play", "-n", str(hands), "--seed", str(seed), "--seats", ",".join(seats)]
    assert main([*args, "-o", str(path), "-q"]) == 0
    page.goto(demo_url)
    played = page.evaluate(
        """async ({ seats, seed, hands }) => {
          const table = window.POKER_TABLE_PLAY;
          await table.load();
          await table.call({ op: "open", table: { seats, seed, hands } });
          while ((await table.call({ op: "step" })).next !== "done");
          await table.call({ op: "get", path: "/api/session" });
          const out = [];
          for (let n = 1; n <= hands; n++) {
            out.push((await table.call({ op: "get", path: `/api/hands/${n}` })).body);
          }
          return out;
        }""",
        {"seats": seats, "seed": seed, "hands": hands},
    )

    def dealt(hand: dict[str, Any]) -> dict[str, Any]:
        """All but the clock. The deck's 63-bit seed reaches the page as a JavaScript number,
        so it is compared at that precision."""
        for decision in hand["decisions"]:
            decision.pop("latency_ms")
        return hand | {"played_at": None, "seed": float(hand["seed"])}

    cli = [dealt(hand_payload(h)) for h in read_jsonl(path)]
    assert [dealt(h) for h in played] == cli
