#!/usr/bin/env node
// Stop hook — mirror the full session transcript into log/raw/.
//
// Registered in .claude/settings.json. Runs after every assistant turn, so the
// mirror is always current without anyone having to remember.
//
// Contract: reads the hook payload as JSON on stdin, writes nothing to stdout,
// and always exits 0. A logging hook must never be able to interrupt a session.
//
// See log/README.md for why this exists and how it relates to docs/adr/.

import { readFileSync, copyFileSync, mkdirSync, existsSync, appendFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Resolve the project root from this file's own location (<root>/.claude/hooks/),
// not from cwd — hooks should not care where they were invoked from.
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const RAW_DIR = join(ROOT, "log", "raw");

function readStdin() {
  try {
    return readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

function note(message) {
  // Best-effort diagnostics. If even this fails, stay silent.
  try {
    mkdirSync(RAW_DIR, { recursive: true });
    appendFileSync(
      join(RAW_DIR, "_hook.log"),
      `${new Date().toISOString()}  ${message}\n`,
    );
  } catch {
    /* ignore */
  }
}

try {
  const raw = readStdin();
  if (!raw.trim()) process.exit(0);

  const payload = JSON.parse(raw);
  const src = payload.transcript_path;

  if (!src || !existsSync(src)) {
    note(`no transcript_path in payload (session ${payload.session_id ?? "?"})`);
    process.exit(0);
  }

  const day = new Date().toISOString().slice(0, 10);
  const session = String(payload.session_id ?? "unknown").slice(0, 8);
  const dest = join(RAW_DIR, `${day}-${session}.jsonl`);

  mkdirSync(RAW_DIR, { recursive: true });

  // The transcript is cumulative JSONL, so a whole-file copy is both correct and
  // idempotent — appending would duplicate every earlier turn on each run.
  copyFileSync(src, dest);
} catch (err) {
  note(`failed: ${err?.message ?? err}`);
}

process.exit(0);
