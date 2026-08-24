import { useState } from "react";
import {
  LayoutDashboard,
  Ticket,
  GitBranch,
  Bot,
  Rocket,
  Settings,
  Bell,
  Search,
  Plus,
  CheckCircle2,
  XCircle,
  Terminal,
  Zap,
  Code2,
  TestTube2,
  Shield,
  Upload,
  BadgeCheck,
  Sparkles,
  User,
  X,
  RefreshCw,
} from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

// ─── Types ───────────────────────────────────────────────────────────────────

type NavView = "dashboard" | "tickets" | "pipeline" | "agents" | "deployments" | "settings";
type TicketStatus = "pending" | "analyzing" | "planning" | "coding" | "testing" | "reviewing" | "deploying" | "completed" | "failed";
type Priority = "critical" | "high" | "medium" | "low";
type TicketType = "feature" | "bugfix" | "refactor" | "infrastructure";

interface TicketItem {
  id: string;
  title: string;
  description: string;
  type: TicketType;
  priority: Priority;
  status: TicketStatus;
  agent: string | null;
  repo: string;
  progress: number;
  createdAt: string;
  duration: string;
  branch?: string | null;
  commits?: number;
  tests?: { passed: number; total: number };
}

interface AgentItem {
  id: string;
  name: string;
  model: string;
  status: "idle" | "busy" | "offline";
  currentTask?: string;
  tasksCompleted: number;
  successRate: number;
  uptime: string;
  cpuUsage: number;
  memUsage: number;
}

interface DeploymentItem {
  id: string;
  ticketId: string;
  ticketTitle: string;
  environment: "production" | "staging" | "preview";
  status: "success" | "failed" | "rollback";
  agent: string;
  deployedAt: string;
  duration: string;
  commit: string;
  repo: string;
}

// ─── Mock Data ───────────────────────────────────────────────────────────────

const TICKETS: TicketItem[] = [
  {
    id: "TKT-3041",
    title: "Add OAuth2 Google login flow",
    description: "Implement Google OAuth2 with session management and refresh token rotation.",
    type: "feature",
    priority: "high",
    status: "coding",
    agent: "Nexus-7",
    repo: "user-service",
    progress: 62,
    createdAt: "2026-08-12 09:14",
    duration: "38m",
    branch: "feature/google-oauth",
    commits: 4,
    tests: { passed: 12, total: 18 },
  },
  {
    id: "TKT-3040",
    title: "Rate limiting middleware for API gateway",
    description: "Sliding window rate limiting with Redis-backed counters per user and per IP.",
    type: "feature",
    priority: "critical",
    status: "testing",
    agent: "Apex-3",
    repo: "api-gateway",
    progress: 81,
    createdAt: "2026-08-12 08:02",
    duration: "1h 12m",
    branch: "feature/rate-limiting",
    commits: 7,
    tests: { passed: 34, total: 41 },
  },
  {
    id: "TKT-3039",
    title: "User analytics dashboard component",
    description: "React dashboard showing DAU, retention curves, and funnel analysis.",
    type: "feature",
    priority: "medium",
    status: "completed",
    agent: "Forge-12",
    repo: "frontend-app",
    progress: 100,
    createdAt: "2026-08-12 06:30",
    duration: "2h 04m",
    branch: "feature/analytics-dashboard",
    commits: 11,
    tests: { passed: 28, total: 28 },
  },
  {
    id: "TKT-3038",
    title: "Fix memory leak in WebSocket handler",
    description: "Event listeners not cleaned up on socket disconnect causing production memory growth.",
    type: "bugfix",
    priority: "critical",
    status: "deploying",
    agent: "Nexus-7",
    repo: "realtime-service",
    progress: 93,
    createdAt: "2026-08-11 22:45",
    duration: "3h 21m",
    branch: "fix/ws-memory-leak",
    commits: 3,
    tests: { passed: 19, total: 19 },
  },
  {
    id: "TKT-3037",
    title: "Migrate billing service to Stripe v4",
    description: "Update SDK, migrate webhook handlers, update payment intent flow.",
    type: "infrastructure",
    priority: "high",
    status: "analyzing",
    agent: "Prism-1",
    repo: "billing-service",
    progress: 18,
    createdAt: "2026-08-12 10:01",
    duration: "12m",
    branch: null,
    commits: 0,
  },
  {
    id: "TKT-3036",
    title: "Refactor auth middleware to async/await",
    description: "Replace callback-based auth checks with async/await throughout the service.",
    type: "refactor",
    priority: "low",
    status: "failed",
    agent: "Apex-3",
    repo: "api-gateway",
    progress: 45,
    createdAt: "2026-08-11 16:30",
    duration: "1h 45m",
    branch: "refactor/auth-async",
    commits: 5,
    tests: { passed: 11, total: 23 },
  },
  {
    id: "TKT-3035",
    title: "Add Postgres full-text search",
    description: "Implement FTS using pg_trgm for product catalog search with ranking.",
    type: "feature",
    priority: "medium",
    status: "pending",
    agent: null,
    repo: "catalog-service",
    progress: 0,
    createdAt: "2026-08-12 10:15",
    duration: "—",
  },
];

const AGENTS: AgentItem[] = [
  {
    id: "nexus-7",
    name: "Nexus-7",
    model: "claude-sonnet-5",
    status: "busy",
    currentTask: "TKT-3041 — Writing OAuth service module",
    tasksCompleted: 147,
    successRate: 94.2,
    uptime: "11d 4h",
    cpuUsage: 78,
    memUsage: 62,
  },
  {
    id: "apex-3",
    name: "Apex-3",
    model: "claude-sonnet-5",
    status: "busy",
    currentTask: "TKT-3040 — Running integration test suite",
    tasksCompleted: 203,
    successRate: 91.8,
    uptime: "18d 11h",
    cpuUsage: 91,
    memUsage: 74,
  },
  {
    id: "forge-12",
    name: "Forge-12",
    model: "claude-haiku-4-5",
    status: "idle",
    tasksCompleted: 88,
    successRate: 96.6,
    uptime: "4d 7h",
    cpuUsage: 12,
    memUsage: 28,
  },
  {
    id: "prism-1",
    name: "Prism-1",
    model: "claude-opus-5",
    status: "busy",
    currentTask: "TKT-3037 — Analyzing Stripe migration scope",
    tasksCompleted: 41,
    successRate: 97.6,
    uptime: "2d 14h",
    cpuUsage: 44,
    memUsage: 51,
  },
  {
    id: "delta-9",
    name: "Delta-9",
    model: "claude-sonnet-5",
    status: "idle",
    tasksCompleted: 312,
    successRate: 89.1,
    uptime: "31d 22h",
    cpuUsage: 8,
    memUsage: 19,
  },
  {
    id: "vector-2",
    name: "Vector-2",
    model: "claude-haiku-4-5",
    status: "offline",
    tasksCompleted: 67,
    successRate: 92.5,
    uptime: "0d 0h",
    cpuUsage: 0,
    memUsage: 0,
  },
];

