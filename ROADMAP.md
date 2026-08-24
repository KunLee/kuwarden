# KuWarden — Roadmap

> Phased delivery plan from MVP to enterprise-grade platform.

---

## Guiding Principles for Delivery

- **Ship working software at the end of every phase.** Each phase produces a usable, deployable artifact.
- **Validate with a real application before expanding.** Phase 1 targets one real app end-to-end before building breadth.
- **Security gates never skipped.** SAST, secret scanning, and signed commits are present from Phase 1.
- **Monitoring UI ships with the engine.** Observability is not deferred to a later phase.

---

## Phase 0 — Foundation (Weeks 1–3)

**Goal:** Project scaffolding, core infrastructure, developer environment.

### Deliverables

- [ ] Monorepo structure: `engine/`, `ui/`, `adapters/`, `charts/`, `docs/`
- [ ] `engine/` — FastAPI skeleton with health check, structured logging, config loading
- [ ] `ui/` — React + TypeScript + Tailwind scaffold, authenticated shell (OIDC login)
- [ ] PostgreSQL schema — `flow_runs`, `flow_steps`, `flow_logs`, `approvals`, `app_registry`
- [ ] Redis setup — task queue and event pub/sub
- [ ] Docker Compose dev environment — all services start with one command
- [ ] Helm chart skeleton — deploys all services to Kubernetes
- [x] GitHub Actions CI — lint, typecheck, unit tests on every PR
- [ ] Secret scanning and SAST in CI (Semgrep, Gitleaks) — **half done.** Gitleaks runs over
      full history on every PR. Semgrep is not wired up, so SAST remains **none** here and in
      invariant 3
- [ ] `kuwarden.yaml` schema definition and validator

**Exit criteria:** `docker compose up` starts a working (empty) KuWarden instance. UI shows login screen. Engine health check passes.

---

## Phase 1 — MVP: Jira → Code → PR (Weeks 4–10)

**Goal:** End-to-end working flow for one real application. Ticket in, PR out.

### Deliverables

- [ ] **Trigger layer:** Jira webhook receiver — parses ticket, creates FlowRun
- [ ] **LLM Adapter:** `OpenAICompatibleAdapter` (connects to vLLM or Ollama)
- [ ] **LLM Adapter:** `BedrockAdapter` (AWS Bedrock Converse API)
- [ ] **Tool server:** `kuwarden-scm` — git clone, branch, read files, write files, commit, push
- [ ] **Tool server:** `kuwarden-jira` — read ticket, post comment, transition status
- [ ] **Planner Agent** — ticket → structured change plan (JSON output)
- [ ] **Coder Agent** — change plan → file modifications → commit to feature branch
- [ ] **Deployer Agent (partial)** — create PR with auto-generated description
- [ ] **Monitoring UI — Run list view** — shows all runs with status
- [ ] **Monitoring UI — Run detail view** — step-by-step progress, live log
- [ ] **Reporter Agent** — posts PR link and summary back to Jira ticket
- [ ] `kuwarden.yaml` loading for a single registered test application
- [ ] End-to-end integration test: Jira ticket → PR raised automatically

**Exit criteria:** Assign a Jira ticket to KuWarden, a PR is raised within 10 minutes with working code and Jira comment updated.

---

## Phase 2 — Quality Gates & Human Approval (Weeks 11–16)

**Goal:** Production-safe pipeline with review, testing, and human approval checkpoints.

### Deliverables

- [ ] **Reviewer Agent** — LLM-based code review, structured review report
- [ ] **Tool server:** `kuwarden-sast` — Semgrep integration, results fed into reviewer
- [ ] **Tester Agent** — LLM test generation, CI trigger, result parsing
- [ ] **Tool server:** `kuwarden-cicd` — GitHub Actions / Jenkins / Azure Pipelines trigger + poll
- [ ] **Approval Gate** — flow pauses, records pending approval in DB
- [ ] **Monitoring UI — Approval queue** — human can view diff, approve or reject with comment
- [ ] **Notification adapter** — Slack / Microsoft Teams notification on gate reached
- [ ] Configurable approval gates per application in `kuwarden.yaml`
- [ ] Flow abort + rollback on test failure (branch deleted, ticket commented)
- [ ] Test coverage threshold enforcement

**Exit criteria:** Pipeline pauses at configured gates. Human approves via UI. Tests must pass before deploy step can proceed.

---

## Phase 3 — Deployment & Environment Promotion (Weeks 17–22)

**Goal:** Full end-to-end flow — ticket to deployed change in Test and UAT.

