# P6-02b — Task/Step write payload, permission and acceptance specification

**Status: FROZEN at revision 3** — revision 1 was independently reviewed (verdict APPROVE WITH NAMED
MANDATORY CHANGES, M1–M6), revision 2 folded those in, and the delta re-review of revision 3
returned **APPROVE (freeze as-is)**, whose eight non-blocking notes revision 3 also carries. Every
section below is the frozen text; the only change after the freeze is a non-normative §13 note and
an evidence-path rename, itemised in the addendum under *Revision history*.
The slice is now **implemented** at `607f511c93fa5273df16187e6d1bce81a187e7e7`, fixed by
`acd260b3e55aae9a16b7544103c3d86dd8773ad8`, and the §11 acceptance plan was executed by an
**independent** validator on a real Core and a real browser — verdict **PASS, 9/9 claims, no
counterexample** (`P6-02-TASKSTEP-WRITE-COMPLIANCE.md`). That verdict covers the seven write routes
only: `P6-02` stays **PARTIAL** (item 5's VERIFIED/ACCEPTED forms and item 6's `evidence/confirm`
remain undelivered), `P6` is not complete, and nothing here releases V1. This document's title keeps
the proposal's own label; the slice's evidence namespace and status label are `taskstep` /
`P6-02 TaskStep write` per the §11 M6 naming rule.
This document freezes what the operator's priority item 1 asks to freeze *first*: the payloads,
permissions, revision handling, replay and audit requirements of the Task/Step write slice — the
routes that **already exist in this baseline Core**. It is written to be reviewed and falsified
before a single line of UI is written, because P6-02's audited writes needed a permission-and-audit
Gate discovered late, and P6-03b1's contract needed a scope defect found by review
(`P6-03B2-WRITE-SPEC.md:15-17`). Nothing here is evidence that any write works from a browser.

**Baseline this is derived from:** `663a941b4fb503e614e0abc9cfb024e39d6d0615`
(`p6-01-readonly-dashboard`), `SCHEMA_VERSION = 8` (`src/jasmine_core/__init__.py:15`). Unlike
P6-03a/b/c, **this slice has no unmerged dependency**: every route in §2 is served by the Core in
this worktree (`src/jasmine_core/api/handlers.py:115-124`). It is therefore the one remaining P6-02
write slice that can be built against the baseline rather than a fixture of another phase.

**Requirement source:** `docs/Jasmine-Core-V1-P6-阶段任务书.md` §5 items 4 and 5 (lines 94-95) and §7
(lines 134-135) in `jasmine-core-v1/`. Where the taskbook differs from the implementation, §11 and
§12 say so and the code/ADR wins; the taskbook itself states that rule
(`Jasmine-Core-V1-P6-阶段任务书.md:16,47`).

Why a frozen specification rather than a plan: the write surface's failure modes are not "does the
form render" but *which* revision was sent, *what* a replay means, *which* actor a key carries, and
*what the UI is permitted to claim after a timeout*. Each of those is decidable from the code today
and expensive to get wrong in a UI.

---

## Revision history

| Rev | Subject | What it is |
| --- | --- | --- |
| 1 | `663a941b4fb503e614e0abc9cfb024e39d6d0615` | The proposal as first written (682 lines, untracked). Independently reviewed by `validation-runs/p6/REVIEW-p6-02-taskstep-write-spec/findings.md`, verdict **APPROVE WITH NAMED MANDATORY CHANGES** (M1–M6, no blocking); the reviewer reproduced its findings live against an isolated Core built from `git archive HEAD` on a temp DB. |
| 2 | `663a941b4fb503e614e0abc9cfb024e39d6d0615` | Folds in M1–M6 and the reviewer's minor corrections: §8/§11 `target_id` made route-specific (M1); `source_event_duplicate` documented as reachable on route 1 and added as §11 negative 8 (M2); the timeout request-id rule made an explicit implementation requirement with the client/proxy changes it needs (M3); §11's positive path reconciled with §12 so Evidence-gated route 6 and Step edges may record BLOCKED/PARTIAL (M4); the proxy-test typo folded into the test obligations and the browser-level out-of-scope refusal added as §11 negative 9 (M5); the evidence namespace disambiguated from milestone B (M6). Also: the `fingerprint_unavailable` reachability claim, the citation slips (the three the review names, plus a fourth `models.py` line found while re-verifying M2), §10's "no safety gain" softened to "the marginal gain is small", and §12's list of closed and corrected items. Nothing else changed. |
| 3 | `663a941b4fb503e614e0abc9cfb024e39d6d0615` (this document) | Folds in the delta re-review's eight non-blocking notes (`validation-runs/p6/REVIEW-p6-02-taskstep-write-spec/RE-REVIEW-findings.md`, verdict **APPROVE (freeze as-is)**): the `objects.py:179` and `objects.py:57-66` citations, the taskbook quote's section/line, "three" citation slips, the `useWriteAction.ts:204-213` ranges; the request-id requirement gains the fourth change it needed (the browser must read the response header, or a proxy-generated refusal could never be named); §11's positive path now requires route 3's non-gated edges to PASS; and §9 records that its ordering is not a total order for route 2 and that Task/Step recovery edges are asymmetric. Nothing else changed. |

**Addendum after the freeze (non-normative; no frozen section changed).** Three edits only:
(1) a §13 bullet recording that no schema-8 route exposes `STEP_EDGES`/`TASK_EDGES`, so the
implementation offers Core's status *vocabulary* rather than the legal-target set — the deviation the
implementation review ruled acceptable under §12, now written down so the frozen sections no longer
imply such an API exists, and filed as the Core-interface-owner coordination request in both this
document and `P6-REMAINING-TASKS.md`; (2) the implementation-status header above; (3) the
review-report path renamed from `REVIEW-p6-02b-write-spec` to `REVIEW-p6-02-taskstep-write-spec` to
honour the §11 M6 naming rule the validator flagged (a parent-side naming error, not a finding
against the review). A delta re-review of this addendum is the reviewer's ruling, not a change it
makes; nothing in §1–§12 moved.

---

## 1. Scope and non-goals

**In scope (7 routes, frozen in §2):** Task create; Step create; Step transition; Task transition;
Task criteria; Task accept; Step criteria. Plus the proxy allowlist entry (§10), the UI submission
rules (§4-§9), and the acceptance plan (§11).

**Item 2 of the operator's list is NOT in this slice.** The VERIFIED/ACCEPTED *forms* — a
criteria→Evidence matrix with rule versions, fingerprint, confirmation source and the missing-item
view — depend on this slice's route semantics, permissions and Evidence error vocabulary being
settled first. This proposal freezes the *server contract* those forms must obey (§7); it
implements nothing. Per the taskbook, the read half of Evidence/criteria is already delivered by
P6-01/A04 and A06 (`P6-REMAINING-TASKS.md:59`).

**Item 3 (`POST /v1/evidence/confirm`) is deliberately out of scope, and is named as such.**
The route exists (`handlers.py:110`, handler `handlers.py:334-339`, scope `evidence:confirm`,
human-only at `handlers.py:336`) and its implementation is not trivial: `record_confirmation`
requires a real same-task human `user.prompt` Event *newer than the current State revision*
(`src/jasmine_core/evidence.py:759-783`) plus a current fingerprint (`evidence.py:784`). ADR 0007
states the rule the browser cannot satisfy: "*`ACCEPTED` also needs an independent current human
`USER_CONFIRMATION` …, bound to the exact Step or Task revision and fingerprint. … No natural-language
keyword extraction is claimed.*" (`docs/adr/0007-p1-evidence-fingerprint-and-codex-hook.md:13`). The
taskbook agrees and reserves the decision: "*新增 Web 原生 human confirmation 的必要性由 Core owner
单独 ADR/API Gate 决定*" (`…P6-阶段任务书.md:96`). `P6-02-COMPLIANCE.md:282` already records it as not
delivered and asserts the absence of any confirm control. **This proposal does not decide whether a
web button may ever mint a confirmation; that is a coordination request to the Core owner, not a
design choice for this slice.**

**Also out of scope, and unchanged:** `POST /v1/resolutions/{res}/manual-reapply`,
`POST /v1/resolve/{int}`, `POST /v1/tool-results`, `POST /v1/workspaces/fingerprint`
(`P6-02-COMPLIANCE.md:276-282`; `web/server/core-paths.json:43`). Fingerprint scanning is not a
read-only button — it can move a VERIFIED Step to STALE (`ADR 0007:15`; taskbook §2 line 40) — so it
stays out even though some Evidence paths will fail without a current fingerprint (§7).

