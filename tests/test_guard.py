"""A table on localhost is not private from the browser you read the web with."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poker_table.cli import main
from poker_table.web.app import create_app
from poker_table.web.guard import hosts_to_answer


@pytest.fixture
def hands_file(tmp_path: Path) -> Path:
    path = tmp_path / "hands.jsonl"
    assert main(["play", "-n", "3", "--seats", "tag,station", "-o", str(path), "-q"]) == 0
    return path


@pytest.fixture
def paid_call(monkeypatch) -> list:
    """Stands in for the model call on the owner's key, and records whether it was reached."""
    from poker_table.coach.narrate import Narration

    called: list = []

    def fake(report, *, model, **_):
        called.append(model)
        return Narration((), "", 0, model, 0.0)

    monkeypatch.setattr("poker_table.coach.narrate.narrate", fake)
    return called


def client_for(hands_file: Path, allowed=("localhost", "127.0.0.1", "::1")) -> TestClient:
    return TestClient(create_app(hands_file, allowed_hosts=allowed), base_url="http://localhost")


@pytest.mark.parametrize(
    "host", ["localhost", "localhost:8765", "LocalHost:8765", "127.0.0.1:8765", "[::1]:8765"]
)
def test_a_table_answers_to_the_names_you_browse_with(hands_file: Path, host: str) -> None:
    assert client_for(hands_file).get("/api/session", headers={"host": host}).status_code == 200


@pytest.mark.parametrize(
    "host",
    ["evil.example", "evil.example:8765", "localhost.evil.example", "127.0.0.1.evil.example"],
)
def test_a_page_that_points_its_own_name_here_gets_nothing(hands_file: Path, host: str) -> None:
    response = client_for(hands_file).get("/api/hands", headers={"host": host})
    assert response.status_code == 400
    assert "--allow-host" in response.text and "tag" not in response.text


def test_a_rebound_page_cannot_spend_the_key(hands_file: Path, paid_call: list) -> None:
    response = client_for(hands_file).post(
        "/api/coach/narrate", json={"player": "tag"}, headers={"host": "evil.example"}
    )
    assert response.status_code == 400 and not paid_call


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:9999"])
def test_a_change_from_another_site_is_refused(hands_file, paid_call, origin: str) -> None:
    response = client_for(hands_file).post(
        "/api/coach/narrate",
        json={"player": "tag"},
        headers={"host": "localhost:8765", "origin": origin},
    )
    assert response.status_code == 403 and not paid_call


def test_the_tables_own_page_and_a_script_are_let_through(hands_file, paid_call) -> None:
    client = client_for(hands_file)
    own_page = {"host": "localhost:8765", "origin": "http://localhost:8765"}
    assert client.post("/api/coach/narrate", json={"player": "tag"}, headers=own_page).is_success
    assert client.post("/api/coach/narrate", json={"player": "tag"}).is_success  # no Origin: curl
    assert len(paid_call) == 2


def test_reading_is_not_refused_for_its_origin(hands_file: Path) -> None:
    headers = {"host": "localhost", "origin": "https://elsewhere.example"}
    assert client_for(hands_file).get("/api/session", headers=headers).status_code == 200


def test_a_tunnels_name_can_be_added(hands_file: Path, paid_call: list) -> None:
    client = client_for(hands_file, allowed=hosts_to_answer("127.0.0.1", ["table.example"]))
    tunnel = {"host": "table.example", "origin": "https://table.example"}
    assert client.get("/api/session", headers=tunnel).status_code == 200
    assert client.post("/api/coach/narrate", json={"player": "tag"}, headers=tunnel).is_success
    # A proxy may rewrite Host, and the Origin still names the site it was told about.
    rewritten = {"host": "127.0.0.1:8765", "origin": "https://table.example"}
    assert client.post("/api/coach/narrate", json={"player": "tag"}, headers=rewritten).is_success
    assert client.get("/api/session", headers={"host": "evil.example"}).status_code == 400


def test_with_no_names_given_only_the_origin_check_applies(hands_file: Path, paid_call) -> None:
    client = TestClient(create_app(hands_file), base_url="http://anything.example")
    assert client.get("/api/session").status_code == 200
    other = {"origin": "https://evil.example"}
    assert (
        client.post("/api/coach/narrate", json={"player": "tag"}, headers=other).status_code == 403
    )
    assert not paid_call


@pytest.mark.parametrize(
    ("bound", "extra", "expected"),
    [
        ("127.0.0.1", [], ["127.0.0.1", "localhost", "127.0.0.1", "::1"]),
        (
            "192.168.1.5",
            ["t.example"],
            ["192.168.1.5", "localhost", "127.0.0.1", "::1", "t.example"],
        ),
        ("0.0.0.0", [], None),
        ("::", [], None),
        ("0.0.0.0", ["localhost"], ["localhost", "127.0.0.1", "::1", "localhost"]),
    ],
)
def test_the_names_follow_from_what_it_is_bound_to(bound, extra, expected) -> None:
    assert hosts_to_answer(bound, extra) == expected


# ----- through the command line ------------------------------------------------------------


@pytest.fixture
def served(monkeypatch) -> dict:
    calls: dict = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: calls.update(kw, app=app))
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    return calls


def asks(served: dict, host: str) -> int:
    client = TestClient(served["app"], base_url="http://localhost")
    return client.get("/api/session", headers={"host": host}).status_code


def test_serve_is_guarded_by_default(hands_file: Path, served: dict, capsys) -> None:
    assert main(["serve", str(hands_file)]) == 0
    assert asks(served, "localhost:8765") == 200 and asks(served, "evil.example") == 400
    assert "every interface" not in capsys.readouterr().err


def test_watch_is_guarded(served: dict) -> None:
    assert main(["watch", "-n", "1", "--pace", "0", "--no-open"]) == 0
    assert asks(served, "evil.example") == 400


def test_every_interface_stays_open_and_says_so(hands_file: Path, served: dict, capsys) -> None:
    assert main(["serve", str(hands_file), "--host", "0.0.0.0"]) == 0
    assert asks(served, "evil.example") == 200
    assert "--allow-host" in capsys.readouterr().err


def test_every_interface_with_names_is_guarded_and_quiet(hands_file, served, capsys) -> None:
    assert main(["serve", str(hands_file), "--host", "0.0.0.0", "--allow-host", "localhost"]) == 0
    assert asks(served, "localhost:8765") == 200 and asks(served, "evil.example") == 400
    assert "every interface" not in capsys.readouterr().err
