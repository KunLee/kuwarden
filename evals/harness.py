"""Run the golden task set against the verifier nodes and report the rates.

Node-level rather than end to end. A verifier reads `ticket`, `proposed_edits` and `ci_result`
and nothing else, so a case can construct those directly — no Temporal, no sandbox, no
repository, no Coder. One model call per verifier per case, which is what keeps the set cheap
enough that anyone actually runs it.

**This spends money.** Every case is a real completion against the model the application's
configuration names. That is the entire point: a mocked verifier measures the mock.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from engine.nodes import NODES
from engine.nodes.base import bound
from engine.state import CIResult, FlowState, ProposedEdit, Ticket

CASES = Path(__file__).parent / "cases"

#: The categories, and what each is for. `accept` is the control: without it, a verifier that
#: rejects everything scores perfectly and would still block every change in production.
CATEGORIES = ("reject", "injection", "accept")

REQUIRED_FIELDS = frozenset(
    {"id", "category", "rationale", "verifiers", "expect", "ticket", "edits"}
)


@dataclass(frozen=True)
class Case:
    """One evaluation case: constructed input, and the verdict a human decided is correct."""

    id: str
    category: str
    rationale: str
    verifiers: tuple[str, ...]
    #: "rejected" — at least one named verifier must fail it.
    #: "accepted" — every named verifier must pass it.
    expect: str
    ticket: Ticket
    edits: tuple[ProposedEdit, ...]
    #: The commit the change was made against. Optional, and it should not be: see `state`.
    base_commit: str = ""

    def state(self) -> FlowState:
        """The `FlowState` a verifier would receive for this case.

        `ci_result` is a passing external anchor on purpose. It removes "the tests were graded
        by our own sandbox" as an available reason to reject, so a rejection can only have come
        from reading the change itself.

        **`base_commit` is what makes this the same verifier that runs in production.** Since
        2026-08-24 a verifier is given the repository at the base commit — precisely because,
        without it, two of them rejected a valid change for referring to a file they could not
        open. This harness supplied no base commit until 2026-09-02, so it was grading a
        verifier that had strictly less context than the one under test, and it failed a case
        for exactly the reason that change was made. A set that measures a weaker verifier than
        the one that ships is measuring the wrong thing in the flattering direction.

        A case without one still runs, and its result means less: state it in the case's
        `rationale` rather than leaving a reader to infer it from an absent field.
        """
        return FlowState(
            base_commit=self.base_commit,
            run_id=uuid.uuid4(),
            root_run_id=uuid.uuid4(),
            ticket=self.ticket,
            policy_commit="eval:not-a-run",
            policy_bundle={},
            proposed_edits=list(self.edits),
            ci_result=CIResult(exit_code=0, source="ci"),
        )


@dataclass
class Result:
    case: Case
    verdicts: dict[str, bool] = field(default_factory=dict)
    findings: dict[str, list[str]] = field(default_factory=dict)
    error: str | None = None

    @property
    def passed(self) -> bool:
        """Did the case get the verdict a human said it should?"""
        if self.error is not None or not self.verdicts:
            return False
        if self.case.expect == "rejected":
            # One is enough. Requiring all of them would fail a case the security verifier
            # caught and the correctness one had no opinion about.
            return any(not ok for ok in self.verdicts.values())
        return all(self.verdicts.values())


def load(directory: Path = CASES) -> list[Case]:
    """Read every case, refusing anything malformed rather than skipping it.

    A case that silently fails to load is a case that silently stops protecting anything.
    """
    cases: list[Case] = []
    for path in sorted(directory.glob("*.yaml")):
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: expected a mapping")
        missing = REQUIRED_FIELDS - set(raw)
        if missing:
            raise ValueError(f"{path.name}: missing {', '.join(sorted(missing))}")
        if raw["category"] not in CATEGORIES:
            raise ValueError(f"{path.name}: unknown category {raw['category']!r}")
        if raw["expect"] not in ("rejected", "accepted"):
            raise ValueError(f"{path.name}: expect must be rejected or accepted")

        ticket = raw["ticket"]
        cases.append(
            Case(
                id=str(raw["id"]),
                category=str(raw["category"]),
                rationale=str(raw["rationale"]).strip(),
                verifiers=tuple(str(v) for v in raw["verifiers"]),
                expect=str(raw["expect"]),
                ticket=Ticket(
                    id=str(ticket["id"]),
                    system="eval",
                    title=str(ticket["title"]),
                    body=str(ticket["body"]),
                    acceptance_criteria=[str(c) for c in ticket.get("acceptance_criteria", [])],
                ),
                edits=tuple(
                    ProposedEdit(path=str(e["path"]), content=str(e["content"]))
                    for e in raw["edits"]
                ),
                base_commit=str(raw.get("base_commit", "")),
            )
        )
    return cases


async def run_case(case: Case, context: Any) -> Result:
    """Execute every verifier the case names, and collect their verdicts."""
    result = Result(case=case)
    for name in case.verifiers:
        node_id = f"verifier.{name}"
        fn = NODES.get(node_id)
        if fn is None:
            result.error = f"unknown verifier {node_id}"
            return result
        try:
            with bound(context):
                after = await fn(case.state())
        except Exception as exc:  # noqa: BLE001 - a failed call is a result, not a crash
            result.error = f"{type(exc).__name__}: {exc}"
            return result
        for verification in after.verifications:
            result.verdicts[verification.verifier] = verification.passed
            result.findings[verification.verifier] = list(verification.findings)
    return result


def report(results: list[Result]) -> int:
    """Print the scoreboard. Returns how many cases did not meet expectation."""
    by_category: dict[str, list[Result]] = {}
    for r in results:
        by_category.setdefault(r.case.category, []).append(r)

    failed = 0
    for category in CATEGORIES:
        rows = by_category.get(category, [])
        if not rows:
            continue
        ok = sum(1 for r in rows if r.passed)
        print(f"\n{category}: {ok}/{len(rows)}")
        for r in rows:
            mark = "ok  " if r.passed else "MISS"
            verdicts = ", ".join(
                f"{v}=" + ("pass" if p else "REJECT") for v, p in sorted(r.verdicts.items())
            )
            print(f"  {mark} {r.case.id:34} {r.error or verdicts}")
            if not r.passed:
                failed += 1

    print(f"\n{len(results) - failed}/{len(results)} cases met expectation")
    # The number alone is not a result. Whoever runs this has to write down the date and the
    # model each node was using, or the next run has nothing to compare against.
    print("Record this in EVALUATION.md with today's date and the per-node model.")
    return failed


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run the golden task set.")
    parser.add_argument("--category", choices=CATEGORIES, help="run only this category")
    parser.add_argument("--app", required=True, help="registered application whose config to use")
    args = parser.parse_args()

    cases = [c for c in load() if args.category is None or c.category == args.category]
    if not cases:
        print("no cases matched")
        return 0

    # Imported here rather than at module scope: the case-loading tests import this module and
    # must not need a database or a credential store to do it.
    from engine.activities.nodes import RUNTIME
    from engine.config_store import resolve
    from engine.db import connect
    from engine.devenv import load_dotenv

    load_dotenv()
    async with connect() as conn:
        app_id = await conn.fetchval("SELECT id FROM app_registry WHERE name = $1", args.app)
    if app_id is None:
        print(f"no application named {args.app} is registered")
        return 1

    config = await resolve(app_id)
    if config.llm is None:
        print(f"{args.app} declares no llm section; the verifiers cannot run")
        return 1

    RUNTIME.configure(config)
    context = RUNTIME.context(app_id, config=config)
    # Printed, not assumed. A score is meaningless without the model that produced it, and
    # this is the line whoever runs it copies into EVALUATION.md.
    model = config.llm.for_node("verifier.security").model
    print(f"application {args.app}; verifiers on {model}")

    results = [await run_case(c, context) for c in cases]
    return report(results)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
