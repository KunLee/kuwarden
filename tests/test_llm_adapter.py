"""The LLM adapter, and the four ways it would otherwise fail quietly.

Every test here targets something that returns a 200 or a plausible-looking object rather
than an obvious error — the failures you only find in production.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from engine.adapters.credentials import EnvCredentialBroker
from engine.adapters.llm import LLMError, LLMRequest, ModelRefusal, Provider
from engine.adapters.llm.anthropic_api import AnthropicLLM
from engine.adapters.llm.factory import llm_adapter
from engine.config import ConfigError, parse
from engine.nodes import REGISTRY
from engine.nodes.base import executing
from tests.conftest import KUWARDEN_YAML

BROKER = EnvCredentialBroker({"KUWARDEN_LLM_API_KEY": "sk-ant-fake"})


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
