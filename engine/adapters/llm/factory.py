"""Configuration to LLM adapter.

An unimplemented provider fails here, at registration, with a message naming what is missing
— not at the first run, three nodes deep, with a ticket already in flight.
"""

from __future__ import annotations

import httpx

from engine.adapters.credentials import CredentialBroker
from engine.adapters.llm import LLMAdapter, Provider
from engine.adapters.llm.anthropic_api import AnthropicLLM
from engine.config import ConfigError, LLMConfig

#: Declared in kuwarden.yaml so configuration need not change when an adapter lands.
NOT_YET_IMPLEMENTED: dict[Provider, str] = {
    Provider.AZURE_OPENAI: "Azure OpenAI",
    Provider.OPENAI_COMPATIBLE: "OpenAI-compatible (vLLM / Ollama)",
    Provider.BEDROCK: "AWS Bedrock",
}


def llm_adapter(
    config: LLMConfig,
    node_id: str,
    broker: CredentialBroker,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LLMAdapter:
    node_model = config.for_node(node_id)

    if config.provider is Provider.ANTHROPIC:
        return AnthropicLLM(
            model=node_model.model,
            broker=broker,
            realm=config.provider.value,
            base_url=config.base_url,
            transport=transport,
        )

    label = NOT_YET_IMPLEMENTED.get(config.provider)
    raise ConfigError(
        f"llm.provider {config.provider.value!r} ({label}) is declared but not implemented yet; "
        "anthropic is the implemented backend"
    )