**Not changed by this slice:** no new scope, no new id prefix, no new table, no new migration, no
new Event type. Every route, scope, prefix and Event type below already exists
(`handlers.py:115-124`, `src/jasmine_core/auth.py:20-23`, `src/jasmine_core/models.py:33-59`). If
implementation appears to need one, §12 requires it be recorded as a coordination request instead.

---

## 2. The seven routes, transcribed from the handler

Route-table scope is the `scope` field of the `Route(...)` tuple in `_base_routes()`
(`handlers.py:115-124`); dispatch requires it before the handler runs (`src/jasmine_core/api/dispatch.py:47-48`).
"Allowed body fields" is the literal set passed to `_state_body` in the handler (§3). "*" marks a
field the code refuses to proceed without.

| # | Method and path | Route-table scope | Allowed body fields | Fresh success |
| --- | --- | --- | --- | --- |
| 1 | `POST /v1/tasks` | `objects:write` (`handlers.py:68`, `_collection_routes`) | `title`\*, `project_id`\*, `host_id`\*, `description`, `acceptance_criteria`, `source_system`, `source_event_id`, `occurred_at`, `event_id`, `actor_id` (`models.py:251-253`) | **201** |
| 2 | `POST /v1/tasks/{tsk}/steps` | `state:write` (`handlers.py:115`) | `title`\*, `description`, `acceptance_criteria`, `expected_revision`\*, `host_id`\*, `event_id` (`handlers.py:473`) | **201** |
| 3 | `POST /v1/steps/{stp}/transition` | `state:write` (`handlers.py:123`) | `status`\*, `reason`, `expected_revision`\*, `host_id`\*, `event_id` (`handlers.py:521`) | 200 |
| 4 | `POST /v1/tasks/{tsk}/transition` | `state:write` (`handlers.py:118`) | `status`\*, `reason`\*, `expected_revision`\*, `host_id`\*, `event_id` (`handlers.py:531`) | 200 |
| 5 | `POST /v1/tasks/{tsk}/criteria` | `state:accept` (`handlers.py:119`) | `acceptance_criteria`\*, `expected_revision`\*, `host_id`\*, `event_id` (`handlers.py:541`) | 200 |
| 6 | `POST /v1/tasks/{tsk}/accept` | `state:accept` (`handlers.py:120`) | `expected_revision`\*, `host_id`\*, `event_id` (`handlers.py:550`) | 200 |
| 7 | `POST /v1/steps/{stp}/criteria` | `state:accept` (`handlers.py:124`) | `acceptance_criteria`\*, `expected_revision`\*, `host_id`\*, `event_id` (`handlers.py:512`) | 200 |

Notes that a client will otherwise get wrong:

* **`expected_revision` is required on all six state routes and rejected on Task create.** The
  create body's `allowed` set contains `expected_revision` but `_reject_unknown` raises
  `unexpected_expected_revision` (400) whenever it is present (`models.py:251-253`, `314-320`; ADR 0002
  §2.3 line 48). So the Task-create form must not send it.
* **`reason` is required on route 4 unconditionally.** `transition_task` calls `self._reason(reason)`
  on every legal Task edge (`state.py:380`), not only on the CANCELLED ones; `_reason` refuses an
  absent/blank/over-4096-character value with `invalid_request` (400) (`state.py:168-172`). On route 3
  `reason` is required only for `target == SKIPPED` or a `BLOCKED`/`SKIPPED` source (`state.py:337-338`).
* **`acceptance_criteria` is required on routes 5 and 7 only in the sense that `validate_criteria`
  defaults `None` to `{"requirements": []}`** (`state.py:41-44`) — the key may be omitted and means
  "no requirements". It is *not* optional in effect: a Step/Task with no requirements cannot reach
  VERIFIED/ACCEPTED (§7).
* **Route 1 needs a second scope when criteria are non-empty.** `_make_create` validates the criteria
  and, if `requirements` is non-empty, additionally requires `state:accept` and a human/system actor
  (`handlers.py:393-398`; `objects.py:122-126`). An agent key creating a task with real criteria gets
  **403 `forbidden_scope`**, not `forbidden_actor_kind` — proven by `tests/test_state.py:99-101`.

### Response shapes (frozen key sets)

Every response is a JSON object; the server also returns `X-Request-Id` (`dispatch.py:110-112`).

| Route | Fresh keys | Replay keys | Source |
| --- | --- | --- | --- |
| 1 Task create | `object`, `event`, `replayed:false` (201, `Location: /v1/tasks/{task_id}`) | same 3 keys, `replayed:true` (200) | `handlers.py:401-405` |
| 2 Step create | `step`, `task_revision`, `event`, `replayed:false` (201) | same 4 keys (200) | `handlers.py:476`; `state.py:275-276`, `230-236` |
| 3 Step transition | `step`, `task_revision`, `evidence_ids`, `rule_versions`, `event`, `replayed:false` (200) | same 6 keys (200) | `state.py:357-359`, `230-236` |
| 4 Task transition | `task`, `event`, `replayed:false` (200) | same 3 keys (200) | `state.py:389`, `230-236` |
| 5 Task criteria | `task`, `event`, `replayed:false` (200) | same 3 keys (200) | `state.py:414`, `230-236` |
| 6 Task accept | `task`, `evidence_ids`, `rule_versions`, `event`, `replayed:false` (200) | same 5 keys (200) | `state.py:452-453`, `230-236` |
| 7 Step criteria | `step`, `task_revision`, `event`, `replayed:false` (200) | same 4 keys (200) | `state.py:307-308`, `230-236` |

**Two properties to freeze explicitly:**

1. **A replay has the same key set as a fresh write** (unlike P4's 7-key/5-key lifecycle split,
   `P6-03B2-WRITE-SPEC.md:34-38`), but the *values* are the first write's frozen snapshot, not the
   current projection: `_event` snapshots `result` into the payload at first write
   (`state.py:181-197`) and `_result` returns `{**payload["result"], "event": …, "replayed": True}`
   (`state.py:230-236`). ADR 0006 line 7: "*A replay of the same Event ID and semantic request
   returns the original Event and original response snapshot, including original revisions and
   Evidence IDs.*" Verified by `tests/test_state.py:37-61`, `63-75`, `167-201`.
2. **Routes 3-7 return 200 for a fresh write as well as a replay.** Only routes 1 and 2 signal a
   fresh write with 201. `replayed` is therefore the discriminator, and no UI may read "HTTP 200" as
   "written" on routes 3-7. (The handler hard-codes `Response(200, result)` at `handlers.py:513`,
   `524`, `534`, `542`, `551`.)

Nested object keys, for the readback row:

* Task row (`objects.get`, `objects.py:57-66`): `task_id`, `project_id`, `title`, `description`,
  `status`, `revision`, `source_event_id`, `created_at`, `updated_at`, `acceptance_criteria`
  (the last is the parsed `acceptance_criteria_json`; the column is added by
  `m0004_task_step_state.py:7`).
* Step row (`state.step`, `state.py:127-133`): `step_id`, `task_id`, `title`, `description`, `status`,
  `revision`, `acceptance_criteria`, `source_event_id`, `created_at`, `updated_at`
  (`m0004_task_step_state.py:15-27`).
* `event` is the full stored Event dict, including `event_id`, `seq`, `event_type`, `recorded_at`,
  `payload`, `body_sha256` (`events.py` insert `_INSERT` at `events.py:186-194`).

---

## 3. `_state_body` semantics

`_state_body(request, allowed)` is the whole body contract for routes 2-7 (`handlers.py:461-466`):

```python
def _state_body(request: Request, allowed: set[str]) -> dict[str, Any]:
    body = request.json_body()
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise errors.InvalidRequest(f"unknown fields: {', '.join(unknown)}", fields=unknown)
    return body
```

* **An unknown field is refused with `400 invalid_request`**, and the `details` payload carries
  `fields` — the sorted list of offending names (`errors.py:35-37`, `19-32`). There is no
  "ignore extras" path. `idempotency_key` is an unknown field on all seven routes; the replay key is
  `event_id` (§5).
* Body parsing is `request.json_body()`: an absent body returns `{}` (`src/jasmine_core/api/request.py:39-40`),
  a non-object body is `invalid_request`, a body over 1 MiB is `payload_too_large` (413), a declared
  non-JSON content type is `unsupported_media_type` (415) (`request.py:41-54`).
