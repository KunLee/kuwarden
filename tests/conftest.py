"""Shared fixtures.

The mocked platform here is what lets the end-to-end flow tests run the *real* nodes —
adapters, credential resolution, protected-path enforcement and all — without an Azure DevOps
organisation, a Jira site, or a GitHub repository. The Flow Engine, Temporal and PostgreSQL
are real; only the far side of the HTTP boundary is not.
"""

from __future__ import annotations

import httpx
import pytest

from engine.activities.nodes import RUNTIME
from engine.adapters.credentials import EnvCredentialBroker
from engine.config import AppConfig, parse

KUWARDEN_YAML = """
version: 1

app:
  name: payments-service

workspace:
  repos:
    - name: payments-service
      provider: github
      org: acme
      repo: payments-service

triggers:
  - provider: jira
    site: https://acme.atlassian.net
    account_email: bot@acme.test
    project: PAY
    label: kuwarden-auto
    max_story_points: 5

delivery:
  integration_model: gated_deployment

toolchain:
  id: python3.12

risk:
  high_labels: [security, payments]

budgets:
  cents_per_run: 500
"""


class FakePlatform:
    """Records every request so a test can assert what KuWarden actually sent."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.pull_requests: list[dict[str, object]] = []
        self.comments: list[str] = []
        self.labels: list[str] = ["kuwarden-auto"]
        self.story_points: int | None = 3

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        import json

        self.requests.append(request)
        path = request.url.path

        if path.endswith("/comment"):
            self.comments.append(json.loads(request.content)["body"]["content"][0]["content"][0]["text"])
            return httpx.Response(201, json={})
        if "/issue/" in path:
            return httpx.Response(
                200,
                json={
                    "key": "PAY-1234",
                    "fields": {
                        "summary": "Add a health endpoint",
                        "description": {
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Return 200"}],
                                }
                            ],
                        },
                        "labels": self.labels,
                        "customfield_points": self.story_points,
                    },
                },
            )
        if path.endswith("/repos/acme/payments-service"):
            return httpx.Response(200, json={"default_branch": "main"})
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base000"}})
        if "/git/commits/" in path:
            return httpx.Response(200, json={"tree": {"sha": "tree-base"}})
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob-1"})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "tree-1"})
        if path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "commit-1"})
        if path.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if path.endswith("/pulls"):
            body = json.loads(request.content)
            self.pull_requests.append(body)
            return httpx.Response(
                201,
                json={"number": 42, "html_url": "https://github.com/acme/payments-service/pull/42"},
            )
        return httpx.Response(404, text=f"fake platform has no route for {path}")


@pytest.fixture
def app_config() -> AppConfig:
    return parse(KUWARDEN_YAML)


@pytest.fixture
def platform(app_config: AppConfig) -> FakePlatform:
    """Binds the worker runtime for the duration of a test."""
    fake = FakePlatform()
    RUNTIME.configure(
        app_config,
        broker=EnvCredentialBroker(
            {"KUWARDEN_TICKET_TOKEN": "jira-t", "KUWARDEN_SCM_TOKEN": "gh-t"}
        ),
        transport=fake.transport(),
    )
    return fake
