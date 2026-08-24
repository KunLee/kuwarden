/**
 * One application's `kuwarden.yaml`, edited in the Workbench.
 *
 * Configuration is operator-owned and stored per application — ADR 0008. It is deliberately
 * *not* read from the application's repository: every setting in this file decides a verdict
 * (`test_command` alone decides what "the tests passed" means), and a team able to edit it in
 * their own pull request could grant themselves a passing gate.
 *
 * A textarea rather than a form. The schema has thirty-odd fields, half of them optional and
 * several of them lists; a generated form would lag the schema and lose the comments, which
 * in this file carry the reasoning for the settings. The server parses before storing, so
 * "it looked fine in the box" is not the failure mode.
 */

import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { useCan } from "../auth";
import { Banner, Button, Card } from "./ui";

export function Configuration({ appId }: { appId: string }) {
  const canAdmin = useCan("admin");
  const [yaml, setYaml] = useState("");
  const [stored, setStored] = useState<boolean | null>(null);
  const [origin, setOrigin] = useState<string>("");
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const current = await api.readConfig(appId);
        setStored(current.stored);
        setYaml(current.yaml ?? "");
        setOrigin(
          current.stored
            ? `stored ${current.updated_at ? new Date(current.updated_at).toLocaleString() : ""}` +
              (current.updated_by ? ` by ${current.updated_by}` : "")
            : (current.detail ?? ""),
        );
      } catch (e) {
        setMessage({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
      }
    })();
  }, [appId]);

  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.storeConfig(appId, yaml);
      setStored(true);
      setOrigin("stored just now");
      // Names the application the file declares, not the one on this page: they must agree,
      // and seeing the parsed name is how an operator notices they pasted the wrong file.
      setMessage({ tone: "ok", text: `Stored. This configuration declares ${result.application}.` });
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Configuration"
      description="This application's kuwarden.yaml. Parsed before it is stored — a run never discovers a typo."
      actions={
        canAdmin && (
          <Button variant="primary" onClick={save} disabled={busy || !yaml.trim()}>
            {busy ? "Saving…" : "Save configuration"}
          </Button>
        )
      }
    >
      {stored === false && (
        <div className="mb-4">
          {/* Stated rather than left blank: "not configured" and "configured as empty" are
              materially different, and the first one means runs are governed by whatever
              file the worker happens to have. */}
          <Banner tone="warn">
            No configuration stored. Runs for this application fall back to the worker&apos;s own
            file, which can only ever be correct for one application.
          </Banner>
        </div>
      )}

      <textarea
        value={yaml}
        onChange={(e) => setYaml(e.target.value)}
        disabled={!canAdmin}
        spellCheck={false}
        rows={24}
        className="mono w-full rounded-lg border border-line bg-surface p-3 text-xs leading-relaxed text-body disabled:opacity-60"
        placeholder="version: 1&#10;app:&#10;  name: your-application"
      />

      <p className="mt-2 text-xs text-faint">{origin}</p>

      {message && (
        <div className="mt-4">
          <Banner tone={message.tone}>{message.text}</Banner>
        </div>
      )}
    </Card>
  );
}
