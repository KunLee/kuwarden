# KuFlow — Architecture

> This document describes the system architecture, component design, data flows, and integration model for KuFlow.

---

## 1. High-Level Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        TRIGGER LAYER                            │
│   Jira Webhook  │  Azure DevOps  │  GitHub Issues  │  Manual    │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      KUFLOW ENGINE                              │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Flow Router  │  │ Flow State   │  │  Approval Gate       │  │
│  │ (matches app │  │ (persisted,  │  │  (human-in-the-loop) │  │
│  │  hook config)│  │  resumable)  │  │                      │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    AGENT PIPELINE                         │   │
│  │                                                           │   │
│  │  [Planner] → [Coder] → [Reviewer] → [Tester] → [Deployer]│   │
│  │                                                           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ LLM Adapter  │  │  Tool Bus    │  │  Event Stream        │  │
│  │ (pluggable)  │  │ (MCP-based)  │  │  (WebSocket / SSE)   │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────────────┐
│   SCM ADAPTER    │ │ CI/CD ADAPTER│ │   DEPLOY ADAPTER     │
│ GitHub / Azure   │ │ GH Actions / │ │  K8s / ArgoCD /      │
│ Repos / GitLab   │ │ Jenkins /    │ │  Helm / Terraform    │
│ / Bitbucket      │ │ Azure Pipes  │ │  / AWS ECS           │
└──────────────────┘ └──────────────┘ └──────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    MONITORING UI                                 │
│   Agent run list  │  Live step log  │  Approval queue  │ Audit  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Components

### 2.1 Flow Engine (Backend — Python / FastAPI)

The heart of KuFlow. Responsibilities:

- Receives incoming trigger events (webhooks) and routes them to the correct registered application flow.
- Instantiates an **agent pipeline run** for each trigger.
- Manages **flow state** — persists step progress so runs are resumable after failure or restart.
- Enforces **approval gates** — pauses the pipeline and notifies humans when a gate is reached.
- Emits a real-time **event stream** to the monitoring UI via WebSocket / Server-Sent Events.
- Exposes a **REST API** for the monitoring UI, manual triggers, and external integrations.

**Tech stack:**
- Runtime: Python 3.12+
- Framework: FastAPI
- State store: PostgreSQL (flow runs, steps, logs, approvals)
- Queue: Redis (agent task dispatch, event fan-out)
- Container: Docker / Kubernetes

---

### 2.2 Agent Pipeline

Each KuFlow flow run executes a configurable pipeline of specialised agents. Each agent is a stateless worker that receives a task, calls the LLM via the LLM Adapter, uses tools via the Tool Bus, and emits structured output.

#### Built-in Agents

| Agent | Role | Key Tools Used |
|---|---|---|
| **Planner** | Reads ticket, analyses codebase, produces a structured change plan | Ticket API, SCM read, LLM |
| **Coder** | Implements the plan — creates/modifies files | SCM write, file tools, LLM |
| **Reviewer** | Reviews generated diff for correctness, style, security | SCM read, SAST runner, LLM |
| **Tester** | Generates or runs tests, validates coverage | CI/CD trigger, test runner, LLM |
| **Deployer** | Raises PR, triggers CI/CD, deploys to target environment | SCM PR API, CI/CD API, Deploy API |
| **Reporter** | Posts results back to ticket, updates status | Ticket API, LLM |

Agents are chained in sequence by default. Each agent's output becomes the next agent's input context. The pipeline is **configurable per application** via `kuflow.yaml`.

---

### 2.3 Tool Bus (MCP-based)

KuFlow uses the **Model Context Protocol (MCP)** as the standard interface for all agent tools. This means:

- Each integration (Jira, GitHub, Jenkins, K8s) is an MCP tool server.
- Agents invoke tools by name via the Tool Bus — they do not call external APIs directly.
- New tools can be added without changing agent code.
- Tool calls are logged for auditability.

**Built-in tool servers:**

| Tool Server | Provides |
|---|---|
| `kuflow-scm` | git clone, file read/write, PR create, branch ops |
| `kuflow-jira` | ticket read, comment, status update, label set |
| `kuflow-ado` | Azure DevOps work item read/update, repo ops |
| `kuflow-cicd` | trigger pipeline, poll status, fetch logs |
| `kuflow-deploy` | kubectl apply, helm upgrade, ArgoCD sync trigger |
| `kuflow-sast` | run Semgrep / Bandit / ESLint security scan |
| `kuflow-test` | run test suite, parse results, compute coverage |

