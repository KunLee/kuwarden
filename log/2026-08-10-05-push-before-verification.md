# 2026-08-10 — moving the push, and the two bugs it uncovered

Previous: [2026-08-09-04](2026-08-09-04-trigger-approval-gate-and-notification.md).

---

## Context

Item 1 on the previous session's "where to pick up" list: *move the branch push to just after
the Coder, because CI cannot run on an unpushed branch, and that blocks the CI adapter, which
blocks invariant 3 holding without qualification.*

Written that way it sounds like a two-line reordering. It is not, and the reasons it is not
are the substance of this session.

---

## What happened

### The reorder is a governance change, not a refactor

Moving the push ahead of Build & Test means **unverified model-written code reaches the
customer's SCM**. Previously nothing left the perimeter until the verifiers and a human had
both passed it. Writing that sentence out is what turned this from a task into
[ADR 0007](../docs/adr/0007-push-before-verification.md).

The ADR is worth reading for the consequences section rather than the decision. Four of the
five concerns have real mitigations already in the codebase — `protected_paths` denied before
the push, no merge credential anywhere, no pull request until after the gate, namespaced
branches. The fifth does not:

> a repository whose CI triggers on every branch push will now run a pipeline against
> agent-written code before any human has seen it. That pipeline may hold credentials KuWarden
> does not control and cannot inspect.

That is the actual price and it is stated as the actual price. The temptation was to list the
four mitigations and stop, which would have read as though the change were free.

### Invariant 10 had to move with it

`protected_paths` was enforced in `build_test`, before execution. With the push ahead of
`build_test`, that placement would let a workflow file **reach origin** and only then be
refused — and a workflow file on origin is executable *there*, whatever KuWarden decides
afterwards.

So the deny moved to Push. Build & Test keeps its check, and both now call one
`assert_not_protected`. Two nodes enforcing the same rule from two copies is the failure mode
CLAUDE.md already names for credential checks: the copies drift, and both keep passing their
own tests while denying different sets.

Net effect on the invariant table: row 10's enforcement got *earlier*, which is the direction
that matters. It is still not as early as the wording implies — the Coder has written the file
into its own sandbox by then — and the row still says so.

### The idempotency key

The push now happens up to four times per run, inside a retried Temporal activity. Two
different repeats, and conflating them would have been easy:

- **A second attempt** must add a commit.
- **A retry of an attempt whose acknowledgement was lost** must not.

The commit message distinguishes them, because it carries `kuwarden-run-id` *and*
`kuwarden-attempt`. An adapter that finds its own message on the branch tip returns instead of
committing again. This is CLAUDE.md's "key every external mutation on `run_id` + step" with the
key being something that was already going to be in the artefact.

A retry is handed the *same input state*, so `head_commit` is still unset when it runs —
which is exactly why the check has to read the remote rather than the state. `test_push.py`
constructs the retry that way on purpose; passing the returned state instead would have made
the test pass without testing anything, which is the failure the previous session already got
caught by once.

### Two pre-existing bugs surfaced

Neither was the point of the session. Both were latent because the push only ever happened
once, at the very end.

**1. Release re-resolved the default branch.** The Coder pins a base commit and reads the tree
at it, with a comment saying "a branch moving under the run cannot change what was reviewed".
Release then called `default_branch()` again and pushed against *that*. The comment was true of
the Coder and false of the system. Fixed by pinning `base_branch` / `base_commit` onto
`FlowState`, which Push and Release both read.

**2. The Azure Repos adapter could not create a branch.** `EMPTY_OBJECT_ID` was defined, with a
docstring saying it is "used when creating a branch", and never referenced anywhere. The push
passed `base.commit` as `oldObjectId`, which Azure rejects for a ref that does not exist. It
now creates the ref at the base first, then pushes onto it.

An unused constant with a docstring explaining its purpose is a decent signal that the thing it
was written for did not get finished.

---

## Decisions