* **`host_id` is required in effect on all seven routes and must be an `hst_` id.** It is not in the
  "required" column of `_state_body` because the check lives in `_event`:
  `if not ids.is_id(host_id, "hst"): raise errors.InvalidRequest("host_id must be a hst_ id", field="host_id")`
  (`state.py:177-178`). For route 1 it is `require_id(body, "host_id", "hst")` (`models.py:265`) plus
  a Registry existence check (`objects.py:117`, `registry.py:39-42` → `404 host_not_found`).
  A missing `host_id` on routes 2-7 is **`400 invalid_request`**, not `missing_expected_revision`,
  because `_event` runs before `_revision` (§9).
* **`event_id` is optional on all seven routes and must be an `evt_` id when present**
  (`state.py:179-180`; `models.py:268`). When omitted the server mints one (`state.py:183`).
  On route 2 it also becomes the created Step's `source_event_id` (`state.py:257`, `193`;
  `m0004_task_step_state.py:24` makes that column `NOT NULL UNIQUE`).
* **No field is silently optional.** Route 2: `title` required non-empty ≤512 chars, `description`
  defaults to `""` and must be a string (`state.py:242-247`). Routes 3-7 have no other optional field
  beyond `reason` on route 3 and `event_id` on all.
* Task create's allowed set is policed separately by `NewObject.task` / `_reject_unknown`
  (`models.py:250-271`, `310-320`), which additionally rejects `expected_revision`
  (`400 unexpected_expected_revision`, §2).

---

## 4. Revision semantics

The single check is `StateStore._revision` (`state.py:152-161`):

```python
if expected is None:                      raise errors.MissingExpectedRevision(...)   # 400
if type(expected) is not int or expected < 1: raise errors.InvalidRequest(...)         # 400
if snapshot["revision"] != expected:      raise errors.RevisionConflict(...)           # 409
```

* **`expected_revision` is required on routes 2-7 and forbidden on route 1** (§2). It is compared
  against the *object the route is about*:

  | Route | Compared against | Source |
  | --- | --- | --- |
  | 2 Step create | the **Task's** revision | `state.py:266` (`self._revision(task, expected, "task")`) |
  | 3 Step transition | the **Step's** revision | `state.py:328` |
  | 4 Task transition | the **Task's** revision | `state.py:377` |
  | 5 Task criteria | the **Task's** revision | `state.py:404` |
  | 6 Task accept | the **Task's** revision | `state.py:428` |
  | 7 Step criteria | the **Step's** revision | `state.py:295` |

  Consequence the UI must honour: **a Step transition needs the Step's revision but changes the Task
  revision too**; the Task's revision must be re-read after any Step write before a Task-level action
  is offered.
* **A mismatch returns `409 revision_conflict`** (`errors.py:89-91`) with
  `details = {expected_revision, current_revision, current_snapshot, kind}` (`state.py:159-161`;
  `kind` is `"task"` or `"step"`). `current_snapshot` is the full current object — the UI should show
  it as Core's current value beside the operator's draft, not silently adopt it. Verified live at
  `tests/test_state.py:92-95` (`details["current_revision"] == 2`).
* **Which write bumps what** (ADR 0006 line 9: "*Step creation requires the current Task revision and
  increments it. Step transition and Step criteria update require the current Step revision and
  increment both Step and Task revisions. … Task transition, criteria update and acceptance require
  the current Task revision.*"):

  | Route | Step revision | Task revision |
  | --- | --- | --- |
  | 2 Step create | new row starts at 1 (`state.py:256`, `271`) | +1 (`state.py:273`) |
  | 3 Step transition | +1 (`state.py:354`) | +1 (`state.py:356`) |
  | 7 Step criteria | +1 (`state.py:303`) | +1 (`state.py:305`) |
  | 4 Task transition | — | +1 (`state.py:387`) |
  | 5 Task criteria | — | +1 (`state.py:412`) |
  | 6 Task accept | — | +1 (`state.py:450`) |
  | 1 Task create | — | row starts at 1 (`objects.py:207`) |

* **UI rule:** read the object first (`GET /v1/tasks/{tsk}` / `GET /v1/steps/{stp}`), send that exact
  integer back, and **re-read after** the write. On `409` keep the draft and show Core's
  `current_snapshot`/`current_revision`; never re-submit the original intent with a new revision
  automatically. This is the existing milestone-B rule (`useWriteAction.ts:24-33`,
  `P6-02-COMPLIANCE.md:196-209`) and the taskbook's (§5 item 3, line 93: "*409 后展示当前值与用户草稿,
  重新审阅再提交,不自动用新 revision 重试原意*").
* **Concurrent writer:** the whole read-check-write runs inside `BEGIN IMMEDIATE`
  (`state.py:240`, `281`, `312`, `363`, `393`, `418`; `db.py:72-98`), so two clients presenting the
  same revision serialize: one gets the write, the other `409 revision_conflict`. Proven by
  `tests/test_state.py:222-235` (`sorted(statuses) == [201, 409]`).
  *Implementation note, recorded rather than hidden:* ADR 0002 §2.3 describes the lock as
  `UPDATE … WHERE revision = :expected` with 0 rows → conflict; the implementation instead SELECTs
  the snapshot and compares in Python before the UPDATE (`state.py:152-161`), and its projection
  UPDATE carries no `WHERE revision`. Under `BEGIN IMMEDIATE` the two are equivalent for a single
  writer, but the ADR's literal SQL is not what runs. This proposal follows the code (taskbook rule),
  and flags the divergence for the reviewer.

---

## 5. Replay, idempotency, and "timeout means unknown"

**The replay key is `event_id`** (ADR 0003 §1.3 line 42: "*幂等键 = `event_id`*"). None of these
routes accepts `idempotency_key`; `_state_body` would refuse it (§3).

Per write, in order:

1. `_event` mints or accepts the `evt_` id and builds a `NewEvent` whose `hash_payload` excludes
   `result`, `projection_at`, `from`, and (for `step.created`) `step_id` (`state.py:194-203`). So
   `expected_revision`, `host_id`, `actor_id`, the target status and the criteria **are** hashed:
   a replay must repeat the command's semantics, not merely its `event_id`.
2. If the id already exists, `StateStore._event` returns `self.events.append(spec)`
   (`state.py:204-205`); `EventStore._append` returns `(existing, True)` when the stored
   `body_sha256` equals the new digest, and raises **`409 event_id_conflict`** when it differs
   (`events.py:142-145`, `165-173`; ADR 0003 §1.3.3; `errors.py:79-81`).
3. `_commit` appends and, if a replay appeared between the preflight and the commit, raises
   `EventIdConflict("event appeared during state write")` (`state.py:208-213`). The check is
   therefore made twice.
4. **A replay returns before every state/revision/Evidence check** (`state.py:264-265`, `293-294`,
   `326-327`, `375-376`, `402-403`, `426-427`). This is deliberate: the first write already
   succeeded, so the operator's retry must not be re-judged against a world that has since moved.
   The cost is that a retry after a *successful but unacknowledged* write **must repeat the exact
   body** (same `expected_revision`, `host_id`, target and criteria), or it is not recognised as a
   replay and will instead fail as a stale revision or a conflict.

**What a replay returns, and with which status:** the original Event and the original response
snapshot, `replayed: true`; HTTP **200** for routes 2-7, **200** for route 1 (whose fresh status was
201) — `handlers.py:401-402`, `476`, and the hard-coded `Response(200, …)` sites in §2. No row is
written (`events.py:128-131`; ADR 0003 §1.3.2). Verified by `tests/test_state.py:49-57` and
`63-75`.

**"Timeout means the result is unknown."** Freeze the following, and nothing weaker:

* A transport failure, abort or timeout is its own state — `unknown` (`useWriteAction.ts:44`, `204-213`;
  `P6-02-COMPLIANCE.md:206`). The UI must **not** show success, must **not** show failure, and must
  **not** auto-retry with a fresh `event_id`.
* **It must show the request id on every failure path, including abort and unreachable — this is an
  explicit implementation requirement of this slice, not a description of the code as it stands.**
  Revision 2 chose to *keep* the check and require the change, rather than drop it: the request id is
  most valuable in exactly the case the rule exists for — an outcome the operator cannot otherwise
  correlate — and the change is small and local. Today the client mints `x-request-id` per request
  (`client.ts:131`, forwarded by the proxy at `core-proxy.ts:70`) but discards it:
  `CoreApiError.requestId` is populated only from a Core error envelope (`client.ts:78`), and
  `useWriteAction`'s abort branch (`:204-213`) and generic-failure branch (`:218-228`) never call
  `setRequestId`, so `WritePanel` renders no id (`WritePanel.tsx:261-265`). The changes this slice
  must carry:
  1. `CoreClient.request` surfaces the id it generated when it throws for a transport failure or an
     abort (attach it to the thrown `CoreApiError`), so the caller can always name the request;
  2. `useWriteAction` calls `setRequestId(...)` in the abort/unreachable branch (`:204-213`) and the
     generic-failure branch (`:218-228`), matching the `CoreApiError` branch that already does
     (`:216`);
  3. the proxy's own refusals set `X-Request-Id` (`refuse()`, `core-proxy.ts:143-149`, sets none
     today) — including the `502 proxy_core_unreachable` (`core-proxy.ts:208-213`) — echoing the
     incoming id when present and minting one otherwise, so a reader that is not the JS client can
     correlate too;
  4. the **browser** client reads a response's `X-Request-Id` header into `CoreApiError.requestId` —
     today `parseErrorBody(status, body)` never looks at headers, so a proxy-generated `403/404/502`
     (which has no Core envelope and, before change 3, no id) leaves the panel unable to name the
     request even after change 3. Without this, change 3 helps only non-JS readers.
  **A proxy `502` that still has no request id is presented as `unknown`** — never success, never
  failure — and read back; the missing id is a correlation gap to record, not a verdict. If the
  operator navigates away before the outcome arrives, the response is discarded rather than attached
  to the object they moved to (`useWriteAction.ts:186-203`; `P6-02-COMPLIANCE.md:371-378`).