---

### 2.4 LLM Adapter

A single internal interface — `LLMProvider` — abstracts all LLM backends. The flow engine and agents never call a model API directly; they call the adapter.

```
LLMProvider
├── OpenAICompatibleAdapter   (vLLM, Ollama, LM Studio, Azure OpenAI)
├── BedrockAdapter            (AWS Bedrock via boto3 Converse API)
├── WatsonxAdapter            (IBM Watsonx.ai)
└── (extensible — add new adapters without changing agent code)
```

**Configuration example (`kuflow.yaml`):**
```yaml
llm:
  planner:
    adapter: bedrock
    model: anthropic.claude-3-5-sonnet-20241022-v2:0
    region: ap-southeast-2
  coder:
    adapter: openai_compatible
    base_url: http://vllm.internal:8000/v1
    model: Qwen2.5-Coder-32B-Instruct
  reviewer:
    adapter: openai_compatible
    base_url: http://vllm.internal:8000/v1
    model: Qwen2.5-Coder-32B-Instruct
```

Different agents can use different models, optimising for cost, quality, and sovereignty requirements.

---

### 2.5 Application Hook (`kuflow.yaml`)

Every application that uses KuFlow registers itself with a `kuflow.yaml` file at the repo root. This is the **single configuration file** that tells KuFlow everything it needs to know about the application.

```yaml
# kuflow.yaml — example for a Java Spring Boot microservice

app:
  name: payments-service
  repo: https://github.com/acme/payments-service
  language: java
  framework: spring-boot

triggers:
  - type: jira
    project_key: PAY
    label: kuflow-auto          # tickets with this label trigger the flow
    min_story_points: 1
    max_story_points: 5         # only auto-handle small tickets

flow:
  pipeline:
    - planner
    - coder
    - reviewer
    - tester
    - deployer
    - reporter

  approval_gates:
    - after: reviewer           # human must approve before tests run
      notify: slack:#pay-team
    - after: tester             # human must approve before deploy
      notify: slack:#pay-team

  deploy:
    test:
      target: kubernetes
      namespace: payments-test
      manifest_path: k8s/test/
    uat:
      target: kubernetes
      namespace: payments-uat
      manifest_path: k8s/uat/
      requires_approval: true

llm:
  planner:
    adapter: bedrock
    model: anthropic.claude-3-5-sonnet-20241022-v2:0
  coder:
    adapter: openai_compatible
    base_url: http://vllm.internal:8000/v1
    model: Qwen2.5-Coder-32B-Instruct

quality:
  sast: semgrep
  test_coverage_threshold: 80
  branch_protection: true
```

---

### 2.6 Monitoring UI (Frontend — React)

A real-time web dashboard that gives full visibility into every KuFlow agent run.

**Key views:**

| View | Content |
|---|---|
| **Run list** | All active and historical flow runs, status, duration, application |
| **Run detail** | Step-by-step pipeline progress, live log streaming, diff viewer |
| **Approval queue** | Pending human approvals with diff preview and one-click approve/reject |
| **Agent log** | Full LLM prompt/response log per step (with sensitive data masked) |
| **Audit trail** | Immutable record of every action taken, who approved, when |
| **App registry** | All registered applications, their hook config, and run history |

**Tech stack:**
- Framework: React 18 + TypeScript
- UI: Tailwind CSS
- Real-time: WebSocket (live log streaming from engine)
- State: React Query + Zustand
- Served by: Nginx (static build, served from same cluster as engine)

---

## 3. Data Flow — End to End

