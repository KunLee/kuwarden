"""Jira Cloud.

Two things differ from Azure DevOps in ways that reach the interface rather than just the
wire format:

**Descriptions are ADF**, Atlassian Document Format — a JSON tree, not markup. It is walked
rather than regex-stripped, which is both more correct and less likely to leave a fragment of
a tag in text a model then reads.

**Transitions are per-workflow.** Jira moves an issue by transition id, and those ids differ
per project and per workflow, so a target state name has to be resolved against what is
actually available. Guessing an id would silently move an issue to the wrong state.
"""

from __future__ import annotations

from typing import Any

import httpx

from engine.adapters.credentials import (
    CredentialBroker,
    CredentialKind,
    CredentialRequest,
)
from engine.adapters.http import RestClient, basic_auth_header
from engine.adapters.protocols import TicketRef
from engine.errors import AdapterError
from engine.state import Ticket

API = "/rest/api/3"


def _adf_text(node: Any) -> str:
    """Flatten an Atlassian Document Format tree to plain text."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_text(child) for child in node)
    if not isinstance(node, dict):
        return ""

    kind = node.get("type")
    if kind == "text":
        return str(node.get("text", ""))
    if kind == "hardBreak":
        return "\n"

    inner = _adf_text(node.get("content", []))
    # Block-level nodes each end a line; inline ones do not.
    if kind in {"paragraph", "heading", "listItem", "blockquote", "codeBlock"}:
        return inner + "\n"
    return inner


def _adf_document(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": line}]}
            if line
            else {"type": "paragraph"}
            for line in text.split("\n")
        ],
    }


class JiraTickets:
    """Implements `TicketAdapter` for Jira Cloud.

    `story_points_field` has no default because Jira stores story points in a custom field
    whose id differs per instance. Hardcoding one instance's id would silently read the wrong
    field — or nothing — on every other instance, and admission control depends on it.
    """

    def __init__(
        self,
        site_url: str,
        account_email: str,
        broker: CredentialBroker,
        *,
        story_points_field: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._site = site_url.rstrip("/")
        self._email = account_email
        self._broker = broker
        self._story_points_field = story_points_field
        self._transport = transport

    async def _client(self, ref: TicketRef) -> RestClient:
        token = await self._broker.resolve(
            CredentialRequest(kind=CredentialKind.TICKET_READ_WRITE, realm=ref.realm)
        )
        return RestClient(
            base_url=self._site,
            auth_header=basic_auth_header(self._email, token),
            transport=self._transport,
        )

    async def fetch(self, ref: TicketRef) -> Ticket:
        async with await self._client(ref) as client:
            body: Any = await client.get(f"{API}/issue/{ref.id}")
        if not isinstance(body, dict):
            raise AdapterError(f"unexpected issue payload for {ref.id}")

        fields: dict[str, Any] = body.get("fields", {})
        points = self._story_points_field and fields.get(self._story_points_field)

        return Ticket(
            id=str(body.get("key", ref.id)),
            system="jira",
            title=str(fields.get("summary", "")),
            body=_adf_text(fields.get("description")).strip(),
            acceptance_criteria=[],
            labels=[str(label) for label in fields.get("labels", [])],
            story_points=int(points) if isinstance(points, int | float) else None,
        )

    async def comment(self, ref: TicketRef, body: str) -> None:
        async with await self._client(ref) as client:
            await client.post(
                f"{API}/issue/{ref.id}/comment", json={"body": _adf_document(body)}
            )

    async def transition(self, ref: TicketRef, state: str) -> None:
        async with await self._client(ref) as client:
            available: Any = await client.get(f"{API}/issue/{ref.id}/transitions")
            transitions = available.get("transitions", []) if isinstance(available, dict) else []

            match = next(
                (
                    t
                    for t in transitions
                    if str(t.get("name", "")).casefold() == state.casefold()
                    or str(t.get("to", {}).get("name", "")).casefold() == state.casefold()
                ),
                None,
            )
            if match is None:
                offered = ", ".join(str(t.get("name")) for t in transitions) or "none"
                raise AdapterError(
                    f"issue {ref.id} has no transition to {state!r}; available: {offered}"
                )

            await client.post(
                f"{API}/issue/{ref.id}/transitions", json={"transition": {"id": match["id"]}}
            )