### Deliverables

- [ ] **Deployer Agent (full)** — Kubernetes deploy via `kubectl apply` / `helm upgrade`
- [ ] **Tool server:** `kuwarden-deploy` — kubectl, Helm, ArgoCD sync trigger
- [ ] Deployment health check polling (pod readiness, service endpoint check)
- [ ] Multi-environment promotion model: Test → UAT → Production (each requires approval)
- [ ] Rollback on failed health check
- [ ] **Monitoring UI — Deployment view** — environment status, pod health, rollback button
- [ ] Audit trail view — immutable log of every action, approval, and deployment

**Exit criteria:** Full flow runs end-to-end: Jira ticket → code → PR → tests pass → deploy to Test → human promotes to UAT.

---

## Phase 4 — Azure DevOps & Breadth (Weeks 23–28)

**Goal:** Expand to Azure DevOps trigger and second SCM platform. Harden for multi-team use.

### Deliverables

- [ ] **Trigger layer:** Azure DevOps webhook receiver
- [ ] **Tool server:** `kuwarden-ado` — Azure DevOps work item read/update, Azure Repos ops
- [ ] **SCM adapter:** Azure Repos (git ops via Azure DevOps API)
- [ ] **App registry UI** — register/deregister applications, view hook config, run history
- [ ] Multi-tenancy: namespace isolation per application team
- [ ] Rate limiting and concurrency controls (max parallel runs per app)
- [ ] `kuwarden.yaml` schema v2 — support for conditional pipeline steps
- [ ] Helm chart: production-hardened, resource limits, PodDisruptionBudget, HPA

**Exit criteria:** An Azure DevOps work item triggers a full end-to-end flow. Multiple application teams can register independently without interfering.

---

## Phase 5 — Enterprise Hardening & Observability (Weeks 29–36)

**Goal:** Production-grade reliability, security, and observability for enterprise adoption.

### Deliverables

- [ ] **Monitoring UI — Full audit trail** — exportable, tamper-evident log
- [ ] Prometheus metrics from engine (run counts, agent durations, failure rates)
- [ ] Grafana dashboard — KuWarden platform health
- [ ] Alerting — failed runs, gate timeouts, LLM errors
- [ ] OIDC / SAML integration for UI authentication (Azure AD, Okta, Keycloak)
- [ ] Role-based access control (RBAC) — viewer, approver, admin roles
- [ ] **WatsonxAdapter** — IBM Watsonx.ai LLM adapter
- [ ] Disaster recovery runbook — PostgreSQL backup/restore, Redis persistence
- [ ] Load testing — validate 50 concurrent flow runs
- [ ] Security penetration test — external review of KuWarden engine API

**Exit criteria:** KuWarden passes security review. Monitoring dashboards live. RBAC enforced. Handles 50 concurrent runs without degradation.

---

## Phase 6 — Platform Extension & Ecosystem (Weeks 37–48)

**Goal:** Make KuWarden extensible so enterprise teams can add custom agents and tool servers.

### Deliverables

- [ ] **Custom agent SDK** — documented interface for teams to write their own agents
- [ ] **Custom tool server SDK** — any MCP-compatible tool server can be registered
- [ ] Plugin registry — list and install community tool servers
- [ ] `kuwarden.yaml` schema v3 — support for custom agent references
- [ ] GitHub Issues trigger adapter
- [ ] GitLab trigger and SCM adapter
- [ ] **UI — Flow builder** — visual drag-and-drop pipeline configuration
- [ ] Multi-LLM routing — automatic fallback if primary LLM is unavailable
- [ ] Cost tracking — LLM token usage and cost per run, per application

---

## Summary Timeline

| Phase | Focus | Duration | End State |
|---|---|---|---|
| 0 | Foundation | Weeks 1–3 | Dev environment running |
| 1 | MVP — Jira → PR | Weeks 4–10 | Ticket creates PR automatically |
| 2 | Quality gates | Weeks 11–16 | Review, tests, human approval |
| 3 | Deploy to environments | Weeks 17–22 | Full ticket-to-deployment flow |
| 4 | Azure DevOps + breadth | Weeks 23–28 | Multi-team, multi-platform |
| 5 | Enterprise hardening | Weeks 29–36 | Production-grade security + ops |
| 6 | Platform extension | Weeks 37–48 | Extensible ecosystem |

---

*See [ARCHITECTURE.md](./ARCHITECTURE.md) for component design.*  
*See [LLM_STRATEGY.md](./LLM_STRATEGY.md) for LLM backend decisions.*
