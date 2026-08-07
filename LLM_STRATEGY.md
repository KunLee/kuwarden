# KuWarden — LLM Strategy & Data Sovereignty

> This document defines how KuWarden handles LLM backend selection, enterprise data sovereignty, and the pluggable model adapter architecture.

---

## 1. Design Principle: Bring Your Own LLM

KuWarden treats the LLM as a **swappable backend**, not a product dependency.

The flow engine and all agents communicate with models through a single internal `LLMProvider` interface. Operators configure which adapter and model to use — per agent, per environment — in the application's `kuwarden.yaml`. No model is hard-coded anywhere in the platform.

This means:
- Enterprises can use whatever model best fits their compliance posture.
- Different agents within the same run can use different models (e.g. a powerful cloud model for planning, a self-hosted model for code generation).
- The platform upgrades to better models without changing agent logic.
- Vendors can be changed or removed without re-architecting.

---

## 2. Supported LLM Backends

### Tier 1 — Fully Self-Hosted (Air-Gapped)

Best for: highly regulated industries (banking, defence, healthcare) where **zero data may leave the enterprise network**.

| Runtime | Recommended Models | GPU Requirement | Licence |
|---|---|---|---|
| **vLLM on Kubernetes** | Qwen2.5-Coder-32B-Instruct | 2× A100 40GB or 1× H100 | Apache 2.0 |
| **vLLM on Kubernetes** | DeepSeek-Coder-V2-Instruct | 2× A100 40GB | MIT |
| **vLLM on Kubernetes** | Llama-3.3-70B-Instruct | 4× A100 40GB | Llama 3 Community |
| **Ollama** (dev/small teams) | Qwen2.5-Coder-32B (Q4_K_M) | 1× RTX 4090 / A10G | Apache 2.0 |
| **AWS SageMaker** | Any Hugging Face model | Managed (ml.g5.48xlarge) | Varies |

vLLM and Ollama both expose an **OpenAI-compatible REST API** (`/v1/chat/completions`). KuWarden's `OpenAICompatibleAdapter` connects to either with zero additional configuration.

**Why Qwen2.5-Coder-32B as the default coding model?**
- Best-in-class score on HumanEval, SWE-bench, and BigCodeBench among open-weight models.
- Apache 2.0 licence — unrestricted commercial use.
- 128K context window — can ingest large files without chunking.
- Excellent instruction-following for structured JSON output (change plans, review reports).

---

### Tier 2 — AWS Bedrock (Private VPC)

Best for: enterprises already on AWS who want **strong data sovereignty without managing GPU infrastructure**.

- Models available: Claude 3.5 Sonnet, Claude 3 Haiku, Llama 3.1/3.3, Mistral Large, Amazon Nova Pro
- **AWS contractually guarantees** your data is never used for model training.
- Access via **VPC Interface Endpoint (PrivateLink)** — traffic never traverses the public internet.
- Compliance certifications: GDPR, HIPAA, SOC 2 Type II, PCI-DSS, ISO 27001.
- KuWarden uses the **Bedrock Converse API** via boto3 — unified interface across all Bedrock models.

**VPC PrivateLink setup:**
```
KuWarden Engine Pod
      │
      │ (private network — no internet gateway)
      ▼
VPC Interface Endpoint
      │
      ▼
AWS Bedrock (within AWS infrastructure, within your AWS account)
```

**Important caveat:** Model weights are hosted by AWS/Anthropic. You control your data — but not the model binary itself. For full model sovereignty, use Tier 1 (SageMaker with your own model snapshot or fully self-hosted vLLM).

---

### Tier 3 — Azure OpenAI Service (Private Endpoint)

Best for: enterprises already on Azure with existing Microsoft agreements.

- Models: GPT-4.1, GPT-4o, GPT-4o-mini
- Accessed via **Azure Private Endpoint** (within your Azure Virtual Network).
- Your data is not used to train Microsoft's models (Azure OpenAI data processing addendum).
- Exposes OpenAI-compatible API — KuWarden's `OpenAICompatibleAdapter` connects directly.
- Compliance: GDPR, HIPAA, SOC 2, ISO 27001.

---

### Tier 4 — IBM Watsonx.ai

Best for: IBM-aligned enterprises using IBM Cloud or on-premise IBM infrastructure.

