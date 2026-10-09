# P6 remaining work, item by item against the P6 taskbook

Source of the item list: `docs/Jasmine-Core-V1-P6-阶段任务书.md` (in `main`), §3–§8. This document
records, for every taskbook item, what is **delivered**, what is **not implemented**, what is
**dependency-blocked**, and **which artefact binds the claim**. It adds no acceptance round: the
integration candidate stays frozen at the SHAs recorded in `P6-02B-INDEX.md`.

Status marks: **D** delivered · **P** partially delivered (gap named) · **N** not implemented ·
**B** dependency-blocked. Evidence binding is a bundle directory under `validation-runs/p6/` plus
the compliance record that maps requirements to code.

## Historical limitations that must not be merged away

Two facts about earlier P6 work stay on this list even though later slices moved past them. They
constrain what may be re-used as evidence; neither is fixed by anything below, and neither may be
quietly dropped when the list is next revised.

1. **The P6-01 / milestone-A binding commit lacks a tracked-source closure.** The commit those
   verdicts bind (`f7addbf` for P6-01's r3 verdict; `693afe6` for milestone A's r2 verdict) is *not*
   a self-contained tracked source tree: `P6-02-COMPLIANCE.md` records that `693afe6` "is complete
   only together with its working tree". So a third party cannot rebuild exactly the sources that
   were reviewed from those commit IDs alone, and any future re-validation must bind a **clean
   archive** (`git archive <commit>`) rather than the commit ID plus whatever happened to be
   untracked. This is why every bundle after P6-02 states that it built from `git archive`.
2. **A later P6-02 product fix cannot inherit `c7c5480`'s conclusion.** `c7c5480` fixed the four
   findings of the milestone-B validator, and the delta re-validation that returned PASS covers
   **that delta only**. The conclusion is not a standing clearance: any product change after it
   needs its own evidence, its own review/validation and, where it touches permissions, provenance,
   revision or the source chain, a re-run of the applicable real Gate. Citing `c7c5480` as the review
   for a later change is exactly the inheritance the taskbook's §8 merge gate forbids ("完整 P6
   verdict 绑定最终 commit 与依赖，不能从前一分支继承 PASS").

## 1. §4 P6-01 — the read-only dashboard (P6-A01…A08)

| Item | Status | Gap / note | Evidence binding |
| --- | --- | --- | --- |
| A01 应用骨架与认证 | **D** | token in memory only, cleared on refresh/exit; no auto-mint | `P6-01-COMPLIANCE.md`; Gate `P6-T01/T02/T07 PASS` at `f7addbf` (r3 verdict); bundles per the P6-01 section of `P6-02B-INDEX.md` |
| A02 浏览器接入 | **D** | same-origin static host + allowlisted proxy, no wildcard CORS, loopback Core | same |
| A03 Overview/Project | **D** | declared-scope counts; unavailable shown as unavailable, never 0 | same |
| A04 Task Board/Detail | **D** | current values match GET readback; EXECUTED/STALE not hidden | same |
| A05 Conversation/Event | **D** | tool events not presented as user speech; Interpretation panel said P2 未接入 at the time (now delivered by P6-02) | same + `P6-02-COMPLIANCE.md` |
| A06 Rule/Evidence | **D** | INFO never rendered as PASS; BROKEN_REFERENCE visible | same |
| A07 列表与一致性 | **D** | paging, cancel-old-request, no cross-project bleed | same |
| A08 人工可用性 | **D** | keyboard source trace, focus/labels, collapsed long text, masked copy | same |

§4 also requires (prose): raw Events byte-preserved, credential masking by default, text-only
rendering (no HTML), frozen safe protocols only, and **no token in localStorage/IndexedDB/service
worker/cache/static bundle/HAR**. Those are asserted by P6-01's T02 Gate and its static-host tests;
they are re-asserted in every bundle by the credential check.

## 2. §5 P6-02 — Extraction Review and audited manual operations (items 1…7)

