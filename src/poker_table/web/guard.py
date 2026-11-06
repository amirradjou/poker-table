"""Who may talk to a table.

A table on localhost is not private from the browser you read the web with. A page on evil.example
can point that name at 127.0.0.1 (DNS rebinding), and the browser then treats the table as that
page's own site: it can read the hands and ask for coach notes on your API key. So a table answers
only to names it was told about. A POST must also come from the table's own page, which browsers
state in the Origin header, rather than relying on a framework refusing odd content types.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

LOOPBACK = ("localhost", "127.0.0.1", "::1")
EVERY_INTERFACE = ("0.0.0.0", "::", "")
_READ_ONLY = ("GET", "HEAD", "OPTIONS")


def hosts_to_answer(bound_to: str, allowed: Iterable[str] = ()) -> list[str] | None:
    """The names a table bound to ``bound_to`` should answer to, or None for any. Bound to every
    interface it cannot know its own names, so that stays open unless it is told them."""
    allowed = list(allowed)
    if bound_to in EVERY_INTERFACE and not allowed:
        return None
    names = [*LOOPBACK, *allowed]
    return names if bound_to in EVERY_INTERFACE else [bound_to, *names]


def _hostname(netloc: str) -> str | None:
    """``"Localhost:8765"`` to ``"localhost"``, ``"[::1]:8765"`` to ``"::1"``; None if malformed."""
    try:
        return urlsplit(f"//{netloc}").hostname
    except ValueError:
        return None


class TableGuard:
    """ASGI middleware, which unlike a function middleware leaves the live event stream alone."""

    def __init__(self, app: Any, allowed_hosts: Iterable[str] | None = None) -> None:
        self.app = app
        self.allowed = None if allowed_hosts is None else {h.lower() for h in allowed_hosts}

    def _refusal(self, scope: dict[str, Any]) -> tuple[int, str] | None:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        if self.allowed is not None and _hostname(host) not in self.allowed:
            return 400, f"this table does not answer to {host!r}: serve --allow-host adds a name"
        origin = headers.get("origin")
        if origin is None or scope["method"] in _READ_ONLY:
            return None
        netloc = urlsplit(origin).netloc
        if netloc.lower() == host.lower():
            return None
        name = _hostname(netloc)
        if self.allowed is not None and name in self.allowed and name not in LOOPBACK:
            return None  # a tunnel that rewrites Host, for a name it was told about
        return 403, "this table only takes changes from its own page"

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        refusal = self._refusal(scope) if scope["type"] == "http" else None
        if refusal is None:
            await self.app(scope, receive, send)
            return
        status, text = refusal
        body = text.encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