- Models: Granite Code 34B, Llama 3.3 70B, Mistral Large on Watsonx
- Available as **IBM Cloud dedicated** (single-tenant, data stays in IBM's isolated environment) or **on-premise** via IBM Cloud Pak for Data.
- IBM provides enterprise data processing agreements and indemnification.
- KuWarden connects via Watsonx.ai REST API with a dedicated adapter.

---

## 3. Recommended Default Configuration

For most enterprises starting with KuWarden, the recommended starting configuration balances quality, cost, and sovereignty:

```yaml
# kuwarden.yaml — recommended default LLM configuration

llm:
  # Planning and reasoning: Claude 3.5 via Bedrock VPC
  # Best reasoning capability. Data stays in your AWS account.
  planner:
    adapter: bedrock
    model: anthropic.claude-3-5-sonnet-20241022-v2:0
    region: ap-southeast-2
    vpc_endpoint: true

  # Code generation: self-hosted Qwen2.5-Coder
  # Best OSS coding model. Air-gapped. No data leaves your cluster.
  coder:
    adapter: openai_compatible
    base_url: http://vllm.kuwarden-system.svc.cluster.local:8000/v1
    model: Qwen2.5-Coder-32B-Instruct
    max_tokens: 8192

  # Code review: same self-hosted model for consistency
  reviewer:
    adapter: openai_compatible
    base_url: http://vllm.kuwarden-system.svc.cluster.local:8000/v1
    model: Qwen2.5-Coder-32B-Instruct

  # Test generation: self-hosted
  tester:
    adapter: openai_compatible
    base_url: http://vllm.kuwarden-system.svc.cluster.local:8000/v1
    model: Qwen2.5-Coder-32B-Instruct

  # Reporting: smaller, faster model for simple text generation
  reporter:
    adapter: bedrock
    model: anthropic.claude-3-haiku-20240307-v1:0
    region: ap-southeast-2
    vpc_endpoint: true
```

---

## 4. Data Sovereignty Matrix

| Data Type | Self-hosted (Tier 1) | Bedrock VPC (Tier 2) | Azure OpenAI (Tier 3) | Public SaaS (not used) |
|---|---|---|---|---|
| Source code | ✅ Stays on-prem | ✅ Stays in your AWS account | ✅ Stays in your Azure tenant | ❌ Leaves network |
| Ticket content | ✅ Stays on-prem | ✅ Stays in your AWS account | ✅ Stays in your Azure tenant | ❌ Leaves network |
| Credentials / secrets | ✅ Never sent to LLM | ✅ Never sent to LLM | ✅ Never sent to LLM | ❌ Risk of leakage |
| Model weights | ✅ You own them | ⚠️ AWS/Anthropic hosted | ⚠️ Microsoft hosted | ❌ Vendor controlled |
| Flow run logs | ✅ Your DB | ✅ Your DB | ✅ Your DB | ❌ Vendor servers |
| Audit trail | ✅ Your infrastructure | ✅ Your infrastructure | ✅ Your infrastructure | ❌ Vendor servers |
| Used for model training | ✅ No (you own it) | ✅ No (contractual) | ✅ No (contractual) | ❌ Possible |

---

## 5. LLM Adapter Interface

All adapters implement the same interface. Adding a new LLM backend requires only implementing this interface — no changes to agent code.

```python
class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        response_format: ResponseFormat | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        ...
```

**Implemented adapters:**

| Adapter Class | Connects To |
|---|---|
| `OpenAICompatibleAdapter` | vLLM, Ollama, LM Studio, Azure OpenAI, OpenAI API |
| `BedrockAdapter` | AWS Bedrock (Converse API) |
| `WatsonxAdapter` | IBM Watsonx.ai REST API |

---

## 6. Security Rules for LLM Usage

These rules are enforced in the platform and cannot be overridden by application hook config:

1. **No secrets in prompts.** The Tool Bus strips any value matching a secret pattern (API keys, passwords, tokens) before it reaches the LLM adapter.
2. **No PII in prompts by default.** A configurable PII scrubber runs on all prompt content before dispatch.
3. **Prompt/response logging is masked.** The monitoring UI logs LLM interactions with sensitive patterns redacted.
4. **Private endpoints only.** The platform will refuse to send requests to a public LLM API endpoint (`api.openai.com`, `api.anthropic.com`) unless the operator explicitly overrides this with `allow_public_endpoints: true` in the platform config. This override is logged and alerted.
5. **Model version pinning.** Production flows must pin an exact model version — not `latest`. This prevents unexpected behaviour changes from model updates.

---

*See [ARCHITECTURE.md](./ARCHITECTURE.md) for how the LLM Adapter fits into the overall system.*  
*See [ROADMAP.md](./ROADMAP.md) for the phased plan to build and validate the adapter layer.*