* **Recovery, in order:** (1) re-send the *identical* body with the same `event_id` — idempotent, per
  above; or (2) read the truth back: `GET /v1/tasks/{tsk}`, `GET /v1/steps/{stp}`,
  `GET /v1/tasks/{tsk}/steps`, and the command history `GET /v1/tasks/{tsk}/history` /
  `GET /v1/steps/{stp}/history`. The history route returns exactly the eight command types of this
  slice (`state.py:144-150`: `task.created`, `task.criteria_updated`, `task.transitioned`,
  `task.accepted`, `step.created`, `step.criteria_updated`, `step.transitioned`, `step.staled`), so a
  completed command is observable there.
* **The audit log is not a UI recovery channel.** `/v1/audit` is deliberately absent from the
  browser allowlist (`core-paths.json:2`; `test_dashboard_proxy_contract.py:47`, `175-194`), so the
  operator cannot read `audit_log` from the dashboard. The Gate may read it from SQLite (as
  milestone B did, `P6-02-COMPLIANCE.md:239-244`); the UI must not imply it can.
* **`source_event_duplicate` (ADR 0003 §1.3.1) *is* reachable on route 1; routes 2-7 cannot produce
  it.** `StateStore._event` sets `source_event_id=None` (`state.py:199`), which covers routes 2-7.
  Route 1 is the exception: `NewObject.task` accepts `source_system`/`source_event_id`
  (`models.py:251-253`; `source_system` at `:263`, `source_event_id` at `:267`),
  `ObjectStore._event_spec` copies both into the
  `NewEvent` (`objects.py:169-192`, `:180-181`), and `EventStore._append` maps the `ux_events_source`
  `IntegrityError` (`m0001_baseline.py:87`) to `SourceEventDuplicate` (`events.py:154-162`). A second
  create with the same source identity and a *different* `event_id` therefore answers **`409
  source_event_duplicate`** with `{source_system, source_event_id, existing_event_id}`. The reviewer
  reproduced this live (`REVIEW-p6-02-taskstep-write-spec/findings.md` F2); it is §11 negative 8. Do not
  instruct the reviewer to skip it.

---

## 6. Permissions and roles

| Route | Scope required by the route table | Additional server-side gate |
| --- | --- | --- |
| 1 Task create | `objects:write` | non-empty `acceptance_criteria` ⇒ `state:accept` **and** human/system (`handlers.py:393-398`) |
| 2 Step create | `state:write` | non-empty `acceptance_criteria` ⇒ `can_accept` **and** human/system (`state.py:249-252`) |
| 3 Step transition | `state:write` | target `SKIPPED`/`ACCEPTED`, or a `SKIPPED` source ⇒ `state:accept` + human/system (`state.py:333-336`) |
| 4 Task transition | `state:write` | CANCELLED source or target ⇒ `state:accept` + human/system (`state.py:381-384`) |
| 5 Task criteria | `state:accept` | human/system via `_role` (route scope already guarantees the scope) |
| 6 Task accept | `state:accept` | human/system via `_role` |
| 7 Step criteria | `state:accept` | human/system via `_role` |

* Scopes are checked at dispatch, before the handler and before any table is touched
  (`dispatch.py:45-51`), by `Principal.require` → **403 `forbidden_scope`** with
  `details = {required, granted}` (`auth.py:34-39`; `errors.py:55-57`).
* `can_accept` is computed in the handler from the authenticated scopes:
  `"state:accept" in principal.scopes` (`handlers.py:475`, `523`, `533`). Routes 5-7 pass
  `can_accept=True` (`handlers.py:514`, `543`, `552`) because their route scope already required
  `state:accept`; the in-store check is then redundant, and `_role` is the remaining guard.
* `_role` refuses a non-human/system actor with **403 `forbidden_actor_kind`**
  (`state.py:163-166`; `errors.py:65-67`) — but only on the paths that call it.
* **An agent credential is refused on a human-only action by two independent mechanisms:**
  1. **Issuance:** `auth.issue_key` refuses to mint a key carrying `state:accept` for an `agent`
     actor — `403 forbidden_actor_kind` (`auth.py:63-64`; `SCOPES` at `auth.py:20-23`;
     `ACTOR_KINDS = ("human","agent","system")` at `registry.py:17`). So an agent *cannot hold* the
     scope that routes 5-7 require.
  2. **Enforcement:** if an agent key somehow carried it, `_role` still refuses (`state.py:164-166`).
     For route 3 an agent key *can* hold `state:write`; asking it to transition to `ACCEPTED` fails
     at `can_accept` with `403 forbidden_scope` (`state.py:334-335`), proven by
     `tests/test_state.py:154-157` for `SKIPPED`.
* **Actor identity is the token, never the body.** For route 1 the optional `actor_id` field must
  equal the authenticated actor or Core answers **403 `actor_mismatch`** (`objects.py:110-116`;
  `errors.py:60-62`). Routes 2-7 have no `actor_id` field at all; sending one is `400 invalid_request`
  via `_state_body`.
* **What the UI must not do:**
  * no local state machine that decides a transition is legal — it may *offer* the actions the
    operator asked for, but the verdict is Core's, rendered from Core's code
    (`STEP_EDGES`/`TASK_EDGES`, `state.py:22-38`; taskbook §5 item 3, line 93);
  * no optimistic status, badge, revision or "已保存" before Core's response (§8);
  * no scope escalation and no credential swap on a refusal: a `403` names the missing
    scope/role and offers no alternative (`WritePanel.tsx:239-252`; `P6-02-COMPLIANCE.md:207-209`);
  * no route into `/v1/evidence/confirm`, `/v1/audit`, `/v1/auth/*` (out of scope; §1, §10).

---

## 7. Evidence thresholds

Evidence is checked **only** on the transitions whose target requires it, and always inside the
state transaction, before the command Event is appended:

| Transition | Threshold | Source |
| --- | --- | --- |
| Step → `VERIFIED` | criteria non-empty, validator passes | `state.py:341-351` |
| Step → `ACCEPTED` | same, plus `require_user_confirmation=True` | `state.py:345-346` |
| Task accept | ≥1 ACCEPTED Step, no unfinished Step, criteria non-empty, `require_user_confirmation=True` | `state.py:434-443` |
| every other legal edge | none | `state.py:339-351` skipped |

**What the server answers when it is insufficient: `422 missing_evidence`** (`errors.py:138-140`),
with different `message`/`details` per cause:

* Step with no requirements: `missing_evidence`, `{"step_id": …}` (`state.py:343-344`).
* Task with no requirements: `missing_evidence`, `{"task_id": …}` (`state.py:439-440`).
* A requirement not matched: `missing_evidence`, `{"requirement_key", "rule_id", "rule_version"}`
  (`evidence.py:1037-1040`).
* `ACCEPTED` without a current human confirmation: `missing_evidence`
  (`evidence.py:1048-1052`).
* Current fingerprint unavailable/partial: `missing_evidence` (`evidence.py:997-1002`).
* An applicable ACTIVE `ACCEPTANCE`/`VERIFY` Rule lacking structured requirements:
  `missing_evidence`, `{"rule_id", "version"}` (`evidence.py:972-975`).
