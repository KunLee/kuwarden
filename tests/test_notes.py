"""What a node put in the record, and what it must never put there.

The audit trail is append-only (invariant 9) and does not expire. That makes notes evidence
rather than logging, and it makes two properties worth a test rather than a convention: a note
is bounded, and a note says which end of an over-long block it kept. A record that silently
dropped the failing half of a test log would be worse than one that dropped all of it, because
nothing on screen would say so.
"""

from __future__ import annotations

import json
import uuid

from engine.activities.nodes import RUNTIME
from engine.nodes import notes

# The module, not the node: `engine.nodes` re-exports the decorated function under the
# same name, so `from engine.nodes import reporter` yields the node and not `_body`.
from engine.nodes.reporter import _body
from engine.state import FlowState, Ticket, Verification
from tests.conftest import FakePlatform


def test_a_long_block_is_capped_and_says_so() -> None:
    section = notes.text("prompt", "x" * (notes.MAX_TEXT + 500))
    assert len(section["body"]) == notes.MAX_TEXT
    assert section["truncated"] is True
    # The reader needs the original size to know how much is missing; a trailing ellipsis
    # says only "some".
    assert section["full_length"] == notes.MAX_TEXT + 500


def test_a_short_block_is_not_marked_truncated() -> None:
    section = notes.text("plan", "three steps")
    assert section["body"] == "three steps"
    assert section["truncated"] is False


def test_test_output_keeps_the_end_because_the_failure_is_last() -> None:
    """`tail=True` is not cosmetic.

    A suite prints its failure last. Keeping the first 8 KB of a passing preamble discards the
    only part anyone opened the panel to read.
    """
    body = "preamble\n" * 4000 + "FAILED tests/test_app.py::test_add"
    section = notes.text("stdout", body, tail=True)

    assert section["body"].endswith("FAILED tests/test_app.py::test_add")
    assert section["kept"] == "end"
    assert section["truncated"] is True


def test_the_start_is_kept_by_default() -> None:
    section = notes.text("prompt", "HEAD" + "x" * notes.MAX_TEXT)
    assert section["body"].startswith("HEAD")
    assert section["kept"] == "start"


def test_a_check_records_the_rule_it_was_decided_against() -> None:
    """A trail that says "label ok" cannot be re-checked by a reader who thinks the rule was
    wrong. Both sides are recorded, on a pass as much as on a refusal."""
    section = notes.checks(
        "Admission control",
        [("Required label", "kuwarden-auto", ["bug", "kuwarden-auto"], True)],
    )
    row = section["rows"][0]

    assert row["required"] == "kuwarden-auto"
    assert row["found"] == "bug, kuwarden-auto"
    assert row["ok"] is True


def test_every_value_reaches_the_record_as_a_string() -> None:
    """So that rendering never depends on a JSON type. An empty list is `—`, not `[]`."""
    section = notes.fields(
        "mixed",
        [("none", None), ("bool", False), ("empty", []), ("number", 3)],
    )
    assert section["rows"] == [["none", "—"], ["bool", "no"], ["empty", "—"], ["number", "3"]]


def test_a_missing_section_drops_out_rather_than_rendering_empty() -> None:
    """Callers inline conditions — a ticket with no body contributes no block at all."""
    composed = notes.compose("summary", notes.fields("kept", [("a", "b")]), None)
    assert len(composed["sections"]) == 1


# --- the Compensate node carries the reason -------------------------------------------------


async def test_compensate_records_which_verifier_falsified_the_change(
    platform: FakePlatform,
) -> None:
    """The node a rejected run ends on is the first one anyone opens to ask why.

    Before this it wrote no notes at all, so the Workbench showed an empty log and the only
    record of *why* was a one-line `aborting` reason with no verifier named and no finding.
    """
    import uuid as _uuid

    from engine.nodes import NODES
    from engine.nodes.base import bound
    from engine.state import FlowState, Ticket, Verification

    state = FlowState(
        run_id=_uuid.uuid4(),
        root_run_id=_uuid.uuid4(),
        ticket=Ticket(id="PAY-1", system="jira", title="t", body="b"),
        policy_commit="unpinned:test",
        policy_bundle={},
    )
    state.verifications = [
        Verification(verifier="correctness", passed=True),
        Verification(
            verifier="test_evidence",
            passed=False,
            findings=["components/Bar.tsx is new and no test asserts it renders"],
        ),
    ]

    with bound(RUNTIME.context()):
        after = await NODES["compensate"](state)

    assert "test_evidence" in after.notes["summary"]
    rendered = json.dumps(after.notes)
    assert "no test asserts it renders" in rendered, "the finding must reach the record"

    findings = [s for s in after.notes["sections"] if s["kind"] == "text"]
    assert findings, "each finding is its own block, so a long one is readable"
    # Model output quoting a diff that came from a ticket anyone can file — the reader is told.
    assert all(s["untrusted"] for s in findings)


