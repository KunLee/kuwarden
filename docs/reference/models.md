# Model reference

**`last_reviewed: 2026-08-08`**

Model identifiers live here and nowhere else. They do not belong in `VISION.md`,
`LLM_STRATEGY.md`, `ARCHITECTURE.md`, or in engine code — a strategy document that names a
model is a document with a shelf life of months, and nobody re-reads it for freshness.
`LLM_STRATEGY.md` named models that were roughly 20 months stale by the time anyone noticed,
which is what this file exists to prevent.

**If the date above is more than three months old, treat everything below as unverified.**

---

## Which model a node uses is configuration, not code

Selection is per-node, declared in `kuwarden.yaml`, and pinned into the audit record at run
start alongside `policy_commit`. Nothing in `engine/` defaults to a model id.

```yaml
llm:
  provider: anthropic          # anthropic | azure_openai | openai_compatible | bedrock
  planner:   { model: claude-opus-5,   effort: high }
  coder:     { model: claude-opus-5,   effort: xhigh }
  verifiers: { model: claude-opus-5,   effort: high }
```

The API key is never in this file. It is resolved by the credential broker from the
environment or the enterprise secret store — see `engine/adapters/credentials.py`.

---

## Anthropic

Verified against the Anthropic API reference on the review date above.

| Model | Id | Context | Max output |
|---|---|---|---|
| Claude Opus 5 | `claude-opus-5` | 1M | 128K |
| Claude Sonnet 5 | `claude-sonnet-5` | 1M | 128K |
| Claude Haiku 4.5 | `claude-haiku-4-5` | 200K | 64K |

Ids are complete as written — **never append a date suffix**.

### Request shape, and three things that are easy to get wrong

| | Current | What breaks |
|---|---|---|
| Thinking | `thinking={"type": "adaptive"}` | `budget_tokens` returns **400** on current models |
| Depth | `output_config={"effort": "..."}` — `low` … `max` | — |
| Sampling | omit entirely | `temperature`, `top_p`, `top_k` return **400** |
| Structured output | `output_config={"format": {"type": "json_schema", ...}}` | Assistant-turn prefill returns **400** |
| Long outputs | stream above ~16K `max_tokens` | The SDK refuses a non-streaming request it estimates will exceed the HTTP timeout |

**Effort by node.** `xhigh` for coding and agentic work; `high` for everything else
intelligence-sensitive; `low`/`medium` for routine work. Sweep it rather than inheriting a
setting from another model — the cost and latency differences are large.

**A refusal is a successful HTTP response.** Safety classifiers return `200` with
`stop_reason: "refusal"`, a `stop_details.category`, and an **empty** `content` array. Code
that reads `content[0]` unconditionally crashes on precisely the inputs KuWarden is built to
survive, since ticket text is hostile by assumption. Always branch on `stop_reason` first —
`engine/adapters/llm/anthropic_api.py` raises `ModelRefusal` rather than returning empty.

---

## Other providers — declared, not yet implemented

`kuwarden.yaml` accepts all four so that configuration does not have to change when the
adapter lands. Selecting an unimplemented one fails at registration with a clear message
rather than at the first run.

| Provider | Status | Note |
|---|---|---|
| `anthropic` | **Implemented** | The demo path |
| `azure_openai` | Not implemented | Fits a tenant already on Azure DevOps; keeps data in-tenant |
| `openai_compatible` | Not implemented | vLLM / Ollama. The air-gapped story in `VISION.md` depends on this one |
| `bedrock` | Not implemented | Adds an AWS dependency to a delivery side that is currently Azure DevOps and GitHub |

`openai_compatible` is the one that matters most to the product's positioning, and it is the
only one that needs no procurement to evaluate.

---

## Review procedure

1. Re-read the provider's model list and note ids, context windows, and output caps.
2. Re-check the request-shape table — parameters move between deprecated and rejected
   without much ceremony, and a 400 in production is how you find out.
3. Update `last_reviewed`.
4. Re-run the golden task set. A model change is a prompt change: it is invisible without
   measurement, which is what `EVALUATION.md` is owed for.
