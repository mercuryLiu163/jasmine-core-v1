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

* **Token in memory only.** It is typed into a password field, used for a real
  `GET /v1/health` check, and then held in React state. There is no
  `localStorage`, `sessionStorage`, `IndexedDB`, service worker or cache write in
  the bundle, and the token never enters the URL, a log line or an error message.
  A refresh returns to the sign-in form — by design.
* **Same-origin only.** The page calls `/v1/...` on its own origin. The dev
  proxy and the static host forward that to the operator-configured loopback
  Core (`P6_CORE_ORIGIN`, default `http://127.0.0.1:8787`), which is validated at
  start-up to be loopback HTTP with no credentials and no path.
* **Path allowlist.** `server/core-paths.json` lists the frozen paths the proxy
  will forward. `/v1/auth/keys` and `/v1/audit` are deliberately absent, and only
  `GET`/`OPTIONS` are forwarded in P6-01, so the browser cannot mint a key, read
  audit, or write Truth even if its token carried wider scopes.
* **Untrusted text is data.** Event and Evidence content is rendered as React
  text nodes — there is no `dangerouslySetInnerHTML` in this bundle. Link targets
  pass `safeHref`, which accepts only `#/…`, `http:`, `https:` and `mailto:`.
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

`P6-01: PARTIAL` — the read-only dashboard, tests and real browser trace exist
and pass on the fixture above. It is **not** `P6` done: P6-02 (audited human
writes) and P6-03a/b/c (P3/P4/P7-01 inspectors) are not delivered, the browser
trace was run against a scratch fixture rather than the operator's own Core, and
an independent reviewer/validator has to confirm the result. See the run
directory under `validation-runs/p6/` for raw evidence.
