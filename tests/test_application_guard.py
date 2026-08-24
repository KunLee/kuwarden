"""One worker serves one application's configuration.

Credentials are resolved per application; configuration is not. A run for application B on a
worker configured for A would read A's repository list, A's tiering rules and A's auto-merge
policy while holding B's tokens — and before this guard, nothing anywhere would say so. The
failure mode is a push to the wrong repository with an audit trail that reads correctly.
"""

from __future__ import annotations

import pytest

from engine.errors import PolicyDenied
from engine.policy.application import assert_configured_for


def test_the_matching_application_passes() -> None:
    # Returns None; the assertion is that it does not raise.
    assert_configured_for("sasagayo", "sasagayo")


def test_a_different_application_is_refused() -> None:
    with pytest.raises(PolicyDenied, match="configured for 'sasagayo' but the run is for"):
        assert_configured_for("sasagayo", "payments-service")


def test_the_refusal_names_both_sides_and_the_fix() -> None:
    """An operator hitting this needs to know which worker to start, not that something broke."""
    with pytest.raises(PolicyDenied) as raised:
        assert_configured_for("sasagayo", "payments-service")
    message = str(raised.value)
    assert "sasagayo" in message
    assert "payments-service" in message
    assert "KUWARDEN_CONFIG" in message


def test_case_and_whitespace_are_not_a_different_application() -> None:
    """`Sasagayo` in the Workbench and `sasagayo` in the YAML is a typo, not another app.

    Failing on it would train people to disable the check, which costs more than it saves.
    """
    assert_configured_for("sasagayo", " Sasagayo ")


def test_a_run_that_names_no_application_is_refused_rather_than_assumed() -> None:
    """Unverifiable is not consent.

    Runs started before this field existed carry an empty name. Refusing costs one line to
    recover from; proceeding could push to a repository nobody chose.
    """
    with pytest.raises(PolicyDenied, match="does not name the application"):
        assert_configured_for("sasagayo", "")


async def test_triage_refuses_before_fetching_the_ticket() -> None:
    """The guard runs before any credential is used or any token is spent.

    Placed at the top of Triage rather than at the API, because the worker is where the
    configuration that governs the run actually lives — an API on another host could be
    reading a different file entirely.
    """
    from pathlib import Path

    # Read from disk rather than through `inspect`: the node decorator wraps the function, so
    # introspection returns the wrapper's source in base.py and the ordering being asserted
    # would silently stop being checked.
    source = (Path(__file__).resolve().parents[1] / "engine/nodes/triage.py").read_text(
        encoding="utf-8"
    )
    guard_at = source.index("assert_configured_for(ctx.config.name")
    fetch_at = source.index("ticket_adapter(")
    assert guard_at < fetch_at, "the guard must run before the ticket is fetched"
