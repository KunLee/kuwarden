"""Agent nodes. The LLM lives here and nowhere else.

Importing this package registers every node, which is what makes `REGISTRY` complete enough
for the class-enforcement tests to be meaningful.
"""

from __future__ import annotations

from engine.nodes import verifiers
from engine.nodes.base import REGISTRY, NodeFn, NodeSpec, current_node, node
from engine.nodes.build_test import build_test
from engine.nodes.coder import coder
from engine.nodes.compensate import compensate
from engine.nodes.planner import planner
from engine.nodes.release import release
from engine.nodes.reporter import reporter
from engine.nodes.triage import triage

NODES: dict[str, NodeFn] = {
    "triage": triage,
    "planner": planner,
    "coder": coder,
    "build_test": build_test,
    "verifier.correctness": verifiers.correctness,
    "verifier.security": verifiers.security,
    "verifier.test_evidence": verifiers.test_evidence,
    "verifier.regression_risk": verifiers.regression_risk,
    "release": release,
    "compensate": compensate,
    "reporter": reporter,
}

__all__ = ["NODES", "REGISTRY", "NodeFn", "NodeSpec", "current_node", "node"]
