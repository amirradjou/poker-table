"""Write the viewer as a static site: the same page, answered by files instead of a server.

`poker-table serve FILE --export DIR` writes `index.html`, the stylesheet and script, and a
`data/` tree holding exactly what the API would have returned. The page notices
`window.POKER_TABLE_STATIC` and fetches `data/…json` instead of `/api/…`, so a session can be
committed to a repo, published to GitHub Pages, or served by any static file server
(`python3 -m http.server -d DIR`; a browser will not fetch from a `file://` page).

What a static site cannot do, and therefore hides: live tables (they need a running session),
drills (they record your answers), Claude's notes (they need an API key), and the Coach tab's
week picker (one report per seat is exported, over all the hands).

With ``play_wheel`` (`--export-play WHEEL`, a wheel of this package) the site also lets a visitor
sit down with the bots: a Play button loads Python in their browser (Pyodide, from a CDN) and
the table from the wheel, and nothing of it loads until they press it. See ``web/demo_play.py``.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from poker_table.web.app import STATIC
from poker_table.web.demo_play import BOTS, DEFAULT_BOTS
from poker_table.web.payloads import (
    HandStore,
    bankroll_payload,
    coach_payload,
    hand_payload,
    hands_payload,
    highlight_of,
    margins_payload,
    odds_payload,
    session_payload,
    stats_payload,
)

# The flag the page reads, injected just before it loads its script.
FLAG = "<script>window.POKER_TABLE_STATIC = true;</script>\n"
PLAY_FILES = ("play.js", "play-worker.js", "play.css")  # static/ files only a site to play needs
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def file_id(hand_id: str) -> str:
    """A hand id as a filename. The viewer applies the same rule to build the URL."""
    return _UNSAFE.sub("_", hand_id) or "_"


def _write(path: Path, payload: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def export_site(
    hands: Path | str,
    out: Path | str,
    *,
    samples: int = 200,
    players: list[str] | None = None,
    odds: bool = False,
    play_wheel: Path | str | None = None,
) -> dict[str, Any]:
    """Write the viewer and one JSON file per API response into ``out``.

    ``players`` limits which seats get a coach report (each one replays every hand, so the
    default — every seat — is the slow part of a big export). ``odds`` adds the probability
    panel, which costs about half a second per hand and is therefore asked for, not assumed.
    ``play_wheel`` adds the Play button: the wheel is copied to ``play/`` under its own name,
    which the browser's installer reads the package's name and version from.
    """
    store = HandStore(Path(hands))
    if not store.hands:
        raise ValueError(f"{hands} has no hands to export")
    wheel = Path(play_wheel) if play_wheel is not None else None
    if wheel is not None and not (wheel.is_file() and wheel.name.startswith("poker_table-")):
        raise ValueError(f"{wheel} is not a wheel of this package (uv build --wheel makes one)")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    page = (STATIC / "index.html").read_text(encoding="utf-8")
    if FLAG not in page:
        page = page.replace('<script src="static/app.js">', FLAG + '<script src="static/app.js">')
    if wheel is not None:
        page = page.replace("</head>", '<link rel="stylesheet" href="static/play.css">\n</head>')
        where = json.dumps({"url": f"play/{wheel.name}", "bytes": wheel.stat().st_size})
        # The bots the visitor can pick from, before Python has loaded to say so itself.
        bots = json.dumps({"names": list(BOTS), "default": DEFAULT_BOTS})
        page = page.replace(
            '<script src="static/app.js"></script>',
            '<script src="static/app.js"></script>\n'
            f"<script>window.POKER_TABLE_PLAY_WHEEL = {where};</script>\n"
            f"<script>window.POKER_TABLE_PLAY_BOTS = {bots};</script>\n"
            '<script src="static/play.js"></script>',
        )
        (out / "play").mkdir(exist_ok=True)
        shutil.copyfile(wheel, out / "play" / wheel.name)
    (out / "index.html").write_text(page, encoding="utf-8")
    skip = ["index.html"] if wheel is not None else ["index.html", *PLAY_FILES]
    shutil.copytree(
        STATIC, out / "static", dirs_exist_ok=True, ignore=shutil.ignore_patterns(*skip)
    )

    # Tagging hands costs what the odds cost, so it comes with them.
    session = session_payload(store) | {"highlights": odds, "odds": odds}
    bytes_written = _write(out / "data" / "session.json", session)
    # The list the viewer pages through is newest first, all of it: the page slices it itself.
    bytes_written += _write(out / "data" / "hands.json", hands_payload(store, 0, len(store.hands)))
    seen: dict[str, str] = {}
    for history in store.hands:
        name = file_id(history.hand_id)
        if name in seen:
            raise ValueError(
                f"hands {seen[name]!r} and {history.hand_id!r} would write the same file {name!r}"
            )
        seen[name] = history.hand_id
        bytes_written += _write(out / "data" / "hands" / f"{name}.json", hand_payload(history))
    bytes_written += _write(out / "data" / "bankroll.json", bankroll_payload(store))
    bytes_written += _write(out / "data" / "stats.json", stats_payload(store))
    bytes_written += _write(out / "data" / "margins.json", margins_payload(store))

    if odds:
        tagged = []
        for history in store.hands:
            payload = odds_payload(history)
            bytes_written += _write(
                out / "data" / "odds" / f"{file_id(history.hand_id)}.json", payload
            )
            tagged.append(highlight_of(history, payload["commentary"]))
        # All at once where the server pages: there is no slow work left for a page to wait on.
        everything = {"total": len(tagged), "next": None, "hands": tagged[::-1]}
        bytes_written += _write(out / "data" / "highlights.json", everything)

    coached = players if players is not None else store.players()
    for player in coached:
        if player not in store.players():
            raise ValueError(f"no seat named {player!r} in {hands}")
        payload = coach_payload(store, player, samples=samples)
        bytes_written += _write(out / "data" / "coach" / f"{file_id(player)}.json", payload)

    return {
        "dir": str(out),
        "hands": len(store.hands),
        "coached": list(coached),
        "odds": odds,
        "play": wheel is not None,
        "bytes": bytes_written,
    }