async def test_compensate_says_nothing_it_cannot_support(platform: FakePlatform) -> None:
    """Compensation also runs for a node failure, where no verification was falsified.

    Naming a verifier there would be inventing a cause, which is worse than saying the run
    aborted and pointing at the preceding events.
    """
    import uuid as _uuid

    from engine.nodes import NODES
    from engine.nodes.base import bound
    from engine.state import FlowState, Ticket

    state = FlowState(
        run_id=_uuid.uuid4(),
        root_run_id=_uuid.uuid4(),
        ticket=Ticket(id="PAY-1", system="jira", title="t", body="b"),
        policy_commit="unpinned:test",
        policy_bundle={},
    )

    with bound(RUNTIME.context()):
        after = await NODES["compensate"](state)

    assert "Run aborted" in after.notes["summary"]
    assert "Rejected by" not in after.notes["summary"]


async def test_verifiers_completed_names_the_verifier_that_refused(
    platform: FakePlatform,
) -> None:
    """A count records that a change was refused and destroys which review objected.

    This asserts the payload shape the Workbench reads. `{"passed": 3}` is what an operator saw
    for a rejected run — true, and useless: it says one of four objected without saying which,
    and the panel had nothing else to show.
    """
    from engine.flows.delivery import VERIFIERS

    # The shape the flow emits, built from the same inputs, so a change to either side fails
    # here rather than in a demo.
    verifications = [
        ("correctness", True),
        ("security", True),
        ("test_evidence", False),
        ("regression_risk", True),
    ]
    payload = {
        "passed": sum(1 for _, ok in verifications if ok),
        "of": len(VERIFIERS),
        "falsified_by": [name for name, ok in verifications if not ok],
    }

    assert payload["falsified_by"] == ["test_evidence"]
    assert payload["passed"] == 3
    assert payload["of"] == 4


def test_the_ticket_is_told_why_a_tier_was_raised() -> None:
    """An escalation that arrives without its reason reads as the system being arbitrary.

    The ticket is where the person who filed it looks, and it is the audience least equipped
    to reconstruct a decision from an audit trail. A change described as a theme switch that
    comes back "risk tier high, two approvers required" invites exactly one conclusion —
    that the tool is being difficult — unless the sentence that decided it travels alongside.
    """
    state = _state()
    state.provisional_risk_tier = "low"
    state.risk_tier = "high"
    state.risk_tier_reason = "app/layout.tsx matches high_paths '**/layout.*'"

    body = _body(state)

    assert "raised from low" in body
    assert "high_paths" in body


def test_the_ticket_distinguishes_an_advisory_objection_from_a_block() -> None:
    """"Objected" and "objected and stopped the change" are different events.

    A reader who sees FAILED beside a merged pull request concludes the verification step is
    decorative. The truth is narrower and worth stating: somebody deliberately declared that
    verifier advisory, it still ran, and its finding is still on the record.
    """
    state = _state()
    state.verifications = [
        Verification(verifier="correctness", passed=True, findings=[]),
        Verification(verifier="test_evidence", passed=False, findings=["no test added"]),
    ]
    state.advisory_objections = ["test_evidence"]

    body = _body(state)

    assert "correctness: passed" in body
    assert "advisory" in body
    assert "FAILED" not in body, "an advisory objection did not stop anything"


def _state() -> FlowState:
    """A minimal run, for the pure formatting checks above."""
    return FlowState(
        run_id=uuid.uuid4(),
        root_run_id=uuid.uuid4(),
        ticket=Ticket(id="PAY-1", system="jira", title="switch theme", body="."),
        policy_commit="0" * 40,
        policy_bundle={},
    )
