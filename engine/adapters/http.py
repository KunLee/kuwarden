"""Shared HTTP plumbing for adapters.

One place that knows how to attach a credential and how to turn a platform's error into a
typed one, so that neither is re-decided per adapter — and so there is a single line to audit
for "where does the token go".
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

import httpx

from engine.adapters.credentials import Secret
from engine.errors import AdapterError, NotFound, PermissionDenied

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def basic_auth_header(username: str, token: Secret) -> str:
    """Azure DevOps authenticates a PAT as HTTP Basic with an empty username.

    Jira Cloud uses the same scheme with the account email as the username.
    """
    raw = f"{username}:{token.reveal()}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def bearer_auth_header(token: Secret) -> str:
    """GitHub, and most things newer than Azure DevOps."""
    return f"Bearer {token.reveal()}"


class RestClient:
    """A thin wrapper that never lets a response body become an untyped surprise."""

    def __init__(
        self,
        base_url: str,
        auth_header: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
            headers={"Accept": "application/json", **(headers or {})},
        )
        # Held apart from the constructor arguments so it never appears in a dataclass repr
        # or a captured frame alongside the rest of the configuration.
        self._auth = auth_header

    async def __aenter__(self) -> RestClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        content_type: str = "application/json",
    ) -> Any:
        headers = {"Authorization": self._auth}
        if json is not None:
            headers["Content-Type"] = content_type
        try:
            response = await self._client.request(
                method, url, params=params, json=json, headers=headers
            )
        except httpx.HTTPError as exc:
            # The token is in `headers`; never let the exception carry the request.
            raise AdapterError(f"{method} {url} failed: {type(exc).__name__}") from None

        # 404 first, and as its own type. Every platform here answers "this ref does not
        # exist" with one, and a caller deciding between creating a branch and updating one
        # must not have to distinguish that case by matching on an error string.
        if response.status_code == 404:
            raise NotFound(f"{method} {url} returned 404: {response.text[:200]}")
        # Typed so the adapter can name the missing grant. A caller that knows it was pushing
        # a branch can say "this needs Contents: Read and write"; the platform's own message
        # cannot, because it does not know why the call was made.
        if response.status_code == 403:
            raise PermissionDenied(f"{method} {url} returned 403: {response.text[:300]}")
        if response.status_code >= 400:
            raise AdapterError(
                f"{method} {url} returned {response.status_code}: {response.text[:500]}"
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise AdapterError(f"{method} {url} returned a non-JSON body") from None

    async def get(self, url: str, *, params: Mapping[str, str] | None = None) -> Any:
        return await self.request("GET", url, params=params)

    async def post(self, url: str, *, json: Any, params: Mapping[str, str] | None = None) -> Any:
        return await self.request("POST", url, json=json, params=params)

    async def patch(self, url: str, *, json: Any, params: Mapping[str, str] | None = None) -> Any:
        return await self.request("PATCH", url, json=json, params=params)

    async def delete(self, url: str, *, params: Mapping[str, str] | None = None) -> Any:
        return await self.request("DELETE", url, params=params)