const DEPLOYMENTS: DeploymentItem[] = [
  { id: "DEP-811", ticketId: "TKT-3039", ticketTitle: "User analytics dashboard component", environment: "production", status: "success", agent: "Forge-12", deployedAt: "08:41", duration: "3m 22s", commit: "a4f91bc", repo: "frontend-app" },
  { id: "DEP-810", ticketId: "TKT-3034", ticketTitle: "Dark mode theme persistence", environment: "production", status: "success", agent: "Delta-9", deployedAt: "20:14", duration: "2m 58s", commit: "77e3d12", repo: "frontend-app" },
  { id: "DEP-809", ticketId: "TKT-3033", ticketTitle: "Email notification templates", environment: "staging", status: "success", agent: "Nexus-7", deployedAt: "17:05", duration: "4m 11s", commit: "c81a45f", repo: "notification-service" },
  { id: "DEP-808", ticketId: "TKT-3032", ticketTitle: "CSV export endpoint", environment: "production", status: "failed", agent: "Apex-3", deployedAt: "14:33", duration: "1m 07s", commit: "b29f88a", repo: "api-gateway" },
  { id: "DEP-807", ticketId: "TKT-3031", ticketTitle: "DB index optimization", environment: "production", status: "rollback", agent: "Prism-1", deployedAt: "11:20", duration: "8m 02s", commit: "e50c71d", repo: "catalog-service" },
  { id: "DEP-806", ticketId: "TKT-3030", ticketTitle: "Webhook retry logic", environment: "production", status: "success", agent: "Forge-12", deployedAt: "09:48", duration: "2m 44s", commit: "f12e093", repo: "billing-service" },
];

const ACTIVITY_DATA = [
  { time: "00:00", pipelines: 2, tickets: 3 },
  { time: "02:00", pipelines: 1, tickets: 2 },
  { time: "04:00", pipelines: 0, tickets: 1 },
  { time: "06:00", pipelines: 3, tickets: 4 },
  { time: "08:00", pipelines: 5, tickets: 7 },
  { time: "10:00", pipelines: 8, tickets: 11 },
  { time: "12:00", pipelines: 6, tickets: 9 },
  { time: "14:00", pipelines: 7, tickets: 10 },
  { time: "16:00", pipelines: 9, tickets: 12 },
  { time: "18:00", pipelines: 5, tickets: 7 },
  { time: "20:00", pipelines: 4, tickets: 6 },
  { time: "22:00", pipelines: 3, tickets: 4 },
];

const PIPELINE_STAGES = [
  { id: "ticket", label: "TICKET", icon: Ticket, description: "Received" },
  { id: "analyze", label: "ANALYZE", icon: Sparkles, description: "AI analysis" },
  { id: "plan", label: "PLAN", icon: GitBranch, description: "Task plan" },
  { id: "code", label: "CODE GEN", icon: Code2, description: "Write code" },
  { id: "test", label: "TEST", icon: TestTube2, description: "Run suite" },
  { id: "review", label: "REVIEW", icon: Shield, description: "AI review" },
  { id: "deploy", label: "DEPLOY", icon: Upload, description: "Ship it" },
  { id: "verify", label: "VERIFY", icon: BadgeCheck, description: "Check live" },
];

const STATUS_STAGE_MAP: Record<TicketStatus, number> = {
  pending: -1,
  analyzing: 1,
  planning: 2,
  coding: 3,
  testing: 4,
  reviewing: 5,
  deploying: 6,
  completed: 8,
  failed: -1,
};

const CONSOLE_LOGS: Record<string, string[]> = {
  "TKT-3041": [
    "[09:14:02] Ticket TKT-3041 received. Parsing requirements...",
    "[09:14:08] Scope confirmed: OAuth2 flow + session management.",
    "[09:14:12] Assigning to Nexus-7 · claude-sonnet-5",
    "[09:16:33] Cloned user-service. Branch: feature/google-oauth",
    "[09:16:41] Reading codebase structure and existing auth patterns...",
    "[09:21:07] Generating src/auth/google.ts (412 lines)...",
    "[09:28:55] Generating src/auth/session.ts (189 lines)...",
    "[09:33:11] Generating src/routes/auth.ts (267 lines)...",
    "[09:38:44] Generating tests/auth/google.test.ts (334 lines)...",
    "[09:41:02] Committing 4 files — feat: add google oauth provider",
    "[09:41:12] Continuing code generation...",
    "▋",
  ],
  "TKT-3040": [
    "[08:02:11] Ticket TKT-3040 received. Priority: CRITICAL.",
    "[08:02:18] Assigning to Apex-3 · claude-sonnet-5",
    "[08:04:33] Cloned api-gateway. Branch: feature/rate-limiting",
    "[08:11:07] Generated: src/middleware/rateLimit.ts",
    "[08:19:22] Generated: src/store/redisCounter.ts",
    "[08:27:55] Generated: 41 test cases across 3 suites",
    "[09:14:01] Running test suite: npm test --coverage",
    "[09:14:44] ✓ 34/41 tests passing · 7 failing",
    "[09:14:44] FAIL src/__tests__/rateLimit.edge.test.ts",
    "[09:14:44]   ✗ concurrent request spike handling",
    "[09:14:44]   ✗ Redis connection timeout fallback",
    "[09:14:44] Investigating failures...",
    "▋",
  ],
  "TKT-3038": [
    "[22:45:01] Ticket TKT-3038 received. Priority: CRITICAL.",
    "[22:45:08] Assigning to Nexus-7 · claude-sonnet-5",
    "[22:47:11] Reproducing memory leak in local env...",
    "[22:51:30] Root cause: removeListener never called on ws.close()",
    "[22:53:14] Fix: src/handlers/wsHandler.ts line 88-112",
    "[01:02:44] All 19/19 tests passing",
    "[01:04:19] PR created — fix: cleanup ws event listeners on disconnect",
    "[01:04:25] AI code review passed",
    "[01:06:00] Deploying to production...",
    "▋",
  ],
};

// ─── Utility ─────────────────────────────────────────────────────────────────

function cx(...classes: (string | boolean | undefined | null)[]) {
  return classes.filter(Boolean).join(" ");
}

