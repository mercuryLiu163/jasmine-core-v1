# P6-01 compliance matrix

Requirement-by-requirement trace from the P6 taskbook (P6-01 work items in §4
and the negative cases in §7) to the code that implements it and the evidence
that exercises it. Anything not listed here is not claimed.

Legend: **D** = delivered, **P** = partially delivered (the gap is named),
**N/A** = out of P6-01 scope by the taskbook, **U** = explicitly 未接入.

## §4 work items

| Item | State | Implementation | Evidence |
| --- | --- | --- | --- |
| P6-A01 app skeleton & auth | D | `web/src/app/session.tsx` (memory-only token), `web/src/components/SignIn.tsx` (password field, accepted only after an **authenticated** `GET /v1/projects?limit=1`; `GET /v1/health` is public and cannot validate), `web/src/api/client.ts` (per-request `Authorization`, no storage, generated `X-Request-Id`) | `web/tests/app.test.tsx` ("accepts a token only after a real authenticated read…", "refuses a bogus token that the public health check would have accepted", "treats a missing scope as a refusal…", "stops sending Authorization after 退出"); browser steps 1–5, 19 |
| P6-A02 browser access | D | `web/server/core-proxy.ts` + `web/server/core-paths.json` (frozen allowlist, loopback-only target from `P6_CORE_ORIGIN`, no injected token, **GET only** — `OPTIONS` was removed after an independent review found it was forwarded to Core), `web/server/serve.mjs` (static host, same allowlist, CSP/XFO headers, rejects embedded credentials and malformed paths), `web/vite.config.ts` (dev proxy) | `web/tests/proxy.test.ts`, `web/tests/endpointContract.test.ts`, `tests/p6/test_dashboard_proxy_contract.py`, `dev-proxy-check.txt`, `proxy-contract.raw.txt` in the run directory |
| P6-A03 Overview / Project | D | `web/src/views/OverviewView.tsx`, `web/src/views/ProjectView.tsx`; loaded-range wording, unavailable panels for Hindsight/Backup/online | browser steps 5–6; `truth-before-after.json` shows the reads behind them |
| P6-A04 Task board / detail | D | `web/src/views/TaskBoard.tsx` (Step-state columns, Task status separate, outstanding-status summary, unknown-status column), `web/src/views/TaskHistory.tsx`, `web/src/views/TaskEvidence.tsx` | `web/tests/app.test.tsx` ("keeps Task status separate from Step columns and never claims all done"); browser step 7 |
| P6-A05 Conversation / Event | D | `web/src/views/ConversationView.tsx`, `web/src/components/EventRow.tsx` (machine-event label, no local chat filler), `web/src/views/EventView.tsx` (deep link) | browser steps 9–11; `web/tests/app.test.tsx` ("labels machine events so a tool result cannot read as a user message") |
| P6-A06 Rule / Evidence | D | `web/src/views/RulesView.tsx` (active list + read-by-ID + history, nullable provenance rendered defensively), `web/src/views/EvidenceView.tsx`, `web/src/views/TaskEvidence.tsx` (Core-recorded `evidence_ids` from `step.transitioned` **and** `task.accepted`, reference status, labelled criteria matrix) | browser steps 12–13; `web/tests/taskEvidence.test.tsx` (task.accepted record; refused steps read), `web/tests/app.test.tsx`, `web/tests/text.test.ts` for INFO≠PASS vocabulary |
| P6-A07 lists & consistency | D | `web/src/app/usePagedResource.ts` (cursor paging, cancellation, stale-response rejection), `web/src/app/useResource.ts` (scope-change reset) | `web/tests/usePagedResource.test.tsx` (5 cases incl. "clears the previous scope…" and "drops a superseded response"), `web/tests/app.test.tsx` project-isolation case |
| P6-A08 human usability | P | `web/src/components/StatusBadge.tsx` (glyph + literal token, never colour-only), `web/src/components/RawText.tsx` / `JsonBlock.tsx` (collapse, masked copy), `web/src/components/TableScroll.tsx` (wide tables scroll instead of widening the page), skip link, labelled inputs, focus styles, `aria-live` loading, `role="alert"` failures | browser steps 8–10 (accessibility name scan, badge glyph check, narrow-viewport 480 px check); manual screenshot review. **Remaining gap:** no automated colour-contrast sweep (manual only) |
| P6-01 read-only scope set | D | `web/src/app/session.tsx` `REQUIRED_READ_SCOPES`; no admin, no fingerprint scan, no writes anywhere in the bundle | `proxy-contract.raw.txt` (`/v1/auth/keys` and every non-GET refused), `truth-before-after.json` (`unchanged`) |

## §7 negative cases (P6-01 applicable)

