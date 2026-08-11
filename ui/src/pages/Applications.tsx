/**
 * Register and list applications.
 *
 * `integration_model` is a required choice with no pre-selected value. ADR 0004 rejected
 * detecting the platform and choosing automatically: which control point governs a
 * deployment is a governance decision, so it must be declared, reviewed, and visible. The
 * probe on the detail page may refuse the declaration; it may not make it.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useCan } from "../auth";
import {
  Banner,
  Button,
  Card,
  Empty,
  Field,
  Input,
  PageHeader,
  Row,
  Select,
  Table,
} from "../components/ui";
import type { Application, IntegrationModel } from "../types";

const MODELS: { value: IntegrationModel; label: string; note: string }[] = [
  {
    value: "gated_deployment",
    label: "gated_deployment",
    note: "The platform pauses its own deployment and asks us. Least invasive, nearly the strongest.",
  },
  {
    value: "gated_merge",
    label: "gated_merge",
    note: "We gate the merge. What the pipeline then deploys is observed, not authorised.",
  },
  {
    value: "kuwarden_deploys",
    label: "kuwarden_deploys",
    note: "We deploy. Strongest audit, but the existing pipeline must be restricted first.",
  },
];

export function Applications() {
  // Hiding the form is a courtesy; the server rejects the POST regardless. A viewer
  // presented with a form they cannot submit learns nothing useful.
  const canAdmin = useCan("admin");
  const [apps, setApps] = useState<Application[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: "",
    scm_provider: "github" as "github" | "azure_repos",
    org: "",
    repo: "",
    project: "",
    integration_model: "" as IntegrationModel | "",
  });

  async function refresh() {
    try {
      setApps(await api.listApplications());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function register() {
    if (!form.integration_model) {
      setError("Choose an integration model — it is never defaulted.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.registerApplication({
        ...form,
        integration_model: form.integration_model,
        project: form.project || null,
      });
      setForm({ ...form, name: "", org: "", repo: "", project: "" });
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const selected = MODELS.find((m) => m.value === form.integration_model);

  return (
    <div>
      <PageHeader
        title="Applications"
        description="Each registered application declares its own repository, its ticketing, and where its control point sits."
      />

      <div className="space-y-6">
      {canAdmin && (
      <Card
        title="Register an application"
        description="Connect a repository. Credentials and the capability probe come next, on the application's own page."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name">
            <Input
              value={form.name}
              placeholder="payments-service"
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </Field>
          <Field label="Source control">
            <Select
              value={form.scm_provider}
              onChange={(e) =>
                setForm({ ...form, scm_provider: e.target.value as "github" | "azure_repos" })
              }
            >
              <option value="github">GitHub</option>
              <option value="azure_repos">Azure Repos</option>
            </Select>
          </Field>
          <Field label="Organisation">
            <Input
              value={form.org}
              placeholder="acme"
              onChange={(e) => setForm({ ...form, org: e.target.value })}
            />
          </Field>
          <Field label="Repository">
            <Input
              value={form.repo}
              placeholder="payments-service"
              onChange={(e) => setForm({ ...form, repo: e.target.value })}
            />
          </Field>
          {form.scm_provider === "azure_repos" && (
            <Field label="Project" hint="Azure Repos nests repositories under a project.">
              <Input
                value={form.project}
                placeholder="Payments"
                onChange={(e) => setForm({ ...form, project: e.target.value })}
              />
            </Field>
          )}
          <Field
            label="Integration model"
            hint={selected?.note ?? "Declared, never inferred — ADR 0004."}
          >
            <Select
              value={form.integration_model}
              onChange={(e) =>
                setForm({ ...form, integration_model: e.target.value as IntegrationModel })
              }
            >
              <option value="">Choose…</option>
              {MODELS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        {error && (
          <div className="mt-4">
            <Banner tone="error">{error}</Banner>
          </div>
        )}

        <div className="mt-6">
          <Button variant="primary" onClick={register} disabled={busy || !form.name}>
            {busy ? "Registering…" : "Register"}
          </Button>
        </div>
      </Card>
      )}

      <Card title="Registered" description={`${apps.length} application(s)`}>
        {apps.length === 0 ? (
          <Empty>Nothing registered yet.</Empty>
        ) : (
          <Table head={["Name", "Repository", "Control point", ""]}>
            {apps.map((app) => (
              <Row key={app.id}>
                <td className="py-3 font-medium">{app.name}</td>
                <td className="py-3 text-[13px] text-muted">{app.repo_url}</td>
                <td className="mono py-3 text-[12px]">{app.integration_model}</td>
                <td className="py-3 text-right">
                  <Link
                    to={`/applications/${app.id}`}
                    className="text-[13px] text-accent hover:underline"
                  >
                    Configure
                  </Link>
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
