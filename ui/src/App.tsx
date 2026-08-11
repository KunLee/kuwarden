/**
 * The Workbench shell.
 *
 * Nothing renders until the session resolves. Everything behind the shell requires an
 * account, and the nav hides what the signed-in role cannot use — a convenience, since the
 * server enforces the same rule and is the one that counts.
 */

import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { useCan, useSession } from "./auth";
import { RoleBadge } from "./components/ui";
import { ApplicationDetail } from "./pages/ApplicationDetail";
import { Applications } from "./pages/Applications";
import { Dashboard } from "./pages/Dashboard";
import { Policy } from "./pages/Policy";
import { RunDetail } from "./pages/RunDetail";
import { Runs } from "./pages/Runs";
import { SignIn } from "./pages/SignIn";
import { Users } from "./pages/Users";
import type { SandboxStatus } from "./types";

/**
 * Warns, on every page, that runs execute under weakened sandbox isolation.
 *
 * Not dismissible, and not confined to the Dashboard. During the testing phase
 * `sandbox.require_full_isolation` is false, which means model-written code runs without a
 * cap on total memory or CPU — an acceptable trade on a development machine and an
 * unacceptable one in production. That distinction is exactly what gets forgotten when the
 * only warning is a log line.
 */
function IsolationBanner() {
  const [status, setStatus] = useState<SandboxStatus | null>(null);

  useEffect(() => {
    void api
      .sandboxStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  if (!status || status.fully_enforced) return null;

  return (
    <div className="border-b border-amber-500/25 bg-amber-500/8">
      <div className="mx-auto max-w-6xl px-8 py-2.5 text-[13px] leading-relaxed">
        <span className="font-semibold text-amber-800 dark:text-amber-300">
          Sandbox isolation is degraded.
        </span>{" "}
        <span className="text-amber-800/85 dark:text-amber-200/85">
          {status.available
            ? `Not enforced on this host: ${status.gaps.join("; ")}. The wall clock, egress block, per-process memory and disk quota still hold — acceptable for testing, not for production.`
            : `The sandbox host is unreachable: ${status.reason ?? "unknown"}.`}
        </span>
      </div>
    </div>
  );
}

function Nav() {
  const canAdmin = useCan("admin");
  const { principal, signOut } = useSession();

  const items = [
    { to: "/", label: "Dashboard", end: true, show: true },
    { to: "/applications", label: "Applications", show: true },
    { to: "/runs", label: "Runs", show: true },
    { to: "/policy", label: "Policy", show: true },
    { to: "/users", label: "Users", show: canAdmin },
  ].filter((item) => item.show);

  return (
    <header className="sticky top-0 z-10 border-b border-line bg-surface/85 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center gap-10 px-8 py-3.5">
        <div className="flex items-baseline gap-2.5">
          <span className="text-[15px] font-semibold tracking-[-0.02em]">KuWarden</span>
          <span className="text-[12px] text-faint">Workbench</span>
        </div>

        <nav className="flex flex-1 gap-1">
          {items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `rounded-lg px-3 py-1.5 text-[13px] transition ${
                  isActive
                    ? "bg-accent-soft font-medium text-accent"
                    : "text-muted hover:text-ink"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        {principal && (
          <div className="flex items-center gap-3">
            <div className="text-right leading-tight">
              <div className="text-[13px] font-medium">{principal.display_name}</div>
              <div className="text-[11px] text-faint">{principal.email}</div>
            </div>
            <RoleBadge role={principal.role} />
            <button
              onClick={() => void signOut()}
              className="text-[12px] text-muted transition hover:text-ink"
            >
              Sign out
            </button>
          </div>
        )}
      </div>
    </header>
  );
}

export default function App() {
  const { principal, ready } = useSession();
  const canAdmin = useCan("admin");

  // Nothing until the session settles, or the sign-in page flashes on every reload for
  // someone who is already signed in.
  if (!ready) return <div className="min-h-screen" />;
  if (!principal) return <SignIn />;

  return (
    <div className="min-h-screen">
      <Nav />
      <IsolationBanner />

      <main className="mx-auto max-w-6xl px-8 py-10">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/applications" element={<Applications />} />
          <Route path="/applications/:id" element={<ApplicationDetail />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/policy" element={<Policy />} />
          {/* Rendered only for admins. The server rejects the underlying calls regardless,
              so this hides a dead end rather than protecting anything. */}
          {canAdmin && <Route path="/users" element={<Users />} />}
        </Routes>
      </main>
    </div>
  );
}