| | |
|---|---|
| Split ⑦ Release into Push (③ⓑ, in the loop) and Release (the pull request only) | [ADR 0007](../docs/adr/0007-push-before-verification.md) |
| Every attempt's tree is `pinned base + that attempt's edits`; the commit is parented on the branch tip | ADR 0007 §3 |
| Pushes are idempotent on the commit message, and never forced | ADR 0007 §4 |
| `branch_pushed` carries no `control_mode` | ADR 0007 §5 |

The last one is small and deliberate. A branch push *is* an external effect in the ordinary
sense, and calling it `external_effect` with `control_mode: authorized` would have been the
path of least resistance. But `authorized` means "KuWarden gated one of the three control
points in ADR 0004", and a push is not one of them. The word is worth something only while it
is narrow.

---

## Corrections

**The tree source and the parent are not the same thing, and I nearly made them the same.**
The first shape of `push_change` took a single `base` meaning both "what this is applied to"
and "what this is parented on". That works for the first push and quietly breaks on the
second: the tree would then be *previous attempt + this attempt's edits*, so a file changed in
attempt 1 and left alone in attempt 2 survives on the branch while being absent from the diff
Build & Test graded. The branch CI runs against and the change under review would disagree
about what the change is — which is the exact class of thing this product exists to prevent.
Split into `base` (tree) and `parent` (history).

**The Azure adapter still has this gap and cannot easily be fixed.** The Pushes API expresses
a commit as changes relative to the branch tip rather than as a tree, so a second push there
really is `tip + edits`. Closing it needs the base content of every path the run has touched,
which `FlowState` does not carry. Written down in the adapter docstring, the protocol
docstring, and ADR 0007's "what we owe" — not silently left as a difference between two
implementations of one interface.

**A test that passed for the wrong reason, caught before it mattered.** The existing GitHub
adapter tests kept passing after the change because their mock transport 404s on unrouted
paths, so `_branch_tip` returned `None` and took the create path by accident rather than by
assertion. Added explicit fast-forward and already-landed cases so the update path is
exercised on purpose. The Azure equivalent did *not* pass — it 404'd on `/refs` and failed
loudly, which is how the two suites differ in temperament.

---

## Open

1. **The CI adapter.** Until it exists this ADR has paid its cost and collected none of its
   benefit. `CIResult.source` still reads `"sandbox"` and the caveat still appears on the
   approval page.
2. **Branch cleanup on abort.** `compensate` is still `return state`. A rejected run now leaves
   a branch on the customer's remote, which was invisible while the push happened after the
   gate and is not now. This got more urgent today.
3. **Trigger guidance for operators**, per the unmitigated residue in ADR 0007.
4. Unmoved from previous sessions: the four verifier nodes are still stubs, no webhook
   receiver, the `control_mode` ADR 0004 deviation on `main`, `ROADMAP.md` contradicting ADR
   0001/0002.

---

## Artefacts

**New**
- `docs/adr/0007-push-before-verification.md`
- `engine/nodes/push.py`
- `tests/test_push.py`

**Changed**
- `engine/flows/delivery.py` — Push inside the loop, `branch_pushed` event
- `engine/nodes/release.py` — pull request only; targets the pinned base
- `engine/nodes/coder.py` — pins `base_branch` / `base_commit` onto the state
- `engine/nodes/build_test.py` — shared `assert_not_protected`
- `engine/policy/protected_paths.py` — `assert_not_protected`
- `engine/state.py` — `base_branch`, `base_commit`, `head_commit`
- `engine/adapters/protocols.py` — `push_change` contract: create-or-update, idempotent, never forced
- `engine/adapters/scm/github.py`, `engine/adapters/scm/azure_repos.py`
- `engine/adapters/http.py`, `engine/errors.py` — `NotFound`, `PATCH`
- `tests/conftest.py` — the fake platform now holds real branch and commit state
- `CLAUDE.md` (invariants 3, 10), `ARCHITECTURE.md`, `docs/END_TO_END.md`, `docs/GLOSSARY.md`,
  `docs/adr/README.md`