// ─── Status Styles ────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<TicketStatus, string> = {
  pending: "text-[#6b6b80] border-[#6b6b80]/30 bg-[#6b6b80]/10",
  analyzing: "text-[#f59e0b] border-[#f59e0b]/30 bg-[#f59e0b]/10",
  planning: "text-[#818cf8] border-[#818cf8]/30 bg-[#818cf8]/10",
  coding: "text-[#60a5fa] border-[#60a5fa]/30 bg-[#60a5fa]/10",
  testing: "text-[#a78bfa] border-[#a78bfa]/30 bg-[#a78bfa]/10",
  reviewing: "text-[#f472b6] border-[#f472b6]/30 bg-[#f472b6]/10",
  deploying: "text-[#5cffa3] border-[#5cffa3]/30 bg-[#5cffa3]/10",
  completed: "text-[#5cffa3] border-[#5cffa3]/30 bg-[#5cffa3]/10",
  failed: "text-[#ff4d6a] border-[#ff4d6a]/30 bg-[#ff4d6a]/10",
};

const PRIORITY_COLORS: Record<Priority, string> = {
  critical: "#ff4d6a",
  high: "#f97316",
  medium: "#f59e0b",
  low: "#4b4b60",
};

const TYPE_STYLES: Record<TicketType, string> = {
  feature: "text-[#60a5fa] bg-[#60a5fa]/10",
  bugfix: "text-[#ff4d6a] bg-[#ff4d6a]/10",
  refactor: "text-[#a78bfa] bg-[#a78bfa]/10",
  infrastructure: "text-[#f59e0b] bg-[#f59e0b]/10",
};

