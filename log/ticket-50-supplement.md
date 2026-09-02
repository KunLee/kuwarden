# Ticket 50 — acceptance criteria, added after two failed attempts

*Paste into the work item description, below the existing text. Written from the findings four
verifiers recorded on runs `521065f3` and `d49100db` — findings that were correct, were never
shown to the approver, and describe precisely what both attempts got wrong.*

---

## What is still wrong after two attempts

Two changes have been merged for this ticket and neither implements it. Both produced a
`UserMenu` dropdown, and both left the same three gaps:

1. **The admin entry point was duplicated, not consolidated.** `components/Header.tsx` still
   renders `AdminNavLink` alongside the new dropdown, so an admin sees *two* separate ways into
   the admin area — the old icon link and a new menu item. The ticket asks for the profile
   control itself to offer the admin jump. The old separate icon must be **removed**, not kept.

2. **Two menu items lead to the same place.** `Profile details` and `Manage profile` both
   navigate to `/settings/profile`. The dropdown therefore offers one action wearing two
   labels. The ticket describes two distinct things — updating profile details, and managing
   the profile — so either give them two distinct destinations, or collapse them into one item.

3. **The menu is not gated on sign-in.** The dropdown renders `Profile details` and
   `Manage profile` unconditionally, regardless of the `signedIn` state fetched from
   `/api/auth`. A signed-out visitor can open the menu and click through to `/settings/profile`.
   The control this replaced was a single `/account` link, which the route itself guarded.

## Acceptance criteria

- [ ] An admin sees **exactly one** admin entry point, and it is inside the profile dropdown.
      `AdminNavLink` is no longer rendered from `Header.tsx`.
- [ ] No two items in the dropdown navigate to the same route.
- [ ] A signed-out visitor does not see profile or settings items in the dropdown.
- [ ] A signed-in non-admin sees profile items and **no** admin item.
- [ ] `/api/auth` and `/api/admin` are each fetched **once** per header render, not once per
      component. Both attempts duplicated `AdminNavLink`'s fetch logic, doubling the calls to
      both endpoints on every page load.
- [ ] The change ships with tests covering the three branches above (signed out, signed in,
      admin). Both attempts added zero test lines for ~87 lines of new interactive behaviour,
      and this is what the `test_evidence` verifier blocked on — twice.

## Constraints

- **Do not change default prop values in `components/ui/dropdown-menu.jsx`.** The first attempt
  changed `className` / `inset` / `checked` from `undefined` to `''` / `false`. Because that
  file is untyped JavaScript, TypeScript infers each component's props from the destructuring
  pattern — a parameter with a default becomes optional, one without becomes required. Changing
  a default therefore flips a prop between optional and required **for every call site in the
  repository**, and it broke the build on `components/Header.tsx` at three call sites that had
  nothing to do with this ticket. Adapt the call sites, not the shared primitive.

- **Admin visibility in the menu is a UI convenience, not an access control.** The check is
  client-side only. Whatever the menu shows or hides, `/admin` must enforce authorisation
  server-side; do not treat hiding the item as protection.

## Note on the current state of `main`

`main` currently contains a hand-resolved merge (`91cf3abd`, merged as `b5b4ef75`) of two
different solutions to this ticket, produced by two runs that branched from the same base and
never saw each other. That merge was not verified by anything. Consider reverting it to
`263a97ec` before starting, so the next attempt begins from a state that CI and the sandbox
both passed.
