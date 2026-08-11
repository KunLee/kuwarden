/**
 * The role graph — read-only, deliberately.
 *
 * ADR 0003 makes `policy.yaml` version-controlled and change-reviewed: editing it is a
 * governance event. A button here that granted a capability would destroy the property that
 * makes the role graph worth handing to an auditor. When this page gains write support it
 * will open a pull request against `policy.yaml`; it will never apply a change.
 */

import { Card, PageHeader } from "../components/ui";

export function Policy() {
  return (
    <div>
      <PageHeader
        title="Policy"
        description="The role graph — who exists, and what each of them may do. Read-only: editing policy.yaml is a governance event, not a button."
      />
      <div className="space-y-6">
      <Card title="Role graph" description="policy.yaml — who exists, and what they may do">
        <p className="text-sm text-muted">
          Not wired up yet. The schema loader and constraint evaluator do not exist, so the
          constraints in <span className="mono text-xs">policy.example.yaml</span> are still
          decorative.
        </p>
      </Card>

      <Card title="What this page will do">
        <ul className="space-y-2.5 text-sm text-muted">
          <li>
            <strong className="text-ink">Read</strong> — identities, capabilities,
            bindings, approval authority, budgets, protected paths.
          </li>
          <li>
            <strong className="text-ink">Simulate</strong> — evaluate the
            constraints against a proposed edit and show what would break, before a pull
            request exists. Granting <span className="mono text-xs">deploy.*</span> to a node
            that runs a model should fail{" "}
            <span className="mono text-xs">no-llm-holds-deploy</span> here, not in CI an hour
            later.
          </li>
          <li>
            <strong className="text-ink">Propose</strong> — open a pull request.
            Never apply. A capability granted by clicking is a capability with no audit trail.
          </li>
        </ul>
      </Card>
      </div>
    </div>
  );
}
