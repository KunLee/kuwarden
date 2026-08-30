"""The LLM adapter, and the four ways it would otherwise fail quietly.

Every test here targets something that returns a 200 or a plausible-looking object rather
than an obvious error — the failures you only find in production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from engine.adapters.credentials import EnvCredentialBroker
from engine.adapters.llm import (
    LLMError,
    LLMRequest,
    LLMRequestRejected,
    ModelRefusal,
    Provider,
)
from engine.adapters.llm.anthropic_api import AnthropicLLM
from engine.adapters.llm.factory import llm_adapter
from engine.config import ConfigError, parse
from engine.nodes import REGISTRY
from engine.nodes.base import executing
from tests.conftest import KUWARDEN_YAML

BROKER = EnvCredentialBroker({"KUWARDEN_LLM_API_KEY": "sk-ant-fake"})
REPO_ROOT = Path(__file__).resolve().parents[1]
#: Read once, so the assertion below and the flow cannot drift apart unnoticed.
NON_RETRYABLE = (REPO_ROOT / "engine" / "flows" / "delivery.py").read_text(encoding="utf-8")


def _message(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": "{}"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }
    body.update(overrides)
    return body


def _adapter(response: dict[str, Any], seen: list[httpx.Request]) -> AnthropicLLM:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=response)

    return AnthropicLLM("claude-opus-5", BROKER, transport=httpx.MockTransport(handler))


async def test_a_refusal_raises_rather_than_returning_empty() -> None:
    """The headline trap: a refusal is HTTP 200 with an empty content array.

    Ticket text is hostile by assumption, so this is the response most likely to arrive on
    exactly the inputs the system exists to survive. Code that reads `content[0]` crashes;
    code that returns the empty string produces a plan nobody wrote.
    """
    seen: list[httpx.Request] = []
    adapter = _adapter(
        _message(
            content=[],
            stop_reason="refusal",
            stop_details={"type": "refusal", "category": "cyber", "explanation": "declined"},
        ),
        seen,
    )
    with executing(REGISTRY["planner"]), pytest.raises(ModelRefusal) as refusal:
        await adapter.complete(LLMRequest(system="s", prompt="p"))
    assert refusal.value.category == "cyber"


async def test_truncation_is_surfaced_not_swallowed() -> None:
    """A plan cut off at max_tokens looks exactly like a short plan."""
    seen: list[httpx.Request] = []
    adapter = _adapter(_message(stop_reason="max_tokens"), seen)
    with executing(REGISTRY["planner"]), pytest.raises(LLMError, match="truncated"):
        await adapter.complete(LLMRequest(system="s", prompt="p"))


async def test_no_sampling_parameters_are_sent() -> None:
    """`temperature`, `top_p`, `top_k` and `budget_tokens` all return 400 on current models."""
    seen: list[httpx.Request] = []
    adapter = _adapter(_message(), seen)
    with executing(REGISTRY["planner"]):
        await adapter.complete(LLMRequest(system="s", prompt="p"))

    body = json.loads(seen[0].content)
    assert not {"temperature", "top_p", "top_k"} & set(body)
    assert body["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in json.dumps(body)


async def test_a_schema_request_constrains_the_response_format() -> None:
    """Structured output, not assistant-turn prefill — prefill now returns 400."""
    seen: list[httpx.Request] = []
    plan = {"summary": "done", "steps": ["one"]}
    adapter = _adapter(_message(content=[{"type": "text", "text": json.dumps(plan)}]), seen)

    with executing(REGISTRY["planner"]):
        completion = await adapter.complete(
            LLMRequest(system="s", prompt="p", schema={"type": "object"}, effort="xhigh")
        )

    body = json.loads(seen[0].content)
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == "xhigh"
    assert completion.parsed == plan
    # No assistant turn — the old way of forcing JSON.
    assert all(m["role"] != "assistant" for m in body["messages"])


async def test_the_guard_runs_before_the_provider() -> None:
    """A misclassified node fails identically on every backend, not just the tested one."""
    adapter = _adapter(_message(), [])
    with executing(REGISTRY["release"]), pytest.raises(Exception, match="may not call"):
        await adapter.complete(LLMRequest(system="s", prompt="p"))


# --- configuration ------------------------------------------------------------------------


def test_a_key_in_kuwarden_yaml_is_refused() -> None:
    """That file lives in the application's repo; a key written there is in its history."""
    text = KUWARDEN_YAML.replace(
        "  provider: anthropic", "  provider: anthropic\n  api_key: sk-ant-oops"
    )
    with pytest.raises(ConfigError, match="must not appear in kuwarden.yaml"):
        parse(text)


def test_provider_is_declared_never_defaulted() -> None:
    text = KUWARDEN_YAML.replace("  provider: anthropic\n", "")
    with pytest.raises(ConfigError, match="llm.provider must be declared"):
        parse(text)