* The default validator always refuses: `RefuseUnverifiedEvidence` (`state.py:101-104`,
  installed unless Evidence wiring is passed at Core construction, `state.py:117`). In a real Core the
  injected validator is `CurrentEvidenceValidator` (`handlers.py:40-41`, `evidence.py:830`).
* `FingerprintUnavailable` (`errors.py:143-144`, a `MissingEvidence` subclass with code
  `fingerprint_unavailable`, 422) exists, but **is not observable through these seven routes**: the
  validator catches it and re-raises plain `missing_evidence` (`evidence.py:999-1000`), and `_fresh`
  swallows it (`evidence.py:943-944`). The advice still stands as defensive UI guidance — the 422
  branch should render whatever code Core actually returns rather than hard-code `missing_evidence`
  (`errors.ts` maps 422 to kind `invalid`; `useWriteAction.ts:108` renders it as `refused`) — but
  **no acceptance negative may depend on `fingerprint_unavailable` appearing**, because through this
  slice it cannot. Producing it needs `POST /v1/workspaces/fingerprint`, which is out of scope (§1).

**The object's state must be unchanged afterwards.** `EvidenceValidator.validate` runs after
`_revision` and the edge checks but before `_commit` (`state.py:345-352`, `441-448`); when it raises,
the surrounding `db.transaction` rolls back (`db.py:92-98`) and neither the command Event nor the
projection exists. ADR 0006 line 7: "*Any validation or commit failure leaves both unchanged.*" ADR
0007 line 15: "*The validation transaction itself raises `missing_evidence` without trying to persist
STALE inside a rollback.*" Verified by `tests/test_state.py:98-115`: after the 422 the Step is still
`EXECUTED` at revision 3.

**Read the criteria↔Evidence matrix; do not recompute it.** The written contract is the validator's
own derivation: it takes `step["acceptance_criteria"]` (`state.py:342`), enumerates every applicable
ACTIVE global/project/task Rule with `_rules` (`evidence.py:960-988`), and matches current,
fingerprint-fresh, revision-bound Evidence with `_fresh`/`_match` (`evidence.py:914-958`,
`1028-1042`). The result it freezes into the Event and response is `evidence_ids` +
`rule_versions` (`state.py:348-351`, `444-447`).

Therefore:
* the write form may **display** criteria read from `GET /v1/tasks/{tsk}` / `GET /v1/steps/{stp}` and
  the `evidence_ids`/`rule_versions` Core returned, and may link each `evd_` to
  `GET /v1/evidence/{evd}` (already in the read allowlist, `core-paths.json:25`);
* the existing front-end reference matrix is already labelled as such — *"criteria → Evidence
  参考矩阵(前端匹配,仅供参考)"* (`TaskEvidence.tsx:244-245`, matching at `TaskEvidence.tsx:89-101`) —
  and a **write** panel must not promote that client-side match to a permission to submit or to a
  claim of sufficiency. Item 2's forms will need this stated the same way;
* the operator must ensure the workspace fingerprint is current, because `_fresh` requires a stored
  fingerprint that compares `SAME` (`evidence.py:914-947`). Refreshing it is
  `POST /v1/workspaces/fingerprint` — **out of scope here** (§1) and able to move VERIFIED Steps to
  STALE (`ADR 0007:15`), so "the form says Verified" is not something this slice may promise.

---

## 8. Audit and command Events

| Route | Command Event type | Object identifiers in the response |
| --- | --- | --- |
| 1 Task create | `task.created` (`models.py:262`, `objects.py:179`) | `object.task_id`, `event.event_id`, `Location` |
| 2 Step create | `step.created` (`state.py:260`) | `step.step_id`, `event.event_id` |
| 3 Step transition | `step.transitioned` (`state.py:320`) | `step.step_id`, `evidence_ids`, `rule_versions`, `event.event_id` |
| 4 Task transition | `task.transitioned` (`state.py:370`) | `task.task_id`, `event.event_id` |
| 5 Task criteria | `task.criteria_updated` (`state.py:397`) | `task.task_id`, `event.event_id` |
| 6 Task accept | `task.accepted` (`state.py:421`) | `task.task_id`, `evidence_ids`, `rule_versions`, `event.event_id` |
| 7 Step criteria | `step.criteria_updated` (`state.py:286`) | `step.step_id`, `event.event_id` |

All seven types are in the frozen enum (`models.py:39`, `45-50`), and all seven are in `StateStore.history`
(`state.py:145-147`), so the history endpoint is the correct readback for them.

* **Audit row per request, including refusals.** `dispatch` records one `audit_log` row in its own
  transaction for every resolved request, with `request_id`, `method`, `path`, `decision`, `actor_id`,
  `status_code`, `scope`, `target_id`, `error_code`, `detail` (`dispatch.py:90-108`; ADR 0003 §1.4
  final paragraph). `decision` is `deny` for 401/403, `error` for 5xx, `allow` otherwise
  (`dispatch.py:77`).
* **`target_id` is route-specific: route 1 records the created `task_id`; the other six record the
  command `event_id`.** `_target_of` (`dispatch.py:115-128`) checks the top-level id list first
  (`:118`), then prefers a nested `object` (`:122-123`) **before** a nested `event` (`:125-126`).
  Route 1's body is `{"object": …, "event": …, "replayed": …}` (`handlers.py:401-405`), so the first
  `_`-containing string value inside `object` wins — `task_id` (the row built at `objects.py:57-66`; on the replay path
  `objects.py:159-166`) → `tsk_…`. The other six responses carry no top-level `task_id`/`step_id` and
  no `object`, so they fall through to `event` → `evt_…`. The reviewer observed this live
  (`REVIEW-p6-02-taskstep-write-spec/findings.md` F1): `POST /v1/tasks` 201 → `target_id = tsk_…`;
  `POST /v1/tasks/{tsk}/steps` 201 → `target_id = evt_…`. This is a falsifiable claim worth an
  explicit Gate check; §11's audit assertion is route-specific to match.
* **Readback requirement.** After a success the UI re-reads the object's own row and the history and
  renders Core's re-read value — never the write response as if it were current state
  (`MemoryWrites.tsx:108-133`, `P6-03B2-WRITE-SPEC.md:131-134` criterion 3). The write response is
  shown verbatim (`WritePanel.tsx:269-291`) with its `replayed` flag; the command Event id it returns
  is linked. Because a Step write also advances the Task revision (§4), the readback must refresh
  the Task as well as the Step.
* The audit readback for the Gate is a **SQLite** read (the browser has no `/v1/audit`, §5); the
  bundle records it as a validator-side check, as milestone B did (`P6-02-COMPLIANCE.md:239-244`).

---

## 9. Error vocabulary and precedence

Wire codes and statuses (ADR 0003 §1.4; `errors.py`), restricted to what this slice can produce:

| HTTP | code | This slice's triggers |
| --- | --- | --- |
| 400 | `invalid_request` | unknown body field (`handlers.py:465`); bad path id (`handlers.py:438-442`); missing/blank title or reason; bad status; non-positive or non-integer `expected_revision`; bad `host_id`/`event_id` shape; invalid criteria (`state.py:41-69`) |
| 400 | `missing_expected_revision` | `expected_revision` absent on routes 2-7 (`state.py:154-155`) |
| 400 | `unexpected_expected_revision` | `expected_revision` on route 1 (`models.py:314-320`) |
| 400 | `invalid_interpretation_source` | not reachable here |
| 401 | `unauthenticated` | no/bad/revoked bearer (`auth.py`; `errors.py:50-52`) |
| 403 | `forbidden_scope` | route scope missing; route 1 criteria without `state:accept`; route 3 target SKIPPED/ACCEPTED without it; `can_accept` false (`handlers.py:396`, `state.py:250-251`, `334-335`) |
| 403 | `forbidden_actor_kind` | agent on a human/system path (`state.py:164-166`); agent key issuance with `state:accept` (`auth.py:63-64`) |
| 403 | `actor_mismatch` | route 1 body `actor_id` ≠ token actor (`objects.py:111-116`) |
| 404 | `task_not_found` / `step_not_found` / `project_not_found` / `host_not_found` | referenced object absent (`errors.py:70-76`; `state.py:119-133`; `objects.py:234`) |
| 404 | `route_not_found` | unknown path (`handlers.py:146`; note the code is `route_not_found`, not `not_found`) |
| 405 | `method_not_allowed` | e.g. `GET /v1/tasks/{tsk}/transition` (`handlers.py:142-145`) |
| 409 | `revision_conflict` | stale `expected_revision` (`errors.py:89-91`) |
| 409 | `invalid_state_transition` | illegal edge; wrong Task status for a child write; criteria on a VERIFIED/terminal Step or terminal Task; Task accept without an ACCEPTED Step (`state.py:268`, `299-300`, `330-332`, `378-379`, `408-409`, `432-437`) |
| 409 | `event_id_conflict` | same `event_id`, different semantic body (`events.py:165-173`; `state.py:212`) |
| 409 | `source_event_duplicate` | **route 1 only**: a second Task create with the same `source_system`/`source_event_id` and a different `event_id` (`events.py:154-162`; `m0001_baseline.py:87`; §5, §11 negative 8). Routes 2-7 pass `source_event_id=None` (`state.py:199`) |
| 413 / 415 | `payload_too_large` / `unsupported_media_type` | `request.py:41-54` |
| 422 | `missing_evidence` | §7. `fingerprint_unavailable` exists (`errors.py:143-144`) but the validator converts it to `missing_evidence` before it leaves (`evidence.py:999-1000`), so it is not observable here |
| 500 | `internal_error` | unexpected failure; rollback (`dispatch.py:80-88`) |
| 503 | `database_busy` | write lock past `busy_timeout` (`db.py:53-66`; `errors.py:126-130`) |

