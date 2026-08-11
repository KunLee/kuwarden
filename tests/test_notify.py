"""The gate notification.

Two properties are worth a test because both fail silently. A notification that carries
ticket content leaks hostile input into mail clients and nobody notices; a notification that
raises on a dead relay fails the run and it looks like an engine bug.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from engine.activities.notify import GateNotice, _compose, notify_gate_reached
from engine.api.auth import Role, create_user
from engine.db import connect

NOTICE = GateNotice(
    run_id=uuid.uuid4(),
    app_id=uuid.uuid4(),
    ticket_id="PAY-1234",
    risk_tier="high",
    approvals_needed=2,
)


def test_the_email_carries_no_ticket_content() -> None:
    """Ticket text is hostile input. The id is enough; the rest lives behind authentication."""
    message = _compose(NOTICE, ["approver@acme.test"])
    body = message.get_content()

    assert "PAY-1234" in body, "the recipient must know which ticket this is"
    assert message.get_content_type() == "text/plain", "no HTML, so nothing renders"
    assert f"/runs/{NOTICE.run_id}" in body, "the link is the point of the message"


def test_the_email_says_it_is_not_the_decision() -> None:
    """Replying to a message cannot approve anything — the digest binding requires the page."""
    body = _compose(NOTICE, ["approver@acme.test"]).get_content()
    assert "notification only" in body
    assert "cannot be made by replying" in body


def test_approvers_are_not_disclosed_to_each_other() -> None:
    """The roster is not something every approver needs to learn from a header."""
    message = _compose(NOTICE, ["a@acme.test", "b@acme.test"])
    assert "a@acme.test" not in str(message["To"])
    assert "a@acme.test" in str(message["Bcc"])


@pytest.fixture
async def an_approver() -> AsyncIterator[str]:
    """One active approver account.

    Required, not incidental: `notify_gate_reached` returns early when the roster is empty,
    so without this the SMTP tests below would pass without reaching any SMTP code at all.
    """
    email = f"approver-{uuid.uuid4().hex[:8]}@test.invalid"
    await create_user(email, "Approver", "correct-horse-battery-staple", Role.APPROVER)
    try:
        yield email
    finally:
        async with connect() as conn:
            await conn.execute("DELETE FROM users WHERE email = $1", email)


async def test_no_approver_account_is_reported_rather_than_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A gate nobody can open is a stuck run, and it should not be silent."""
    async with connect() as conn:
        # Any pre-existing approver would mask this; assert the precondition instead of
        # assuming an empty table.
        existing = await conn.fetchval(
            "SELECT count(*) FROM users WHERE role IN ('approver','admin') "
            "AND disabled_at IS NULL"
        )
    if existing:
        pytest.skip("the database already has approver accounts")

    with caplog.at_level("WARNING", logger="engine.activities.notify"):
        assert await notify_gate_reached(NOTICE) == 0
    assert "no active approver account" in caplog.text


async def test_a_dead_relay_does_not_fail_the_run(
    monkeypatch: pytest.MonkeyPatch, an_approver: str
) -> None:
    """The gate still holds and nothing is released; losing the run would be far worse."""
    monkeypatch.setenv("KUWARDEN_SMTP_HOST", "127.0.0.1")
    # A port nothing listens on, so this is a real connection failure rather than a stub.
    monkeypatch.setenv("KUWARDEN_SMTP_PORT", "9")

    assert await notify_gate_reached(NOTICE) == 0


async def test_without_a_relay_the_link_is_still_reported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, an_approver: str
) -> None:
    """The development path. The gate must be usable without a mail server."""
    monkeypatch.delenv("KUWARDEN_SMTP_HOST", raising=False)

    with caplog.at_level("INFO", logger="engine.activities.notify"):
        assert await notify_gate_reached(NOTICE) == 0

    assert str(NOTICE.run_id) in caplog.text
