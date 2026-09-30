# Jasmine Core Dashboard (P6-01, read-only)

A browser UI for inspecting the Jasmine Core V1 Truth store. It is a **window on
Core**, never a source of truth: it issues only the frozen `GET` endpoints of
`docs/api/core-api-v1.md`, it never writes, and it never decides whether a Step
is verified.

This is the **P6-01** delivery: the part of the P6 taskbook that the P1-scope
Core can already support. Interpretation/Resolver (P2), Context/Checkpoint (P3),
Memory (P4) and Validation (P7-01) are **not** wired — those screens say
`未接入` and name the owning phase instead of showing sample data.

## What is implemented

| Area | Screen | Core reads |
| --- | --- | --- |
| Health & schema | Overview | `/v1/health`, `/v1/meta/schema` |
| Projects | Overview, Project | `/v1/projects`, `/v1/projects/{id}` |
| Registry | Overview | `/v1/hosts`, `/v1/actors` (static rows, no online state) |
| Tasks | Project | `/v1/tasks?project_id=…` with `limit`/`offset` |
| **Step board (看板)** | Task → 看板 | `/v1/tasks/{id}/steps` |
| State history | Task → 历史 | `/v1/tasks/{id}/history`, `/v1/steps/{id}/history` |
| Conversation | Conversation | `/v1/events` with `session_id`/`task_id`/`project_id`/`event_type`/`source_system`/`after_seq` |
| Source Event | Event deep link | `/v1/events/{id}` |
| Authority | Rules | `/v1/rules/active`, `/v1/rules/{id}`, `/v1/rules/{id}/history` |
| Evidence | Task → Evidence, Evidence deep link | `/v1/tasks/{id}/evidence`, `/v1/evidence/{id}` |

The board's columns are **Step** statuses (`PLANNED`, `IN_PROGRESS`, `EXECUTED`,
`VERIFIED`, `ACCEPTED`, `FAILED`, `BLOCKED`, `STALE`, `SKIPPED`). The **Task**
status (`ACTIVE`/`BLOCKED`/`ACCEPTED`/`CANCELLED`) is shown once above the board
and is never mixed into the columns. A Step status this build does not know gets
its own loud column; nothing is folded into "other".

## Security posture

* **Token in memory only, validated by an authenticated read.** It is typed into
  a password field and checked with `GET /v1/projects?limit=1`, which requires
  `objects:read`; only then does it become the session token. (`GET /v1/health`
  is a public endpoint that answers 200 for *any* token, so it is deliberately
  not used for validation — the browser trace proves a bogus token is refused.)
  The token is held in React state only: no `localStorage`, `sessionStorage`,
  `IndexedDB`, service worker or cache write exists in `web/src`, and it never
  enters the URL, a log line or an error message. A refresh returns to the
  sign-in form — by design.
* **Same-origin only.** The page calls `/v1/...` on its own origin. The dev
  proxy and the static host forward that to the operator-configured loopback
  Core (`P6_CORE_ORIGIN`, default `http://127.0.0.1:8787`), which is validated at
  start-up to be loopback HTTP with no credentials and no path.
* **Path allowlist.** `server/core-paths.json` lists the frozen paths the proxy
  will forward. `/v1/auth/keys` and `/v1/audit` are deliberately absent, and only
  `GET` is forwarded in P6-01 (same-origin, so no preflight is needed; forwarding
  `OPTIONS` was removed after review), so the browser cannot mint a key, read
  audit, issue a write, or even probe a mutation path through this channel.
  The allowlist is read **once at start-up** (`createDashboardServer` ->
  `loadAllowlist()`), so editing `server/core-paths.json` requires restarting the
  host: a long-running process keeps the allowlist it booted with.
* **Untrusted text is data.** Event and Evidence content is rendered as React
  text nodes; `web/src` contains no `dangerouslySetInnerHTML` and no `innerHTML`
  assignment (the occurrences in `dist/assets/*.js` are React DOM internals).
  No link target is built from Core data — every link is an internal `#/…` route
  with a percent-encoded ID. `safeHref` (only `#/…`, `http:`, `https:`, `mailto:`)
  is the unit-tested policy any future external link must pass; today it guards
  the `artifact_uri` note, which is rendered as inert text and never opened.
  Credential-shaped text is masked by default with a per-field "显示原文" toggle.
