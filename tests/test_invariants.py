"""The invariants from CLAUDE.md, as tests rather than as prose.

A governance rule that only exists in a document is a rule that will be violated without
anyone noticing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from engine.adapters.llm import assert_may_call_llm
from engine.errors import InvariantViolation, RiskTierLowered
from engine.nodes import NODES, REGISTRY
from engine.nodes.base import executing
from engine.policy.protected_paths import DEFAULT_PROTECTED_PATHS, ProtectedPaths
from engine.policy.tiering import assert_not_lowered, raise_to, required_approvals
from engine.state import NodeClass

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- invariant 1: the Flow Engine contains no LLM ----------------------------------------


def test_flow_engine_may_not_call_a_model() -> None:
    """No node context means the caller is engine plumbing, which never gets a model."""
    with pytest.raises(InvariantViolation, match="outside any node"):
        assert_may_call_llm()


@pytest.mark.parametrize(
    "node_id", [n for n, s in REGISTRY.items() if s.node_class is NodeClass.DETERMINISTIC]
)
def test_deterministic_nodes_may_not_call_a_model(node_id: str) -> None:
    with executing(REGISTRY[node_id]), pytest.raises(InvariantViolation, match="may not call"):
        assert_may_call_llm()


@pytest.mark.parametrize(
    "node_id", [n for n, s in REGISTRY.items() if s.node_class is not NodeClass.DETERMINISTIC]
)
def test_generative_and_verifier_nodes_pass_the_guard(node_id: str) -> None:
    with executing(REGISTRY[node_id]):
        assert_may_call_llm()


# --- invariant 4: verifiers get a fresh context -------------------------------------------


def test_every_verifier_is_classified_verifier() -> None:
    verifiers = [s for n, s in REGISTRY.items() if n.startswith("verifier.")]
    assert len(verifiers) == 4
    assert all(s.node_class is NodeClass.VERIFIER for s in verifiers)


# --- invariant 5: risk_tier may only be raised --------------------------------------------


def test_tier_is_raised_not_lowered() -> None:
    assert raise_to("low", "high") == "high"
    assert raise_to("high", "low") == "high"
    assert raise_to("medium", "medium") == "medium"


def test_lowering_is_rejected_where_it_would_be_a_defect() -> None:
    assert_not_lowered("low", "high")
    with pytest.raises(RiskTierLowered):
        assert_not_lowered("high", "medium")


def test_gate_depth_follows_tier() -> None:
    assert required_approvals("low") == 0
    assert required_approvals("medium") == 1
    assert required_approvals("high") == 2


# --- invariant 10: agents never write protected paths -------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        ".github/workflows/nested/deep.yml",
        ".github/actions/build/action.yml",
        "Jenkinsfile",
        "charts/app/values.yaml",
        "terraform/main.tf",
        "envs/prod.tfvars",
        "kuwarden.yaml",
        "services/payments/kuwarden.yaml",
        "policy.yaml",
    ],
)
def test_protected_paths_are_denied(path: str) -> None:
    assert ProtectedPaths().matches(path) is not None


@pytest.mark.parametrize(
    "path",
    [
        "src/main.py",
        "README.md",
        "docs/github/workflows.md",
        "charts.py",
        "tests/test_terraform_helpers.py",
    ],
)
def test_ordinary_paths_are_allowed(path: str) -> None:
    assert ProtectedPaths().matches(path) is None


def test_single_star_does_not_cross_a_separator() -> None:
    """`fnmatch` would match here, which is why it is not used."""
    assert ProtectedPaths(patterns=("charts/*",)).matches("charts/app/values.yaml") is None
    assert ProtectedPaths(patterns=("charts/**",)).matches("charts/app/values.yaml") is not None


def test_enforced_protected_paths_match_policy_example() -> None:
    """The enforced copy and the documented one must not drift apart."""
    text = (REPO_ROOT / "docs" / "reference" / "policy.example.yaml").read_text(encoding="utf-8")
    block = re.search(r"^protected_paths:\n((?:\s+-.*\n|\s*#.*\n|\s*\n)*)", text, re.MULTILINE)
    assert block is not None, "protected_paths block not found in policy.example.yaml"
    documented = set(re.findall(r'-\s*"([^"]+)"', block.group(1)))
    assert documented == set(DEFAULT_PROTECTED_PATHS)


# --- the node contract --------------------------------------------------------------------


def test_registry_and_dispatch_agree() -> None:
    assert set(NODES) == set(REGISTRY)


def test_topology_has_the_expected_nodes() -> None:
    assert set(REGISTRY) == {
        "triage",
        "planner",
        "coder",
        "build_test",
        "verifier.correctness",
        "verifier.security",
        "verifier.test_evidence",
        "verifier.regression_risk",
        "release",
        "compensate",
        "reporter",
    }