**Precedence, as the code actually orders it** (a client that assumes a different order will
mis-read a compound bad request):

1. route resolution → 405 or 404 (`handlers.py:133-146`);
2. authentication → 401 (`dispatch.py:46`);
3. route scope → 403 `forbidden_scope` (`dispatch.py:47-48`);
4. path-id shape → 400 `invalid_request` (`handlers.py:472`, `511`, `520`, `530`, `540`, `549`);
5. body parse → 413/415/400 (`request.py:39-54`);
6. unknown fields → 400 `invalid_request` (`handlers.py:465`);
7. object load → 404 (`state.py:120-133`);
8. **route 3/4 only:** target status validity → 400, *before* `_event` (`state.py:316-317`, `366-367`);
9. `_event`: `host_id`/`event_id` shape → 400, then the replay branch
   (`state.py:177-180`, `204-205`);
10. `_revision`: 400 missing / 400 invalid / 409 conflict (`state.py:152-161`);
11. state, actor kind and reason checks → 403/409/400 (`state.py:163-172`, `329-338`, `378-384`, …).
    **Not a total order for route 2**: its `can_accept`/`_role` check runs *before* `_event`/`_revision`
    (`state.py:249-252`), so a Step create that needs `state:accept` answers 403 before a stale
    `expected_revision` would answer 409; the table above is the common order, not a guarantee that
    every route checks in it. The **recovery edges are also asymmetric** between the two objects —
    Task recovery is `CANCELLED→ACTIVE` only, while Step recovery is
    `FAILED|BLOCKED|STALE|SKIPPED→IN_PROGRESS` (`docs/adr/0006-…:11-13`; `state.py:20-38`) — so the UI
    must read the edge set it is given and must never assume symmetry, and this slice exercises
    neither recovery path beyond what §11 lists;
12. Evidence → 422 (`state.py:341-351`, `438-448`).

Two consequences to freeze because they are counter-intuitive:
* **A request missing both `host_id` and `expected_revision` answers `400 invalid_request` (host_id),
  not `missing_expected_revision`**, because step 9 precedes step 10. `tests/test_state.py:83-85`
  supplies `host_id` and therefore sees `missing_expected_revision`; a body missing both is a
  different answer.
* **Route 1 (Task create) never returns `missing_expected_revision`** — `expected_revision` is
  refused up front as `unexpected_expected_revision` (§2).

---

## 10. The proxy allowlist change

**Where the allowlist lives:** `web/server/core-paths.json` is the single source read by both hosts —
the Vite dev/preview middleware (`web/server/core-proxy.ts:22`, `47-61`) and the standalone static
server (`web/server/serve.mjs:110-156`); both select the read list or the write list purely by HTTP
method (`core-proxy.ts:136-141`; `serve.mjs:178-187`). One edit covers both.

**Exact patterns to add to `write_paths`** (alongside the existing four milestone-B families,
`core-paths.json:37-42`), with `ID_RE = [a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}` as the file already
spells it (`core-paths.json:12-31`; `test_dashboard_proxy_contract.py:56`):

```
^/v1/tasks$                                    (POST: Task create)
^/v1/tasks/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/steps$
^/v1/tasks/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/transition$
^/v1/tasks/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/criteria$
^/v1/tasks/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/accept$
^/v1/steps/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/transition$
^/v1/steps/[a-z]{3}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}/criteria$
```

Seven patterns for seven routes. `write_methods` stays `["POST"]` (`core-paths.json:34-36`); the GET
list (`methods`, `paths`) is **unchanged**, and no new pattern may appear in it
(`test_dashboard_proxy_contract.py:196-204`, `230-231`).

**These are always-on, not behind a switch — and the file's own note is why.** The `*_pending_*`
lists exist for routes that are real on an *unmerged* phase branch and absent from this baseline's
Core, so forwarding them would send the browser to a Core that cannot answer
(`core-paths.json:50`, `60`, `74`). The seven state routes are the opposite case: this baseline
serves them (`handlers.py:115-124`), and `tests/test_state.py` exercises them over the real HTTP
surface today. The `write_note` states the separate, narrower reason a write is kept out:
`manual-reapply`, `resolve/{int}` and `evidence/confirm` "*need an explicit operator decision outside
this slice*" (`core-paths.json:43`) — an operator decision, or ADR-0007 provenance the browser cannot
mint. **Neither reason applies to these seven**: Core itself enforces the scope, the human/system
actor kind, the revision, the state machine, the Evidence threshold and the `event_id` idempotency on
every request (`core-proxy.ts:12-15`), so the proxy decision is only "may the browser ask", and the
answer for baseline-served, audited routes is yes. An opt-in flag would add a failure mode
(`proxy_path_not_allowed` shown as a missing dependency). **A flag is not pure ceremony — it would
remove some attack surface — so the accurate claim is that its marginal safety gain over the
existing always-on milestone-B surface is *small*, not nil**, while the consistency cost and the
false-status cost (`errors.ts:69-83` maps `proxy_path_not_allowed` to "dependency pending (P3 not
merged)") are real. The reviewer reached the same weighting (`REVIEW-p6-02-taskstep-write-spec/findings.md`
§3.1) and made it conditional on two mandatory Gate obligations, both folded in here:
* the `test_dashboard_proxy_contract.py:224` typo is fixed (below); and
* **a browser-level Gate negative proves the proxy still refuses the out-of-scope POSTs** —
  `evidence/confirm`, `workspaces/fingerprint`, `tool-results`, `resolve/{int}`, `manual-reapply` —
  with `proxy_path_not_allowed`, driven through a real browser (§11 negative 9). That is what makes
  "always-on" auditable rather than merely asserted.

**Deliberately still absent** and to be asserted absent by the updated test: `POST /v1/evidence/confirm`
(§1), `POST /v1/tool-results`, `POST /v1/workspaces/fingerprint`, `POST /v1/resolve/{int}`,
`POST /v1/resolutions/{id}/manual-reapply` (`test_dashboard_proxy_contract.py:218-229`). **The
existing absence check has a defect that must be fixed in the same edit:** line `224` names the
*singular* `/v1/workspace/fingerprint`, while Core's route is `POST /v1/workspaces/fingerprint`
(`handlers.py:113`), so that one assertion is vacuous — it cannot fail for the real path. The
implementation must correct it to the plural path, keep the exact-list assertions
(`tests/p6/test_dashboard_proxy_contract.py:128-156`) otherwise unchanged, and rely on the
browser-level counterpart (§11 negative 9) for the always-on condition.

**Test obligations the implementation must carry, stated now so a reviewer sees the diff:**
`tests/p6/test_dashboard_proxy_contract.py:128-156` pins `write_paths` **exactly** (and the GET list
exactly), and `:206-231` requires every write pattern to be backed by a real Core route and the
out-of-scope paths to remain closed. Adding the seven means a deliberate edit to that exact-list
assertion — not a wildcard, not a widened id segment — **and** the `:224` singular-path fix from the
paragraph above, so the closed set the test asserts is the real one. On the JS side,
`web/tests/endpointContract.test.ts:1-23`, `:40-77` calls every endpoint the UI has and asserts the
proxy would forward it, so a new client call without an allowlist entry fails there. Neither test is
sufficient by itself for the always-on decision: the browser-level negative (§11 negative 9) is the
second condition §10 states.

