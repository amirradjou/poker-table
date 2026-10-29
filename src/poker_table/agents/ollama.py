"""A local model in a seat: Ollama's chat API behind the one call an LLM seat makes.

``LLMAgent`` asks Claude through ``client.messages.create(...)``. :class:`OllamaClient`
answers the same call from a model running on this machine, so ``llm:nerd@ollama/qwen2.5:7b``
plays with no API key and at no cost. The structured-output schema becomes Ollama's
``format``, the system blocks one system message and ``max_tokens`` its ``num_predict``;
``effort`` and cache hints have no Ollama equivalent and are dropped.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

PREFIX = "ollama/"
DEFAULT_HOST = "http://localhost:11434"

# GET when the body is None, POST otherwise; returns the decoded JSON reply.
Transport = Callable[[str, dict[str, Any] | None], dict[str, Any]]


def is_local(model: str) -> bool:
    return model.startswith(PREFIX)


def default_host() -> str:
    """``$OLLAMA_HOST`` as Ollama itself reads it (``host:port`` is fine), or localhost."""
    host = os.environ.get("OLLAMA_HOST", "").strip() or DEFAULT_HOST
    if "://" not in host:
        host = "http://" + host
    return host.rstrip("/")


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True, slots=True)
class LocalUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class LocalResponse:
    """Shaped like the Messages API response the seat already reads."""

    content: list[TextBlock]
    stop_reason: str
    model: str
    usage: LocalUsage = field(default_factory=LocalUsage)


class _Messages:
    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str | list[dict[str, Any]] = "",
        messages: list[dict[str, Any]],
        output_config: dict[str, Any] | None = None,
        **_unsupported: Any,
    ) -> LocalResponse:
        name = model.removeprefix(PREFIX)
        text = system if isinstance(system, str) else "\n".join(b["text"] for b in system)
        chat = ([{"role": "system", "content": text}] if text else []) + [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
        body: dict[str, Any] = {
            "model": name,
            "messages": chat,
            "stream": False,
            "keep_alive": self._client.keep_alive,
            "options": {**self._client.options, "num_predict": max_tokens},
        }
        schema = ((output_config or {}).get("format") or {}).get("schema")
        if schema is not None:
            body["format"] = schema
        data = self._client.transport("/api/chat", body)
        stop = "max_tokens" if data.get("done_reason") == "length" else "end_turn"
        return LocalResponse(
            content=[TextBlock(str((data.get("message") or {}).get("content", "")))],
            stop_reason=stop,
            model=model,
            usage=LocalUsage(
                input_tokens=int(data.get("prompt_eval_count") or 0),
                output_tokens=int(data.get("eval_count") or 0),
            ),
        )


class OllamaClient:
    """Enough of ``anthropic.Anthropic`` for a seat, served by a local Ollama."""

    def __init__(
        self,
        host: str | None = None,
        *,
        transport: Transport | None = None,
        options: dict[str, Any] | None = None,
        timeout: float = 600.0,
        keep_alive: str = "30m",
    ) -> None:
        self.host = (host or default_host()).rstrip("/")
        self.timeout = timeout
        self.keep_alive = keep_alive
        # A poker prompt with reads and notes runs past Ollama's small default context.
        self.options: dict[str, Any] = {"num_ctx": 8192, **(options or {})}
        self.transport: Transport = transport or self._http
        self.messages = _Messages(self)

    def _http(self, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.host + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as reply:
                return json.load(reply)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except json.JSONDecodeError:
                pass
            raise RuntimeError(f"ollama {exc.code}: {detail}") from None

    def models(self) -> list[str]:
        """The models pulled into this Ollama, e.g. ``["qwen2.5:7b"]``."""
        return [m["name"] for m in self.transport("/api/tags", None).get("models", [])]

    def check(self, model: str) -> None:
        """Fail before the first hand when the server is down or the model is not pulled."""
        name = model.removeprefix(PREFIX)
        try:
            pulled = self.models()
        except (OSError, RuntimeError) as exc:
            raise ValueError(
                f"no Ollama at {self.host} ({exc}); start it with `ollama serve`"
            ) from None
        if name not in pulled and f"{name}:latest" not in pulled:
            have = ", ".join(pulled) or "none"
            raise ValueError(f"Ollama has no {name!r} (pulled: {have}); run `ollama pull {name}`")