def test_verifiers_share_one_setting_unless_named() -> None:
    config = parse(KUWARDEN_YAML)
    assert config.llm is not None
    assert config.llm.for_node("verifier.security").effort == "high"
    assert config.llm.for_node("coder").effort == "xhigh"


@pytest.mark.parametrize(
    "provider", [Provider.AZURE_OPENAI, Provider.OPENAI_COMPATIBLE, Provider.BEDROCK]
)
def test_an_undeclared_backend_fails_at_registration(provider: Provider) -> None:
    """Not three nodes into a run with a ticket already in flight."""
    config = parse(KUWARDEN_YAML.replace("provider: anthropic", f"provider: {provider.value}"))
    assert config.llm is not None
    with pytest.raises(ConfigError, match="not implemented yet"):
        llm_adapter(config.llm, "planner", BROKER)


async def test_a_rejected_key_is_not_retried() -> None:
    """A bad key is still bad three attempts later.

    `LLMAuthError` is listed as non-retryable in the flow, so this type is what makes the
    difference between failing in seconds and putting three refused requests on an account.
    """
    from engine.adapters.llm import LLMAuthError

    def unauthorised(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})

    adapter = AnthropicLLM(
        model="claude-opus-5",
        broker=EnvCredentialBroker({"KUWARDEN_LLM_API_KEY": "sk-wrong"}),
        realm="anthropic",
        transport=httpx.MockTransport(unauthorised),
    )
    with executing(REGISTRY["planner"]), pytest.raises(LLMAuthError) as caught:
        await adapter.complete(LLMRequest(system="s", prompt="p", max_tokens=16))

    assert "llm.api_key" in str(caught.value), "the message says which credential to replace"
    assert "sk-wrong" not in str(caught.value), "and never echoes the key"


async def test_a_truncated_response_says_it_was_truncated() -> None:
    """The check must run *before* the JSON parse.

    A response cut off at `max_tokens` is incomplete JSON. Parsed first, it fails with "the
    response was not JSON" — true, useless, and it sends the reader to debug the model's
    formatting rather than raise a limit. That is exactly what happened on a real run, and the
    check for the real cause was sitting below the parse where it could never fire.
    """

    def truncated(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_cut",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                # Valid JSON that simply stops — what truncation actually looks like.
                "content": [{"type": "text", "text": '{"reasoning": "x", "edits": [{"path'}],
                "stop_reason": "max_tokens",
                "usage": {"input_tokens": 100, "output_tokens": 8192},
            },
        )

    adapter = AnthropicLLM(
        model="claude-opus-5",
        broker=EnvCredentialBroker({"KUWARDEN_LLM_API_KEY": "sk-x"}),
        realm="anthropic",
        transport=httpx.MockTransport(truncated),
    )
    with executing(REGISTRY["coder"]), pytest.raises(LLMError) as caught:
        await adapter.complete(
            LLMRequest(system="s", prompt="p", max_tokens=8192, schema={"type": "object"})
        )

    assert "max_tokens" in str(caught.value)
    assert "8192 output tokens" in str(caught.value)
    assert "not JSON" not in str(caught.value), "the symptom must not mask the cause"


