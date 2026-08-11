/**
 * Sign in.
 *
 * One message for every kind of failure, matching the server: distinguishing "no such
 * account" from "wrong password" hands an unauthenticated caller a way to enumerate valid
 * addresses. The empty-deployment case is different and is explained, because "nobody has
 * set this up yet" is not a secret and leaving an operator guessing helps nobody.
 */

import { useState } from "react";
import { api, ApiError } from "../api";
import { useSession } from "../auth";
import { Banner, Button, Field, Input } from "../components/ui";

export function SignIn() {
  const { signIn, configured } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <div className="mb-9 text-center">
          <div className="text-xl font-semibold tracking-[-0.02em]">KuWarden</div>
          <p className="mt-1.5 text-[13px] text-muted">
            Governed, auditable change delivery
          </p>
        </div>

        {!configured ? (
          <div className="rounded-2xl border border-line bg-surface p-7">
            <h1 className="text-[15px] font-semibold">No accounts yet</h1>
            <p className="mt-2 text-[13px] leading-relaxed text-muted">
              This deployment has no users. Create the first one from the host — deliberately
              not from here, because a web form that creates the first admin means whoever
              reaches a fresh deployment first owns it.
            </p>
            <pre className="mono mt-4 overflow-x-auto rounded-xl bg-canvas px-3.5 py-3 text-[12px]">
              uv run python -m engine.api create-user you@example.com admin
            </pre>
          </div>
        ) : (
          <form
            onSubmit={submit}
            className="rounded-2xl border border-line bg-surface p-7"
          >
            <div className="space-y-4">
              <Field label="Email">
                <Input
                  type="email"
                  value={email}
                  autoComplete="username"
                  autoFocus
                  onChange={(e) => setEmail(e.target.value)}
                />
              </Field>
              <Field label="Password">
                <Input
                  type="password"
                  value={password}
                  autoComplete="current-password"
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
            </div>

            {error && (
              <div className="mt-4">
                <Banner tone="error">{error}</Banner>
              </div>
            )}

            <div className="mt-6">
              <Button type="submit" variant="primary" disabled={busy || !email || !password}>
                {busy ? "Signing in…" : "Sign in"}
              </Button>
            </div>
          </form>
        )}

        <p className="mt-6 text-center text-[12px] text-faint">
          Local accounts. No external identity provider — this deployment may be air-gapped.
        </p>
      </div>
    </div>
  );
}

/** Kept beside the sign-in form so the two stay consistent if the endpoint changes. */
export async function isConfigured(): Promise<boolean> {
  return (await api.bootstrapState()).configured;
}