| Negative case | State | Where it is exercised |
| --- | --- | --- |
| No/revoked/missing-scope token | D | 401 path in `web/tests/app.test.tsx` and `web/tests/client.test.ts`; real curl in `proxy-contract.raw.txt` |
| Agent key attempting management/acceptance | N/A | P6-02; no write UI exists in P6-01 |
| API 404/401/403/422/503, lost connection, unknown enum, paging truncation | P | `web/src/api/errors.ts` + `web/src/components/States.tsx`; `web/tests/degraded.test.tsx` drives 503 (must not read as "0 projects"), 403 (names the scope, reuses one credential) and an unreachable Core through the real views; unknown Step status column in `web/tests/app.test.tsx`; paging cursor in `web/tests/usePagedResource.test.tsx`. **Gap:** paging truncation and API-down/restore are not driven through the browser |
| secret-looking raw text, HTML/script, dangerous links | D | `web/src/lib/text.ts` (`maskCredentials`, `safeHref`), `web/src/components/RawText.tsx`; `web/tests/text.test.ts` and "renders Event text as text, masks credentials…" in `web/tests/app.test.tsx` |
| project A slow request landing after switching to B | D | `web/tests/usePagedResource.test.tsx` and the project-isolation component test |
| Memory unavailable / old checkpoint / Context over budget | U | P3/P4 — `web/src/views/PendingView.tsx` |
| Validation missing Context/Agent transcript | U | P7-01 — `web/src/views/PendingView.tsx` |
| stale `expected_revision` / duplicate click / idempotency | N/A | P6-02; no write forms in P6-01 |
| criteria/Evidence missing, old fingerprint, BROKEN_REFERENCE, no human confirmation | P | Read-side only: `reference_status` badge and the labelled criteria matrix in `TaskEvidence.tsx`/`EvidenceView.tsx`, with the explicit note that Core is the judge. The write-side rejection is P6-02 |

## Deliberately not delivered in this PR

| Area | Owner | Why |
| --- | --- | --- |
| P6-02 Extraction review, audited human writes | P6-02 | Needs frozen P2 contracts and the independent permission Gate |
| P6-03a Checkpoint / Context inspector | P3 | Persisted pack + injection receipt contracts not frozen |
| P6-03b Memory jobs / recall | P4 | Memory read + management API not frozen |
| P6-03c Validation inspector | P7-01 | Minimal validation read API + safe artifact contract not frozen |

## Independent review / validation

- Independent review of `4a4ee17`: 4 blockers raised (sign-in validated with the
  public `/v1/health`; the proxy forwarded `OPTIONS`; the Evidence panel missed
  `task.accepted`; a refused `steps` read rendered as "no requirements"). All four
  are fixed, each with a regression test; r2 confirmed the two that can be
  falsified from a browser.
- Independent validation of `4a4ee17` (r1): P6-T01/T02 **PASS**, T07 **PARTIAL**,
  plus an integrity incident — the worktree was committed to mid-run, so that
  verdict binds `4a4ee17` only.
- Independent re-validation of `a5a92ca` (r2): P6-T01/T02 **PASS**, T07
  **PARTIAL**; the tracked commit did not move during the run. It also found a
  stale pre-fix server process on the dashboard port (the allowlist is read once
  at start-up), the stale sign-in label, and the missing real `task.accepted`
  readback.
- All three r2 findings are closed in `f7addbf`, whose evidence is
  `validation-runs/p6/20260930T060437Z-p6-01-gate/` (20/20 browser steps, no Truth
  write) and `validation-runs/p6/20260930T060050Z-p6-t07/` (11/11: 259-Event
  pagination, 216,015-character Event, API-down/restore, real `task.accepted`).
- Independent re-check of `f7addbf` (r3): see `validation-runs/p6/20260930T060528Z-p6-01-independent-r3/validation-independent-r3.md`.

## Status

`P6-01: PASS on the scratch fixture for T01/T02/T07` as of the r3 verdict on
`f7addbf`; **`P6` as a stage is not complete** and nothing here releases V1.

Still open, and deliberately not claimed:

- P6-T03 (audited human writes) — P6-02 is not delivered; no write UI exists.
- P6-T04/T05/T06 — P3 Context, P4 Memory and P7-01 Validation contracts are not
  frozen; those views are explicit `未接入` panels.
- The operator's own Core and a real captured conversation were not used; every
  Gate ran against an isolated scratch fixture.
- Throttling/back-pressure and an automated colour-contrast sweep are still not
  exercised (A08 remains `P`).
- The independent verdicts bind their commits: `4a4ee17` (r1), `a5a92ca` (r2),
  `f7addbf` (r3). A later docs-only commit does not inherit them.
