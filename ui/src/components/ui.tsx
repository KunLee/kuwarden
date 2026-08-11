/**
 * Shared presentational primitives.
 *
 * One file because there are few of them and they are all small; a directory would cost more
 * navigation than it saves. Spacing is generous on purpose — the alternative to dense panels
 * is not smaller text, it is more air.
 */

import type { ReactNode } from "react";
import type { RiskTier, RunStatus } from "../types";

/** A panel. The only container in the Workbench — nesting them is a layout smell. */
export function Card({
  title,
  description,
  actions,
  children,
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-line bg-surface">
      {(title || actions) && (
        <header className="flex items-start justify-between gap-6 px-7 pt-6 pb-5">
          <div className="min-w-0">
            {title && (
              <h2 className="text-[15px] font-semibold tracking-[-0.01em]">{title}</h2>
            )}
            {description && (
              <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-muted">
                {description}
              </p>
            )}
          </div>
          {actions && <div className="shrink-0">{actions}</div>}
        </header>
      )}
      <div className={title ? "px-7 pb-7" : "p-7"}>{children}</div>
    </section>
  );
}

/** Page heading. Larger and lighter than a card title, so hierarchy reads without rules. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-8 flex items-end justify-between gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em]">{title}</h1>
        {description && (
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted">
            {description}
          </p>
        )}
      </div>
      {actions}
    </div>
  );
}

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  const styles = {
    default:
      "border-line bg-surface hover:border-faint shadow-xs",
    primary:
      "border-transparent bg-accent text-white hover:brightness-110 shadow-sm",
    danger: "border-transparent text-red-600 hover:bg-red-500/8 dark:text-red-400",
    ghost: "border-transparent text-muted hover:text-ink",
  }[variant];

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-xl border px-4 py-2 text-[13px] font-medium transition
        disabled:cursor-not-allowed disabled:opacity-40 ${styles}`}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[12px] font-medium text-muted">
        {label}
      </span>
      {children}
      {hint && (
        <span className="mt-1.5 block text-[12px] leading-relaxed text-faint">
          {hint}
        </span>
      )}
    </label>
  );
}

const inputStyles =
  "w-full rounded-xl border border-line bg-surface px-3.5 py-2.5 " +
  "text-sm outline-none transition placeholder:text-faint " +
  "focus:border-accent focus:ring-4 focus:ring-accent/10";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={inputStyles} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={inputStyles} />;
}

/** Table primitives, so every table in the Workbench has the same rhythm. */
export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] text-sm">
        <thead>
          <tr className="border-b border-line">
            {head.map((label, i) => (
              <th
                key={label || i}
                className="pb-3 text-left text-[12px] font-medium text-faint"
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Row({ children }: { children: ReactNode }) {
  return (
    <tr className="row-hover border-b border-line transition last:border-0">
      {children}
    </tr>
  );
}

const badge = "inline-flex rounded-lg px-2.5 py-1 text-[12px] font-medium";

/**
 * Risk tier — the thing an operator scans a run list for, because it decides how much human
 * attention the change consumes (ADR 0002).
 */
export function RiskBadge({ tier }: { tier: RiskTier }) {
  const styles = {
    low: "bg-slate-500/10 text-slate-600 dark:text-slate-300",
    medium: "bg-amber-500/12 text-amber-700 dark:text-amber-300",
    high: "bg-red-500/12 text-red-700 dark:text-red-300",
  }[tier];
  return <span className={`${badge} ${styles}`}>{tier}</span>;
}

export function StatusBadge({ status }: { status: RunStatus }) {
  const styles: Record<RunStatus, string> = {
    running: "bg-blue-500/12 text-blue-700 dark:text-blue-300",
    // Suspended is not a failure. The run is waiting on a human and holds no resource open.
    suspended: "bg-violet-500/12 text-violet-700 dark:text-violet-300",
    succeeded: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
    rejected: "bg-amber-500/12 text-amber-700 dark:text-amber-300",
    failed: "bg-red-500/12 text-red-700 dark:text-red-300",
    aborted: "bg-slate-500/10 text-slate-500 dark:text-slate-400",
  };
  return <span className={`${badge} ${styles[status]}`}>{status}</span>;
}

export function RoleBadge({ role }: { role: string }) {
  const styles: Record<string, string> = {
    admin: "bg-accent/12 text-accent",
    approver: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
    viewer: "bg-slate-500/10 text-slate-600 dark:text-slate-300",
  };
  return <span className={`${badge} ${styles[role] ?? styles.viewer}`}>{role}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="py-12 text-center text-sm text-faint">{children}</p>
  );
}

export function Banner({
  tone,
  children,
}: {
  tone: "error" | "ok" | "warn";
  children: ReactNode;
}) {
  const styles = {
    error: "border-red-500/25 bg-red-500/6 text-red-700 dark:text-red-300",
    ok: "border-emerald-500/25 bg-emerald-500/6 text-emerald-700 dark:text-emerald-300",
    warn: "border-amber-500/30 bg-amber-500/8 text-amber-800 dark:text-amber-300",
  }[tone];
  return (
    <div className={`rounded-xl border px-4 py-3 text-[13px] leading-relaxed ${styles}`}>
      {children}
    </div>
  );
}