| Item | Status | Gap / note | Evidence binding |
| --- | --- | --- | --- |
| 1 Conversation↔Interpretation read (model/extractor/version/confidence, Resolver reason, derived ids; pending count from the API) | **D** | milestone A, independently reviewed and validated | `P6-02-COMPLIANCE.md`; HTTP `20261007T135254Z-p6-02b-t03-gate/` (76/76); browser `20261007T135314Z-p6-02b-clickthrough/` (53/53); candidate `959e8df`, asset `index-CZ4ocGOP.js` |
| 2 Correct / Reject / Re-run (old vs new, affected Truth, reason, frozen version; reject keeps the raw Event; re-run does not auto-change Truth) | **P** | all three delivered behind the default-off switch with per-action audit readback; **browser end-to-end submissions exist for 4 of the 9 writes** (interpretation.reject, review.reject, rule.propose, rule.approve) — the rest are HTTP + audit readback, which the INDEX says explicitly | as above; nine write endpoints in `server/core-paths.json` |
| 3 Rule proposal vs approve/supersede/retire (scope, version/revision, immutable history; 409 keeps the draft) | **P** | same browser-coverage note | as above |
| 4 Task/Step create/transition/criteria (real state machine, `expected_revision` from readback, idempotent `event_id`, timeout = 结果未知) | **D** | contract frozen (`P6-02B-TASK-STEP-WRITE-SPEC.md` rev 3, delta re-review APPROVE freeze-as-is), implemented at `607f511` + review-fix `acd260b`, then executed by an **independent** validator on a real Core + real browser: PASS 9/9 claims, no counterexample, all seven routes + the §11 negatives. Its Evidence-gated route 6 and the two gated Step edges need a fingerprint/confirmation environment the browser alone cannot mint (both minting routes are proxy-refused by design), so a fresh environment must record them BLOCKED/PARTIAL — the spec's own permitted outcome | `P6-02-TASKSTEP-WRITE-COMPLIANCE.md`; validation `validation-runs/p6/VALIDATE-p6-02-taskstep-write/`; reviews `REVIEW-p6-02-taskstep-write-spec/`, `REVIEW-p6-02-taskstep-write-impl/` |
| 5 VERIFIED/ACCEPTED forms (criteria→Evidence matrix, rule version, fingerprint, `422 missing_evidence` keeps state, agent credential refused) | **N** | the dependency (item 4) is now **delivered**, so this is the next slice; the *read* side of Evidence/criteria exists (P6-01/A04, A06) and the write-side “`422 missing_evidence` keeps the state unchanged, no Event” property is already independently observed for route 6 and the two gated Step edges | same |
| 6 `evidence/confirm` | **N** | deliberately absent: needs a real human prompt (ADR 0007) that a browser cannot mint; the browser test asserts the control is **absent**. New decision belongs to the Core owner as its own ADR/API Gate | same |
| 7 Every manual change has an immutable command Event + revision/provenance + audit metadata; audit readback uses an authorised validator | **D** | for the nine delivered writes: per-action audit readback was added for all nine after the validator found 6 missing (`c7c5480`); the delta re-validation returned PASS | milestone-B bundle `20261008T053936Z-p6-03b2-clickthrough/` + INDEX |
| — `POST /v1/resolutions/{id}/manual-reapply` | **N** | needs its own frozen payload/permission table **and an explicit operator decision**; a web button cannot mint the ADR 0007 human-prompt provenance it records | `P6-02-COMPLIANCE.md` “Not delivered” |
| — real P2 message → Review → write chain (P6-T03 Gate) | **B** | the writes are gated against fixtures and the real P4 Core; a **real captured conversation** with the Resolver policy Gate has not been run (operator's capture path) | `P6-02B-INDEX.md` P6-02 sections; `P6-T03` in §7 |

**Status text:** `P6-02 milestone A: delivered, independently reviewed and validated`;
`milestone B: independently validated, PASS-WITH-CAVEATS` (four findings fixed in `c7c5480`);
`P6-02 TaskStep write (item 4): PASS` for the seven write routes only — contract frozen, implemented
and independently validated, with route 6/gated Step edges `BLOCKED`/`PARTIAL` in any environment
that lacks the out-of-band fingerprint and confirmation. `P6-02` as a whole stays **PARTIAL**.

## 3. §6 P6-03 — domain-by-domain inspectors

| View | Status | Gap / note | Evidence binding |
| --- | --- | --- | --- |
| Checkpoint Compare (P6-03a) | **P** | views delivered and verified against a real P3 Core; the negatives “old checkpoint is only history, revision-ahead/partial fingerprint must not show match” are **not all exercised** — the real run compares equal revisions (`STATE_EQUAL`) and a second checkpoint exists only for the switch check | real `20261008T075256Z-p6-03a-realcore/` (26/26 at `f323c779`); simulated `20261008T075317Z-p6-03a-gate-regression/` (15/15) |
| Context Inspector (P6-03a) | **P** | persisted pack, exact rendered content, section/token metrics, rule versions, memory ids, state revision all verified. **Injection attempt/receipt is not available**, so “built ≠ injected” is stated and the receipt half cannot PASS | same |
| Memory Jobs/Recall (P6-03b1) | **P** | jobs/detail/retry, bank mapping, source Event/Evidence, query/result trace verified against real P4 code; **engine-dependent behaviour and the “engine unavailable ⇒ degraded, P1 views unaffected” negative are not covered** | real `20261008T075258Z-p6-03-realcore/` (35/35) + `...-ui/` (10/10); simulated `20261008T075355Z-p6-03b1-clickthrough/` (11/11) |
| Memory Inspector 管理 (P6-03b2) | **P** | the five commands + `reflect` delivered behind a default-off switch, exercised through the real P4 Core and through the browser; soft-delete/provenance/idempotency verified; **“historical Memory conflicting with current Authority is conspicuous” is not exercised**, and engine-dependent `reflect` content is unproven | real Core 35/35 + UI 10/10; fixture `20261008T075346Z-p6-03b2-clickthrough/` (16/16 twice + 4/4 off); `P6-03B2-COMPLIANCE.md` criterion 9b |
| Validation Inspector (P6-03c) | **P + B** | **delivered** behind `P6_ENABLE_P7_READS`: the proxy allowlist (`p7_pending_paths`, five routes), the Inspector (run list with Core's filters, detail with the eight T06 chain links and their verbatim `missing_reason`, seven-section `present_kind`, manifest with the unauthenticated warning, artifact metadata and text-only content with separate masked/original digests) and the real-browser drill-down harness. **What is not PASS**: P7-01 acceptance criterion 1 — no real FAIL run exists on disk, so the FAIL display path is exercised only by a clearly-labelled synthetic fixture; and the drill-down's artifact half necessarily runs on a labelled fixture root, because the nine real records carry no manifest and no artifacts. `web/P6-03C-COMPLIANCE.md` | real records + labelled fixture: browser `20261008T181740Z-p6-03c-exit-code-positive/` (24 PASS / 0 FAIL, 5 PASS / 0 FAIL / 1 BLOCKED python), earlier `20261008T174844Z-p6-03c-validation-drilldown/`; independent review `REVIEW-p6-03c-*` and validation `VALIDATE-p6-03c-*` |
| online / backup / storage | **N + B** | needs the P5/P7 readable contracts and real health stamps; today the UI does not fake health | same |

## 4. §7 Gates and negative cases

| Gate | Status | Evidence |
| --- | --- | --- |
| P6-T01 P1 source trace | **D** (PASS) | P6-01 r3 verdict at `f7addbf`; bundles per INDEX |
| P6-T02 permissions & secrets | **D** (PASS) | same |
| P6-T03 audited manual maintenance | **B** (real chain) | nine writes + audit readback gated against fixtures and real P4; real P2 conversation chain not run |
| P6-T04 actual Context (incl. injection receipt) | **B** (receipt) | real P3 pack/rendered content verified; no receipt exists to match |
| P6-T05 Memory degradation | **P** | three quarters now have evidence: real P4 jobs/retry/withholding in the real-Core matrix; the outage (`Hindsight unavailable ⇒ degraded, P1 unaffected`) **and the same-environment recovery** in `20261008T130315Z-p6-t05-degraded/` (**19/19** — 9 Core checks + 10 browser records across two phases — frozen at `31d738f…`). Still **P**, not PASS: the engine is a labelled wire double stopped and restarted on the same pinned port, so no real Hindsight outage is claimed |
| P6-T06 Validation drill-down | **P** (criterion 1 BLOCKED) | run against the real P7-01 Core over the nine real P0 records through the dashboard's own proxy in a real browser: list/paging, recorded verdicts unchanged, the eight chain links with verbatim missing reasons (Context absent in all nine across its three recorded forms; Agent absent in the six BLOCKED runs), `manifest_status: absent` for a manifestless record, `acl` grant-filtering and the one-code `404`, in-browser `403 forbidden_scope`, and `503 validation_not_configured` on an unset root. **Not PASS**, and the reason is data, not code: criterion 1 needs a real FAIL run, and the artifact/masking negatives can only run on a labelled fixture root because the real records have no manifest. Unblock: the P7 owner records one genuinely failing scenario with `recorder.py` into the configured run root | browser `20261008T181740Z-p6-03c-exit-code-positive/` + `20261008T174844Z-p6-03c-validation-drilldown/`; `NEGATIVE-*` bundles are the exit-code failure negatives and are **not** acceptance runs |
| P6-T07 maintainability | **D** (PASS) | P6-01 r3 |

Negative cases (§7), which ones are actually covered:

| Negative case | Status |
| --- | --- |
| stale `expected_revision`, double click, timeout retry, `event_id` payload change | **covered** — `W4.replay`, `W4.revision_conflict`, real `event_id_conflict` |
| criteria/Evidence missing, old fingerprint, BROKEN_REFERENCE, INFO result, no real human confirmation | **partial** — read-side rendering covered (A04/A06); the VERIFIED/ACCEPTED submission forms are not delivered; the confirm control is asserted absent |
| agent-proposed Rule, tentative “先研究”, cross-scope supersede | **covered** — milestone-B tests + real P4 issuance refusal |
| project A slow response arriving after switching to B; same id, different revision | **covered** — T07 (the rapid-switch phase was falsified and fixed) + `revision_conflict` |
| API 404/401/403/422/503, disconnection, unknown enum, truncated paging | **covered** across the P1/P6-01/P6-02/P6-03 Gates |
| secret-looking raw text, HTML/script, artifact path traversal, external links | **covered for text/protocol** (T02, proxy allowlist), and now for artifacts: the Inspector refuses `../`, absolute, `C:`, UNC, URL and symlinked declared paths, renders artifact content as text only (a browser probe confirms nothing from an HTML artifact is parsed and the global it sets stays undefined), masks by default and withholds the original with Core's own reason. Exercised on a **labelled fixture root**, since the real records have no manifest | `20261008T174844Z-p6-03c-validation-drilldown/`; `web/P6-03C-COMPLIANCE.md` |
| **Memory unavailable / stale Memory vs Authority conflict / old Checkpoint / Context over budget** | **partly covered now**: the outage *and the same-environment recovery* are covered by `20261008T130315Z-p6-t05-degraded/` — the engine is stopped (P4's own probe records `unreachable`, Core stays `ok`, the P1 reads and views are unaffected, and a 200-with-`degraded`-payload `reflect` is recorded as a degradation, not a success), then restarted on the **same address** without restarting the Core, after which health reports `degraded: false` / `core_impact: none` and a real `reflect` reaches the engine. **Still not covered**: stale Memory vs current Authority, an old/atypical Checkpoint, and an over-budget Context |
| Validation missing Context or Agent transcript / mocked response | **covered for the real data**: every one of the 63 real cases shows the Context link missing with its own `missing_reason` (27 reason-only, 30 recorded-but-no-pack, 6 sentinel, 0 packs), `agent_response` is missing in all 42 cases of the six BLOCKED runs, the recorded verdict is served unchanged, and a record marked `synthetic: true` is graded `simulated` and never `real`. An **unmarked** mock under a `mode: real` manifest remains undetectable by a read-only contract, and the API says so rather than claiming otherwise | `20261008T174844Z-p6-03c-validation-drilldown/`; ADR 0015 §4.2 in the P7-01 worktree |

## 5. §8 bundle format and merge gate

Every current bundle is frozen and verifiable (`BUNDLE-FROZEN.json`), but the **file set differs
from §8's required list**. §8 asks each bundle for `manifest.json`, `scenario.md`,
`click-trace.jsonl`, masked `api-requests.jsonl`/`api-responses.jsonl`, `truth-before-after.json`,
the relevant source mappings, `screenshots/`, raw test output and `result.md`. What exists today:
`click-trace.jsonl`, `browser-requests.jsonl` (the request log, with `fixtureLabel`),
`evidence.json`/`manifest.json` (Core checks and calls), `README.md`, `screenshots/`, raw logs, and
`BUNDLE-FROZEN.json`. Missing by name/semantics: `scenario.md`, `api-requests.jsonl` /
`api-responses.jsonl` as separate files, `truth-before-after.json`, `result.md`.

Merge gate: the taskbook allows **P6-01 to merge first**; P6-02 waits on the P2 API (merged) plus its
own Gate; P6-03a on P3, 03b on P4, 03c on P7-01. None of the dependency **phases** claims acceptance,
so merging the integration candidate is an operator decision, not a P6 result, and must not be
described as P6 PASS.

## 6. 下一个最小交付 (decision)

**Recommended: the §8 evidence-format conformance generator.** It is the only remaining item that is
certainly unblocked, needs no dependency or operator decision, and directly serves the taskbook:

* scope: one harness script that, for a frozen bundle, emits the §8-named files from the bundle's own
  records — `manifest.json` (commit/deps/mode/time), `scenario.md` (the records the run performed),
  `api-requests.jsonl`/`api-responses.jsonl` (masked; derived from `browser-requests.jsonl` +
  `evidence.json` calls), `truth-before-after.json` (the mutations the run read back, empty for
  read-only runs, explicitly `not-recorded` where the bundle has none) and `result.md` (the
  PASS/FAIL/BLOCKED/PARTIAL table generated from the records);
* it must **not** invent data: fields the bundle does not contain are written as `not-recorded`, and
  the generator refuses to run on a bundle without `BUNDLE-FROZEN.json`;
* done when: re-running it over the nine current bundles produces §8-shaped files, a reviewer can
  trace every line to an existing record, and a second run is byte-identical (idempotent) — proven by
  a test, not by inspection;
* what it does **not** claim: it changes neither any Gate result nor any code, and it does not make
  P6 PASS.

**Runner-up (behavioural, needs one probe first): the “Memory engine unavailable ⇒ degraded” slice of
P6-T05.** It closes a named §7 negative case using the existing isolated P4 Core and its labelled
wire double — no real deployment, no operator service. First step is a 10-minute probe of whether
P4's isolated environment can express “engine unavailable” without a real Hindsight; if it cannot,
the row stays dependency-blocked and this drops off the list.

**Dependency actions to open now (no P6 work can substitute):** the P7-01 request is **delivered** —
the contract is frozen in the P7-01 worktree and copied here, the read slice is implemented and
independently validated, and the dashboard proxy is open behind `P6_ENABLE_P7_READS`. What remains
is a **data** action, not a dependency: to close P7-01 criterion 1, the P7 owner records one
genuinely failing scenario with `recorder.py` into the configured run root, after which the T06
drill-down is re-run and the criterion re-validated. Record the P6-03a **injection-receipt**
question with the Agent/Context owner (unblocks T04's second half). Ask the P4 owner for the
interpreter-symlink fix task (`TASK-p4-interpreter-symlink.md`, registered separately). File the
**coordination request to the Core interface owner** with the same scope and phrase as the frozen
spec's §13 bullet: no route in schema 8 exposes `STEP_EDGES`/`TASK_EDGES`, so the Task/Step
transition UI can only offer Core's status **vocabulary** — a superset of the legal-target set — and
Core stays the only verdict on legality (`409 invalid_state_transition` with `from_status`/
`to_status`); the ask is a route that publishes the two edge sets. It is a presentation limit that
the implementation review ruled acceptable under §12, it blocks no item, and it changes no P6 status.

**Harness gates closed before that re-run** (all found by the independent review/validation rounds
and fixed, so the harness is now fit for the tenth record):
1. the exit code is a gate — `check_exit_code` decides it from the browser records and the python
   checks, with `False` failing the run and `None` failing it unless the check declares
   `blocked_is_expected` (criterion 1 is that declared block). Failure negatives:
   `tests/p6/test_p7_realcore_ui_exit_code.py` and the end-to-end
   `scripts/p6/p7_realcore_ui_exit_negative.py`, whose falsified bundle is written under
   `NEGATIVE-*/` with a `NOT-AN-ACCEPTANCE-RUN.md` banner.
2. the DOM verdict extraction reads the run row's `data-testid="run-verdict"` hook instead of the
   last `verdict(按记录)` label, which every case card also renders;
3. the criterion-1 heading, intro, unverified-scope bullet, `scenario.md`, `manifest.json`'s
   `phase_acceptance`/`validation_root`/`dirty_note`, the census check's name and its `ok`, and the
   env banner/`descriptor.json` note are all **derived** from the census, so a tree that carries a
   FAIL case produces a coherent bundle that says so;
4. the record count is derived: the env builder accepts any usable tree, the driver hands the trace
   a `p7-trace-expectations.json` read from disk, and the list/paging/ACL/agent assertions compare
   exact id sets, so the harness produces a bundle for the tenth record too.

**Open advisories on the harness** (recorded, none blocking): the run-verdict hook's fallback can
still pick a case's verdict if the hook is present but `run.verdict` is null (unreachable through
the P7-01 detail route, which requires a verdict); a falsified expectations file whose id does not
exist makes the trace navigate to it and throw rather than record a FAIL for that step (cannot
happen when the expectations come from the records); nothing unit-tests `render_result`; the
criterion-1 section body says "finds a FAIL case" where `failure_class`/`failure_output` alone also
demonstrate it; derived strings render "1 FAIL cases"; and each trace run leaves a small
`$TMPDIR/p6-03c-chrome-*` profile directory behind.

## 7. 明确不做

* **Do not auto-restart the P4 G01 benefit-verification experiment.** It was stopped for its own
  reasons; no instruction here reopens it, and a real Hindsight deployment alone would not.
* **Do not start a real Hindsight deployment as the “next step” of P6-03.** It would not clear G01
  and it is not required by any P6 Gate that is currently reachable.
* **Do not turn any of this into another P6-03 acceptance round.** The candidate is frozen; only the
  tasks above (or new dependencies) should move.
* **Do not touch the live entry point, global hooks, credentials, existing services or the
  operator's Core.**

## 8. Post-acceptance additions (docs and tooling only)

After the acceptance battery at `f323c779bc91893b5908218d8c5c148619159180`, the tree gained only
documentation and harness tooling:

* tracked docs: this file, `web/TASK-p4-interpreter-symlink.md`, and the status text in
  `web/P6-03A-COMPLIANCE.md` / `web/REAL-CORE-ACCEPTANCE-PLAN.md`;
* the **T05 degraded slice** (the runner-up in §6, now delivered): `scripts/p6/p4_realcore_env.py`
  gained `--engine-state unavailable` (default unchanged), and
  `scripts/p6/run_t05_degraded.py` + `scripts/p6/t05_degraded_trace.mjs` run and freeze it. The
  bundle `20261008T130315Z-p6-t05-degraded/` is **19/19** (9 Core checks + 10 browser records, in an
  outage phase and a recovery phase separated by the restore), frozen at
  `31d738f08e9613bb8d8071197a67c77633d675b1`, and keeps the labels straight: `mode: boundary-fake`
  means real Core with a simulated engine boundary, the "unavailable" engine is a **stopped loopback
  wire double** restarted on the same pinned port (not a real Hindsight outage), and
  `POST /v1/memory/reflect`'s **HTTP 200 with a `degraded` payload** is recorded as a degradation,
  never as a success. Three gaps an independent review reproduced were closed: the revision check now
  reads `task.revision`/`rule.revision` from the layer they live in and **fails on a missing field**
  instead of comparing two nulls (with unit tests); every browser view requires a **freshly observed**
  200 plus **the object the page is about** (project/task/event/rule/evidence id) instead of any
  earlier 200 and a panel title; and the recovery half now exists at all (pinned engine port,
  loopback-only control channel, `engine_events` log read after the restore). The earlier
  `20261008T123611Z-…` bundle stays as written and is superseded by this one. No product path changed;
* harness tooling: `scripts/p6/bundle_conformance.py` (the taskbook-§8 renderer described in the
  next-minimal-delivery section) and `tests/p6/test_bundle_conformance.py` — **20** hermetic tests
  after the operator reproduced three defects in the first version and they were fixed: a symlink at
  a derived-file name could write **through** to a source file (now refused; files are staged and
  installed with `os.replace`, which cannot follow a link), an interrupted write could leave a
  half-written output (a fresh output now appears by one atomic rename, and a failed refresh restores
  the previous derivation), and non-empty request bodies plus nested sub-run records were dropped
  without a note (every field of every record is now carried, with
  `manifest.json.field_accounting.not_carried` proving it). A second reproduction by the operator
  then found that the first fix introduced two destructive paths — "refreshing" an existing output
  directory could delete a source bundle that lived inside it, or one that occupied the fixed backup
  path — and both are closed by construction: **only a new output directory is written to** (an
  existing one is compared with `--check`), containment is refused in both directions, and there is
  no backup path and no deletion other than the tool's own staging directory. 23 hermetic tests. No
  verdict and no Gate result changes.

The exact diff list against the acceptance commit is
`git diff --name-only f323c779… HEAD`: documentation and harness paths only, with **no `src/`, schema
or `web/` product path** — so the acceptance bundles still bind the same product code and the
dashboard results (tsc, 328/328) still apply unchanged. The
battery was deliberately **not** re-run for this: a tool that only reads frozen bundles and a test
that only builds temp trees cannot change a Gate result, and re-running one to make a new tip look
"verified" would be the wrong reason to run a Gate. The Python suite's count does move (the 13 new
tests), so no count recorded at the acceptance commit is reused for a post-acceptance tip.
