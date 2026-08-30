"""The only door to a model.

Two things live here. The **guard** decides whether the caller is entitled to a model at
all — invariant 1 says the Flow Engine contains no LLM, and a `deterministic` node may never
call one. The **protocol** is the vendor-neutral interface every backend implements.

The guard runs before any provider code, so a misclassified node fails the same way on every
backend rather than only on the one someone happened to test.

Model identifiers do not appear here or in any strategy document. They live in
`docs/reference/models.md` with a review date, because they go stale in months.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from engine.errors import InvariantViolation, KuWardenError


class LLMError(KuWardenError):
    """A model backend failed, or declined."""


class LLMAuthError(LLMError):
    """The provider rejected the credential.

    Separated from the rest of `LLMError` because it is the one kind that a retry cannot fix.
    A bad key is still bad three attempts later, and retrying it spends the run's wall clock
    and puts three rejected requests on someone's account instead of one.
    """


class LLMOutputTruncated(LLMError):
    """The response was cut off at `max_tokens`.

    Non-retryable for the same reason as `LLMAuthError`: the same prompt under the same cap
    truncates at the same place. The fix is configuration, not another attempt — and each
    attempt is minutes of wall clock and a full charge for output nobody can use.
    """


class LLMRequestRejected(LLMError):
    """The provider refused the request itself — an HTTP 400.

    Non-retryable, because a 400 says the request as sent can never be served. Retrying
    an identical request three times cannot change a malformed schema, an unsupported
    parameter, a prompt past the context window, or an account that cannot be billed.

    That last one is why this class exists rather than being folded into `LLMError`.
    Anthropic returns an exhausted credit balance as `invalid_request_error` — a 400,
    not a 402 — so the single most likely cause of a rejected request in practice was
    being retried as though it were transient, and the run spent minutes discovering
    what the first response already said.
    """


class ModelRefusal(LLMError):
    """The provider's safety classifiers declined the request.

    Not an error in the transport sense — the call succeeded and returned no usable content.
    It is raised rather than returned because a node that silently proceeds on an empty
    completion produces a change nobody authored.

    Expected on hostile ticket content, which is the input this system is built to survive.
    """

    def __init__(self, category: str | None, detail: str = "") -> None:
        super().__init__(f"model declined the request (category={category or 'unspecified'})")
        self.category = category
        self.detail = detail


class Provider(StrEnum):
    ANTHROPIC = "anthropic"
    AZURE_OPENAI = "azure_openai"
    OPENAI_COMPATIBLE = "openai_compatible"
    BEDROCK = "bedrock"


@dataclass(frozen=True)
class Completion:
    text: str
    #: Parsed object when a schema was supplied. `None` when the model returned prose.
    parsed: dict[str, Any] | None
    input_tokens: int
    output_tokens: int
    #: What actually served the request, which is not always what was asked for.
    model: str
    stop_reason: str
    #: Prompt-cache accounting, straight from the provider. Recorded rather than inferred
    #: because caching is the one optimisation whose failure is invisible: a cache that never
    #: hits looks exactly like a cache that is working, except on the invoice. A write costs
    #: more than an ordinary input token and a read costs a fraction of one, so a run where
    #: every call wrote and none read is *worse* than no caching at all — and that is the
    #: expected outcome for calls issued in parallel, which the verifier fan-out does.
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass(frozen=True)
class LLMRequest:
    """Everything a node asks of a model.

    Deliberately narrow. Sampling parameters are absent: they are rejected outright by
    current Anthropic models, and a knob that works on one backend and 400s on another is
    worse than no knob. Determinism is not something a temperature setting was ever going to
    provide — it comes from the control plane.
    """

    system: str
    prompt: str
    #: A stable prefix placed before `prompt` and marked cacheable.
    #:
    #: Split out rather than concatenated because the provider caches a *prefix*: everything
    #: up to the marker is reusable only if it is byte-identical and comes first. The
    #: repository context qualifies — it is the largest part of the prompt and identical
    #: across the Coder's four attempts and across all four verifiers. The ticket, the plan
    #: and the previous attempt's failure do not, and putting them first would mean nothing
    #: was ever reusable.
    #:
    #: `None` sends a single unmarked block, which is what every caller did before caching
    #: existed and remains correct for short prompts — a cache write below the provider's
    #: minimum is refused, and one that is never read is a surcharge.
    cacheable_prefix: str | None = None
    max_tokens: int = 8192
    #: Vendor-neutral depth hint. Providers map it onto whatever they actually support.
    effort: str = "high"
    #: JSON Schema. When set, the provider constrains output to it and `parsed` is populated.
    schema: dict[str, Any] | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class LLMAdapter(Protocol):
    """One interface, N implementations — same contract as the SCM and ticket adapters."""

    @property
    def provider(self) -> Provider: ...

    async def ping(self) -> str:
        """Prove the credential is accepted, without generating anything.

        Cheap enough to run from a button and free of model output, so an operator can find
        out the key is wrong in a second rather than three nodes into a run. The SCM and
        ticket credentials already have this; leaving the model key uncovered meant two green
        checks and a failure on the third.
        """
        ...

    async def complete(self, request: LLMRequest) -> Completion: ...


def assert_may_call_llm() -> None:
    """Refuse to serve a model call from anywhere that must not make one.

    Two failures are caught. A `deterministic` node calling a model violates its declared
    class. Code with no node context at all is workflow or activity plumbing — the Flow
    Engine — and invariant 1 says the Flow Engine contains no LLM.
    """
    # Imported here, not at module scope: importing `engine.nodes.base` executes the
    # `engine.nodes` package, which imports the nodes, which import this module.
    from engine.nodes.base import current_node

    spec = current_node()
    if spec is None:
        raise InvariantViolation(
            "LLM call attempted outside any node. The Flow Engine contains no LLM (invariant 1)."
        )
    if not spec.may_call_llm:
        raise InvariantViolation(
            f"node {spec.id!r} is classified {spec.node_class.value!r} and may not call a model"
        )


__all__ = [
    "Completion",
    "LLMAdapter",
    "LLMError",
    "LLMRequest",
    "ModelRefusal",
    "Provider",
    "assert_may_call_llm",
]
