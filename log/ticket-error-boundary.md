# Ticket draft — a client error must be reportable

*For sasagayo. Paste into the work item; the title is the first line.*

---

**Add a global error boundary, and report client errors to the server**

## Problem

When a client-side error occurs, the app renders a black page reading *"This page couldn't
load"* with Reload and Back, and nothing else. No message, no error id, nothing a user can
quote and nothing anyone can search for.

This is not hypothetical. Clicking the user avatar threw `Base UI error #31`
(`MenuGroupContext is missing`), and the only way to discover that was to open devtools and
read the console by hand. A user reporting it could truthfully say no more than *"clicking user
profile shows an error page"* — which is what the bug report said, and it was all they had.

There is currently no `error.tsx`, no `global-error.tsx` and no `not-found.tsx` anywhere in
`app/`.

## Why it is worth doing

The console line was worth more than everything else in that bug report combined: with it, the
cause was identified in one step; without it, the defect is invisible in the source — the
components involved each look correct in isolation and only their composition is wrong.

Right now getting that line depends on the reporter knowing to press F12. It should be a
property of the application, not of the reporter's skill.

## Acceptance criteria

- [ ] A client-side render error shows a page that includes the error **digest** (React's
      production identifier) and, where available, the message — in a form a user can select
      and copy.
- [ ] The same error is **reported to the server**, carrying at least: message, digest, stack,
      the URL it happened on, user agent, and a timestamp.
- [ ] Reload and Back still work from that page.
- [ ] A missing route renders a not-found page rather than the generic error page — the two
      are different facts and should not look identical.
- [ ] Both branches work signed out and signed in. Errors do not wait for a session.

## Constraints

- `app/global-error.tsx` in the App Router replaces the root layout when it renders, so it
  must supply its own `<html>` and `<body>`. A boundary that assumes the layout is present
  fails exactly when it is needed.
- **Do not include request bodies, cookies, or authorization headers** in the report. A stack
  trace and a URL are diagnostic; a session token in a log is an incident.
- The reporting endpoint is reached by users who may not be signed in, so it cannot require
  auth — bound it some other way (a size cap and a simple rate limit are enough) rather than
  leaving it unbounded.
- Keep the visible page calm. It is shown to someone whose action just failed; the digest
  belongs there because it makes the report useful, not to look technical.

## Out of scope

Fixing any particular error. This ticket is about being able to see them.
