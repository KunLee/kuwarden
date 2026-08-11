/**
 * User management.
 *
 * Roles are not a convenience feature. ADR 0003's `no-agent-self-approval` and
 * `prod-requires-two-humans` constraints require approvers to be real, distinct, identifiable
 * humans — without accounts, an approval gate records that *something* clicked approve, which
 * is not evidence of review.
 */

import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { useSession } from "../auth";
import {
  Banner,
  Button,
  Card,
  Empty,
  Field,
  Input,
  PageHeader,
  RoleBadge,
  Row,
  Select,
  Table,
} from "../components/ui";
import type { Role, User } from "../types";

const ROLE_NOTES: Record<Role, string> = {
  viewer: "Reads runs, applications and the audit trail. Cannot configure or approve.",
  approver: "Everything a viewer can do, plus deciding at approval gates.",
  admin: "Everything, plus registering applications and storing credentials.",
};

export function Users() {
  const { principal } = useSession();
  const [users, setUsers] = useState<User[]>([]);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [draft, setDraft] = useState({
    email: "",
    display_name: "",
    password: "",
    role: "viewer" as Role,
  });

  async function refresh() {
    try {
      setUsers(await api.listUsers());
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function add() {
    setMessage(null);
    try {
      await api.addUser(draft);
      setDraft({ email: "", display_name: "", password: "", role: "viewer" });
      setMessage({ tone: "ok", text: "Account created." });
      await refresh();
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    }
  }

  async function disable(user: User) {
    if (!confirm(`Disable ${user.email}? Their sessions end immediately.`)) return;
    try {
      await api.disableUser(user.id);
      await refresh();
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    }
  }

  return (
    <div>
      <PageHeader
        title="Users"
        description="Local accounts. Approval gates are only evidence of review if the approver is a real, identifiable person."
      />

      <div className="space-y-6">
        {message && <Banner tone={message.tone}>{message.text}</Banner>}

        <Card title="Add an account">
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="Email">
              <Input
                type="email"
                value={draft.email}
                placeholder="alice@acme.test"
                onChange={(e) => setDraft({ ...draft, email: e.target.value })}
              />
            </Field>
            <Field label="Display name">
              <Input
                value={draft.display_name}
                placeholder="Alice Chen"
                onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
              />
            </Field>
            <Field
              label="Password"
              hint="At least 12 characters. Length is the only rule — composition rules push people towards predictable substitutions."
            >
              <Input
                type="password"
                value={draft.password}
                autoComplete="new-password"
                onChange={(e) => setDraft({ ...draft, password: e.target.value })}
              />
            </Field>
            <Field label="Role" hint={ROLE_NOTES[draft.role]}>
              <Select
                value={draft.role}
                onChange={(e) => setDraft({ ...draft, role: e.target.value as Role })}
              >
                <option value="viewer">viewer</option>
                <option value="approver">approver</option>
                <option value="admin">admin</option>
              </Select>
            </Field>
          </div>

          <div className="mt-6">
            <Button
              variant="primary"
              onClick={add}
              disabled={!draft.email || !draft.display_name || draft.password.length < 12}
            >
              Create account
            </Button>
          </div>
        </Card>

        <Card title="Accounts" description={`${users.length} total`}>
          {users.length === 0 ? (
            <Empty>No accounts.</Empty>
          ) : (
            <Table head={["Name", "Email", "Role", "Last signed in", ""]}>
              {users.map((user) => (
                <Row key={user.id}>
                  <td className="py-3 font-medium">
                    {user.display_name}
                    {user.id === principal?.id && (
                      <span className="ml-2 text-[12px] text-faint">you</span>
                    )}
                  </td>
                  <td className="py-3 text-muted">{user.email}</td>
                  <td className="py-3">
                    <RoleBadge role={user.role} />
                  </td>
                  <td className="py-3 text-[13px] text-muted">
                    {user.last_login_at
                      ? new Date(user.last_login_at).toLocaleString()
                      : "never"}
                  </td>
                  <td className="py-3 text-right">
                    {user.disabled_at ? (
                      <span className="text-[12px] text-faint">disabled</span>
                    ) : (
                      user.id !== principal?.id && (
                        <button
                          onClick={() => disable(user)}
                          className="text-[12px] text-red-600 hover:underline dark:text-red-400"
                        >
                          Disable
                        </button>
                      )
                    )}
                  </td>
                </Row>
              ))}
            </Table>
          )}
        </Card>
      </div>
    </div>
  );
}
