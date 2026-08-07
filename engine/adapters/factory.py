"""Configuration to adapter.

The one place that maps a declared provider string onto an implementation, so that adding a
platform is one entry here rather than a conditional in every node.
"""

from __future__ import annotations

import httpx

from engine.adapters.credentials import CredentialBroker
from engine.adapters.protocols import ScmAdapter, TicketAdapter
from engine.adapters.scm.azure_repos import AzureReposScm
from engine.adapters.scm.github import GitHubScm
from engine.adapters.ticket.azure_devops import AzureDevOpsTickets
from engine.adapters.ticket.jira import JiraTickets
from engine.config import ConfigError, RepoConfig, TriggerConfig


def ticket_adapter(
    trigger: TriggerConfig,
    broker: CredentialBroker,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TicketAdapter:
    match trigger.provider:
        case "jira":
            if not trigger.site or not trigger.account_email:
                raise ConfigError("jira triggers need both site and account_email")
            return JiraTickets(
                trigger.site,
                trigger.account_email,
                broker,
                story_points_field=trigger.story_points_field,
                transport=transport,
            )
        case "azure_devops":
            if not trigger.organisation:
                raise ConfigError("azure_devops triggers need an organisation")
            return AzureDevOpsTickets(trigger.organisation, broker, transport=transport)
    raise ConfigError(f"no ticket adapter for provider {trigger.provider!r}")


def scm_adapter(
    repo: RepoConfig,
    broker: CredentialBroker,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ScmAdapter:
    match repo.provider:
        case "github":
            return GitHubScm(broker, transport=transport)
        case "azure_repos":
            return AzureReposScm(broker, transport=transport)
    raise ConfigError(f"no SCM adapter for provider {repo.provider!r}")
