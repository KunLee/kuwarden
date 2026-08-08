"""Anthropic Messages API.

Uses the official SDK rather than raw HTTP. Three things here are not obvious and are the
usual sources of a silent defect:

**A refusal is a successful HTTP response.** Safety classifiers return 200 with
`stop_reason: "refusal"` and an empty `content` array. Code that reads `content[0]`
unconditionally crashes on exactly the inputs this system exists to survive — ticket text is
hostile by assumption. `stop_reason` is checked first, always.

**Sampling parameters are rejected.** `temperature`, `top_p`, `top_k` and the older
`budget_tokens` all return 400 on current models. Thinking depth is `effort`, not a token
budget.

**Structured output replaces prefill.** Assistant-turn prefill — the old way to force JSON —
now returns 400. `output_config.format` does the job properly and validates.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from engine.adapters.credentials import (
    CredentialBroker,
    CredentialKind,
    CredentialRequest,
)
from engine.adapters.llm import (
    Completion,
    LLMError,
    LLMRequest,
    ModelRefusal,
    Provider,
    assert_may_call_llm,
)

#: Streaming above this, because a non-streaming request the SDK estimates will run past the
#: HTTP timeout raises rather than completing. Nodes that produce a diff sit well above it.
STREAM_ABOVE_MAX_TOKENS = 16_000


class AnthropicLLM:
    """Implements `LLMAdapter` against the Anthropic Messages API.

    The model id is supplied by configuration, never defaulted here — see
    `docs/reference/models.md`. A model id baked into engine code is a fact with a shelf life
    of months hiding in a file nobody reviews for freshness.
    """

    def __init__(
        self,
        model: str,
        broker: CredentialBroker,
        *,
        realm: str = "anthropic",
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._broker = broker
        self._realm = realm
        self._base_url = base_url
        # Same seam as every other adapter: the far side of HTTP is the only thing a test
        # replaces, so the SDK, the request shape and the response handling stay real.
        self._transport = transport

    @property
    def provider(self) -> Provider:
        return Provider.ANTHROPIC

    async def _client(self) -> Any:
        # Imported here so that a deployment using a self-hosted OpenAI-compatible endpoint
        # never needs the package present.
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - depends on install profile
            raise LLMError("the `anthropic` package is not installed") from exc

        token = await self._broker.resolve(
            CredentialRequest(kind=CredentialKind.LLM_API_KEY, realm=self._realm)
        )
        return AsyncAnthropic(
            api_key=token.reveal(),
            base_url=self._base_url or os.environ.get("ANTHROPIC_BASE_URL") or None,
            http_client=(
                httpx.AsyncClient(transport=self._transport) if self._transport else None
            ),
        )

    async def complete(self, request: LLMRequest) -> Completion:
        assert_may_call_llm()
        client = await self._client()

        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.prompt}],
            # Adaptive rather than a token budget: `budget_tokens` returns 400 on current
            # models, and the model calibrates depth better than a fixed ceiling did.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": request.effort},
        }
        if request.schema is not None:
            params["output_config"]["format"] = {
                "type": "json_schema",
                "schema": request.schema,
            }

        try:
            if request.max_tokens > STREAM_ABOVE_MAX_TOKENS:
                async with client.messages.stream(**params) as stream:
                    message = await stream.get_final_message()
            else:
                message = await client.messages.create(**params)
        except Exception as exc:
            raise LLMError(f"{type(exc).__name__} from the Anthropic API") from None

        return _to_completion(message, expects_schema=request.schema is not None)


def _to_completion(message: Any, *, expects_schema: bool) -> Completion:
    # Before content, always. A refusal carries an empty content array.
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise ModelRefusal(
            category=getattr(details, "category", None),
            detail=getattr(details, "explanation", "") or "",
        )

    text = "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )

    parsed: dict[str, Any] | None = None
    if expects_schema:
        try:
            candidate = json.loads(text)
        except ValueError:
            raise LLMError("schema was requested but the response was not JSON") from None
        if not isinstance(candidate, dict):
            raise LLMError(f"schema was requested but the response was a {type(candidate)}")
        parsed = candidate

    if message.stop_reason == "max_tokens":
        # Surfaced rather than swallowed: a truncated plan looks like a short plan.
        raise LLMError("response hit max_tokens; the output is truncated")

    return Completion(
        text=text,
        parsed=parsed,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        model=message.model,
        stop_reason=message.stop_reason,
    )