* **Honest failure states.** Loading, empty, refused and not-implemented are four
  different things on screen. `count` from Core is described as *this page's*
  length, never as a total, and a page shorter than `limit` is the only proof of
  the end of a list.
* **CSP** is sent as a real response header by `server/serve.mjs`
  (`frame-ancestors` cannot be delivered in a meta element, so it is not faked
  there).

## Running it

```bash
cd web
npm install                      # uses the committed package-lock.json
npm run typecheck && npm test    # unit + component tests
npm run build                    # tsc --noEmit && vite build

# development (Vite dev server + proxy to a loopback Core)
P6_CORE_ORIGIN=http://127.0.0.1:8789 npm run dev

# static bundle on loopback with the same proxy and allowlist
P6_CORE_ORIGIN=http://127.0.0.1:8789 npm run build
P6_CORE_ORIGIN=http://127.0.0.1:8789 npm start -- --port 5180
```

Then open the printed URL, paste a **read-only** token with these scopes, and
sign in:

```
objects:read  events:read  state:read  authority:read  evidence:read
```

The page never mints a token, never bootstraps an admin key and never runs a
fingerprint scan. Mint a scoped token yourself, for example:

```bash
JASMINE_CORE_DB=/path/to/core.db PYTHONPATH=src \
  python3 -m jasmine_core.cli bootstrap --scopes objects:read events:read state:read authority:read evidence:read
```

(That command prints the token once; it is not recoverable, and the dashboard
never stores it.)

## Fixture and end-to-end trace

`scripts/p6/p6_scratch.py` builds a throwaway database with realistic data
through the real HTTP API, and `scripts/p6/browser_trace.mjs` drives a real
headless Chrome through sign-in, source tracing, deep-link refresh and sign-out,
writing screenshots plus a redacted request log to a run directory:

```bash
cd ..
python3 scripts/p6/p6_scratch.py build --out /private/tmp/p6-scratch
python3 scripts/p6/p6_scratch.py serve --out /private/tmp/p6-scratch --port 8789 &

(cd web && P6_CORE_ORIGIN=http://127.0.0.1:8789 npm run build && \
  P6_CORE_ORIGIN=http://127.0.0.1:8789 node server/serve.mjs --root dist --port 5180 &)

node scripts/p6/browser_trace.mjs --ui http://127.0.0.1:5180 \
  --token-file /private/tmp/p6-scratch/tokens.json --token-key read \
  --project prj_… --task tsk_… --rule rul_… --evidence evd_… \
  --out validation-runs/p6/$(date -u +%Y%m%dT%H%M%SZ)-p6-01-browser
```

The trace never writes the Authorization header value; it records only
`present(redacted)`/`absent`.

## Requirement trace

`P6-01-COMPLIANCE.md` maps every P6-01 work item (P6-A01…A08) and every
applicable negative case to its implementation, its evidence, and what is
explicitly *not* delivered.

## Deliberately not implemented here

| Screen | Owner | Waiting on |
| --- | --- | --- |
| Interpretation / Review, Correct / Reject / Re-run | P2 | `/interpret`, interpretation and review contracts |
| Checkpoint compare | P3 | checkpoint read API |
| Context inspector (actual injection) | P3 | persisted pack + injection receipt |
| Memory jobs / recall / management | P4 | Memory adapter read + management API |
| Validation inspector (run list, artifacts) | P7-01 | minimal validation read API + safe artifact contract |
| online / backup / storage | P5 / P7 | health read contracts |
| All writes (Rule, Task, Step, Evidence confirmation) | P6-02 | P2 policy gate + independent permission Gate |

## Phase status

`P6-01: T01 PASS, T02 PASS, T07 PARTIAL` on an isolated scratch fixture — the
independent r3 verdict on `f7addbf`, whose product code and built bundle are
byte-identical to the tip (see `P6-01-COMPLIANCE.md` for the commit binding of
each verdict). It is **not** `P6` done: P6-02 (audited human writes) and
P6-03a/b/c (P3/P4/P7-01 inspectors) are not delivered, no Gate ran against the
operator's own Core or a real captured conversation, and throttling plus an
automated contrast sweep remain uncovered. See the run directories under
`validation-runs/p6/` for raw evidence.