async def test_a_rejected_request_is_not_retried_and_says_where_the_reason_is() -> None:
    """A 400 says the request as sent can never be served, so retrying it is pure delay.

    The case that made this matter is not a malformed request at all: Anthropic returns an
    exhausted credit balance as `invalid_request_error` — a 400, not a 402 — so the most
    likely cause of a rejected request in practice was being retried as though a payment
    problem were transient, three times, before failing.

    The provider's own sentence stays out of the exception, because it can quote the request
    and the request carries ticket text. It goes to the worker log instead, and the exception
    says where. Discarding it entirely was the earlier behaviour, and it made every 400 read
    identically whether the account was unpaid, the schema wrong, or the prompt too long.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low to access the Anthropic API.",
                },
            },
        )

    adapter = AnthropicLLM("claude-sonnet-5", BROKER, transport=httpx.MockTransport(handler))
    with executing(REGISTRY["planner"]), pytest.raises(LLMRequestRejected) as rejected:
        await adapter.complete(LLMRequest(system="s", prompt="p"))

    message = str(rejected.value)
    assert "worker log" in message, "an operator has to be told where the reason is"
    assert "cannot be billed" in message
    # The classification is decorative unless the flow acts on it.
    assert "LLMRequestRejected" in NON_RETRYABLE


def test_the_flow_declares_a_rejected_request_non_retryable() -> None:
    """Read from the flow source, so the two cannot drift apart silently."""
    source = (REPO_ROOT / "engine" / "flows" / "delivery.py").read_text(encoding="utf-8")
    assert '"LLMRequestRejected"' in source


async def test_a_cacheable_prefix_is_sent_as_a_marked_first_block() -> None:
    """Caching is a *prefix* mechanism, so what is marked must come first and be stable.

    The provider reuses everything up to the marker, and only if it is byte-identical between
    calls. Concatenating the repository with the ticket and the previous attempt's failure —
    which is what the prompt did before this — means the reusable part is never at the front
    and nothing is ever a hit.
    """
    seen: list[httpx.Request] = []
    adapter = _adapter(_message(), seen)

    with executing(REGISTRY["planner"]):
        await adapter.complete(
            LLMRequest(system="s", prompt="the variable tail", cacheable_prefix="the stable part")
        )

    content = json.loads(seen[0].content)["messages"][0]["content"]
    assert isinstance(content, list), "two blocks, not one concatenated string"
    assert content[0]["text"] == "the stable part"
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[1]["text"] == "the variable tail"
    assert "cache_control" not in content[1], "only the prefix is reusable"


async def test_a_prefix_with_no_tail_yet_does_not_send_an_empty_block() -> None:
    """The Coder's first attempt, which had no previous failure to report.

    Its tail is empty, and the API rejects a text block containing no text. Every run would
    have died at the Coder with a 400 — which the flow classifies non-retryable, so the run
    would not have recovered — and it would have died before writing the cache entry the
    later attempts were the whole reason for.
    """
    seen: list[httpx.Request] = []
    adapter = _adapter(_message(), seen)

    with executing(REGISTRY["planner"]):
        await adapter.complete(
            LLMRequest(system="s", prompt="", cacheable_prefix="the stable part")
        )

    content = json.loads(seen[0].content)["messages"][0]["content"]
    assert isinstance(content, list)
    assert len(content) == 1, "an empty tail is no block at all, not a block containing ''"
    assert content[0]["cache_control"] == {"type": "ephemeral"}


async def test_a_short_prefix_is_still_marked_rather_than_second_guessed() -> None:
    """The adapter does not apply a size threshold of its own, and must not grow one back.

    A `MIN_CACHEABLE_BYTES` constant lived here briefly. Below the provider's minimum a marker
    is silently ignored and *not charged*, so withholding it saves nothing — and the minimum
    is model-dependent and not monotonic (512 tokens on Opus 5, 1024 on Sonnet 5, 4096 on
    Haiku 4.5), so any constant declines to mark blocks that would have cached on the models
    with a lower threshold. The provider applies its own threshold for free.
    """
    seen: list[httpx.Request] = []
    adapter = _adapter(_message(), seen)

    with executing(REGISTRY["planner"]):
        await adapter.complete(
            LLMRequest(system="s", prompt="tail", cacheable_prefix="tiny")
        )

    content = json.loads(seen[0].content)["messages"][0]["content"]
    assert isinstance(content, list), "marked regardless of size; the provider decides"
    assert content[0]["cache_control"] == {"type": "ephemeral"}


async def test_without_a_prefix_the_request_shape_is_unchanged() -> None:
    """A prompt too short to cache must not pay for a cache entry.

    A write costs more than an ordinary input token, so marking a small prompt is a surcharge
    with no offsetting read. Callers that pass nothing get exactly the shape they had before
    caching existed.
    """
    seen: list[httpx.Request] = []
    adapter = _adapter(_message(), seen)

    with executing(REGISTRY["planner"]):
        await adapter.complete(LLMRequest(system="s", prompt="short"))

    assert json.loads(seen[0].content)["messages"][0]["content"] == "short"


async def test_cache_accounting_is_carried_back_from_the_provider() -> None:
    """A cache that never hits looks exactly like one that works, except on the invoice.

    Writes cost more than ordinary input tokens and reads cost a fraction, so "every call
    wrote, none read" is *worse* than not caching. The numbers are recorded per node so the
    two cases can be told apart from the run's own record rather than from a bill.
    """
    seen: list[httpx.Request] = []
    adapter = _adapter(
        _message(usage={
            "input_tokens": 40,
            "output_tokens": 20,
            "cache_creation_input_tokens": 9_000,
            "cache_read_input_tokens": 0,
        }),
        seen,
    )

    with executing(REGISTRY["planner"]):
        completion = await adapter.complete(LLMRequest(system="s", prompt="p"))

    assert completion.cache_write_tokens == 9_000
    assert completion.cache_read_tokens == 0


async def test_a_response_without_cache_fields_is_not_an_error() -> None:
    """Absent on any deployment that does not cache, and on every fixture predating it."""
    seen: list[httpx.Request] = []
    adapter = _adapter(_message(), seen)

    with executing(REGISTRY["planner"]):
        completion = await adapter.complete(LLMRequest(system="s", prompt="p"))

    assert completion.cache_write_tokens == 0
    assert completion.cache_read_tokens == 0
