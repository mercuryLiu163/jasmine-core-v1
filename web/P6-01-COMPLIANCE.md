# P6-01 compliance matrix

Requirement-by-requirement trace from the P6 taskbook (P6-01 work items in §4
and the negative cases in §7) to the code that implements it and the evidence
that exercises it. Anything not listed here is not claimed.

Legend: **D** = delivered, **P** = partially delivered (the gap is named),
**N/A** = out of P6-01 scope by the taskbook, **U** = explicitly 未接入.

## §4 work items

| Item | State | Implementation | Evidence |
| --- | --- | --- | --- |
| P6-A01 app skeleton & auth | D | `web/src/app/session.tsx` (memory-only token), `web/src/components/SignIn.tsx` (password field, accepted only after a real `GET /v1/health`), `web/src/api/client.ts` (per-request `Authorization`, no storage, generated `X-Request-Id`) | `web/tests/app.test.tsx` ("accepts a token only after a real authenticated read, and never writes storage", "shows a 401 refusal instead of an empty database", "stops sending Authorization after 退出"); browser steps 1–4, 17 |
| P6-A02 browser access | D | `web/server/core-proxy.ts` + `web/server/core-paths.json` (frozen allowlist, loopback-only target from `P6_CORE_ORIGIN`, no injected token), `web/server/serve.mjs` (static host, same allowlist, CSP/XFO headers), `web/vite.config.ts` (dev proxy) | `web/tests/proxy.test.ts`, `web/tests/endpointContract.test.ts`, `tests/p6/test_dashboard_proxy_contract.py`, `dev-proxy-check.txt`, `proxy-contract.raw.txt` in the run directory |
| P6-A03 Overview / Project | D | `web/src/views/OverviewView.tsx`, `web/src/views/ProjectView.tsx`; loaded-range wording, unavailable panels for Hindsight/Backup/online | browser steps 5–6; `truth-before-after.json` shows the reads behind them |
| P6-A04 Task board / detail | D | `web/src/views/TaskBoard.tsx` (Step-state columns, Task status separate, outstanding-status summary, unknown-status column), `web/src/views/TaskHistory.tsx`, `web/src/views/TaskEvidence.tsx` | `web/tests/app.test.tsx` ("keeps Task status separate from Step columns and never claims all done"); browser step 7 |
| P6-A05 Conversation / Event | D | `web/src/views/ConversationView.tsx`, `web/src/components/EventRow.tsx` (machine-event label, no local chat filler), `web/src/views/EventView.tsx` (deep link) | browser steps 9–11; `web/tests/app.test.tsx` ("labels machine events so a tool result cannot read as a user message") |
| P6-A06 Rule / Evidence | D | `web/src/views/RulesView.tsx` (active list + read-by-ID + history), `web/src/views/EvidenceView.tsx`, `web/src/views/TaskEvidence.tsx` (Core-recorded `evidence_ids`, reference status, labelled criteria matrix) | browser steps 12–13; `web/tests/app.test.tsx`; `web/tests/text.test.ts` for INFO≠PASS vocabulary |
| P6-A07 lists & consistency | D | `web/src/app/usePagedResource.ts` (cursor paging, cancellation, stale-response rejection), `web/src/app/useResource.ts` (scope-change reset) | `web/tests/usePagedResource.test.tsx` (5 cases incl. "clears the previous scope…" and "drops a superseded response"), `web/tests/app.test.tsx` project-isolation case |
| P6-A08 human usability | P | `web/src/components/StatusBadge.tsx` (glyph + literal token, never colour-only), `web/src/components/RawText.tsx` / `JsonBlock.tsx` (collapse, masked copy), skip link, labelled inputs, focus styles, `aria-live` loading, `role="alert"` failures | browser step 8 accessibility scan (no unnamed interactive node); manual screenshot review. **Gap:** no automated contrast/long-text sweep, and no small-viewport pass recorded |
| P6-01 read-only scope set | D | `web/src/app/session.tsx` `REQUIRED_READ_SCOPES`; no admin, no fingerprint scan, no writes anywhere in the bundle | `proxy-contract.raw.txt` (`/v1/auth/keys` and every non-GET refused), `truth-before-after.json` (`unchanged`) |

## §7 negative cases (P6-01 applicable)

| Negative case | State | Where it is exercised |
| --- | --- | --- |
| No/revoked/missing-scope token | D | 401 path in `web/tests/app.test.tsx` and `web/tests/client.test.ts`; real curl in `proxy-contract.raw.txt` |
| Agent key attempting management/acceptance | N/A | P6-02; no write UI exists in P6-01 |
| API 404/401/403/422/503, lost connection, unknown enum, paging truncation | P | Error kinds and copy in `web/src/api/errors.ts` + `web/src/components/States.tsx`; unknown Step status column tested in `web/tests/app.test.tsx`; unreachable Core tested in `web/tests/client.test.ts`. **Gap:** 503/paging-truncation behaviour is unit-tested only, not driven through the browser |
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

## Status

`P6-01: PARTIAL`. The read-only dashboard and its real-Core browser trace work
on an isolated scratch fixture; P6-01 is not "done" until a reviewer and an
independent validator confirm it and the A08 gaps above are closed. P6 as a
stage, and V1 as a release, are unaffected by this PR.
