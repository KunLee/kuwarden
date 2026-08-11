"""Telling a human that a run is waiting for them.

An approval gate with no notification is a queue with no doorbell — the run suspends, nobody
is watching, and the platform's headline metric (human minutes per run) gets worse rather
than better.

Two constraints shape what this sends.

**The email is a notification, never the decision.** It carries a link and nothing else that
matters. Approving by replying to a message, or by clicking a signed link that acts directly,
would mean the approval is not bound to the evidence the approver read — the digest check in
`engine.evidence` is the whole control, and it only works if the decision is made on an
authenticated page that rendered that evidence.

**The email carries no ticket content.** Ticket text is hostile input (it reaches a model, and
anyone who can file a ticket can write it) and mail clients render more than they should. The
ticket *id* is enough for the recipient to know what this is; everything else lives behind
authentication.

SMTP rather than a vendor API, because the flagship deployment is air-gapped and an internal
relay is the one thing such an environment reliably has.
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from uuid import UUID

from temporalio import activity

from engine.db import connect

log = logging.getLogger(__name__)


@dataclass
class GateNotice:
    """Why someone is being emailed.

    Deliberately holds no ticket text — see the module docstring.
    """

    run_id: UUID
    app_id: UUID
    ticket_id: str
    risk_tier: str
    approvals_needed: int


def _base_url() -> str:
    """Where the Workbench is reachable from a recipient's browser.

    Not derived from the request that started the run: the engine may be behind a proxy, and
    a link built from an internal hostname is a link nobody can open.
    """
    return os.environ.get("KUWARDEN_BASE_URL", "http://localhost:5173").rstrip("/")


async def _approvers() -> list[str]:
    """Who may decide, from the account table.

    Reads the role rather than a configured mailing list, so revoking someone's approver role
    also stops the mail. Two sources of truth for "who approves" is one too many.
    """
    async with connect() as conn:
        rows = await conn.fetch(
            "SELECT email FROM users WHERE role IN ('approver', 'admin') AND disabled_at IS NULL"
        )
    return [row["email"] for row in rows]


@activity.defn
async def notify_gate_reached(notice: GateNotice) -> int:
    """Email every active approver that a run is waiting. Returns how many were addressed.

    Never raises on a delivery failure. A relay being down must not fail the run: the gate
    still holds, the Workbench still shows the run as suspended, and the change is not
    released either way. Losing the run because the mail server hiccuped would turn a
    notification problem into a delivery outage.
    """
    recipients = await _approvers()
    if not recipients:
        log.warning("run %s reached its gate and no active approver account exists", notice.run_id)
        return 0

    host = os.environ.get("KUWARDEN_SMTP_HOST")
    if not host:
        # Expected in development. Logged at INFO with the link, so the gate is still usable
        # without a mail server — and not at WARNING, which would train people to ignore it.
        log.info(
            "no KUWARDEN_SMTP_HOST configured; run %s awaits %d approval(s) at %s/runs/%s",
            notice.run_id,
            notice.approvals_needed,
            _base_url(),
            notice.run_id,
        )
        return 0

    message = _compose(notice, recipients)
    try:
        # smtplib is blocking; the worker's event loop is not free to stall on a mail relay.
        await asyncio.to_thread(_send, message, host)
    except Exception as exc:  # noqa: BLE001 - see the docstring: delivery must not fail a run
        log.warning("could not notify approvers for run %s: %s", notice.run_id, exc)
        return 0
    return len(recipients)


def _compose(notice: GateNotice, recipients: list[str]) -> EmailMessage:
    """Build the message. Plain text only — no HTML, no images, no tracking pixel."""
    link = f"{_base_url()}/runs/{notice.run_id}"
    message = EmailMessage()
    message["Subject"] = f"[KuWarden] {notice.ticket_id} needs approval ({notice.risk_tier} risk)"
    message["From"] = os.environ.get("KUWARDEN_SMTP_FROM", "kuwarden@localhost")
    # Bcc rather than To: the recipient list is the deployment's approver roster, and there
    # is no reason for every approver to learn who else holds the role.
    message["To"] = message["From"]
    message["Bcc"] = ", ".join(recipients)
    message.set_content(
        f"A change is waiting for approval.\n\n"
        f"  Ticket:     {notice.ticket_id}\n"
        f"  Risk tier:  {notice.risk_tier}\n"
        f"  Approvals:  {notice.approvals_needed} required\n\n"
        f"Review the evidence and decide here:\n\n"
        f"  {link}\n\n"
        f"This message is a notification only. The decision is recorded against the evidence "
        f"document shown on that page, so it cannot be made by replying to this email.\n"
    )
    return message


def _send(message: EmailMessage, host: str) -> None:
    """Hand the message to the relay.

    STARTTLS is attempted and not required: an internal relay on a trusted segment frequently
    offers no TLS at all, and refusing to send there would mean no notifications in exactly
    the deployment this is built for. The message carries no secret — the link it contains is
    useless without a session.
    """
    port = int(os.environ.get("KUWARDEN_SMTP_PORT", "25"))
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        # EHLO first: `has_extn` reads the feature list the server announces, and before any
        # greeting that list is empty -- so checking without this always reports no TLS.
        smtp.ehlo()
        if smtp.has_extn("starttls"):
            smtp.starttls()
            # The feature list is renegotiated after the upgrade; the pre-TLS one is not
            # trustworthy and AUTH is frequently only offered afterwards.
            smtp.ehlo()
        user = os.environ.get("KUWARDEN_SMTP_USER")
        password = os.environ.get("KUWARDEN_SMTP_PASSWORD")
        if user and password:
            smtp.login(user, password)
        smtp.send_message(message)
