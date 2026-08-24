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
import { RegisterApplication } from "./pages/RegisterApplication";
import { Dashboard } from "./pages/Dashboard";
import { Policy } from "./pages/Policy";
import { RunDetail } from "./pages/RunDetail";
import { Runs } from "./pages/Runs";
import { SignIn } from "./pages/SignIn";
import { Users } from "./pages/Users";
import { useDensity, useTheme } from "./theme";
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
      <div className="px-8 py-2.5 text-[13px] leading-relaxed">
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

/** Inline paths. An icon set is another dependency for six glyphs. */
function NavIcon({ name }: { name: string }) {
  const base = { width: 16, height: 16, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.6, "aria-hidden": true } as const;
  if (name === "dashboard")
    return (
      <svg {...base}>
        <rect x="2" y="2" width="5" height="5" rx="1" />
        <rect x="9" y="2" width="5" height="9" rx="1" />
        <rect x="2" y="9" width="5" height="5" rx="1" />
        <rect x="9" y="13" width="5" height="1" rx="0.5" />
      </svg>
    );
  if (name === "applications")
    return (
      <svg {...base}>
        <rect x="2" y="3" width="12" height="10" rx="2" />
        <path d="M2 6.5h12M5 3v3.5" strokeLinecap="round" />
      </svg>
    );
  if (name === "runs")
    return (
      <svg {...base}>
        <circle cx="8" cy="8" r="6" />
        <path d="M8 4.5V8l2.5 1.5" strokeLinecap="round" />
      </svg>
    );
  if (name === "policy")
    return (
      <svg {...base}>
        <path d="M8 1.8l5 2v4.4c0 3-2.1 5.2-5 6-2.9-.8-5-3-5-6V3.8z" />
        <path d="M5.8 8l1.6 1.6L10.4 6.6" strokeLinecap="round" />
      </svg>
    );
  if (name === "collapse")
    return (
      <svg {...base}>
        <path d="M9.5 4L5.5 8l4 4" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M12.5 3v10" strokeLinecap="round" />
      </svg>
    );
  if (name === "expand")
    return (
      <svg {...base}>
        <path d="M6.5 4l4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M3.5 3v10" strokeLinecap="round" />
      </svg>
    );
  if (name === "signout")
    return (
      <svg {...base}>
        <path d="M6 2.5H3.5v11H6" strokeLinecap="round" />
        <path d="M9 5.5L11.5 8 9 10.5M11.5 8h-6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  if (name === "dark")
    return (
      <svg {...base}>
        <path d="M13 9.8A5.6 5.6 0 016.2 3 5.6 5.6 0 108 14a5.6 5.6 0 005-4.2z" />
      </svg>
    );
  if (name === "light")
    return (
      <svg {...base}>
        <circle cx="8" cy="8" r="3" />
        <path
          d="M8 1v1.6M8 13.4V15M15 8h-1.6M2.6 8H1M12.9 3.1l-1.1 1.1M4.2 11.8l-1.1 1.1M12.9 12.9l-1.1-1.1M4.2 4.2L3.1 3.1"
          strokeLinecap="round"
        />
      </svg>
    );
  return (
    <svg {...base}>
      <circle cx="6" cy="5.5" r="2.4" />
      <path d="M1.8 13.5c.4-2.2 2.2-3.5 4.2-3.5s3.8 1.3 4.2 3.5M11 4.2a2.2 2.2 0 010 3.6M12.2 13.5c-.15-1-.5-1.9-1-2.6" strokeLinecap="round" />
    </svg>
  );
}

/**
 * The sidebar.
 *
 * A left rail rather than a top bar: the Workbench has five destinations and will gain more,
 * and a horizontal nav prices every addition in the same scarce row that also holds the
 * product name and the signed-in account. Vertical space is the one thing this layout has
 * spare.
 *
 * Items the signed-in role cannot use are hidden. That is a courtesy — the server rejects the
 * underlying calls regardless, so this removes a dead end rather than protecting anything.
 */
function Sidebar({
  collapsed,
  onToggle,
}: {
  collapsed: boolean;
  onToggle: () => void;
}) {
  const canAdmin = useCan("admin");
  const { principal, signOut } = useSession();
  const [theme, toggleTheme] = useTheme();
  const [density, toggleDensity] = useDensity();

  const items = [
    { to: "/", label: "Dashboard", icon: "dashboard", end: true, show: true },
    { to: "/applications", label: "Applications", icon: "applications", show: true },
    { to: "/runs", label: "Runs", icon: "runs", show: true },
    { to: "/policy", label: "Policy", icon: "policy", show: true },
    { to: "/users", label: "Users", icon: "users", show: canAdmin },
  ].filter((item) => item.show);

  return (
    <aside
      className={`flex h-full shrink-0 flex-col border-r border-line bg-surface transition-[width] duration-150 ${
        collapsed ? "w-16" : "w-56"
      }`}
    >
      <div className={`flex items-center gap-2 py-5 ${collapsed ? "justify-center px-2" : "px-5"}`}>
        {!collapsed && (
          <div className="min-w-0 flex-1">
            <div className="truncate text-[15px] font-semibold tracking-[-0.02em]">KuWarden</div>
            <div className="text-[12px] text-faint">Workbench</div>
          </div>
        )}
        <button
          type="button"
          onClick={onToggle}
          title={collapsed ? "Expand" : "Collapse"}
          aria-label={collapsed ? "Expand the sidebar" : "Collapse the sidebar"}
          aria-expanded={!collapsed}
          className="rounded-lg p-1.5 text-muted transition hover:bg-canvas hover:text-ink"
        >
          <NavIcon name={collapsed ? "expand" : "collapse"} />
        </button>
      </div>

      {/* The nav scrolls, not the sidebar. A deployment with enough destinations to overflow
          must not push the account block off the bottom. */}
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3">
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            // The label disappears when collapsed, so the title carries it — an icon rail
            // whose icons cannot be identified is a puzzle, not a navigation.
            title={collapsed ? item.label : undefined}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-lg py-2 text-[13px] transition ${
                collapsed ? "justify-center px-2" : "px-3"
              } ${
                isActive
                  ? "bg-accent-soft font-medium text-accent"
                  : "text-muted hover:bg-canvas hover:text-ink"
              }`
            }
          >
            <NavIcon name={item.icon} />
            {!collapsed && item.label}
          </NavLink>
        ))}
      </nav>

      {/* Above the account block and outside its `principal` guard: appearance is a property
          of this browser, not of who is signed in. */}
      <div className={`border-t border-line py-2 ${collapsed ? "px-2" : "px-3"}`}>
        <button
          type="button"
          onClick={toggleTheme}
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
          aria-label={theme === "dark" ? "Switch to the light theme" : "Switch to the dark theme"}
          className={`flex w-full items-center gap-2.5 rounded-lg py-2 text-[13px] text-muted transition hover:bg-canvas hover:text-ink ${
            collapsed ? "justify-center px-2" : "px-3"
          }`}
        >
          {/* The icon shows what you would get, not what you have — a sun on a light screen
              is a button whose meaning has to be guessed at. */}
          <NavIcon name={theme === "dark" ? "light" : "dark"} />
          {!collapsed && (theme === "dark" ? "Light" : "Dark")}
        </button>

        {/* Density, next to the theme because both are properties of this screen rather than
            of the deployment or of who is signed in. `presentation` scales the root, and the
            whole type scale follows because every step is in `rem`. */}
        <button
          type="button"
          onClick={toggleDensity}
          title={
            density === "presentation"
              ? "Back to the dense console"
              : "Larger, for recording or projecting"
          }
          aria-pressed={density === "presentation"}
          className={`flex w-full items-center gap-2.5 rounded-lg py-2 text-body text-muted transition hover:bg-canvas hover:text-ink ${
            collapsed ? "justify-center px-2" : "px-3"
          }`}
        >
          <span aria-hidden className="mono text-micro">
            {density === "presentation" ? "–A" : "+A"}
          </span>
          {!collapsed && (density === "presentation" ? "Compact" : "Present")}
        </button>
      </div>

      {principal && (
        <div className={`border-t border-line py-4 ${collapsed ? "px-2" : "px-5"}`}>
          {collapsed ? (
            <button
              type="button"
              onClick={() => void signOut()}
              title={`${principal.email} \u2014 sign out`}
              className="flex w-full items-center justify-center rounded-lg py-1.5 text-muted transition hover:bg-canvas hover:text-ink"
            >
              <NavIcon name="signout" />
            </button>
          ) : (
            <>
              <div className="truncate text-[13px] font-medium">{principal.display_name}</div>
              <div className="truncate text-[11px] text-faint">{principal.email}</div>
              <div className="mt-2 flex items-center justify-between">
                <RoleBadge role={principal.role} />
                <button
                  onClick={() => void signOut()}
                  className="text-[12px] text-muted transition hover:text-ink"
                >
                  Sign out
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </aside>
  );
}


/** Remembered, because a rail that re-expands on every navigation is not collapsible. */
const COLLAPSED_KEY = "kuwarden.sidebar.collapsed";

export default function App() {
  const { principal, ready } = useSession();
  const canAdmin = useCan("admin");
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSED_KEY) === "1",
  );

  useEffect(() => {
    localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  // Nothing until the session settles, or the sign-in page flashes on every reload for
  // someone who is already signed in.
  if (!ready) return <div className="min-h-screen" />;
  if (!principal) return <SignIn />;

  return (
    // `h-screen overflow-hidden`, so the *page* never scrolls -- only the main pane does.
    //
    // With a scrolling page the sidebar is a flex item that grows with the content, and its
    // account block travels to the bottom of a long document instead of staying at the bottom
    // of the window. Confining the scroll to `main` pins the rail without `position: fixed`
    // and without the content having to know how wide it is.
    <div className="flex h-screen overflow-hidden">
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <IsolationBanner />
        <main className="flex-1 overflow-y-auto px-8 py-10">
        <div className="mx-auto max-w-5xl">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/applications" element={<Applications />} />
          {/* Before `/:id`, or "new" is read as an application id. */}
          <Route path="/applications/new" element={<RegisterApplication />} />
          <Route path="/applications/:id" element={<ApplicationDetail />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/policy" element={<Policy />} />
          {/* Rendered only for admins. The server rejects the underlying calls regardless,
              so this hides a dead end rather than protecting anything. */}
          {canAdmin && <Route path="/users" element={<Users />} />}
        </Routes>
        </div>
        </main>
      </div>
    </div>
  );
}
