"""The one way a repository is rendered into a prompt.

The decision, its three prior failures and its known weaknesses are in
[ADR 0010](../../docs/adr/0010-context-assembly.md).

Extracted so the Coder and the verifiers show a model the *same* repository under the same
rules. Two implementations would drift — one inlining lockfiles the other withheld, one
treating a file as binary that the other decoded — and a verifier disagreeing with the Coder
about what the repository contains is indistinguishable, in the record, from a verifier
disagreeing about the change.

Two files are listed but never inlined, and both exclusions are about what the content *is*
rather than how much room is left:

**Binaries** do not decode, and their bytes are noise to a model.

**Lockfiles** are machine-generated. `package-lock.json` in the demo application is 426 KB
against 300 KB for the entire hand-written codebase — 59% of every prompt, describing a
dependency graph no ticket asks to change. Withholding one is safe in a way withholding
source is not: the Coder must not edit a lockfile anyway, since a dependency change needs a
resolver run and the sandbox has no network.

There is deliberately no size cap. A cap does not choose *less* context, it chooses an
arbitrary subset — and the alphabetical one this replaced sent `app/admin/` to a ticket about
`components/Header.tsx`, then failed three nodes downstream with a message naming neither.
What replaced it is selection *by the model*, expanded along the import graph; see the ADR.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Machine-generated dependency manifests, by exact filename so the rule is checkable.
GENERATED_FILES = frozenset(
    {
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
        "uv.lock",
        "poetry.lock",
        "Cargo.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
    }
)


def is_inlinable(path: str, body: bytes) -> bool:
    """Whether this file's contents belong in a prompt, as opposed to only its name."""
    # A NUL in the first kilobyte is the same heuristic git uses to call a file binary.
    return b"\x00" not in body[:1024] and path.rsplit("/", 1)[-1] not in GENERATED_FILES


#: Module specifiers, as written in source. Covers the ES/TS forms (`import x from "y"`,
#: `export * from "y"`, `import("y")`, `require("y")`) and Python's `from x import y`.
#: Deliberately a regex rather than a parser: this decides what to *offer* a model, and being
#: approximately right is fine when the model is told what was withheld and can ask for it.
_SPECIFIER = re.compile(
    r"""(?:from|import|require)\s*\(?\s*["']([^"']+)["']|^\s*from\s+([\w.]+)\s+import\s""",
    re.MULTILINE,
)

#: How far to follow imports from a seed file. Two hops reaches a component's children and
#: their shared helpers, which is where the answer to "does this call site still work" lives.
#: Unbounded closure on a real application is the whole repository again, in slow motion.
_MAX_DEPTH = 2


def imports_of(body: bytes) -> list[str]:
    """Every module specifier this file references, as written."""
    text = body.decode("utf-8", "replace")
    return [a or b for a, b in _SPECIFIER.findall(text) if (a or b)]


def _resolve(specifier: str, source: str, known: set[str]) -> str | None:
    """Turn a specifier into a repository path, or None if it names a package.

    Handles the three shapes an application actually uses: `@/components/Header` (the alias
    Next.js and Vite both default to), `./sibling`, and `engine.nodes.coder`. A bare `react`
    resolves to nothing, which is correct — it is a dependency, not a file of theirs.
    """
    if specifier.startswith("@/"):
        stem = specifier[2:]
    elif specifier.startswith("."):
        base = source.rsplit("/", 1)[0] if "/" in source else ""
        parts = [p for p in f"{base}/{specifier}".split("/") if p not in ("", ".")]
        stack: list[str] = []
        for part in parts:
            if part == "..":
                if stack:
                    stack.pop()
            else:
                stack.append(part)
        stem = "/".join(stack)
    elif "." in specifier and "/" not in specifier:
        stem = specifier.replace(".", "/")
    else:
        stem = specifier

    for candidate in (
        stem,
        *(f"{stem}{ext}" for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".css", ".py")),
        *(f"{stem}/index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx")),
    ):
        if candidate in known:
            return candidate
    return None


