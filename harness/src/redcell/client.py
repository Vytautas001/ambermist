"""OpenAI-compatible client for the Qwen3.8 endpoint behind the LiteLLM router.

Deliberately thin: the router handles load balancing, failover and per-team
limits, so this only needs correct retries, long timeouts and clean tool-call
parsing.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

import httpx
from .config import EndpointConfig, SamplingConfig

log = logging.getLogger(__name__)

try:
    from tenacity import (
        retry, retry_if_exception_type, stop_after_attempt,
        wait_exponential_jitter, before_sleep_log,
    )
    _HAVE_TENACITY = True
except ImportError:  # pragma: no cover - fallback for minimal environments
    _HAVE_TENACITY = False

    def retry(**_kw):
        """Minimal stand-in: retry on the same exception types tenacity would."""
        def deco(fn):
            import functools, time
            @functools.wraps(fn)
            def wrapper(*a, **k):
                last = None
                for attempt in range(4):
                    try:
                        return fn(*a, **k)
                    except (RetryableModelError, httpx.TransportError) as e:
                        last = e
                        if attempt < 3:
                            time.sleep(min(2 * 2 ** attempt, 30))
                raise last
            return wrapper
        return deco

# Worth retrying: the router is queueing or rebalancing, or a node is still loading
# weights (the ~111 GiB GGUF loads from the shared volume and can take a while).
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class ModelError(RuntimeError):
    pass


class RetryableModelError(ModelError):
    pass


class ChatResponse:
    """One assistant turn: text, any tool calls, reasoning trace and usage."""

    def __init__(self, raw: dict[str, Any]):
        self.raw = raw
        choice = (raw.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        self.content: str = msg.get("content") or ""
        self.reasoning: str = msg.get("reasoning_content") or msg.get("reasoning") or ""
        self.tool_calls: list[dict[str, Any]] = msg.get("tool_calls") or []
        self.finish_reason: str = choice.get("finish_reason") or ""
        self.usage: dict[str, Any] = raw.get("usage") or {}
        self.model: str = raw.get("model") or ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    def parsed_tool_calls(self) -> list[tuple[str, str, dict[str, Any]]]:
        """(call_id, tool_name, arguments). Malformed JSON arguments are a real
        failure mode with open-weight models, so surface them as an error the
        agent loop can feed back to the model rather than crashing the session."""
        out = []
        for tc in self.tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError as e:
                raise ModelError(
                    f"tool call {name!r} had unparseable arguments: {e}\n"
                    f"raw: {raw_args[:400]}"
                ) from e
            out.append((tc.get("id") or "", name, args))
        return out


class RedCellClient:
    def __init__(self, endpoint: EndpointConfig, sampling: SamplingConfig,
                 api_key_override: str | None = None):
        self.endpoint = endpoint
        self.sampling = sampling
        key = api_key_override or endpoint.api_key
        self._client = httpx.Client(
            base_url=endpoint.base_url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(endpoint.timeout_s, connect=endpoint.connect_timeout_s),
        )

    def __enter__(self) -> "RedCellClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def health(self) -> bool:
        try:
            r = self._client.get("/models", timeout=10.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    @retry(reraise=True)
    def chat(self, messages: list[dict[str, Any]],
             tools: Iterable[dict[str, Any]] | None = None,
             **overrides: Any) -> ChatResponse:
        body: dict[str, Any] = {
            "model": self.endpoint.model,
            "messages": messages,
            "temperature": overrides.get("temperature", self.sampling.temperature),
            "top_p": overrides.get("top_p", self.sampling.top_p),
            "max_tokens": overrides.get("max_tokens", self.sampling.max_tokens),
        }
        tools = list(tools or [])
        if tools:
            body["tools"] = tools
            # Keep tool_choice="auto"; the harness does not force a specific tool.
            body["tool_choice"] = "auto"

        try:
            r = self._client.post("/chat/completions", json=body)
        except httpx.TransportError:
            raise
        if r.status_code in RETRYABLE_STATUS:
            raise RetryableModelError(f"HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code >= 400:
            raise ModelError(f"HTTP {r.status_code}: {r.text[:500]}")
        return ChatResponse(r.json())
