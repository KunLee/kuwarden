# KuFlow — Vision

> **Automate the journey from idea to deployed change — across every enterprise application, every team, every tool.**

---

## What is KuFlow?

KuFlow is a **self-hosted, vendor-neutral AI-driven change automation platform** for the enterprise.

It sits between your ticket system (Jira, Azure DevOps, or any trigger) and your deployment environments (Test, UAT, Production), and uses AI agents to autonomously plan, implement, test, and deploy software changes — with full human oversight and a live monitoring UI.

Think of it as an **intelligent flow engine**: you register an application once, connect your tools, and from that point forward a ticket can become a deployed change without manual developer handoff at every step.

---

## The Problem We Solve

Modern enterprises face a contradiction:

- **Development teams are overwhelmed** with repetitive, low-complexity changes (config updates, dependency bumps, boilerplate features, bug fixes from well-defined tickets).
- **The toolchain is fragmented** — Jira here, Azure Repos there, Jenkins on one side, Kubernetes on another. Stitching them together for every change is manual and error-prone.
- **Existing AI coding tools are SaaS-locked** (Devin, GitHub Copilot Workspace, Atlassian Rovo Dev) — your source code and ticket data leave your network.
- **There is no reusable platform** — every team that wants automation builds their own bespoke pipeline, which does not scale across the enterprise.

KuFlow solves all four problems in one platform.

---

## Our Vision

> **Every software change in the enterprise, from ticket to deployment, should be automatable — with humans in control, not in the loop by default.**

We envision a world where:

1. An engineer writes a clear Jira ticket and assigns it to a KuFlow agent.
2. The agent reads the ticket, plans the change, generates the code, raises a PR, passes all quality gates, and deploys to the test environment — automatically.
3. The engineer reviews the result, approves, and promotes to UAT or Production with a single click.
4. Everything is auditable: every agent decision, every file changed, every test result, visible in the KuFlow monitoring UI.
5. This works for **any application** in the enterprise — not just greenfield projects, not just a specific language or framework.

---

## Core Design Principles

| Principle | What it means in practice |
|---|---|
| **Self-hosted first** | The KuFlow engine, agents, and all flow state run on your own infrastructure. Nothing leaves your network by default. |
| **Vendor-neutral** | No lock-in to a single LLM provider, ticket system, SCM platform, CI/CD tool, or cloud. |
| **Application hook model** | Register a repo once with a `kuflow.yaml`. The platform handles all subsequent runs. |
| **Human-in-the-loop by design** | Every flow has configurable approval gates. Humans approve; agents execute. |
| **Observability first** | A live monitoring dashboard is a first-class feature — not an afterthought. |
| **Security by default** | Secrets never passed to LLMs. Least-privilege service accounts. Signed commits. SAST on every generated change. |
| **Pluggable everything** | LLM backends, SCM providers, CI/CD adapters, and deploy targets are all swappable adapters. |

---

## Who is KuFlow For?

- **Enterprise engineering teams** who want to automate repetitive change delivery without adopting a SaaS product that owns their code.
- **Platform / DevOps teams** who need a single, governable automation platform across hundreds of application teams.
- **Regulated industries** (finance, healthcare, government) where source code and design data must never leave the enterprise network.
- **IBM-aligned organisations** already using Watsonx, IBM Cloud, or on-premise infrastructure.

---

## What Makes KuFlow Different

| Feature | KuFlow | Devin | GitHub Copilot Workspace | OpenHands | AWS Kiro | Cline |
|---|---|---|---|---|---|---|
| Self-hosted | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ |
| Any ticket system trigger | ✅ | Linear/GitHub only | GitHub Issues only | ❌ | ❌ | ❌ |
| Any LLM backend | ✅ | ❌ Proprietary | ❌ GPT-4o only | ✅ | ❌ Bedrock only | ✅ |
| Any CI/CD integration | ✅ | ❌ | ❌ | ❌ | Partial (headless CLI) | ❌ |
| Auto-deploy to environments | ✅ | Partial | ❌ | ❌ | ❌ | ❌ |
| Live monitoring UI | ✅ | Basic | ❌ | Basic | ❌ | ❌ |
| Application hook model | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Enterprise audit trail | ✅ | ❌ | ❌ | Partial | ❌ | ❌ |
| Air-gap / data sovereignty | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ |
| Serverless / headless agents | ✅ | ✅ | ❌ | ✅ | ✅ (CLI) | Partial (SDK) |

> See [docs/TOOLS_LANDSCAPE.md](./docs/TOOLS_LANDSCAPE.md) for a detailed breakdown of how Kiro, Cline, OpenHands, and Devin relate to KuFlow.

---

## The Bigger Picture

KuFlow is not just a tool — it is **infrastructure for the AI-native enterprise software factory**.

As AI models improve, the scope of what KuFlow can autonomously handle will grow. The platform is designed to accommodate that evolution without requiring teams to re-architect their pipelines. The flow engine, adapters, and monitoring UI will remain stable; the intelligence inside each agent will compound over time.

The ultimate goal: **reduce the time from ticket creation to production-verified deployment from days to minutes, for the majority of enterprise change types.**

---

*See [ARCHITECTURE.md](./ARCHITECTURE.md) for system design details.*  
*See [LLM_STRATEGY.md](./LLM_STRATEGY.md) for LLM backend and data sovereignty decisions.*  
*See [ROADMAP.md](./ROADMAP.md) for phased delivery plan.*  
*See [docs/TOOLS_LANDSCAPE.md](./docs/TOOLS_LANDSCAPE.md) for competitor and related tool analysis.*
