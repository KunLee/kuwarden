"""Auto-merge — ADR 0004 model B's control point actually being exercised.

`gated_merge` is defined as KuWarden *holding merge authority*. Until now it opened a pull
request and stopped, so the model was declared and not implemented. These tests cover the
conditions under which it now merges, and — more importantly — the ones under which it must
not, because every one of those is a route by which unreviewed code reaches a default branch.
"""

from __future__ import annotations

import uuid

import pytest

from engine.adapters.protocols import IntegrationModel
from engine.config import AppConfig, AutoMergeConfig, ConfigError, parse
from engine.nodes.release import _may_merge
from engine.state import CIResult, Diff, FileChange, FlowState, Ticket
from tests.conftest import KUWARDEN_YAML


def _state(
    *,
    tier: str = "low",
    files: int = 1,
    anchor: str | None = "ci",
) -> FlowState:
    state = FlowState(
        run_id=uuid.uuid4(),
        root_run_id=uuid.uuid4(),
        ticket=Ticket(id="PAY-1", system="azure_devops", title="t", body="b"),
        policy_commit="unpinned:test",
        policy_bundle={},
    )
    state.risk_tier = tier  # type: ignore[assignment]
    state.branch, state.head_commit, state.base_branch = "kuwarden/1", "c0ffee", "main"
    state.diff = Diff(
        files=[FileChange(path=f"src/{i}.js", added=1, removed=0) for i in range(files)]
    )
    if anchor is not None:
        state.ci_result = CIResult(exit_code=0, source=anchor)  # type: ignore[arg-type]
    else:
        state.ci_detail = "no pipeline run appeared within 90s"
    return state


def _config(**overrides: object) -> AppConfig:
    """The example config, with delivery overridden to gated_merge plus a policy."""
    cfg = parse(KUWARDEN_YAML)
    object.__setattr__(cfg, "integration_model", IntegrationModel.GATED_MERGE)
    object.__setattr__(cfg, "auto_merge", AutoMergeConfig(enabled=True, **overrides))  # type: ignore[arg-type]
    return cfg


def test_a_low_risk_anchored_change_within_the_file_limit_merges() -> None:
    verdict = _may_merge(_config(max_files_changed=3), _state(tier="low", files=2))
    assert verdict.allowed, verdict.detail


def test_auto_merge_is_off_unless_declared() -> None:
    """Not a default anyone should acquire by upgrading."""
    cfg = parse(KUWARDEN_YAML)
    object.__setattr__(cfg, "integration_model", IntegrationModel.GATED_MERGE)
    object.__setattr__(cfg, "auto_merge", AutoMergeConfig())
    assert not _may_merge(cfg, _state()).allowed


def test_a_tier_above_the_ceiling_is_left_for_a_human() -> None:
    verdict = _may_merge(_config(max_risk_tier="low"), _state(tier="high"))
    assert not verdict.allowed
    assert "above the low ceiling" in verdict.detail


def test_too_many_files_is_left_for_a_human() -> None:
    verdict = _may_merge(_config(max_files_changed=2), _state(files=7))
    assert not verdict.allowed
    assert "7 files changed" in verdict.detail


def test_a_sandbox_only_verdict_does_not_reach_the_default_branch() -> None:
    """The clause that matters most.

    Without it a change reaches `main` graded only by the sandbox that produced it — the same
    system marking its own work, which is the arrangement invariant 3 exists to refuse.
    """
    verdict = _may_merge(_config(), _state(anchor="sandbox"))
    assert not verdict.allowed
    assert "no external anchor" in verdict.detail


def test_no_verdict_at_all_does_not_reach_the_default_branch() -> None:
    """Absence is not a pass here either."""
    verdict = _may_merge(_config(), _state(anchor=None))
    assert not verdict.allowed
    assert "no external anchor" in verdict.detail


def test_the_anchor_requirement_can_be_waived_only_deliberately() -> None:
    """It is a line someone has to type, and the default keeps the requirement."""
    assert AutoMergeConfig().require_external_anchor is True
    verdict = _may_merge(_config(require_external_anchor=False), _state(anchor="sandbox"))
    assert verdict.allowed


def test_a_human_rejection_outranks_any_policy_ceiling() -> None:
    from engine.state import Approval

    state = _state()
    state.approvals = [
        Approval(
            principal="someone@test.invalid",
            approved=False,
            risk_tier="low",
            evidence_digest="sha256:what-they-were-shown",
        )
    ]
    verdict = _may_merge(_config(), state)
    assert not verdict.allowed
    assert "rejected" in verdict.detail


def test_auto_merge_may_not_be_declared_under_another_integration_model() -> None:
    """ADR 0004 gives merge authority to model B alone.

    Ignoring the key would leave a config that appears to grant a control the deployment does
    not have, which is worse than refusing to start.
    """
    yaml = KUWARDEN_YAML.replace(
        "integration_model: gated_merge",
        "integration_model: gated_deployment",
    ).replace(
        "delivery:",
        "delivery:\n  auto_merge:\n    enabled: true",
        1,
    )
    with pytest.raises(ConfigError, match="only available under integration_model: gated_merge"):
        parse(yaml)