const ACTIVE_STATUSES: TicketStatus[] = ["analyzing", "planning", "coding", "testing", "reviewing", "deploying"];

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: TicketStatus }) {
  const isAnimated = ACTIVE_STATUSES.includes(status);
  return (
    <span className={cx(
      "inline-flex items-center gap-1.5 px-2 py-0.5 text-[10px] font-mono font-medium uppercase tracking-widest border",
      STATUS_STYLES[status]
    )}>
      {isAnimated && <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse flex-shrink-0" />}
      {status}
    </span>
  );
}

function ProgressBar({ value, status }: { value: number; status: TicketStatus }) {
  const color = status === "failed" ? "#ff4d6a" : "#5cffa3";
  const opacity = status === "failed" ? 0.5 : 1;
  return (
    <div className="h-px bg-white/[0.06] w-full">
      <div className="h-full transition-all duration-700" style={{ width: `${value}%`, backgroundColor: color, opacity }} />
    </div>
  );
}

function MiniBar({ value, color }: { value: number; color: string }) {
  return (
    <div className="h-1 bg-white/[0.06]">
      <div className="h-full transition-all" style={{ width: `${value}%`, backgroundColor: color }} />
    </div>
  );
}

// ─── Sidebar ─────────────────────────────────────────────────────────────────

const NAV_ITEMS = [
  { id: "dashboard" as NavView, icon: LayoutDashboard, label: "Dashboard" },
  { id: "tickets" as NavView, icon: Ticket, label: "Tickets" },
  { id: "pipeline" as NavView, icon: GitBranch, label: "Pipelines" },
  { id: "agents" as NavView, icon: Bot, label: "Agents" },
  { id: "deployments" as NavView, icon: Rocket, label: "Deployments" },
];

function Sidebar({ active, setActive, onNewTicket }: {
  active: NavView;
  setActive: (v: NavView) => void;
  onNewTicket: () => void;
}) {
  return (
    <div className="w-[220px] flex-shrink-0 bg-[#080810] border-r border-white/[0.06] flex flex-col">
      {/* Logo */}
      <div className="px-5 pt-6 pb-5 border-b border-white/[0.06]">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-[#5cffa3] flex items-center justify-center flex-shrink-0">
            <Zap size={15} className="text-[#050510]" fill="currentColor" />
          </div>
          <div>
            <div className="font-mono text-[13px] font-bold tracking-wider text-white uppercase leading-tight">Agentic</div>
            <div className="font-mono text-[9px] tracking-[0.2em] text-[#5cffa3]/50 uppercase">Workflow OS</div>
          </div>
        </div>
      </div>

      {/* New Ticket CTA */}
      <div className="px-4 py-4">
        <button
          onClick={onNewTicket}
          className="w-full flex items-center justify-center gap-2 py-2.5 bg-[#5cffa3] text-[#050510] font-mono text-[11px] font-bold uppercase tracking-widest hover:bg-[#80ffba] transition-colors"
        >
          <Plus size={12} />
          New Ticket
        </button>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 space-y-px">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            onClick={() => setActive(item.id)}
            className={cx(
              "w-full flex items-center gap-3 px-3 py-2.5 text-left transition-all font-mono text-[11px] tracking-wider uppercase",
              active === item.id
                ? "bg-white/[0.06] text-white border-l-2 border-[#5cffa3]"
                : "text-[#5b5b78] hover:text-white hover:bg-white/[0.03] border-l-2 border-transparent"
            )}
          >
            <item.icon size={13} />
            {item.label}
          </button>
        ))}
      </nav>

      {/* Bottom */}
      <div className="px-3 pb-5 pt-3 border-t border-white/[0.06] space-y-px">
        <button
          onClick={() => setActive("settings")}
          className={cx(
            "w-full flex items-center gap-3 px-3 py-2.5 text-left transition-all font-mono text-[11px] tracking-wider uppercase",
            active === "settings"
              ? "bg-white/[0.06] text-white border-l-2 border-[#5cffa3]"
              : "text-[#5b5b78] hover:text-white hover:bg-white/[0.03] border-l-2 border-transparent"
          )}
        >
          <Settings size={13} />
          Settings
        </button>

        {/* User */}
        <div className="flex items-center gap-2.5 px-3 py-2 mt-2">
          <div className="w-6 h-6 bg-[#5cffa3]/15 flex items-center justify-center flex-shrink-0">
            <User size={11} className="text-[#5cffa3]" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="font-mono text-[10px] text-white/70 truncate">alex@team.io</div>
            <div className="font-mono text-[9px] text-[#4b4b60] tracking-widest uppercase">Admin</div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Header ───────────────────────────────────────────────────────────────────

const VIEW_TITLES: Record<NavView, string> = {
  dashboard: "Dashboard",
  tickets: "Tickets",
  pipeline: "Pipelines",
  agents: "Agent Fleet",
  deployments: "Deployments",
  settings: "Settings",
};

function Header({ activeView, onNewTicket }: { activeView: NavView; onNewTicket: () => void }) {
  const activeCount = AGENTS.filter((a) => a.status === "busy").length;
  return (
    <div className="h-[50px] flex items-center justify-between px-6 border-b border-white/[0.06] bg-[#07070f] flex-shrink-0">
      <div className="flex items-center gap-3">
        <span className="font-mono text-[11px] font-bold uppercase tracking-[0.22em] text-white">
          {VIEW_TITLES[activeView]}
        </span>
        <span className="text-white/10 font-mono">·</span>
        <span className="font-mono text-[10px] text-[#5cffa3]/40 tracking-widest uppercase">Aug 12, 2026</span>
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 bg-white/[0.03] border border-white/[0.06] px-3 py-1.5 w-[220px]">
          <Search size={11} className="text-[#4b4b60] flex-shrink-0" />
          <input
            placeholder="Search tickets, agents..."
            className="bg-transparent text-[11px] font-mono text-white placeholder-[#3d3d55] outline-none w-full"
          />
        </div>
        <button className="relative p-1.5 text-[#4b4b60] hover:text-white transition-colors">
          <Bell size={14} />
          <span className="absolute top-1 right-1 w-1.5 h-1.5 bg-[#5cffa3] rounded-full" />
        </button>
        <div className="w-px h-4 bg-white/[0.08]" />
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-[#5cffa3] animate-pulse" />
          <span className="font-mono text-[10px] text-[#5cffa3]/70 uppercase tracking-widest">{activeCount} Active</span>
        </div>
        <button
          onClick={onNewTicket}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-[#5cffa3]/10 border border-[#5cffa3]/30 text-[#5cffa3] font-mono text-[10px] uppercase tracking-widest hover:bg-[#5cffa3]/20 transition-colors"
        >
          <Plus size={11} />
          New
        </button>
      </div>
    </div>
  );
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

function StatCard({ label, value, sub, accentColor }: {
  label: string; value: string | number; sub: string; accentColor: string;
}) {
  return (
    <div className="border border-white/[0.07] bg-[#0d0d1a] p-5 relative overflow-hidden hover:border-white/[0.12] transition-all">
      <div className="absolute top-0 left-0 right-0 h-[1px]" style={{ backgroundColor: accentColor, opacity: 0.5 }} />
      <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5b5b78] mb-3">{label}</div>
      <div className="font-mono text-3xl font-bold text-white tracking-tight mb-1">{value}</div>
      <div className="font-mono text-[10px] text-[#4b4b60] uppercase tracking-wider">{sub}</div>
    </div>
  );
}

function DashboardView({ onViewTicket }: { onViewTicket: (id: string) => void }) {
  const running = TICKETS.filter((t) => ACTIVE_STATUSES.includes(t.status));
  const completed = TICKETS.filter((t) => t.status === "completed");
  const rate = Math.round((completed.length / TICKETS.filter((t) => t.status !== "pending").length) * 100);
  const activeAgents = AGENTS.filter((a) => a.status === "busy").length;

  return (
    <div className="p-6 space-y-5">
      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard label="Active Agents" value={activeAgents} sub={`${AGENTS.filter((a) => a.status === "idle").length} idle · 1 offline`} accentColor="#5cffa3" />
        <StatCard label="Running Pipelines" value={running.length} sub="across 4 repos" accentColor="#60a5fa" />
        <StatCard label="Tickets Today" value={TICKETS.length} sub="↑ 3 from yesterday" accentColor="#a78bfa" />
        <StatCard label="Success Rate" value={`${rate}%`} sub="last 30 deployments" accentColor="#f59e0b" />
      </div>

      {/* Charts + Feed */}
      <div className="grid grid-cols-5 gap-4">
        {/* Area Chart */}
        <div className="col-span-3 border border-white/[0.07] bg-[#0d0d1a] p-5">
          <div className="flex items-center justify-between mb-5">
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5b5b78]">Pipeline Activity</div>
              <div className="font-mono text-xs text-white mt-0.5">Today · Aug 12, 2026</div>
            </div>
            <div className="flex items-center gap-5">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-[#5cffa3]" />
                <span className="font-mono text-[10px] text-[#5b5b78] uppercase">Pipelines</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-[#60a5fa]" />
                <span className="font-mono text-[10px] text-[#5b5b78] uppercase">Tickets</span>
              </div>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={175}>
            <AreaChart data={ACTIVITY_DATA} margin={{ top: 5, right: 0, bottom: 0, left: -28 }}>
              <defs>
                <linearGradient id="gMint" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#5cffa3" stopOpacity={0.18} />
                  <stop offset="100%" stopColor="#5cffa3" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="gBlue" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#60a5fa" stopOpacity={0.14} />
                  <stop offset="100%" stopColor="#60a5fa" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="2 6" stroke="rgba(255,255,255,0.04)" />
              <XAxis dataKey="time" tick={{ fill: "#3d3d55", fontSize: 9, fontFamily: "JetBrains Mono" }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "#3d3d55", fontSize: 9, fontFamily: "JetBrains Mono" }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{ backgroundColor: "#0d0d1a", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 0, fontFamily: "JetBrains Mono", fontSize: 11, color: "#e8e8f0" }}
                labelStyle={{ color: "#5cffa3" }}
                itemStyle={{ color: "#9b9bb8" }}
                cursor={{ stroke: "rgba(255,255,255,0.08)" }}
              />
              <Area type="monotone" dataKey="pipelines" stroke="#5cffa3" strokeWidth={1.5} fill="url(#gMint)" />
              <Area type="monotone" dataKey="tickets" stroke="#60a5fa" strokeWidth={1.5} fill="url(#gBlue)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Live Feed */}
        <div className="col-span-2 border border-white/[0.07] bg-[#0d0d1a] p-5 flex flex-col">
          <div className="flex items-center justify-between mb-4">
            <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5b5b78]">Live Feed</div>
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-[#5cffa3] animate-pulse" />
              <span className="font-mono text-[9px] text-[#5cffa3]/60 uppercase tracking-widest">Live</span>
            </div>
          </div>
          <div className="flex-1 space-y-3 overflow-auto">
            {[
              { time: "10:18", color: "#5cffa3", msg: "TKT-3041 entered CODING stage", sub: "Nexus-7" },
              { time: "10:14", color: "#60a5fa", msg: "TKT-3037 assigned to Prism-1", sub: "Auto-assign" },
              { time: "10:01", color: "#a78bfa", msg: "TKT-3035 created — queued", sub: "alex@team.io" },
              { time: "09:58", color: "#f59e0b", msg: "TKT-3040 — 34/41 tests passing", sub: "Apex-3" },
              { time: "09:41", color: "#5cffa3", msg: "TKT-3039 deployed to production", sub: "Forge-12" },
              { time: "09:14", color: "#ff4d6a", msg: "TKT-3036 failed at testing stage", sub: "Apex-3" },
              { time: "08:02", color: "#60a5fa", msg: "TKT-3040 pipeline started", sub: "Apex-3" },
            ].map((item, i) => (
              <div key={i} className="flex gap-2.5 items-start">
                <span className="font-mono text-[9px] text-[#3d3d50] pt-0.5 flex-shrink-0 w-10">{item.time}</span>
                <span className="text-[8px] pt-1 flex-shrink-0" style={{ color: item.color }}>●</span>
                <div className="min-w-0">
                  <div className="font-mono text-[10px] text-white/70 leading-snug">{item.msg}</div>
                  <div className="font-mono text-[9px] text-[#3d3d55]">{item.sub}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Active Pipelines */}
      <div className="border border-white/[0.07] bg-[#0d0d1a]">
        <div className="flex items-center justify-between px-5 py-3 border-b border-white/[0.06]">
          <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5b5b78]">Active Pipelines</div>
          <button className="font-mono text-[10px] text-[#5cffa3]/50 hover:text-[#5cffa3] uppercase tracking-widest transition-colors">
            View All →
          </button>
        </div>
        <div className="divide-y divide-white/[0.04]">
          {running.map((ticket) => {
            const si = STATUS_STAGE_MAP[ticket.status];
            return (
              <div
                key={ticket.id}
                onClick={() => onViewTicket(ticket.id)}
                className="px-5 py-4 flex items-center gap-5 hover:bg-white/[0.02] cursor-pointer transition-colors"
              >
                <div className="w-[76px] flex-shrink-0">
                  <div className="font-mono text-[11px] text-[#5cffa3] tracking-wider">{ticket.id}</div>
                  <div className="font-mono text-[9px] text-[#3d3d55] uppercase tracking-wider mt-0.5">{ticket.agent}</div>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-mono text-[11px] text-white truncate mb-2">{ticket.title}</div>
                  {/* Stage bar */}
                  <div className="flex items-center gap-0.5">
                    {PIPELINE_STAGES.slice(1).map((_, i) => (
                      <div
                        key={i}
                        className={cx(
                          "flex-1 h-1.5 transition-all",
                          i < si - 1 ? "bg-[#5cffa3]" :
                          i === si - 1 ? "bg-[#5cffa3] animate-pulse" :
                          "bg-white/[0.07]"
                        )}
                      />
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-4 flex-shrink-0">
                  <StatusBadge status={ticket.status} />
                  <span className="font-mono text-[10px] text-[#4b4b60] w-8 text-right">{ticket.progress}%</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ─── Tickets ──────────────────────────────────────────────────────────────────

type FilterOption = "all" | TicketStatus;
const TICKET_FILTERS: FilterOption[] = ["all", "pending", "analyzing", "coding", "testing", "deploying", "completed", "failed"];

function TicketsView({ onViewPipeline }: { onViewPipeline: (id: string) => void }) {
  const [filter, setFilter] = useState<FilterOption>("all");

  const filtered = filter === "all" ? TICKETS : TICKETS.filter((t) => t.status === filter);

  return (
    <div className="p-6 space-y-4">
      {/* Filter bar */}
      <div className="flex items-center gap-0 border border-white/[0.07]">
        {TICKET_FILTERS.map((f) => {
          const count = f === "all" ? TICKETS.length : TICKETS.filter((t) => t.status === f).length;
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cx(
                "flex items-center gap-1.5 px-4 py-2 font-mono text-[10px] uppercase tracking-widest border-r border-white/[0.06] last:border-r-0 transition-all",
                filter === f
                  ? "bg-[#5cffa3] text-[#050510] font-bold"
                  : "text-[#5b5b78] hover:text-white hover:bg-white/[0.04]"
              )}
            >
              {f}
              <span className={cx("text-[9px]", filter === f ? "text-[#050510]/60" : "text-[#3d3d55]")}>{count}</span>
            </button>
          );
        })}
      </div>

      {/* Table */}
      <div className="border border-white/[0.07] bg-[#0d0d1a]">
        <div className="grid grid-cols-[100px_1fr_90px_80px_80px_130px_130px_60px] px-4 py-2.5 border-b border-white/[0.06]">
          {["ID", "TITLE / REPO", "TYPE", "PRIORITY", "AGENT", "STATUS", "PROGRESS", "TIME"].map((h) => (
            <div key={h} className="font-mono text-[9px] uppercase tracking-[0.18em] text-[#3d3d55]">{h}</div>
          ))}
        </div>
        <div className="divide-y divide-white/[0.04]">
          {filtered.map((ticket) => (
            <div
              key={ticket.id}
              onClick={() => ticket.status !== "pending" && onViewPipeline(ticket.id)}
              className={cx(
                "grid grid-cols-[100px_1fr_90px_80px_80px_130px_130px_60px] px-4 py-3.5 items-center transition-all group",
                ticket.status !== "pending" ? "hover:bg-white/[0.02] cursor-pointer" : ""
              )}
            >
              <div className="font-mono text-[11px] text-[#5cffa3] tracking-wider">{ticket.id}</div>
              <div className="pr-4">
                <div className="font-mono text-[11px] text-white group-hover:text-[#5cffa3] transition-colors truncate">{ticket.title}</div>
                <div className="font-mono text-[9px] text-[#3d3d55] mt-0.5">{ticket.repo}</div>
              </div>
              <div>
                <span className={cx("font-mono text-[9px] uppercase tracking-wider px-2 py-0.5", TYPE_STYLES[ticket.type])}>
                  {ticket.type === "infrastructure" ? "infra" : ticket.type}
                </span>
              </div>
              <div className="flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ backgroundColor: PRIORITY_COLORS[ticket.priority] }} />
                <span className="font-mono text-[10px] uppercase tracking-wider" style={{ color: PRIORITY_COLORS[ticket.priority] }}>
                  {ticket.priority}
                </span>
              </div>
              <div className="font-mono text-[10px] text-white/50">{ticket.agent ?? "—"}</div>
              <div><StatusBadge status={ticket.status} /></div>
              <div className="pr-4">
                <ProgressBar value={ticket.progress} status={ticket.status} />
                <div className="font-mono text-[9px] text-[#3d3d55] mt-1.5">{ticket.progress}%{ticket.tests ? ` · ${ticket.tests.passed}/${ticket.tests.total} tests` : ""}</div>
              </div>
              <div className="font-mono text-[10px] text-[#4b4b60]">{ticket.duration}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Pipeline ─────────────────────────────────────────────────────────────────

function PipelineView({ selectedId }: { selectedId?: string }) {
  const activePipelineTickets = TICKETS.filter((t) => t.status !== "pending" && t.status !== "failed");
  const [selected, setSelected] = useState(selectedId ?? activePipelineTickets[0]?.id ?? TICKETS[0].id);

  const ticket = TICKETS.find((t) => t.id === selected) ?? TICKETS[0];
  const stageIndex = STATUS_STAGE_MAP[ticket.status];
  const logs = CONSOLE_LOGS[ticket.id] ?? ["[--:--:--] No logs available for this ticket."];

  return (
    <div className="p-6 space-y-4">
      {/* Ticket picker */}
      <div className="flex gap-2 flex-wrap">
        {activePipelineTickets.map((t) => (
          <button
            key={t.id}
            onClick={() => setSelected(t.id)}
            className={cx(
              "font-mono text-[10px] tracking-wider px-3 py-2 border transition-all uppercase",
              selected === t.id
                ? "border-[#5cffa3] text-[#5cffa3] bg-[#5cffa3]/10"
                : "border-white/[0.08] text-[#5b5b78] hover:border-white/20 hover:text-white"
            )}
          >
            {t.id}
          </button>
        ))}
      </div>

      {/* Ticket info bar */}
      <div className="border border-white/[0.07] bg-[#0d0d1a] px-5 py-4 flex items-center gap-8">
        <div className="flex-1 min-w-0">
          <div className="font-mono text-[10px] text-[#5cffa3] tracking-wider mb-0.5">{ticket.id}</div>
          <div className="font-mono text-sm text-white truncate">{ticket.title}</div>
        </div>
        <div className="flex items-center gap-8 text-right flex-shrink-0">
          {[
            { label: "Agent", val: ticket.agent ?? "—" },
            { label: "Repo", val: ticket.repo },
            { label: "Duration", val: ticket.duration },
            { label: "Tests", val: ticket.tests ? `${ticket.tests.passed}/${ticket.tests.total}` : "—" },
            { label: "Commits", val: ticket.commits != null ? String(ticket.commits) : "—" },
          ].map(({ label, val }) => (
            <div key={label}>
              <div className="font-mono text-[9px] text-[#4b4b60] uppercase tracking-widest">{label}</div>
              <div className="font-mono text-[11px] text-white">{val}</div>
            </div>
          ))}
          <StatusBadge status={ticket.status} />
        </div>
      </div>

      {/* Pipeline stages */}
      <div className="border border-white/[0.07] bg-[#0d0d1a] p-6">
        <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-[#5b5b78] mb-6">Execution Pipeline</div>
        <div className="flex">
          {PIPELINE_STAGES.map((stage, i) => {
            const isCompleted = ticket.status === "completed" || (stageIndex > i && stageIndex !== -1);
            const isActive = i === stageIndex && ticket.status !== "failed" && ticket.status !== "completed";
            const isFailed = ticket.status === "failed";
            const isFuture = stageIndex < i && !isCompleted;

            return (
              <div key={stage.id} className="flex-1 flex flex-col items-center relative">
                {/* Connector line */}
                {i < PIPELINE_STAGES.length - 1 && (
                  <div
                    className="absolute top-[19px] left-[calc(50%+20px)] right-[calc(-50%+20px)] h-px"
                    style={{ backgroundColor: isCompleted ? "#5cffa3" : "rgba(255,255,255,0.07)" }}
                  />
                )}
                <div
                  className={cx(
                    "w-10 h-10 flex items-center justify-center mb-3 transition-all",
                    isCompleted ? "bg-[#5cffa3] text-[#050510]" :
                    isActive ? "bg-[#5cffa3]/15 text-[#5cffa3] ring-1 ring-[#5cffa3]" :
                    isFailed && i === 4 ? "bg-[#ff4d6a]/15 text-[#ff4d6a] ring-1 ring-[#ff4d6a]" :
                    "bg-white/[0.04] text-[#3d3d55]"
                  )}
                  style={isActive ? { animation: "pulse 2s cubic-bezier(0.4,0,0.6,1) infinite" } : {}}
                >
                  <stage.icon size={15} />
                </div>
                <div className="text-center px-1">
                  <div className={cx(
                    "font-mono text-[9px] font-bold uppercase tracking-[0.15em]",
                    isCompleted || isActive ? "text-white" : isFuture ? "text-[#3d3d55]" : "text-[#3d3d55]"
                  )}>{stage.label}</div>
                  <div className="font-mono text-[8px] text-[#3d3d55] mt-0.5">{stage.description}</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Console */}
      <div className="border border-white/[0.07] bg-[#060610]">
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-white/[0.06]">
          <Terminal size={11} className="text-[#5cffa3]" />
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#5b5b78]">Agent Console · {ticket.id} · {ticket.agent}</span>
          {ACTIVE_STATUSES.includes(ticket.status) && (
            <div className="ml-auto flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-[#5cffa3] animate-pulse" />
              <span className="font-mono text-[9px] text-[#5cffa3]/60 uppercase tracking-widest">Streaming</span>
            </div>
          )}
        </div>
        <div className="p-4 h-[220px] overflow-auto space-y-1">
          {logs.map((line, i) => (
            <div
              key={i}
              className={cx(
                "font-mono text-[11px] leading-relaxed",
                line === "▋" ? "text-[#5cffa3] animate-pulse" :
                line.includes("✓") || line.includes("deployed") || line.includes("Deploying") ? "text-[#5cffa3]" :
                line.includes("✗") || line.includes("FAIL") || line.includes("failing") ? "text-[#ff4d6a]" :
                line.includes("Generated") || line.includes("Generating") || line.includes("Committing") ? "text-[#60a5fa]" :
                line.includes("Assigning") || line.includes("Cloned") || line.includes("Branch") ? "text-[#a78bfa]" :
                "text-[#4b4b68]"
              )}
            >
              {line}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Agents ───────────────────────────────────────────────────────────────────

function AgentsView() {
  return (
    <div className="p-6">
      <div className="grid grid-cols-3 gap-4">
        {AGENTS.map((agent) => (
          <div
            key={agent.id}
            className={cx(
              "border bg-[#0d0d1a] p-5 relative overflow-hidden transition-all",
              agent.status === "busy" ? "border-white/[0.1] hover:border-white/[0.16]" :
              agent.status === "idle" ? "border-white/[0.07] hover:border-white/[0.11]" :
              "border-white/[0.04] opacity-50"
            )}
          >
            {/* Status stripe */}
            <div
              className="absolute top-0 left-0 right-0 h-[2px]"
              style={{
                backgroundColor: agent.status === "busy" ? "#5cffa3" : agent.status === "idle" ? "#3d3d55" : "transparent",
              }}
            />

            <div className="flex items-start justify-between mb-4">
              <div>
                <div className="font-mono text-sm font-bold text-white tracking-wide">{agent.name}</div>
                <div className="font-mono text-[10px] text-[#4b4b60] tracking-widest uppercase mt-0.5">{agent.model}</div>
              </div>
              <div className={cx(
                "font-mono text-[9px] px-2.5 py-1 uppercase tracking-widest border flex items-center gap-1.5",
                agent.status === "busy" ? "text-[#5cffa3] border-[#5cffa3]/30 bg-[#5cffa3]/10" :
                agent.status === "idle" ? "text-[#5b5b78] border-white/[0.08]" :
                "text-[#3d3d50] border-white/[0.04]"
              )}>
                {agent.status === "busy" && <span className="w-1.5 h-1.5 rounded-full bg-[#5cffa3] animate-pulse" />}
                {agent.status}
              </div>
            </div>

            {agent.currentTask && (
              <div className="font-mono text-[10px] text-[#5b5b78] mb-4 leading-relaxed pl-3 border-l border-white/[0.07]">
                {agent.currentTask}
              </div>
            )}

            <div className="grid grid-cols-2 gap-3 mb-4">
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="font-mono text-[9px] text-[#4b4b60] uppercase tracking-widest">CPU</span>
                  <span className="font-mono text-[9px] text-white">{agent.cpuUsage}%</span>
                </div>
                <MiniBar value={agent.cpuUsage} color={agent.cpuUsage > 80 ? "#ff4d6a" : "#5cffa3"} />
              </div>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="font-mono text-[9px] text-[#4b4b60] uppercase tracking-widest">MEM</span>
                  <span className="font-mono text-[9px] text-white">{agent.memUsage}%</span>
                </div>
                <MiniBar value={agent.memUsage} color="#60a5fa" />
              </div>
            </div>

            <div className="flex items-center justify-between pt-3.5 border-t border-white/[0.06]">
              <div>
                <div className="font-mono text-[9px] text-[#4b4b60] uppercase tracking-widest mb-1">Tasks Done</div>
                <div className="font-mono text-lg font-bold text-white leading-none">{agent.tasksCompleted}</div>
              </div>
              <div className="text-right">
                <div className="font-mono text-[9px] text-[#4b4b60] uppercase tracking-widest mb-1">Success</div>
                <div className="font-mono text-lg font-bold text-[#5cffa3] leading-none">{agent.successRate}%</div>
              </div>
              <div className="text-right">
                <div className="font-mono text-[9px] text-[#4b4b60] uppercase tracking-widest mb-1">Uptime</div>
                <div className="font-mono text-xs text-white">{agent.uptime}</div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Deployments ──────────────────────────────────────────────────────────────

const ENV_BADGE: Record<string, string> = {
  production: "text-[#5cffa3] border-[#5cffa3]/30 bg-[#5cffa3]/10",
  staging: "text-[#f59e0b] border-[#f59e0b]/30 bg-[#f59e0b]/10",
  preview: "text-[#60a5fa] border-[#60a5fa]/30 bg-[#60a5fa]/10",
};

const DEP_STATUS_ICON = {
  success: CheckCircle2,
  failed: XCircle,
  rollback: RefreshCw,
};

const DEP_STATUS_COLOR = {
  success: "#5cffa3",
  failed: "#ff4d6a",
  rollback: "#f59e0b",
};

function DeploymentsView() {
  return (
    <div className="p-6">
      <div className="border border-white/[0.07] bg-[#0d0d1a]">
        <div className="grid grid-cols-[90px_1fr_100px_110px_80px_90px_100px] px-4 py-2.5 border-b border-white/[0.06]">
          {["ID", "TICKET", "ENVIRONMENT", "STATUS", "AGENT", "COMMIT", "DEPLOYED"].map((h) => (
            <div key={h} className="font-mono text-[9px] uppercase tracking-[0.18em] text-[#3d3d55]">{h}</div>
          ))}
        </div>
        <div className="divide-y divide-white/[0.04]">
          {DEPLOYMENTS.map((dep) => {
            const Icon = DEP_STATUS_ICON[dep.status];
            const color = DEP_STATUS_COLOR[dep.status];
            return (
              <div key={dep.id} className="grid grid-cols-[90px_1fr_100px_110px_80px_90px_100px] px-4 py-4 hover:bg-white/[0.02] transition-colors items-center cursor-pointer group">
                <div className="font-mono text-[10px] text-[#5cffa3] tracking-wider">{dep.id}</div>
                <div className="pr-4">
                  <div className="font-mono text-[11px] text-white group-hover:text-[#5cffa3] transition-colors truncate">{dep.ticketTitle}</div>
                  <div className="font-mono text-[9px] text-[#3d3d55] mt-0.5">{dep.repo} · {dep.duration}</div>
                </div>
                <div>
                  <span className={cx("font-mono text-[9px] uppercase tracking-wider px-2 py-0.5 border", ENV_BADGE[dep.environment])}>
                    {dep.environment}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Icon size={12} style={{ color }} />
                  <span className="font-mono text-[10px] uppercase tracking-wider" style={{ color }}>{dep.status}</span>
                </div>
                <div className="font-mono text-[10px] text-white/50">{dep.agent}</div>
                <div className="font-mono text-[10px] text-[#4b4b60]">{dep.commit}</div>
                <div className="font-mono text-[10px] text-[#4b4b60]">{dep.deployedAt}</div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ─── Settings ─────────────────────────────────────────────────────────────────

function SettingsView() {
  const [notifications, setNotifications] = useState(true);
  const [autoAssign, setAutoAssign] = useState(true);
  const [autoDeploy, setAutoDeploy] = useState(false);

  const Toggle = ({ value, onChange }: { value: boolean; onChange: () => void }) => (
    <button
      onClick={onChange}
      className={cx(
        "w-10 h-5 relative transition-colors border",
        value ? "bg-[#5cffa3]/20 border-[#5cffa3]/40" : "bg-white/[0.05] border-white/[0.08]"
      )}
    >
      <span className={cx("absolute top-0.5 h-4 w-4 transition-all", value ? "left-5 bg-[#5cffa3]" : "left-0.5 bg-[#4b4b60]")} />
    </button>
  );

  return (
    <div className="p-6 space-y-4 max-w-2xl">
      {[
        {
          section: "Agent Configuration",
          items: [
            { label: "Default Model", desc: "Model used for new agent assignments", control: <span className="font-mono text-[11px] text-[#5cffa3] border border-[#5cffa3]/30 px-3 py-1">claude-sonnet-5</span> },
            { label: "Auto-assign Agents", desc: "Automatically pick the best available agent", control: <Toggle value={autoAssign} onChange={() => setAutoAssign((v) => !v)} /> },
            { label: "Max Concurrent Agents", desc: "Limit simultaneous pipeline executions", control: <span className="font-mono text-[11px] text-white border border-white/[0.1] px-3 py-1">6</span> },
          ],
        },
        {
          section: "Pipeline Behavior",
          items: [
            { label: "Auto-deploy on Pass", desc: "Deploy automatically when all tests pass", control: <Toggle value={autoDeploy} onChange={() => setAutoDeploy((v) => !v)} /> },
            { label: "Require Code Review", desc: "AI review stage before deployment", control: <Toggle value={true} onChange={() => {}} /> },
            { label: "Branch Prefix", desc: "Prefix for auto-created branches", control: <span className="font-mono text-[11px] text-white border border-white/[0.1] px-3 py-1">feature/</span> },
          ],
        },
        {
          section: "Notifications",
          items: [
            { label: "Pipeline Alerts", desc: "Notify on stage transitions and failures", control: <Toggle value={notifications} onChange={() => setNotifications((v) => !v)} /> },
            { label: "Slack Webhook", desc: "Post updates to a Slack channel", control: <span className="font-mono text-[10px] text-[#3d3d55] border border-white/[0.06] px-3 py-1">Not configured</span> },
          ],
        },
      ].map(({ section, items }) => (
        <div key={section} className="border border-white/[0.07] bg-[#0d0d1a]">
          <div className="px-5 py-3 border-b border-white/[0.06]">
            <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5b5b78]">{section}</div>
          </div>
          <div className="divide-y divide-white/[0.04]">
            {items.map(({ label, desc, control }) => (
              <div key={label} className="flex items-center justify-between px-5 py-4">
                <div>
                  <div className="font-mono text-[11px] text-white">{label}</div>
                  <div className="font-mono text-[10px] text-[#4b4b60] mt-0.5">{desc}</div>
                </div>
                {control}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── New Ticket Modal ─────────────────────────────────────────────────────────

function NewTicketModal({ onClose }: { onClose: () => void }) {
  const [type, setType] = useState<TicketType>("feature");
  const [priority, setPriority] = useState<Priority>("medium");
  const [title, setTitle] = useState("");
  const [repo, setRepo] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-[620px] bg-[#09090f] border border-white/[0.1] flex flex-col max-h-[90vh] shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.07]">
          <div>
            <div className="flex items-center gap-2">
              <Zap size={13} className="text-[#5cffa3]" />
              <span className="font-mono text-[11px] font-bold uppercase tracking-[0.22em] text-[#5cffa3]">New Ticket</span>
            </div>
            <div className="font-mono text-[10px] text-[#4b4b60] mt-0.5 uppercase tracking-widest">Trigger an AI pipeline</div>
          </div>
          <button onClick={onClose} className="text-[#4b4b60] hover:text-white transition-colors p-1">
            <X size={15} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto p-6 space-y-5">
          {/* Title */}
          <div>
            <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5b5b78] block mb-2">Title</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Add JWT refresh token rotation"
              className="w-full bg-white/[0.04] border border-white/[0.08] px-4 py-3 font-mono text-sm text-white placeholder-[#3d3d50] outline-none focus:border-[#5cffa3]/50 transition-colors"
            />
          </div>

          {/* Type + Priority */}
          <div className="grid grid-cols-2 gap-5">
            <div>
              <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5b5b78] block mb-2">Type</label>
              <div className="flex">
                {(["feature", "bugfix", "refactor", "infrastructure"] as TicketType[]).map((t, i) => (
                  <button
                    key={t}
                    onClick={() => setType(t)}
                    className={cx(
                      "flex-1 py-2 font-mono text-[9px] uppercase tracking-widest transition-all",
                      i < 3 ? "border-r border-white/[0.07]" : "",
                      type === t
                        ? "bg-[#5cffa3] text-[#050510] font-bold"
                        : "bg-white/[0.03] border-y border-l first:border-l border-white/[0.07] last:border-r text-[#5b5b78] hover:text-white hover:bg-white/[0.05]"
                    )}
                  >
                    {t === "infrastructure" ? "infra" : t}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5b5b78] block mb-2">Priority</label>
              <div className="flex border border-white/[0.08]">
                {(["low", "medium", "high", "critical"] as Priority[]).map((p, i) => (
                  <button
                    key={p}
                    onClick={() => setPriority(p)}
                    className={cx(
                      "flex-1 py-2 font-mono text-[9px] uppercase tracking-widest transition-all",
                      i < 3 ? "border-r border-white/[0.07]" : "",
                      priority === p
                        ? "bg-[#5cffa3] text-[#050510] font-bold"
                        : "bg-white/[0.03] text-[#5b5b78] hover:text-white hover:bg-white/[0.05]"
                    )}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Repo */}
          <div>
            <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5b5b78] block mb-2">Target Repository</label>
            <input
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="e.g. user-service, api-gateway, frontend-app"
              className="w-full bg-white/[0.04] border border-white/[0.08] px-4 py-3 font-mono text-sm text-white placeholder-[#3d3d50] outline-none focus:border-[#5cffa3]/50 transition-colors"
            />
          </div>

          {/* Description */}
          <div>
            <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5b5b78] block mb-2">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe what needs to be built or fixed in detail..."
              rows={4}
              className="w-full bg-white/[0.04] border border-white/[0.08] px-4 py-3 font-mono text-sm text-white placeholder-[#3d3d50] outline-none focus:border-[#5cffa3]/50 transition-colors resize-none"
            />
          </div>

          {/* AI Instructions */}
          <div>
            <label className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#5b5b78] block mb-1">
              Agent Instructions
              <span className="ml-2 normal-case text-[#3d3d55] tracking-normal font-normal">— optional hints for the AI</span>
            </label>
            <textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="e.g. Use passport.js for OAuth. Follow existing auth patterns. Store tokens in httpOnly cookies only..."
              rows={3}
              className="w-full bg-[#5cffa3]/[0.03] border border-[#5cffa3]/20 px-4 py-3 font-mono text-sm text-white placeholder-[#3d3d50] outline-none focus:border-[#5cffa3]/40 transition-colors resize-none"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-white/[0.07]">
          <div className="font-mono text-[10px] text-[#3d3d55] uppercase tracking-widest">Best available agent auto-assigned</div>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="px-5 py-2.5 font-mono text-[11px] uppercase tracking-widest text-[#5b5b78] border border-white/[0.08] hover:text-white transition-colors"
            >
              Cancel
            </button>
            <button className="flex items-center gap-2 px-5 py-2.5 font-mono text-[11px] uppercase tracking-widest bg-[#5cffa3] text-[#050510] font-bold hover:bg-[#80ffba] transition-colors">
              <Zap size={12} />
              Launch Pipeline
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [activeNav, setActiveNav] = useState<NavView>("dashboard");
  const [showNewTicket, setShowNewTicket] = useState(false);
  const [selectedPipelineId, setSelectedPipelineId] = useState<string | undefined>();

  const handleViewTicket = (id: string) => {
    setSelectedPipelineId(id);
    setActiveNav("pipeline");
  };

  return (
    <div
      className="flex h-screen bg-[#05050d] overflow-hidden"
      style={{ fontFamily: "'Inter', sans-serif" }}
    >
      <Sidebar active={activeNav} setActive={setActiveNav} onNewTicket={() => setShowNewTicket(true)} />

      <div className="flex-1 flex flex-col overflow-hidden">
        <Header activeView={activeNav} onNewTicket={() => setShowNewTicket(true)} />
        <main className="flex-1 overflow-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {activeNav === "dashboard" && <DashboardView onViewTicket={handleViewTicket} />}
          {activeNav === "tickets" && <TicketsView onViewPipeline={handleViewTicket} />}
          {activeNav === "pipeline" && <PipelineView selectedId={selectedPipelineId} />}
          {activeNav === "agents" && <AgentsView />}
          {activeNav === "deployments" && <DeploymentsView />}
          {activeNav === "settings" && <SettingsView />}
        </main>
      </div>

      {showNewTicket && <NewTicketModal onClose={() => setShowNewTicket(false)} />}
    </div>
  );
}