---

## 11. Acceptance plan

**This is a proposal for a Gate, not a Gate.** It follows the shape of the milestone-B Gate and the
real-Core plan (`P6-02-COMPLIANCE.md:211-244`; `web/REAL-CORE-ACCEPTANCE-PLAN.md:18-60`): an isolated
Core from a pinned archive with its own `core.db`, workspace and keys, the dashboard served from a
pinned archive over the proxy, a real browser driving real clicks, and every write verified by
re-reading the object — not by a banner.

**Positive path (all seven routes, real human key, real browser):** for each route record
`method · path · scope · actor kind · host_id · event_id · expected and observed revisions · Core's
status and response · the command Event id · the audit row · the object's re-read row`. Assert:
fresh route 1/2 answer 201, routes 4/5/7 answer 200, **and route 3 answers 200 on every edge that is
not Evidence-gated** (only its `→VERIFIED/→ACCEPTED` edges consult the validator, `state.py:341-351`,
so e.g. `PLANNED→IN_PROGRESS` is an always-producible fresh 200 and is a required PASS); `replayed` is
`false`; each command Event exists
with the expected `event_type`; `audit_log` gained exactly one row for a unique `X-Request-Id`
naming the actor, the POST, the decision and the status, **with `target_id` equal to the created
`task_id` for route 1 and to the command `event_id` for the other six** (`_target_of`,
`dispatch.py:115-128`; §8); and the re-read Task/Step revision matches the response's
`task_revision`/row revision. Task create must show `acceptance_criteria` in the returned object; a
Step write must be followed by a re-read of **both** the Step and the Task.

**Route 3 (Step transition) and route 6 (Task accept) are Evidence-gated, and the Gate must record
them as such rather than require a PASS the environment cannot produce.** `accept_task` always
validates Evidence (`state.py:441-448`), so route 6 has no fresh-200 path without a *passing*
validation; route 3's `VERIFIED`/`ACCEPTED` targets are gated the same way (`state.py:341-351`),
while its other legal edges are not (`state.py:339-351` is skipped). §12 states plainly that a
passing validation may be impossible in the operator's environment. Therefore:
* where the environment can produce a passing validation, record route 6 and the gated route-3 edges
  as **PASS** with the 200, `replayed:false`, the command Event, the audit row and the re-read; and
* where it cannot, record route 6 (and any gated route-3 edge that fails) as **BLOCKED** — or
  **PARTIAL** when some gated edges pass and others do not — **with the reason**: the exact `422`
  code and `details` Core returned, the fingerprint state, and the fact that no write occurred.
  Assert the negative side in that case instead: the projection and revision are unchanged and no
  command Event was appended (`state.py:345-352`, `441-448`; `tests/test_state.py:98-115`).

A Gate that reports route 6 as a failure, or that quietly omits it, misstates both the slice and the
environment. This paragraph is what reconciles the positive path with §12.

