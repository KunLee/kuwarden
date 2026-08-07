"""`kuwarden.yaml` — an application's declaration of how its changes are delivered.

Distinct from `policy.yaml`, which describes the platform deployment. An application cannot
grant itself capabilities: this file may only *select from* what `policy.yaml` already
permits — ADR 0003 §1. It is also a protected path, so an agent may never write it.

The example in ARCHITECTURE.md §2.5 predates ADR 0002 and ADR 0004 and describes a linear
pipeline with uniform approval gates. This is the schema the ADRs actually imply: no
pipeline key at all, because the topology is fixed and is not per-application configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from engine.adapters.protocols import IntegrationModel, RepoRef, TicketRef
from engine.errors import KuWardenError
from engine.state import RiskTier


class ConfigError(KuWardenError):
    """`kuwarden.yaml` is absent, malformed, or declares something unsupportable."""


@dataclass(frozen=True)
class RepoConfig:
    name: str
    provider: str  # "github" | "azure_repos"
    org: str
    repo: str
    project: str | None = None
    path: str = "."

    def ref(self) -> RepoRef:
        host = "github.com" if self.provider == "github" else "dev.azure.com"
        return RepoRef(host=host, org=self.org, repo=self.repo, project=self.project)


@dataclass(frozen=True)
class TriggerConfig:
    provider: str  # "jira" | "azure_devops"
    project: str
    site: str | None = None
    account_email: str | None = None
    organisation: str | None = None
    label: str | None = None
    max_story_points: int | None = None
    story_points_field: str | None = None

    def ref(self, ticket_id: str) -> TicketRef:
        return TicketRef(system=self.provider, project=self.project, id=ticket_id)


@dataclass(frozen=True)
class RiskConfig:
    """Rules-first tiering. An LLM may raise a tier from here; it may never lower one."""

    high_paths: list[str] = field(default_factory=list)
    medium_paths: list[str] = field(default_factory=list)
    high_labels: list[str] = field(default_factory=list)
    #: Above this many changed files, the change stops being small whatever it touched.
    high_changed_files: int | None = None


@dataclass(frozen=True)
class AppConfig:
    name: str
    repos: list[RepoConfig]
    triggers: list[TriggerConfig]
    integration_model: IntegrationModel
    toolchain_id: str = "none"
    risk: RiskConfig = field(default_factory=RiskConfig)
    budget_cents_per_run: int = 0
    max_coder_retries: int = 3
    default_risk_tier: RiskTier = "low"

    @property
    def primary(self) -> RepoConfig:
        return self.repos[0]


def load(path: str | Path) -> AppConfig:
    file = Path(path)
    if not file.exists():
        raise ConfigError(f"no kuwarden.yaml at {file}")
    return parse(file.read_text(encoding="utf-8"))


def parse(text: str) -> AppConfig:
    """`safe_load`, never `load`.

    This file is read from an application repository, which means it is written by whoever
    can open a pull request there. `yaml.load` constructs arbitrary Python objects.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"kuwarden.yaml is not valid YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError("kuwarden.yaml must be a mapping")

    version = raw.get("version")
    if version != 1:
        raise ConfigError(f"unsupported kuwarden.yaml version {version!r}; expected 1")

    app = _mapping(raw, "app")
    name = app.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError("app.name is required")

    repos = [_repo(entry, i) for i, entry in enumerate(_sequence(raw, "workspace", "repos"))]
    if not repos:
        raise ConfigError("workspace.repos must contain at least one repository")

    triggers = [_trigger(entry, i) for i, entry in enumerate(raw.get("triggers") or [])]

    delivery = _mapping(raw, "delivery")
    declared = delivery.get("integration_model")
    # Never inferred and never defaulted: which control point governs a deployment is a
    # governance decision -- ADR 0004. An absent value is an error, not model C.
    if declared is None:
        raise ConfigError(
            "delivery.integration_model must be declared explicitly "
            f"(one of {', '.join(m.value for m in IntegrationModel)})"
        )
    try:
        integration_model = IntegrationModel(declared)
    except ValueError:
        raise ConfigError(f"unknown integration_model {declared!r}") from None

    risk_raw = raw.get("risk") or {}
    budgets = raw.get("budgets") or {}

    return AppConfig(
        name=name,
        repos=repos,
        triggers=triggers,
        integration_model=integration_model,
        toolchain_id=str(raw.get("toolchain", {}).get("id", "none")),
        risk=RiskConfig(
            high_paths=[str(p) for p in risk_raw.get("high_paths", [])],
            medium_paths=[str(p) for p in risk_raw.get("medium_paths", [])],
            high_labels=[str(label) for label in risk_raw.get("high_labels", [])],
            high_changed_files=risk_raw.get("high_changed_files"),
        ),
        budget_cents_per_run=int(budgets.get("cents_per_run", 0)),
        max_coder_retries=int(raw.get("flow", {}).get("max_coder_retries", 3)),
    )


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _sequence(raw: dict[str, Any], outer: str, inner: str) -> list[Any]:
    value = _mapping(raw, outer).get(inner)
    if not isinstance(value, list):
        raise ConfigError(f"{outer}.{inner} must be a list")
    return value


def _repo(entry: Any, index: int) -> RepoConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"workspace.repos[{index}] must be a mapping")
    provider = str(entry.get("provider", ""))
    if provider not in {"github", "azure_repos"}:
        raise ConfigError(
            f"workspace.repos[{index}].provider must be github or azure_repos, got {provider!r}"
        )
    if provider == "azure_repos" and not entry.get("project"):
        raise ConfigError(f"workspace.repos[{index}] is azure_repos and needs a project")
    for required in ("name", "org", "repo"):
        if not entry.get(required):
            raise ConfigError(f"workspace.repos[{index}].{required} is required")
    return RepoConfig(
        name=str(entry["name"]),
        provider=provider,
        org=str(entry["org"]),
        repo=str(entry["repo"]),
        project=str(entry["project"]) if entry.get("project") else None,
        path=str(entry.get("path", ".")),
    )


def _trigger(entry: Any, index: int) -> TriggerConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"triggers[{index}] must be a mapping")
    provider = str(entry.get("provider", ""))
    if provider not in {"jira", "azure_devops"}:
        raise ConfigError(
            f"triggers[{index}].provider must be jira or azure_devops, got {provider!r}"
        )
    if provider == "jira" and not entry.get("site"):
        raise ConfigError(f"triggers[{index}] is jira and needs a site url")
    if provider == "azure_devops" and not entry.get("organisation"):
        raise ConfigError(f"triggers[{index}] is azure_devops and needs an organisation")
    if not entry.get("project"):
        raise ConfigError(f"triggers[{index}].project is required")
    return TriggerConfig(
        provider=provider,
        project=str(entry["project"]),
        site=str(entry["site"]) if entry.get("site") else None,
        account_email=str(entry["account_email"]) if entry.get("account_email") else None,
        organisation=str(entry["organisation"]) if entry.get("organisation") else None,
        label=str(entry["label"]) if entry.get("label") else None,
        max_story_points=entry.get("max_story_points"),
        story_points_field=(
            str(entry["story_points_field"]) if entry.get("story_points_field") else None
        ),
    )
