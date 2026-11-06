"""A seat on any OpenAI-compatible API: OpenAI, Groq, OpenRouter, Gemini or a server of your own.

``llm:nerd@groq/<model>`` runs on the key in ``GROQ_API_KEY``. :class:`CompatClient` answers the
one call a seat makes, so the seat's parsing, fallbacks and accounting stay as they are. Providers
differ in what they honour, so structured output steps down from a JSON schema to plain JSON to
asking nicely, and the reply is cleaned before the seat reads it.

The key stays in this file's request header. A base URL only ever comes from the environment,
never from an agents or experiment file, so a file from someone else cannot send your key elsewhere.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from poker_table import __version__

CUSTOM_URL_ENV = "POKER_TABLE_BASE_URL"
TIMEOUT_SECONDS = 120.0
RETRIES = 4  # free tiers often answer 429, and a table can afford to wait a little
LONGEST_WAIT = 60.0
AGENT = f"poker-table/{__version__}"  # Groq refuses Python's default User-Agent outright


@dataclass(frozen=True, slots=True)
class Provider:
    name: str
    base_url: str
    key_env: str
    max_tokens_field: str = "max_tokens"
    reports_cost: bool = False  # puts the dollars in its reply, so a budget can work
    key_path: str = "/models"  # a free request that fails on a bad key


PROVIDERS = {
    "openai": Provider(
        "OpenAI",
        "https://api.openai.com/v1",
        "OPENAI_API_KEY",
        max_tokens_field="max_completion_tokens",
    ),
    "groq": Provider("Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": Provider(
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        reports_cost=True,
        key_path="/key",  # its model list is public, so it says nothing about a key
    ),
    "gemini": Provider(
        "Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"
    ),
    # Its own key variable, so that a real OPENAI_API_KEY is never sent to a local server.
    "compat": Provider("your server", "", "POKER_TABLE_API_KEY"),
}


def provider_of(model: str) -> Provider | None:
    prefix, slash, _ = model.partition("/")
    return PROVIDERS.get(prefix) if slash else None


def is_compat(model: str) -> bool:
    return provider_of(model) is not None


def is_priced(model: str) -> bool:
    """Whether this table can say what a call to ``model`` cost. It only knows Claude's prices and
    what a provider reports, and a budget cannot limit what it cannot see."""
    provider = provider_of(model)
    return provider is None or provider.reports_cost


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True, slots=True)
class CompatUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class CompatResponse:
    """Shaped like the Messages API response the seat already reads."""

    content: list[TextBlock]
    stop_reason: str
    model: str
    usage: CompatUsage = field(default_factory=CompatUsage)


class CompatError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """urllib would carry the key along a redirect to any other host."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def clean_reply(text: str) -> str:
    """The JSON in a reply: without a reasoning model's <think> block or a code fence around it."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text


class _Messages:
    def __init__(self, client: CompatClient) -> None:
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
    ) -> CompatResponse:
        text = system if isinstance(system, str) else "\n".join(b["text"] for b in system)
        schema = ((output_config or {}).get("format") or {}).get("schema")
        data = self._client.chat(model, max_tokens, text, messages, schema)
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason")
        stop = {"length": "max_tokens", "content_filter": "refusal"}.get(finish, "end_turn")
        usage = data.get("usage") or {}
        return CompatResponse(
            content=[
                TextBlock(clean_reply(str((choice.get("message") or {}).get("content") or "")))
            ],
            stop_reason=stop,
            model=model,
            usage=CompatUsage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                cost_usd=float(usage.get("cost") or 0.0),
            ),
        )


class CompatClient:
    """Enough of ``anthropic.Anthropic`` for a seat, served by an OpenAI-compatible API."""

    def __init__(self, model: str, *, sleep: Callable[[float], None] = time.sleep) -> None:
        provider = provider_of(model)
        if provider is None:
            raise ValueError(f"{model} does not name a provider: {', '.join(PROVIDERS)}")
        self.provider = provider
        self.base_url = (provider.base_url or os.environ.get(CUSTOM_URL_ENV, "")).rstrip("/")
        self.key = os.environ.get(provider.key_env, "").strip()
        self._sleep = sleep
        self._open = urllib.request.build_opener(_NoRedirects).open
        self._strictness = 0  # how far down the output steps this provider has sent us
        self.messages = _Messages(self)

    def _scrub(self, text: str) -> str:
        """A provider can quote the key back in an error, and errors end up in hand files."""
        return text.replace(self.key, "***") if self.key else text

    def _send(self, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "User-Agent": AGENT},
        )
        if self.key:
            request.add_header("Authorization", f"Bearer {self.key}")
        try:
            with self._open(request, timeout=TIMEOUT_SECONDS) as reply:
                return json.load(reply)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                detail = json.loads(raw).get("error", raw)
                detail = detail.get("message", str(detail)) if isinstance(detail, dict) else detail
            except (json.JSONDecodeError, AttributeError):
                detail = raw
            error = CompatError(exc.code, self._scrub(str(detail))[:300])
            error.retry_after = retry_after  # type: ignore[attr-defined]
            raise error from None

    def _send_waiting(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """Send, waiting out rate limits and a server's bad moments."""
        for attempt in range(RETRIES):
            try:
                return self._send(path, body)
            except CompatError as exc:
                if exc.status not in (429, 500, 502, 503) or attempt == RETRIES - 1:
                    raise
                told = getattr(exc, "retry_after", None)
                wait = float(told) if told and str(told).isdigit() else 2.0**attempt
                self._sleep(min(wait, LONGEST_WAIT))
        raise AssertionError("unreachable")

    def chat(
        self,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        name = model.partition("/")[2]
        while True:
            body = self._body(name, max_tokens, system, messages, schema)
            try:
                return self._send_waiting("/chat/completions", body)
            except CompatError as exc:
                # A 400 may only mean the provider has no such output format, so step down.
                if exc.status != 400 or schema is None or self._strictness >= 2:
                    raise
                self._strictness += 1

    def _body(
        self,
        name: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if schema is not None and self._strictness > 0:
            keys = ", ".join(schema.get("properties", {}))
            system = f"{system}\n\nReply with one JSON object and nothing else, with keys: {keys}."
        chat = ([{"role": "system", "content": system}] if system else []) + [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
        body: dict[str, Any] = {
            "model": name,
            "messages": chat,
            self.provider.max_tokens_field: max_tokens,
        }
        if schema is not None and self._strictness == 0:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "decision", "strict": True, "schema": schema},
            }
        elif schema is not None and self._strictness == 1:
            body["response_format"] = {"type": "json_object"}
        return body

    def check(self, model: str) -> None:
        """Fail before the first hand on a missing or refused key or a wrong name. This is free."""
        provider = self.provider
        if not self.base_url:
            raise ValueError(f"{model} needs {CUSTOM_URL_ENV}, the address of your server")
        if not self.key and provider.base_url:
            raise ValueError(
                f"{model} runs on your own {provider.name} key, and {provider.key_env} is not set"
            )
        try:
            if provider.key_path != "/models":
                self._send(provider.key_path, None)
            listed = self._send("/models", None).get("data") or []
        except CompatError as exc:
            # Gemini says this with a 400, where the others use 401 or 403.
            refused = exc.status in (401, 403) or (
                exc.status == 400 and "key" in exc.message.lower()
            )
            if refused:
                raise ValueError(
                    f"{provider.name} refused the key in {provider.key_env}: {exc.message}"
                ) from None
            if exc.status == 404:  # not every server lists its models, and that is no fault
                return
            raise ValueError(f"{provider.name} answered {exc}") from None
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot reach {provider.name} at {self.base_url}: {exc}") from None
        name = model.partition("/")[2]
        ids = {str(m.get("id", "")).removeprefix("models/") for m in listed}
        if ids and name not in ids:
            near = sorted(i for i in ids if name.split("-")[0].lower() in i.lower())[:3]
            hint = f" Close: {', '.join(near)}." if near else ""
            raise ValueError(f"{provider.name} has no model named {name}.{hint}")