**Negatives that must be required, at minimum** (taskbook §7 lines 134-135; the first five are also
named in the operator's brief):

1. **Stale `expected_revision` keeps the draft and returns the conflict error.** Submit with revision
   *n*, then submit the same intent with the now-stale *n*: expect `409 revision_conflict`, the
   operator's draft still in the field, Core's `current_revision`/`current_snapshot` displayed, and
   **no second POST** issued by the panel (`useWriteAction.ts:24-33`; pattern proven at
   `web/tests/memoryWriteViews.test.tsx:229-259`). Measure truth unchanged with a per-table digest
   (`P6-02-COMPLIANCE.md:224-227`).
2. **A transition without the required Evidence leaves the state unchanged and says
   `missing_evidence`.** Drive a Step to `EXECUTED`, submit `VERIFIED` with no satisfying Evidence:
   expect `422 missing_evidence`, the Step still `EXECUTED` at the same revision, and no new Event
   (`tests/test_state.py:98-115` is the component-level precedent; the Gate must reproduce it through
   the proxy and the browser). Also exercise `ACCEPTED` without a current human confirmation
   (`422`) and Task accept with no ACCEPTED Step (`409`). **Assert the *cause*, not merely the
   status:** the confirmation-specific branch (`evidence.py:1048-1052`) is reached only *after* the
   requirements match (`evidence.py:1043`), so without a satisfying Evidence item a bare `422` can
   come from the wrong branch; the Gate must check the message/`details` (for example the
   `requirement_key` form at `evidence.py:1037-1040`) or record this check **BLOCKED** with that
   reason when no satisfying Evidence exists in the environment (M4; reviewer caveat 11.4).
3. **An `agent`-kind key is refused server-side on a `state:accept` action even though the UI would
   let a human click it.** Two checks, both required: (a) key issuance for an agent actor with
   `state:accept` is refused `403 forbidden_actor_kind` (`auth.py:63-64`); (b) an agent key holding
   `state:write` that submits a Step transition to `ACCEPTED`/`SKIPPED` is refused
   `403 forbidden_scope` with no write (`state.py:334-335`; `tests/test_state.py:154-157`). And a
   Task create with non-empty criteria from the agent key → `403 forbidden_scope`
   (`tests/test_state.py:99-101`).
4. **A replayed `event_id` does not double-write.** Repeat the identical body: expect `replayed:true`,
   the same `event_id`, the same frozen Step/Task/revisions/`evidence_ids`, and the `events` table
   unchanged (the replay wrote nothing) while `audit_log` gains one row for the retry request
   (`tests/test_state.py:49-57`). Then send the same `event_id` with a changed body → `409
   event_id_conflict` and no write.
5. **A client-side timeout is presented as *unknown*, not as success or failure.** Abort the request
   in the browser (or stop Core after the request is sent): expect the `unknown` state with the
   request id and a readback instruction, **no** success banner and **no** failure verdict; then read
   back the object/history and show the truth (`useWriteAction.ts:204-213`;
   `P6-02-COMPLIANCE.md:196-209`). **This negative is stated against the code this slice must
   produce, not the code as it stands: it cannot pass until the three request-id changes in §5 are
   carried.** That is deliberate — the check is what drives the change — and a Gate run before the
   change must record the negative FAILED (or, if it runs the degraded form, record `unknown` with
   *no* id as a known gap), never silently drop it. A proxy `502` with no id is still `unknown`:
   never success, never failure.
6. **Unknown field and shape refusals reach Core:** `idempotency_key` on any of the seven →
   `400 invalid_request` naming the field; `expected_revision` on Task create →
   `400 unexpected_expected_revision`; a malformed `host_id`/`event_id` → `400 invalid_request`.
7. **Empty criteria refuse at the gate, not in the UI:** a Step with `{"requirements": []}` cannot
   reach `VERIFIED`/`ACCEPTED` (`422`), and acceptance shows Core's `evidence_ids`/`rule_versions`
   from the response rather than a client-computed matrix.
8. **A second Task create with the same source identity is refused `409 source_event_duplicate`.**
   From one key, create a Task carrying `source_system`/`source_event_id`; then submit otherwise
   valid create content with the same source identity but a *different* `event_id`: expect
   `409 source_event_duplicate` with `{source_system, source_event_id, existing_event_id}`
   (`events.py:154-162`; `m0001_baseline.py:87`), and assert that exactly one Task and one
   `task.created` Event exist (the refused create wrote nothing). Re-confirm that the *same*
   `event_id` with the same body is a replay, not a duplicate (negative 4). Route 1 is the only route
   that can reach this: routes 2-7 pass `source_event_id=None` (`state.py:199`). Reviewer F2.
9. **The out-of-scope POSTs are still refused by the proxy, driven from a real browser.** From the
   served dashboard, attempt `POST /v1/evidence/confirm`, `POST /v1/workspaces/fingerprint`,
   `POST /v1/tool-results`, `POST /v1/resolve/{int}` and
   `POST /v1/resolutions/{res}/manual-reapply`: each must be answered by the proxy itself with
   **404 `proxy_path_not_allowed`** (`core-proxy.ts:143-149`, `:165-171`) and must never reach Core.
   This is the browser-level condition the always-on decision in §10 depends on, and the plural
   fingerprint path must be the one asserted (`tests/p6/test_dashboard_proxy_contract.py:224`, fixed
   per §10).

**Evidence pack:** the §8 files and redaction rules of the taskbook
(`Jasmine-Core-V1-P6-阶段任务书.md:145-147`), under `validation-runs/p6/<stamp>-p6-02-taskstep-write/`,
containing `manifest.json` (commit/deps/mode/time), `scenario.md`, `click-trace.jsonl`, masked
request/response logs with `request_id`, `truth-before-after.json`, screenshots, raw test output and
`result.md`. **Status honesty:** this Gate can pass while item 2 (VERIFIED/ACCEPTED forms) and item 3
(`evidence/confirm`) remain undelivered. It therefore records `P6-02 TaskStep write: PASS` (with
route 6 and the gated Step edges `BLOCKED`/`PARTIAL` per the positive path above) for this slice
only; `P6-02` stays **PARTIAL** (`P6-02-COMPLIANCE.md:365-367`; `P6-REMAINING-TASKS.md:58-60`,
`65-67`), and nothing here may be described as P6 or V1 complete.

**Naming rule (M6).** The evidence namespace for this slice carries the token `taskstep`: directory
`<stamp>-p6-02-taskstep-write/`, status label `P6-02 TaskStep write`. The token `p6-02b` is
**reserved for P6-02 milestone B** — the P2 audited-write bundles, e.g.
`validation-runs/p6/*-p6-02b-t03-gate/`, `*-p6-02b-clickthrough/`, `*-p6-02b-wires/` — and must
never appear in this slice's bundle name or status label. The document's own file name and the
parent's shorthand (`P6-02B-TASK-STEP-WRITE-SPEC.md`, "P6-02b") are the *proposal's* label, not an
evidence namespace; wherever the two could be confused in a bundle listing or a status line, the
`taskstep` token wins.

---

## 12. What is frozen, what is left to implementation, what could not be verified

**Frozen by this proposal (a reviewer should reject a diff that contradicts any of these):**
the seven route/method/scope pairs; the allowed and required body fields per route; the 201/200
status split and the response key sets, including that a replay has the same keys as a fresh write;
`_state_body`'s unknown-field refusal; `host_id` required-as-`hst_`, `event_id` optional-as-`evt_`;
which revision each route reads and which counters each write bumps; `409 revision_conflict` keeping
the draft with no automatic retry; `event_id` as the replay key with `replayed` as the discriminator;
the timeout-as-unknown rule, the request-id surfacing requirement and the readback recovery; the
scope and actor-kind gates and the two independent agent refusals; the Evidence thresholds, the
`422 missing_evidence` outcomes, the state-unchanged requirement, and read-don't-recompute for the
criteria matrix; the command Event and the route-specific audit expectations (route 1 records the
created `task_id`; the other six the command `event_id`); the error vocabulary and its precedence; the
seven always-on proxy patterns; and the negatives in §11.

**Left to implementation (deliberately not frozen here):** component structure; which Task tab hosts
which form (`TaskTab = 'board' | 'history' | 'evidence'`, `web/src/app/router.ts:15`); whether the
write surfaces are navigation entries or deep links (milestone B chose deep links for Memory,
`P6-03B2-WRITE-SPEC.md:172`; the taskbook §5 wants the actions discoverable on the Task view, so a
Task-tab form is the likely answer — an implementation choice, not a contract); test ids and wording;
the exact fixture and Gate harness scripts; and how the UI presents the `STEP_EDGES`/`TASK_EDGES`
(`state.py:22-38`) as offered actions, provided legality is still Core's verdict. Reusing
`web/src/lib/ids.ts`'s `newEventId()` for the `evt_` replay key and `useWriteAction`/`WritePanel`
with `keyKind='event_id'` is the expected, but not mandated, route.

**Could not verify from the code (stated rather than assumed):**
* **Whether the operator's environment can produce a *passing* Evidence validation at all.** The
  validator needs a current, complete workspace fingerprint and Evidence satisfying the criteria and
  applicable Authority Rules (`evidence.py:914-1056`); whether `fingerprint.capture()` returns
  `complete` in a given environment, and whether an owner-only producer file is configured for
  specialised kinds (`ADR 0007:9`), is environment state that no source read here can establish.
  This is why §11 treats the `422` negatives as the required ones, allows route 6 and the gated Step
  edges to be recorded BLOCKED/PARTIAL, and does not require a positive VERIFIED/ACCEPTED acceptance
  in this slice (M4).
* **Anything about the operator's live Core** — this is a static reading of `663a941b`; no server was
  started and no browser was opened for this document.

**Two revision-1 items are closed and removed from that list** — the reviewer answered both from the
code and reproduced one live (`REVIEW-p6-02-taskstep-write-spec/findings.md` §1, rows 12.1 and 12.4):
* *A Task-create response's `object` always contains `acceptance_criteria` as a parsed object.* It
  does by construction: `ObjectStore.get` pops `acceptance_criteria_json` into `acceptance_criteria`
  (`objects.py:64-65`), the fresh create returns that row (`objects.py:222-224`), the projection
  stores `{"requirements": []}` when the field is omitted (`objects.py:210`), and the create-replay
  path builds the key explicitly (`objects.py:159-166`). The Gate still checks the key set; it is no
  longer an open question.
* *Scope is checked before the body is parsed.* Confirmed live: a malformed body on a route whose
  scope is missing answers `403 forbidden_scope`, not `400` — dispatch authenticates and checks the
  route scope (`dispatch.py:45-52`) before the handler reads `request.json_body()`.

**Three claims stated as fact in revision 1 were false and are corrected above; they are listed here
so the next reader sees they were wrong and are now right** (`REVIEW-p6-02-taskstep-write-spec/findings.md`
F1–F3):
1. *"The audit row's `target_id` is the command `event_id` for all seven routes"* (§8, §11) — false
   for route 1, which records the created `tsk_…` because `_target_of` prefers the nested `object`
   (`dispatch.py:115-128`; `objects.py:64-65`). Corrected in §8 and §11 (M1).
2. *"`source_event_duplicate` cannot arise from these routes"* (§5, §9) — false; route 1 accepts
   `source_system`/`source_event_id` (`models.py:251-253`; `:263`, `:267`; `objects.py:180-181`) and a
   second create with the same source identity answers `409` (`events.py:154-162`;
   `m0001_baseline.py:87`). Corrected in §5/§9 and tested by §11 negative 8 (M2).
3. *"It shows the last `request_id`" on a timeout* (§5, §11 negative 5) — not true of the cited code:
   the client-generated id is discarded (`client.ts:131` versus `:78`) and the abort/unreachable
   branches never set it (`useWriteAction.ts:204-214`, `:218-228`). Corrected by making the surfacing
   an explicit implementation requirement in §5 that §11 negative 5 then checks (M3).

The minor reachability correction to `fingerprint_unavailable` (F5) is recorded in §7 and §9; the
Evidence-gated positive-path correction (F4) is in §11; the citation slips (F7) are fixed at their
sites, along with one further `models.py` `host_id` line found while re-verifying M2 (`:264` → `:265`).

---

## 13. What this proposal deliberately does not decide

* Whether a web button may ever mint a human `USER_CONFIRMATION` — Core-owner ADR/API decision
  (§1).
* The VERIFIED/ACCEPTED *form* design and the criteria→Evidence matrix presentation — item 2, which
  depends on this slice (§1).
* Whether `POST /v1/resolutions/{id}/manual-reapply` or `POST /v1/resolve/{int}` are ever opened to
  the browser — separate operator decision (`core-paths.json:43`).
* Anything about `POST /v1/workspaces/fingerprint`, including when the fingerprint is refreshed or
  whether a VERIFIED Step may be moved to STALE — P1/P7 domain (`ADR 0007:15`).
* Any change to Core, Schema, scopes, id prefixes, Event types or `STEP_EDGES`/`TASK_EDGES`. If the
  implementation appears to need one, it is a **coordination request to the Core interface owner**
  (taskbook §3, line 59), not a change this slice may make.
* **The legal-target set for Task/Step transitions.** No route in schema 8 exposes `STEP_EDGES` or
  `TASK_EDGES`, so the implementation cannot learn from the API which targets are legal. It therefore
  offers Core's whole status **vocabulary** — a superset of the legal-target set — disables nothing as
  illegal and assumes no symmetry, and Core stays the only verdict on legality (`409
  invalid_state_transition` with `from_status`/`to_status`, §9). Recorded as the **coordination
  request to the Core interface owner** (taskbook §3, line 59; `P6-REMAINING-TASKS.md` §6) for a route
  that publishes the edge sets. This is a presentation limit, not a contract deviation: the
  implementation review ruled it acceptable under §12 and the independent validator observed exactly
  this behaviour with no false claim (its report, §10.8).
* The Gate's evidence-bundle format beyond naming it (§11): the §8 conformance generator is a
  separate, already-recorded work item (`P6-REMAINING-TASKS.md:121-138`).