```
1. TRIGGER
   Jira ticket labelled "kuflow-auto"
   → Jira webhook fires POST /api/trigger
   → Flow Router matches ticket to payments-service hook config
   → New FlowRun created in PostgreSQL (state: RUNNING)
   → Run ID returned immediately (async execution begins)

2. PLANNER AGENT
   → Reads Jira ticket via kuflow-jira tool
   → Clones repo HEAD via kuflow-scm tool
   → Calls LLM (Bedrock Claude): "Analyse this ticket and this codebase.
     Produce a structured change plan with affected files and approach."
   → Outputs: Changeplan JSON (files to touch, approach, test strategy)
   → Step logged to DB + event emitted to UI

3. CODER AGENT
   → Receives Changeplan
   → Creates feature branch via kuflow-scm
   → For each file in plan: reads current content, calls LLM (vLLM Qwen2.5-Coder),
     writes updated content
   → Commits changes with signed commit (bot service account)
   → Step logged + event emitted

4. APPROVAL GATE 1 (after reviewer)
   → Reviewer agent runs SAST via kuflow-sast
   → Reviewer calls LLM to review diff for correctness and security
   → Review summary posted to Monitoring UI approval queue
   → Notification sent to slack:#pay-team
   → Pipeline PAUSED — waits for human approval

5. HUMAN APPROVES (via Monitoring UI or Slack /approve command)
   → FlowRun state updated: gate passed
   → Pipeline resumes

6. TESTER AGENT
   → Calls LLM to generate/augment unit tests for changed code
   → Triggers CI pipeline via kuflow-cicd
   → Polls CI for result
   → If coverage < 80% or tests fail: flow aborts, reporter posts failure to ticket

7. APPROVAL GATE 2 (after tester)
   → Test results + coverage shown in UI
   → Human approves deploy to test environment

8. DEPLOYER AGENT
   → Raises PR via kuflow-scm (with full description auto-generated)
   → Triggers deploy to payments-test namespace via kuflow-deploy
   → Polls deployment health check
   → On success: emits DEPLOY_SUCCESS event

9. REPORTER AGENT
   → Posts comment to Jira ticket: PR link, test results, deploy URL, summary
   → Transitions ticket status to "In Review"
   → FlowRun state: COMPLETED

10. MONITORING UI
    → Full run visible: all steps, logs, diffs, approvals, timings
    → Audit record persisted permanently
```

---

## 4. Deployment Architecture

KuFlow is deployed as a set of Kubernetes workloads. All components run inside the enterprise's own cluster.

```
┌────────────────────────────────── Kubernetes Cluster ────────────────────────────────┐
│                                                                                       │
│  ┌─────────────────┐   ┌─────────────────┐   ┌──────────────────────────────────┐   │
│  │  kuflow-engine  │   │  kuflow-ui      │   │  kuflow-worker (agent runners)   │   │
│  │  (FastAPI)      │   │  (React/Nginx)  │   │  (auto-scaled, ephemeral pods)   │   │
│  │  2 replicas     │   │  2 replicas     │   │  0-N replicas (HPA)              │   │
│  └────────┬────────┘   └─────────────────┘   └──────────────────────────────────┘   │
│           │                                                                           │
│  ┌────────▼────────┐   ┌─────────────────┐   ┌──────────────────────────────────┐   │
│  │  PostgreSQL     │   │  Redis          │   │  vLLM (optional self-hosted LLM) │   │
│  │  (flow state)   │   │  (task queue)   │   │  GPU node pool                   │   │
│  └─────────────────┘   └─────────────────┘   └──────────────────────────────────┘   │
│                                                                                       │
└───────────────────────────────────────────────────────────────────────────────────────┘
         │                         │                          │
         ▼                         ▼                          ▼
   Jira / Azure DevOps        GitHub / GitLab /          AWS Bedrock
   (webhook in)               Azure Repos                (via VPC endpoint)
```

**Helm chart provided** — single `helm install kuflow ./charts/kuflow` to deploy everything.

---

## 5. Security Architecture

| Concern | Approach |
|---|---|
| **Source code in LLM prompts** | Code snippets sent to LLM only over private endpoints (VPC / self-hosted). Never to public APIs. |
| **Secrets management** | All secrets in Kubernetes Secrets / HashiCorp Vault. Never passed to LLM prompts. |
| **SCM authentication** | Short-lived tokens via GitHub App / Azure DevOps service connection. Rotated per run. |
| **Commit signing** | All agent commits signed with a KuFlow bot GPG key. |
| **Least privilege** | Each tool server uses its own service account with minimum required permissions. |
| **Audit log integrity** | Flow run logs written append-only to PostgreSQL. Cannot be modified after write. |
| **UI authentication** | OIDC / OAuth 2.0 via enterprise IdP (Azure AD, Okta, Keycloak). |
| **Network policy** | Kubernetes NetworkPolicy — engine pods cannot reach the internet directly. |

---

*See [LLM_STRATEGY.md](./LLM_STRATEGY.md) for detailed LLM backend options.*  
*See [ROADMAP.md](./ROADMAP.md) for phased delivery plan.*
