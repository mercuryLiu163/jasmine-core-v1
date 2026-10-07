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
- Independent re-validation of `f7addbf` (r3): **P6-T01 PASS, P6-T02 PASS,
  P6-T07 PARTIAL**; the commit did not move during the run and every finding
  above is recorded as closed. It then verified that the docs-and-tooling commit
  after it touches no product path and leaves the built bundle byte-identical, so
  the same verdict carries to the tip. Report:
  `validation-runs/p6/20260930T060528Z-p6-01-independent-r3/validation-independent-r3.md`.
- r3 §9: after the rapid-switch phase was added, the validator ran it itself
  (3/3) and falsified it with its own control — the same delayed response *does*
  render while project A is still active (so the assertion is non-vacuous), while
  with the switch it never appears under B. It therefore upgraded T07 to
  **PASS** for the taskbook action list. Two caveats it recorded, kept here:
  (1) the anti-bleed it observed is cancel-on-scope-change (`useResource` aborts),
  so the "a landed response is discarded" branch rests on the code guard
  `if (!live || signal.aborted) return` and the component test, not on a positive
  browser observation; (2) throttling/back-pressure remains unexercised, which is
  outside the T07 action list.
- **Final verdict for the code commit `f7addbf`, carried unchanged through
  `44cd6b6` and `7464b83` (product paths and built bundle byte-identical):
  P6-T01 PASS, P6-T02 PASS, P6-T07 PASS.** P6-T03/T04/T05/T06 remain out of scope.

## Rebase onto `main` after P2 merged (branch rewritten)

`main` advanced from the P1 baseline `9059625` to `850fbec` (P2-01 interpretation,
P2-02 resolver/review, P2-03 admission all merged; **schema v6 -> v8**, migrations
`m0007_interpretations` and `m0008_resolutions`). The P6 branch was rebased onto
`850fbec`, so the pre-rebase commits above were rewritten:

| pre-rebase | post-rebase |
| --- | --- |
| `4a4ee17` | `61ead22` |
| `121bc07` | `5203671` |
| `a5a92ca` | `b7c3265` |
| `f7addbf` (code verdict) | `8060546` |
| `44cd6b6` | `07ddb95` |
| `7464b83` | `9072d44` |
| `37cc68a` | `a43f452` |

Evidence that the rebase did not change the dashboard:
`git diff 37cc68a a43f452 -- web scripts/p6 tests/p6` is **empty**, and
`web/dist/assets/index-Bn3SCuxd.js` still hashes to `1047915010c1e2…9d6e58`.

Re-verification after the rebase, by the implementer, on the new base (isolated
v8 fixture, `validation-runs/p6/20261007T105630Z-p6-01-rebased-v8/`): tsc 0;
56 web tests in 9 files; the standard browser trace **20/20 against a schema-v8
P2-era Core**; the `未接入` walk 10/10; the repository Core suite **350 tests OK**.
The independent r3 verdict binds the pre-rebase code and the v6 base; a fresh
independent confirmation on the rebased tip is a follow-up, not something this
document claims.

### Post-rebase findings (not fixed here, to keep the verified bundle intact)

1. **Event-type filter is incomplete.** Core now has 30 event types; the
   Conversation filter dropdown (`web/src/views/ConversationView.tsx`) still lists
   the 23 P1 ones, so `interpretation.bound`, `interpretation.corrected`,
   `interpretation.rejected`, `interpretation.rerun`, `resolution.command`,
   `review.approved` and `review.rejected` cannot be selected. They still *render*
   when present (unknown types are never dropped), and the `source_system` filter
   is a free-text field, so this is a usability gap rather than a truth gap.
2. **The Interpretation `未接入` copy is now stale in its reason.** It says the
   contract is not frozen, but P2 is merged and `/v1/interpretations`,
   `/v1/reviews`, `/v1/resolutions` exist. The correct status is now "P6-02 has
   not delivered this view yet", not "waiting for P2". The proxy allowlist
   (`web/server/core-paths.json`) also has to grow those read paths, together with
   the `tests/p6` contract test, before any of them can be used from the browser.

Both belong to the next P6 PR (P6-02/P6-03a), which is now unblocked because the
P2 contracts exist.

## Status

`P6-01: T01 PASS, T02 PASS, T07 PASS` on an isolated scratch fixture — the r3
verdict on `f7addbf` (T07 upgraded in r3 §9 after the validator ran and
falsified the rapid-switch phase itself), whose product code is byte-identical to
the tip. **`P6` as a stage is not complete** and nothing here releases V1.

Still open, and deliberately not claimed:

- P6-T03 (audited human writes) — P6-02 is not delivered; no write UI exists.
- P6-T04/T05/T06 — P3 Context, P4 Memory and P7-01 Validation contracts are not
  frozen; those views are explicit `未接入` panels.
- The operator's own Core and a real captured conversation were not used; every
  Gate ran against an isolated scratch fixture.
- Throttling/back-pressure is not exercised, and the "landed stale response is
  discarded" branch of the anti-bleed guard is covered by a component test rather
  than a positive browser observation (the browser shows cancellation instead).
- An automated colour-contrast sweep is still missing (A08 remains `P`).
- The independent verdicts bind their commits: `4a4ee17` (r1), `a5a92ca` (r2),
  `f7addbf` (r3). The later commits are documentation and read-only tooling with
  byte-identical product paths and bundle, which the validator verified itself;
  any future product-path change or rebuild voids the verdict.