def closure(files: dict[str, bytes], seeds: Iterable[str], depth: int = _MAX_DEPTH) -> set[str]:
    """`seeds` plus what they import, transitively, bounded by `depth`.

    The point is to send a model the neighbourhood of a change rather than the repository:
    the file it is editing, what that file depends on, and one more hop. Sending everything
    was correct and cost roughly twenty times what it needed to; sending only the changed
    files is what made verifiers reject valid work because they could not open a referenced
    file.
    """
    known = set(files)
    chosen = {s for s in seeds if s in known}
    frontier = set(chosen)
    for _ in range(depth):
        found: set[str] = set()
        for path in frontier:
            for specifier in imports_of(files[path]):
                target = _resolve(specifier, path, known)
                if target is not None and target not in chosen:
                    found.add(target)
        if not found:
            break
        chosen |= found
        frontier = found
    return chosen


def dependents(files: dict[str, bytes], targets: Iterable[str]) -> set[str]:
    """Every file that imports one of `targets` — the closure run backwards.

    `closure` answers "what does this file depend on". This answers "who depends on it", and
    they are different questions serving different readers.

    The `regression_risk` verifier exists to ask *what else does this break*, and the only
    honest answer lives in the callers — a changed prop, a changed return type or a removed
    export is invisible in the file that changed and obvious in the file that uses it. Its own
    prompt has been reduced to admitting this: "you are seeing only the changed files, so say
    plainly when a call site you would need is not in front of you". That admission is
    accurate reporting of a context problem, not a review.

    Requires parsing every file rather than following a chain, so it is linear in repository
    size. That is cheap — no model is involved — and it is the reason it is computed here
    rather than asked for.
    """
    known = set(files)
    wanted = {t for t in targets if t in known}
    if not wanted:
        return set()
    found: set[str] = set()
    for path, body in files.items():
        if path in wanted:
            continue
        for specifier in imports_of(body):
            if _resolve(specifier, path, known) in wanted:
                found.add(path)
                break
    return found


def render(
    files: dict[str, bytes], tag: str, include: Iterable[str] | None = None
) -> tuple[str, dict[str, int]]:
    """The repository as prompt text, plus counts for the audit record.

    **The listing is always complete; only the contents are selective.** `include` names which
    files are inlined — `None` means all of them. That asymmetry is the whole safety property:
    a model always knows every path that exists, so it can never conclude a file is absent, and
    it is told in as many words that it may ask for one it was not given.

    Selecting contents is what makes a run affordable. Sending the entire repository to five
    calls cost ~621,000 input tokens per run against ~4,000 of output — the models were barely
    writing, they were re-reading a codebase nobody had changed. But an arbitrary selection is
    how this went wrong before: an alphabetical byte budget sent `app/admin/` to a ticket about
    `components/Header.tsx` and failed three nodes later naming neither. So the rule is never
    "guess what matters" — it is the model's own answer, or the import closure of the diff.

    `tag` names the enclosing element, because the Coder and the verifiers describe the same
    tree differently ("what you may change" versus "what this change was made against").
    """
    listing = "\n".join(sorted(files))
    wanted = set(files) if include is None else {p for p in include if p in files}
    shown: list[str] = []
    unreadable = 0
    withheld = 0
    bytes_used = 0

    for path in sorted(files):
        body = files[path]
        if not is_inlinable(path, body):
            unreadable += 1
            continue
        if path not in wanted:
            withheld += 1
            continue
        bytes_used += len(body)
        shown.append(f"<file path={path!r}>\n{body.decode('utf-8', 'replace')}\n</file>")

    parts = [f"<{tag}_listing>\n{listing}\n</{tag}_listing>", *shown]
    if unreadable or withheld:
        # Named rather than silently absent, and the two reasons are kept apart. "Not shown
        # because it is a lockfile" and "not shown because nobody asked for it" call for
        # different responses, and a model that cannot tell them apart either edits a lockfile
        # or concludes a source file does not exist.
        reasons = []
        if withheld:
            reasons.append(
                f"{withheld} were not selected for this request — say so in `reasoning` if you "
                "need one and it will be provided"
            )
        if unreadable:
            reasons.append(f"{unreadable} are binary or machine-generated (do not edit a lockfile)")
        parts.append(
            "<note>Every file in the repository is listed above. Contents are shown for the "
            f"selected ones only: {'; '.join(reasons)}. Never assume a listed file is empty "
            "or irrelevant because its contents are absent.</note>"
        )

    return "\n\n".join(parts), {
        "listed": len(files),
        "shown": len(shown),
        "withheld": withheld,
        "omitted": unreadable,
        "bytes_used": bytes_used,
    }
