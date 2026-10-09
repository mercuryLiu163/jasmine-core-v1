# P6-02 item 5 — VERIFIED/ACCEPTED forms, the criteria↔Evidence matrix, and the Evidence-insufficient refusal

**Status: FROZEN at revision 2.** Revision 1 was independently reviewed by an adversarial
specification reviewer — verdict **APPROVE WITH MANDATORY CHANGES** (no BLOCKING; 4 MAJOR, 12 MINOR,
4 NOTE), all 20 findings folded into rev 2. Rev 2 was then delta re-reviewed by the same reviewer:
verdict **APPROVE WITH NAMED MANDATORY CHANGES**, no BLOCKING and no MAJOR, with all 20 rev-1 findings
verified fixed **in the text against the code** (not against the revision-history claims), and three
one-line text defects (D1–D3) prescribed. This revision carries D1–D3 applied verbatim from the delta
report, which is what the reviewer required for the freeze; it stated explicitly that **no further
delta round is required**. Reports:
`validation-runs/p6/REVIEW-p6-02-item5-verified-accepted/findings.md` (rev 1) and
`DELTA-findings.md` (rev 2).

**Not implemented.** This is a specification document, not an implementation and not a Gate. Nothing
below is evidence that any screen exists or that any write works from a browser. Implementation, its
independent review and its independent validation are still to come on this frozen contract, and
`P6-02` remains **PARTIAL**.

The slice is the layer above the seven delivered Task/Step write routes: the **criteria↔Evidence
matrix** that the VERIFIED and ACCEPTED forms must present, and the exact contract of the
**Evidence-insufficient refusal**. `evidence/confirm` is explicitly **out of scope** here and waits on
a Core ADR/API decision (see §11).

**Requirement source (taskbook).** `docs/Jasmine-Core-V1-P6-阶段任务书.md` §5 item 5 (line 95):
"VERIFIED/ACCEPTED 表单展示 criteria→Evidence matrix、规则版本、fingerprint、确认来源和缺项。接受动作只发
请求,成功后从 Core 读回;HTTP 200 或前端 optimistic update 不能制造验收。`422 missing_evidence`
保持原状态;Agent credential 即使 UI 显示 human 按钮也应被服务端拒绝。" Also §7's negative table
(lines 134-135) and §3's ownership/coordination rule (line 59). Where this proposal and the taskbook
differ, §12 records the divergence and files it as a coordination request; the taskbook's own line 16
is **not** used as a general "code wins" rule (rev 2, m10 — see the revision history).

---

## Revision history

| Rev | Subject | What it is |
| --- | --- | --- |
| 1 | untracked draft (746 lines, SHA-256 `f519aa1dc0bd413f45c6b05c8fca3a924d2469ae54b53a1cc27ff2d9c12c20a7`) | The proposal as first written. Independently reviewed by an adversarial specification reviewer (`validation-runs/p6/REVIEW-p6-02-item5-verified-accepted/findings.md`): verdict **APPROVE WITH MANDATORY CHANGES**, no BLOCKING; 25/25 provenance anchors verified; the central API claim, the Evidence-insufficient contract, the `ACCEPTED`/environment boundary, the permissions table and the item-4 reuse all verified sound. 4 MAJOR + 12 MINOR + 4 NOTE, all completeness/correctness defects in required contract lines and negatives. |
| 2 | this document | Folds in **all 20 findings**. MAJOR: **M1** — §3.2 replaces the five-item "readable subset" with a complete table of `_fresh` (F1–F9, F9 split into F9a–F9e) and corrects "two unreadable inputs" to five (F1, F2, F8, F9d, F9e); the readable parts of `_source_valid` (F9a–F9c) are folded into the candidate predicate and the rest is assigned to CR-1/CR-4. **M2** — §3.3 no longer claims both Rule-half refusals are reachable; the activation-Event branch is marked defensive/unreachable-through-the-API in §5.1 with the `authority.py:405-412`/`_change` argument. **M3** — §8 negative 3's PASS condition no longer requires precondition `details`; `state.py:437` raises with no kwargs, so `details == {}`. **M4** — §8 negative 4 drops the unimplementable "the UI never offers the action to an agent-key session" and asserts only what is checkable; actor-kind/scope visibility becomes **CR-7**. MINOR: **m1** "only write" qualified for `api_keys.last_used_at` (`dispatch.py:51`); **m2** the activation Event is readable via `status_event_id` and the claim corrected; **m3** the payload-write ordering restated (the pending spec mutates before `_commit`; only `_commit` persists); **m4** §5.1's title scoped and the out-of-slice `evidence.py:687` branch added; **m5** §5.2 names the raw `details` block as the guarantee for branch 5's `version` key; **m6** §0 "clean" → "no tracked modifications"; **m7** `core-api-v1.md:231`→`:230`; **m8** the item-4 boundary citation re-derived at the current tip to `P6-02-TASKSTEP-WRITE-COMPLIANCE.md:76-81` (the reviewer's `:62-67` was correct at `658072f`; the record's own line numbers moved in `1a79016` and again in `a270d5c`); **m9** CR-6 listed; **m10** the "code wins" attribution to taskbook:16 removed; **m11** §3.6 restated as the flat set; **m12** §8 negative 4(a) recorded as a Core-side, non-browser check. NOTE: **n1** the unsatisfiable `USER_CONFIRMATION`+`PASS` requirement recorded (§3.2, §5.1, §12 D7); **n2** the `cycle_cutoff` parenthetical corrected; **n3** CR-1 must satisfy `validate`'s in-transaction assert and needs a proxy pattern; **n4** §0 re-pinned to the current tip `a270d5c` with the citation argument re-derived. Nothing was disagreed with; no finding was silently kept. |

---

**Freeze errata (non-normative, applied at the freeze; the delta reviewer prescribed these exact
edits).** **D1** — the `_fresh` summary counts corrected to "**eight** of the **thirteen** parts are
browser-readable; the remainder is **five conditions** (F1, F2, F8, F9d, F9e) falling into **four**
coordination-request families (CR-2, CR-3, CR-4)". **D2** — `actor_id` and `host_id` added to R-TE's
field list, which F9b/F9c read (`evidence.py:851`, `:854`). **D3** — the `status_event_id` citation
corrected from `authority.py:210-212` to `:213`. No contract line, display state, permission,
revision/replay rule, error branch or negative changed; the F-table and the candidate predicate were
already correct. The delta reviewer's NOTE **N1** (`_source_valid` compares the source Event's
`project_id` to the *owning task's* project, `evidence.py:848`, because the `evidence` table has no
`project_id` column) is recorded as an accepted conservative classification rather than rewritten.

## 0. Source and provenance

Every contract line below is derived from the committed code in this worktree at the SHA named here. A
line without a `source` is not frozen.

| | |
| --- | --- |
| Worktree | `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p6-01` |
| Branch | `p6-01-readonly-dashboard` |
| **HEAD derived from (rev 2)** | **`a270d5ca103ca176f06dd8ba13ef75999058a4c6`** ("P6-02 TaskStep write: correct the closure paragraph's failure classification") |
| Brief's named HEAD | `acd260b3e55aae9a16b7544103c3d86dd8773ad8` — an **ancestor**. `git diff --name-only acd260b…a270d5c` returns exactly `web/P6-02-COMPLIANCE.md`, `web/P6-02-TASKSTEP-WRITE-COMPLIANCE.md`, `web/P6-02B-TASK-STEP-WRITE-SPEC.md`, `web/P6-REMAINING-TASKS.md`, `web/README.md`. **No `src/`, `web/src/`, `web/server/` or `tests/` path changed**, so every code citation below is valid at every SHA from `acd260b` to this tip. (Recorded because the tip moved four times while this proposal was written — `acd260b` → `5d085de` → `658072f` → `1a79016` → `a270d5c`, all doc-only.) |
| Schema | `SCHEMA_VERSION = 8` (`src/jasmine_core/__init__.py:15`) |
| Item-4 contract (model, frozen, untouched) | `web/P6-02B-TASK-STEP-WRITE-SPEC.md` rev 3 |
| Item-4 record | `web/P6-02-TASKSTEP-WRITE-COMPLIANCE.md` (independent validation PASS 9/9); its out-of-band boundary is at `:76-81` at this tip (rev 1 cited `:57-62`; m8 — the record's own line numbers moved twice after the review, from the reviewer's `:62-67` at `658072f` to `:72-77` at `1a79016` and `:76-81` at `a270d5c`) |
| Working tree | **no tracked modifications**; the only `git status --short` entry is this untracked proposal (m6) |
| Method | static reading only. No server was started, no database touched, no browser opened, no network used. |

### Provenance table — every frozen line and the code it comes from

| # | Contract line | Source |
| --- | --- | --- |
| P1 | Step → `VERIFIED`/`ACCEPTED` is Evidence-gated; the target status is compared against `STEP_EDGES` | `src/jasmine_core/state.py:331-332`, `341-351`; edges `state.py:22-32` |
| P2 | Task accept is **always** Evidence-gated (no fresh-200 path) | `state.py:441-448` |
| P3 | A Step with no requirements cannot reach `VERIFIED`/`ACCEPTED`; a Task with none cannot be accepted | `state.py:343-344`, `439-440` |
| P4 | Task accept preconditions: ACTIVE Task, ≥1 `ACCEPTED` Step, all others `ACCEPTED`/`SKIPPED` | `state.py:432-437`; ADR 0006:21 |
| P5 | The validator is `CurrentEvidenceValidator`, injected at Core construction | `src/jasmine_core/api/handlers.py:40-41`; `evidence.py:830-833` |
| P6 | The validator returns `evidence_ids` + `rule_versions`, frozen into the Event/response | `state.py:216-228`, `348-351`, `444-447`; ADR 0006:19 |
| P7 | `422 missing_evidence` is `MissingEvidence`, status 422 | `src/jasmine_core/errors.py:138-140` |
| P8 | Freshness = stored-snapshot identity + fingerprint `SAME` + revision binding + cycle cutoff + reference status + producer mapping + source revalidation (the full list is §3.2's F1–F9) | `evidence.py:914-947` |
| P9 | Requirement matching = `kind`, `required_result`, optional `tool_name`/`command_sha256`/`artifact_sha256`; rule-scoped confirmations bind `confirmed_rule_id`/`version` | `evidence.py:949-958` |
| P10 | Applicable rules = ACTIVE and (current version `kind='ACCEPTANCE'` or `enforcement='VERIFY'`), global/project/task scoped, with a structured mapping and an activation Event | `evidence.py:960-988` |
| P11 | `satisfy` reports `requirement_key` (+ `rule_id`/`rule_version` when rule-scoped) | `evidence.py:1028-1041` |
| P12 | `ACCEPTED` needs a current human `USER_CONFIRMATION` | `evidence.py:1048-1053`; ADR 0006:19 |
| P13 | Rollback on any failure: Event and projection both vanish | `src/jasmine_core/db.py:88-98`; ADR 0006:7; ADR 0002 §2.1 lines 27, 31, §2.2 lines 36-38 |
| P14 | Revision is read from the Step for route 3 and the Task for route 6; route 3 bumps both | `state.py:152-161`, `328`, `428`, `354-356`, `450`; ADR 0006:9 |
| P15 | Route scopes and human/system gates | `handlers.py:115-124`; `src/jasmine_core/api/dispatch.py:45-51`; `src/jasmine_core/auth.py:34-39`; `state.py:163-166`, `333-336`, `429-431` |
| P16 | An agent key cannot be issued `state:accept` | `auth.py:63-64`; `auth.py:20-23` |
| P17 | `evidence:read` is the Task-Evidence read scope; `state:read` the Step/history scope; `authority:read` the Rule scope | `handlers.py:111-112`, `116-117`, `121-122`, `100-101`; `objects:read` for `GET /v1/tasks/{tsk}` at `handlers.py:69-72` |
| P18 | Audit row per request, including refusals; 422 is audited `decision=allow` | `dispatch.py:90-108`, `77` |
| P19 | Proxy allowlist: seven write patterns always-on; `evidence/confirm`, `workspaces/fingerprint`, `workspaces/compare`, `tool-results`, `resolve/{int}`, `manual-reapply`, `/v1/auth/*` and a POST to `/v1/events` absent | `web/server/core-paths.json:1-2`, `20`, `34-50`; `tests/p6/test_dashboard_proxy_contract.py:143-167`, `248-273` |
| P20 | Existing read-side matrix is labelled UI matching, never a verdict | `web/src/views/TaskEvidence.tsx:244-300` (`matches` at `:89-100`) |
| P21 | Existing 422 rendering is BLOCKED with Core's code/message/details | `web/src/components/WritePanel.tsx:258-273` |
| P22 | The write hook's error classification: `missing_evidence` (kind `invalid`) → `refused` | `web/src/app/useWriteAction.ts:102-110`; `web/src/api/errors.ts:52-64` |
| P23 | ADR 0006 criteria structure, eight kinds, `required_result`, optional exact-match fields, nonempty-criteria gate | `docs/adr/0006-task-step-state-and-evidence-boundaries.md:17`; `state.py:41-69` |
| P24 | ADR 0006: validator runs **inside** the state transaction before Event append | `docs/adr/0006-…:19`; `evidence.py:991-992` |
| P25 | Replay returns before the revision and Evidence checks | `state.py:293-294`, `326-327`, `426-427`; ADR 0006:7 |
| P26 | The dashboard never learns its own actor kind or granted scopes (rev 2, M4/CR-7) | `handlers.py:91-95`; `web/src/app/session.tsx:16-27`; `core-paths.json:1-2` |
| P27 | The item-4 out-of-band Evidence boundary | `web/P6-02-TASKSTEP-WRITE-COMPLIANCE.md:76-81` |
| P28 | A refused request also touches `api_keys.last_used_at` (rev 2, m1) | `dispatch.py:51`; `auth.py:105-118` |

---

## 1. Scope and non-goals

**In scope of this proposal:** the display contract of the VERIFIED and ACCEPTED forms (§3), the
submission contract they reuse from the frozen TaskStep spec (§4), the Evidence-insufficient refusal
and its falsifiable "state unchanged" check (§5), permissions (§6), audit/readback (§7), the acceptance
plan (§8), status honesty (§9), and a named list of what is deliberately not decided (§11).

**Implemented by this proposal: nothing.** It is a proposal for a review and a later Gate.

**Out of scope and named:**

* `POST /v1/evidence/confirm` — the route exists (`handlers.py:110`; handler `handlers.py:334-339`;
  scope `evidence:confirm`; human-only at `handlers.py:336`) but is **deliberately proxy-refused**
  (`core-paths.json:50`; absence asserted at `tests/p6/test_dashboard_proxy_contract.py:252`, `268`).
  ADR 0007:13 fixes the rule a browser cannot satisfy: "*`ACCEPTED` also needs an independent current
  human `USER_CONFIRMATION` …, bound to the exact Step or Task revision and fingerprint. … No
  natural-language keyword extraction is claimed.*" The taskbook reserves the decision to the Core
  owner (`…P6-阶段任务书.md:96`). **This proposal does not decide whether a web button may ever mint a
  confirmation.**
* `POST /v1/workspaces/fingerprint` — proxy-refused and able to move VERIFIED Steps to `STALE`
  (`evidence.py:326-397`; core-paths `write_note` at `core-paths.json:50`).
* `POST /v1/workspaces/compare` — a real Core route with scope `fingerprint:read`
  (`handlers.py:114`; `docs/api/core-api-v1.md:230`) that is in **neither** proxy list, so the browser
  cannot call it (see §3.4).
* `POST /v1/tool-results`, `POST /v1/codex-exec-observations`, `POST /v1/resolve/{int}`,
  `POST /v1/resolutions/{res}/manual-reapply`, `POST /v1/events`, `POST /v1/auth/*` — unchanged, still
  refused (`core-paths.json:20`, `37-49`).
* Any change to Core, schema, scopes, id prefixes or Event types. If the implementation appears to need
  one, §11 records it as a **coordination request to the Core interface owner**, never a change this
  slice may make.

**Not changed by this slice:** no new route, no new scope, no new table, no migration, no new id
prefix, no new Event type. Every read route named in §3 and every write route named in §4 already
exists (`handlers.py:99-124`; `auth.py:20-23`; `models.py:33-59`; `ids.py:27`).

---

## 2. What item 4 already delivered, and what item 5 adds

The seven write routes, their bodies, revision rules, replay semantics, permissions, Evidence
thresholds, audit expectations and error vocabulary are **frozen** in
`web/P6-02B-TASK-STEP-WRITE-SPEC.md` rev 3 and implemented (`607f511`, fixed at `acd260b`), with an
independent validation PASS 9/9 (`web/P6-02-TASKSTEP-WRITE-COMPLIANCE.md`). This proposal **cites**
that document rather than restating it:

* route table, allowed/required body fields, 200/201 status split, response key sets — spec §2;
* unknown-field refusal and body shapes — spec §3;
* which revision each route reads and bumps — spec §4;
* `event_id` replay, `replayed` discriminator, timeout-is-unknown — spec §5;
* scope and actor-kind gates — spec §6;
* Evidence thresholds and the `422 missing_evidence` vocabulary — spec §7;
* command Events and route-specific audit `target_id` — spec §8;
* error vocabulary and precedence — spec §9;
* the always-on proxy allowlist — spec §10.

**What item 5 adds is not a route.** It is (a) the per-requirement matrix the two forms must show, and
(b) the precise, falsifiable contract of the refusal the forms must render when the matrix is
insufficient. Both are properties of the *browser's claims*, not of Core.

---

## 3. The criteria↔Evidence matrix contract

### 3.1 Which read route supplies what

All paths below are already in the baseline proxy allowlist (`core-paths.json:6-32`; exact-list
assertion `tests/p6/test_dashboard_proxy_contract.py:128-141`) and served by this baseline Core
(`handlers.py:99-124`). Shorthand used throughout: **R-T** task, **R-S** step, **R-SL** step list,
**R-TH/R-SH** history, **R-TE** task evidence list, **R-E** one Evidence, **R-EV** one Event,
**R-RA** active rules, **R-R** one rule.

| Read | Route | Scope | Supplies |
| --- | --- | --- | --- |
| R-T | `GET /v1/tasks/{tsk}` | `objects:read` (`handlers.py:71`) | `acceptance_criteria` (parsed `requirements`), `status`, `revision` (`objects.py:57-66`) |
| R-S | `GET /v1/steps/{stp}` | `state:read` (`handlers.py:121`) | same fields for the Step (`state.py:127-133`) |
| R-SL | `GET /v1/tasks/{tsk}/steps` | `state:read` (`handlers.py:116`) | every Step row, for the Task-level view and route-6 precondition display |
| R-TH / R-SH | `GET /v1/tasks/{tsk}/history`, `GET /v1/steps/{stp}/history` | `state:read` (`handlers.py:117`, `122`) | the eight command Event types with `seq`, `event_type`, `payload` (incl. `evidence_ids`/`rule_versions`), for cycle cutoffs and for Core's **recorded** verdicts |
| R-TE | `GET /v1/tasks/{tsk}/evidence` | `evidence:read` (`handlers.py:112`) | every `evd_` row for the task: `kind`, `status`, `step_id`, `actor_id`, `host_id`, `task_revision`, `step_revision`, `fingerprint_sha256`, `reference_status`, `confirmed_rule_id`/`version`, `tool_name`, `command_sha256`, `artifact_sha256`, `source_event_id`, `change_event_id`, `related_posttool_event_id`, `created_at`, `result` (`evidence.py:426-433`, `452-456`; column set `m0005_evidence_fingerprint.py:21-53`, `m0006_codex_exec_observations.py:11`) |
| R-E | `GET /v1/evidence/{evd}` | `evidence:read` (`handlers.py:111`) | one row, `reference_status` recomputed on read (`evidence.py:435-447`) |
| R-EV | `GET /v1/events/{evt}` | `events:read` (`handlers.py:99`) | the source/prompt/change Event with `actor_id`, `actor_kind`, `event_type`, `seq`, `task_id`, `project_id`, `payload` (`events.py:51-70`) |
| R-RA | `GET /v1/rules/active?project_id=&task_id=` | `authority:read` (`handlers.py:100`) | each ACTIVE rule's `version`, `revision`, `status`, `kind`, `enforcement`, `verification_requirements`, `status_event_id` (`authority.py:216-235`, `174-214`) |
| R-R | `GET /v1/rules/{rul}` | `authority:read` (`handlers.py:101`) | one rule version, same fields |

**Scope consequence the form must render honestly.** The matrix spans four scopes —
`objects:read` (R-T), `state:read` (R-S/R-TH/R-SH), `evidence:read` (R-TE/R-E), `authority:read`
(R-RA) — while the ACCEPTED submissions need `state:accept` (route 6 and route 3→ACCEPTED) and the
VERIFIED submission needs `state:write`. A key that can submit may therefore be **unable to read** part
of the matrix; the form renders the per-panel `403 forbidden_scope` (`dispatch.py:47-48`;
`auth.py:34-39`) and must not fall back to a different credential or to an empty/zero panel
(`P6-02-COMPLIANCE.md:213`; `WritePanel.tsx:275-291`).

### 3.2 The per-requirement display states

The form shows one row per `requirements[]` entry of the object it is about (each entry has a unique
`key`, a `kind` from the eight of ADR 0006:17, a `required_result`, and optional exact-match fields —
`state.py:41-69`). For each row it may compute exactly the readable predicates below. Shorthand:
`REQ` = the requirement; `rows` = the `evd_` rows R-TE returned for this task (filtered to the Step when
the form is a Step form, `step_id == step`).

**Every `_fresh` condition, classified (rev 2, M1).** `_fresh` (`evidence.py:914-947`) is a conjunction;
a row is a candidate only if it passes all of its parts. **Eight** of the **thirteen** parts are
browser-readable; the remainder is **five conditions** (F1, F2, F8, F9d, F9e) falling into **four**
coordination-request families (CR-2, CR-3, CR-4). (Rev 1 called these "the two unreadable inputs" and
omitted the stored-row check and all of `_source_valid`; that was wrong.)

| # | `_fresh` condition | Code | Readable in the browser? |
| --- | --- | --- | --- |
| F1 | a stored `workspace_fingerprints` row exists for `row.fingerprint_sha256` **and** its `project_id` is this project | `evidence.py:917-920` | **No.** No route exposes `workspace_fingerprints`; only the Evidence row's digest is readable, not the stored row. **CR-2** |
| F2 | that stored snapshot compares `SAME` with the **current** server fingerprint | `:921-922` | **No.** The current fingerprint and its completeness are not readable at all. **CR-2** |
| F3 | `row.task_revision <= current task revision`; for a Task form (`step_id is None`) `<= expected_revision` | `:923-924` | **Yes** — R-T / R-S |
| F4 | Step form: `row.step_id == step_id` **and** `row.step_revision <= expected_revision` | `:925-927` | **Yes** — R-TE / R-S |
| F5 | `seq(row.change_event_id) > cycle_cutoff(row)` | `:928-931`; cutoff `:1003-1021` | **Yes** — the `seq` via R-EV (`events.py:53`), the cutoff from R-TH/R-SH. The cutoff's owner is `step_id or row["step_id"]`: a Step form (or a Step-scoped row on a Task form) uses that Step's own `step.criteria_updated`/`→IN_PROGRESS` cycle; only a Task-scoped row on a Task form uses the task-level query (`:1003-1021`) (rev 2, n2) |
| F6 | `USER_CONFIRMATION`: `row.step_revision == expected_revision` (Step) or `row.task_revision == expected_revision` (Task), exactly | `:932-936` | **Yes** — R-TE |
| F7 | `reference_status != "BROKEN_REFERENCE"` | `:937-938`; computed on every read at `:435-447` | **Yes** — R-E |
| F8 | for kinds other than `COMMAND_RESULT`/`USER_CONFIRMATION`: `row.producer_config_sha256` equals the current `_producer_kind(kind, tool_name, command_sha256)` | `:939-946`; mapping `:214-247` (reads `JASMINE_CORE_EVIDENCE_PRODUCERS`) | **No.** No route exposes the mapping. **CR-3** |
| F9a | `_source_valid`: the `source_event_id` Event exists and its `task_id`/`project_id` match the row | `:846-849` | **Yes** — R-EV + R-TE |
| F9b | `USER_CONFIRMATION` rows: that source Event is `user.prompt` with `actor_kind == "human"` and the row's `actor_id` | `:850-852` | **Yes** — R-EV + R-TE |
| F9c | tool rows: that source Event is `tool.result` with `actor_kind == "system"` and the row's `actor_id` | `:853-855` | **Yes** — R-EV + R-TE |
| F9d | the tool status Core re-derives from the source Event's payload equals the row's `status` | `:858-859`; `_tool_result` `:95-108` | **No** — re-deriving it in the browser would be the client computing Core's predicate, which §4.2 forbids. **CR-4** |
| F9e | native rows (`related_posttool_event_id != null`): the full prompt/Pre/Post chain and the `codex_jsonl`/`hook_trace_jsonl` reparse | `:856-857`, `:861-912` | **No.** **CR-4** |

So the **candidate predicate** is the conjunction of F3–F7 and F9a–F9c; the **unreadable remainder** is
F1, F2, F8, F9d and F9e — five conditions, not two. A row can pass everything in the table's "readable"
column and still fail `_fresh`.

| Display state | Exact readable predicate | Supplied by | What it does **not** mean |
| --- | --- | --- | --- |
| **Candidate match** (the form's "satisfied as far as the read surface shows") | `∃ row ∈ rows`: `row.kind == REQ.kind` **and** `row.status == REQ.required_result` **and** every optional field present on `REQ` (`tool_name`, `command_sha256`, `artifact_sha256`) equals the row's; **and**, for a requirement taken from a Rule, a `USER_CONFIRMATION` row binds `confirmed_rule_id`/`confirmed_rule_version` to that rule version (`evidence.py:956-957`); **and** the row passes F3–F7 **and F9a–F9c** | R-TE ± R-E and R-EV; predicate copied from `evidence.py:949-958`, the readable half of `:914-947`, and `:846-855` | Not "Core will accept". F1, F2, F8, F9d and F9e are unreadable (§3.4), so the honest label is *candidate*, and the verdict belongs to Core at submission. |
| **Missing** | `∄ row ∈ rows` with `row.kind == REQ.kind` and `row.status == REQ.required_result` (regardless of freshness) | R-TE | Not "Core will answer `requirement_key`": a row can *match* and still be stale, and Core reports the same `requirement_key` for both (`evidence.py:1037-1040`). The form must not claim which branch Core will take. |
| **Stale — proven by a readable fact** | A matching row exists but fails a **readable** `_fresh` condition: F3 (`:923-924`), F4 (`:925-927`), F5 (`:928-931`), F6 (`:932-936`), F7 (`:937-938`), or F9a–F9c (`:846-855`) | R-TE + R-S/R-T + R-TH/R-SH + R-EV | This is a *positive falsification*: the row cannot satisfy the gate. It is still not a statement about a different row, and the Task/Step revision that makes it stale is Core's re-read value, shown beside the operator's draft (spec §4). |
| **Stale — unprovable here** | A matching row passes every readable condition, but F1/F2/F8/F9d/F9e remain unknown | — | Not "fresh". The form must show the residual as an explicit "Core 未判定/无法在此判定" note, never a green claim. |
| **Wrong kind** | Rows exist for this task/Step whose `kind` differs from `REQ.kind`, or whose `status` differs from `REQ.required_result` | R-TE | Not a match, and never rendered as PASS. It exists so the operator can see *what* Evidence describes this work (`TaskEvidence.tsx:89-100` already matches only on equal `kind`/`status`). **`INFO` must never be shown as `PASS`** (taskbook §7 line 135; `StatusBadge` renders Core's own string, `TaskEvidence.tsx:204`). |

**A permanent "Missing" the code cannot avoid (rev 2, n1).** `validate_criteria` permits
`required_result: "PASS"` for a `USER_CONFIRMATION` requirement (`state.py:59`; ADR 0006:17), but
`record_confirmation` always inserts `status="INFO"` (`evidence.py:815-816`) and `_match` requires exact
status equality (`:951`). Such a requirement is therefore **unsatisfiable by the only route that mints a
confirmation**, and the matrix will show it as permanently missing. The form must not present it as an
operator error; it is a criteria-authoring hazard the matrix should name.

### 3.3 The applicable-Rule half of the matrix

Core's validator does **not** only check the object's own criteria: `_rules` enumerates every ACTIVE
Rule whose current version is `kind='ACCEPTANCE'` or `enforcement='VERIFY'` and whose scope is global,
this project, or this task, and then requires the rule's own structured requirements to be satisfied by
Evidence **newer than the Rule's activation Event** (`evidence.py:960-988`, `1044-1047`; ADR 0006:19;
`docs/api/core-api-v1.md:283`). Two refusals live in that enumeration, and their reachability differs
(rev 2, M2):

* an applicable ACTIVE `ACCEPTANCE`/`VERIFY` Rule with **no** `verification_requirements` mapping →
  `422 missing_evidence`, `details = {rule_id, version}` (`evidence.py:972-976`; API doc line 283) —
  **reachable**: `verification_requirements` is optional at propose and supersede
  (`authority.py:266-269`, `345-349`);
* an ACTIVE Rule with no activation Event → `422`, `details = {rule_id}` (`evidence.py:983-985`) —
  **defensive, not reachable through the API**: `AuthorityStore.transition` always inserts the matching
  `rule_changes` row carrying the new status and the command `event_id` (`authority.py:405-412`;
  `_change` at `:321-324`), and no migration seeds a rule (`m0003_authority.py` creates tables only), so
  the join at `evidence.py:977-982` always finds an Event. It is listed in §5.1 with that note.

The form may list the applicable rules and their `verification_requirements` from R-RA/R-R, and may
**copy** the predicate (`kind='ACCEPTANCE' OR enforcement='VERIFY'`, scope match) to filter that list.
It must say that this is a copy of Core's WHERE clause, not Core's answer: R-RA returns *all* ACTIVE
rules (`authority.py:229-234`). The activation Event **is** readable — R-RA/R-R expose
`status_event_id`, which for an ACTIVE rule is the `rule_changes.change_event_id` at the rule's current
revision (`authority.py:189-196`, `:213`), and R-EV returns its `seq` (rev 2, m2; rev 1 wrongly
claimed no read route exposed it). What is *not* readable is whether that mapping's requirements are
satisfiable — the mapping's satisfiability is exactly what CR-1 would return.

### 3.4 What is unknowable from the API today — coordination requests, not inventions

These are the inputs `_fresh` uses that **no read route exposes**. They are recorded as requests to the
Core interface owner (taskbook §3 line 59 requires missing reads to go through a separate API PR; this
slice may not add one):

* **CR-1 — the satisfaction matrix itself.** `CurrentEvidenceValidator.validate` runs only inside a
  state write: `validate` even asserts `self.conn.in_transaction` (`evidence.py:991-992`), and both
  call sites are inside `db.transaction` (`state.py:345`, `441`). ADR 0006:19 puts the evaluation
  *inside* the state transaction precisely so the result can be frozen into the Event. **So ADR 0006
  does still leave the criteria↔Evidence satisfaction matrix as a Core-side, in-transaction
  derivation: the API cannot return it before a submission.** Requested: a read-only route that runs
  the same validator and returns per-requirement status plus `evidence_ids`/`rule_versions` **without
  appending an Event, without minting Evidence, and without moving any Step to `STALE`** (e.g.
  `GET /v1/tasks/{tsk}/evidence-matrix?step_id=`, scope `evidence:read`+`state:read`). Two constraints
  the route must satisfy (rev 2, n3): it must either run inside a transaction that rolls back so that
  `validate`'s `conn.in_transaction` assert holds (`evidence.py:991-992`), or the Core owner must
  refactor that assert; and it needs a **new proxy allowlist pattern** (`core-paths.json:6-32`,
  `37-49`) before the browser can reach it, exactly as §10 of the frozen TaskStep spec describes.
* **CR-2 — the current workspace fingerprint and the stored snapshot's identity.** F1 and F2: the
  stored-snapshot row and its `project_id` (`evidence.py:917-920`) and the current fingerprint's
  `SAME`/completeness (`:921-922`, `:1001-1002`) are unreachable. The only fingerprint routes are
  `POST /v1/workspaces/fingerprint` (mutating, proxy-refused) and `POST /v1/workspaces/compare`
  (`fingerprint:read`, `handlers.py:114`), which is also proxy-refused (absent from
  `core-paths.json:37-49`; the `write_paths` exact-list assertion
  `tests/p6/test_dashboard_proxy_contract.py:143-167` pins the eleven patterns and nothing else, and
  the five out-of-scope POSTs are additionally asserted closed at `:248-273`) and needs two *already
  known* digests, so even opening it would not supply the current one. Requested: a read-only
  current-fingerprint read (digest + `complete`) under the existing `fingerprint:read`, or a
  per-Evidence read-time `freshness`/`fingerprint_current: bool` computed by the same validator. It
  must not perform stale writes (the API doc's own rule, `docs/api/core-api-v1.md:276`).
* **CR-3 — the producer-configuration mapping (F8).** `_fresh` recomputes the expected producer config
  from `(kind, tool_name, command_sha256)` through `_producer_kind` (`evidence.py:939-946`,
  `214-247`), which reads the operator-owned `JASMINE_CORE_EVIDENCE_PRODUCERS` file. The browser has no
  route to it. Requested: expose the expected `producer_config_sha256` for a triple, or fold it into
  CR-2's boolean.
* **CR-4 — source revalidation (F9d, F9e).** `_source_valid`'s final condition re-derives the tool
  status from the source payload (`evidence.py:858-859`, `_tool_result` `:95-108`), and a native row
  additionally re-parses the stored chain (`:856-857`, `:861-912`). The readable parts (F9a–F9c) are in
  the candidate predicate; the rest is **not browser-verifiable** and must not be recomputed client-side
  (§4.2). Fold into CR-1/CR-2, or expose a per-Evidence `source_revalidated: bool`.
* **CR-5 — the legal-target sets.** No schema-8 route exposes `STEP_EDGES`/`TASK_EDGES`
  (`state.py:22-38`). This is the same coordination request already filed by the frozen TaskStep spec
  (§13, `P6-REMAINING-TASKS.md` §6) and this slice inherits it unchanged.
* **CR-6 — the `evidence/confirm` ADR question** (§11).
* **CR-7 — the caller's own actor kind and granted scopes (rev 2, M4).** No schema-8 route reports the
  authenticated key's actor kind or its scopes: `GET /v1/actors` lists *all* actors (`handlers.py:92`),
  the key routes need `admin` (`handlers.py:93-95`) and `/v1/auth/*` is outside the proxy allowlist
  (`core-paths.json:1-2`), and `web/src/app/session.tsx:16-27` records only the scopes the dashboard
  *asks* the operator to grant. Requested only if a future item needs the UI to know its own identity;
  **this slice does not**, and CR-7 must not be used to invent a UI actor-kind rule (§8.4).

### 3.5 What the form must never claim

Derived from the taskbook's own prohibition ("HTTP 200 或前端 optimistic update 不能制造验收",
`…P6-阶段任务书.md:95`) and from ADR 0006:19:

1. Never "satisfied", "通过", "可提交" or any sufficiency verdict — the strongest label is *candidate
   match, Core 未判定*, with the F1/F2/F8/F9d/F9e residual named.
2. Never "fingerprint current / 一致". It may show each Evidence row's stored `fingerprint_sha256`
   (`EvidenceView.tsx:83-85` already does) as **the digest recorded with that Evidence**, and must say
   the current fingerprint is not readable through this API.
3. Never "confirmation present" unless a `USER_CONFIRMATION` row is actually in R-TE, and then only as
   that row, with its `source_event_id` shown as the confirmation **source** Event (`user.prompt`,
   human actor — F9b) — never a synthesized confirmation.
4. Never promote `TaskEvidence.tsx`'s client-side reference matrix (`TaskEvidence.tsx:244-300`) into a
   permission to submit or a claim of sufficiency (item 4 already forbade this for its write panel,
   spec §7).
5. Never show `INFO` as `PASS`, and never show "0 requirements" as "nothing to do" — an empty
   requirements list is an explicit refusal path (§5).
6. Never show a success banner, status badge or revision from the request body: HTTP 200 on routes 3
   and 6 is also the fresh-write status, so `replayed` (spec §2, §5) plus a **re-read** are the only
   success evidence, and the readback must refresh the Task as well as the Step (spec §4, §8).
7. Never claim that Core refuses an agent credential on Step →`VERIFIED` (§6, §8.4, §12 D1).

### 3.6 Does ADR 0006 leave the matrix to the client?

**No.** ADR 0006:19 fixes the evaluation inside Core, in the state transaction, with the result frozen
into the transition Event, history and response — and the code matches (`state.py:345-352`, `441-448`;
`evidence.py:990-1056`). There is no read-only matrix route in the handler table (`handlers.py:99-124`)
or in the frozen API doc (`docs/api/core-api-v1.md` P1-02/P1-03 tables, lines 194-231). Therefore:

* Core's **recorded** verdicts are readable only *after* a gate has already run, and only as the flat
  `evidence_ids` + `rule_versions` set written onto `step.transitioned`/`task.accepted` (spec §8;
  `state.py:348-351`, `444-447`) — **not per requirement**; `TaskEvidence.tsx:55-87` already reads
  exactly that flat set (rev 2, m11). The per-requirement attribution CR-1 requests does not exist.
* Before a submission the UI may display **read facts** (criteria, Evidence rows, applicable rules and
  their requirements, command history) and a clearly-labelled **reference match** — nothing more.
* The authoritative pre-submission matrix requires **CR-1**. Until the Core interface owner answers,
  the form's honest pre-submission state is "Core 未判定", and the only way to obtain a verdict is to
  submit the transition and read Core's response.

---

## 4. The VERIFIED and ACCEPTED submission contract

### 4.1 Routes, bodies, scopes, revisions — reused, not restated

| Form | Route | Route scope | Body | Revision read | Bumps | Response |
| --- | --- | --- | --- | --- | --- | --- |
| Step → `VERIFIED` | `POST /v1/steps/{stp}/transition` (spec §2 route 3) | `state:write` (`handlers.py:123`) | `status="VERIFIED"`, `expected_revision`, `host_id`, `event_id`, optional `reason` (`handlers.py:521`) | the **Step's** (`state.py:328`) | Step +1, Task +1 (`:354-356`) | `step`, `task_revision`, `evidence_ids`, `rule_versions`, `event`, `replayed` (`:357-359`) |
| Step → `ACCEPTED` | same route, `status="ACCEPTED"` | `state:write` **plus** `state:accept` + human/system (`state.py:333-336`) | same | the **Step's** | Step +1, Task +1 | same |
| Task → `ACCEPTED` | `POST /v1/tasks/{tsk}/accept` (spec §2 route 6) | `state:accept` (`handlers.py:120`) | `expected_revision`, `host_id`, `event_id` **only** (`handlers.py:550`) | the **Task's** (`state.py:428`) | Task +1 (`:450`) | `task`, `evidence_ids`, `rule_versions`, `event`, `replayed` (`:452-453`) |

Everything else about these three submissions is the frozen TaskStep contract: response key sets and
the 200-vs-201/`replayed` rule (spec §2), `_state_body`'s unknown-field refusal (spec §3), the
`expected_revision` rule and the `409 revision_conflict` payload with `current_snapshot` (spec §4),
`event_id` replay with the identical semantic body (spec §5), the timeout-is-unknown rule and request-id
surfacing (spec §5), the command Event types `step.transitioned` / `task.accepted` (spec §8), and the
error vocabulary and precedence (spec §9).

**Two route-specific facts an ACCEPTED form must encode (both from spec §9, verified in code):**

* Route 6 **rejects `reason` and `status`**: they are not in its allowed set, so sending either is
  `400 invalid_request` (`handlers.py:550`, `461-466`). Route 3 accepts an optional `reason`.
* Route 6's precedence is: revision (409) → `can_accept` (403) → actor kind (403) → Task ACTIVE (409) →
  accepted-step precondition (409) → requirements non-empty (422 `{task_id}`) → validator (422). So a
  Task accept with no ACCEPTED Step answers **409**, not 422 (`state.py:428-442`). Route 3's order is:
  revision (409) → Task ACTIVE (409) → edge legality (409) → accept/role (403) → reason (400) →
  requirements non-empty (422 `{step_id}`) → validator (422) (`state.py:328-345`).

### 4.2 Core — never the form — decides legality and sufficiency

This is the taskbook's own rule (`…P6-阶段任务书.md:95`) and the frozen spec's §6 rule ("no local state
machine that decides a transition is legal"). Concretely the form:

* offers the action the operator asked for, and sends it; it does not disable a target as "illegal"
  (the edge set is not published — CR-5) and does not compute sufficiency (CR-1);
* renders Core's `409 invalid_state_transition` with `from_status`/`to_status` and Core's `422
  missing_evidence` with its message/details as **Core's** verdict, verbatim
  (`WritePanel.tsx:219-241`, `258-273`, `RefusalFields` at `:367-400`);
* never replaces Core's answer with the client-side reference match (§3.5 item 4);
* never recomputes F9d/F9e in the browser (§3.2, CR-4);
* reads back after success (spec §8) and shows `replayed` truthfully (spec §2).

### 4.3 The `USER_CONFIRMATION` binding and ordering — where the environment boundary bites

For an `ACCEPTED` target the validator requires a current human `USER_CONFIRMATION`
(`state.py:346` passes `require_user_confirmation = (target == "ACCEPTED")`; route 6 passes `True`;
`evidence.py:1048-1053`; ADR 0006:19). The binding is to the **exact revision**:

* a Step confirmation is fresh only when `row.step_revision == expected_revision` (`evidence.py:933-935`);
* a Task confirmation only when `row.task_revision == expected_revision` (`:935`).

And `POST /v1/evidence/confirm` itself requires, in order, a same-task human `user.prompt` Event by the
authenticated actor that is **newer than the current State revision** (`evidence.py:759-783`) and
`current_revision == expected_revision` (`:803-805`), then stores `step_revision`/`task_revision` from
the row it read (`:814-820`; `_insert` at `:494-495`).

Two consequences the form and the Gate must both honour:

1. **Ordering matters.** A Step confirmation recorded before the Step reached its final
   pre-ACCEPTED revision will not satisfy the ACCEPTED transition, and any Step write between the
   confirmation and the Task acceptance bumps the Task revision and invalidates a Task confirmation.
   The form should therefore show the confirmation row's `step_revision`/`task_revision` beside the
   revision it is about to submit, and say when they differ.
2. **The browser cannot create this environment.** `POST /v1/evidence/confirm` is proxy-refused
   (`core-paths.json:50`) and so is `POST /v1/events` — `^/v1/events$` is in the **read** list
   (`core-paths.json:20`, GET-only) and absent from `write_paths` (`:37-49`), so the browser cannot even
   mint the originating `user.prompt`. A fresh environment therefore **cannot** produce a passing
   ACCEPTED validation through the browser alone. This is exactly the boundary
   `P6-02-TASKSTEP-WRITE-COMPLIANCE.md:76-81` records, and it is why §8's ACCEPTED cases must be
   recorded **BLOCKED/PARTIAL** rather than PASS unless the environment was prepared out-of-band.

---

## 5. The Evidence-insufficient contract

### 5.1 `422 missing_evidence` — every branch these forms can reach, plus the out-of-slice ones

`422` is `errors.MissingEvidence` (`errors.py:138-140`). `CoreError.__init__` drops `None` details
(`errors.py:22`), so a detail key appears only when the branch supplies it. `details` is rendered as
Core's own JSON (`WritePanel.tsx:270`, `:288`).

| # | Cause | Message | `details` | Source |
| --- | --- | --- | --- | --- |
| 1 | Step has no requirements | "Step has no acceptance requirements" | `{step_id}` | `state.py:343-344` |
| 2 | Task has no requirements | "Task has no acceptance requirements" | `{task_id}` | `state.py:439-440` |
| 3 | A direct requirement is unsatisfied | "Evidence requirement is not satisfied" | `{requirement_key}` (rule `None`/`None` dropped) | `evidence.py:1037-1040` |
| 4 | A Rule requirement is unsatisfied | same | `{requirement_key, rule_id, rule_version}` | same (`:1028-1032`, `1046`) |
| 5 | Applicable ACTIVE `ACCEPTANCE`/`VERIFY` Rule has no mapping | "active VERIFY/ACCEPTANCE Rule has no structured Evidence requirements" | `{rule_id, version}` — note the key is **`version`**, not `rule_version` | `evidence.py:972-976`; reachable (`authority.py:266-269`, `345-349`) |
| 6 | ACTIVE Rule lacks an activation Event | "active Rule lacks activation Event" | `{rule_id}` | `evidence.py:983-985`; **defensive, not reachable through the API** (see notes; rev 2, M2) |
| 7 | `ACCEPTED` without a current human confirmation | "current human USER_CONFIRMATION is required" | **none** | `evidence.py:1048-1053` |
| 8 | Current fingerprint unavailable | "current workspace fingerprint is unavailable" | **none** | `evidence.py:999-1000` |
| 9 | Current fingerprint partial/unknown | "current workspace fingerprint is partial or unknown" | **none** | `evidence.py:1001-1002` |
| 10 | No matching current Evidence at all | "no matching current Evidence" | **none** | `evidence.py:1054-1055`; not reachable via routes 3/6 (see notes) |
| 11 | Validator returned a malformed result | "Evidence validator returned an invalid result" / "Evidence validator returned no valid Evidence IDs" / "Evidence validator returned invalid Rule versions" | **none** | `state.py:216-228`; defensive |
| 12 | No validator installed at all | "Evidence validation is unavailable; state remains unchanged" | `{target_status}` | `state.py:101-104`; not reachable in the real server |
| 13 | **Out of slice:** `POST /v1/codex-exec-observations` when the workspace moved since the Post | "workspace changed since PostToolUse" | **none** | `evidence.py:687`; route `handlers.py:109`, proxy-refused (`core-paths.json:37-49`) (rev 2, m4) |

**Reachability notes (stated, not assumed):**

* Branch 6 is **defensive, not reachable through the API** (rev 2, M2): every ACTIVE rule is written by
  `AuthorityStore.transition`, which always inserts the matching `rule_changes` row carrying the status
  and the command `event_id` (`authority.py:405-412`; `_change` `:321-324`), the `change_event_id` FK
  guarantees the Event exists, and no migration seeds a rule (`m0003_authority.py`). The join at
  `evidence.py:977-982` therefore always finds an Event, so `MAX(e.seq)` is non-null.
* Branch 10 is **not reachable through routes 3 and 6 as the code stands**: a non-empty criteria set
  with every requirement satisfied puts at least one id in `used` (`evidence.py:1028-1041`), and both
  callers refuse empty criteria first (branches 1/2). It is listed because it exists, and no negative
  may depend on it.
* Branch 12 is **not reachable in the real server**: `Core` injects `CurrentEvidenceValidator`
  (`handlers.py:40-41`). It is the fail-closed default only when `StateStore` is built without one
  (`state.py:117`).
* Branch 11 can only fire if a validator returns something `_validated` rejects; `CurrentEvidenceValidator`
  cannot (`evidence.py:1054-1056` returns a well-formed result or raises). Defensive, not expected.
* Branch 13 is **out of slice**: the route is proxy-refused and the forms do not call it. It is listed so
  the table is not read as exhaustive of the codebase (rev 2, m4).
* `FingerprintUnavailable` (`errors.py:143-144`, code `fingerprint_unavailable`) is **not observable**
  through these routes: `validate` catches it and re-raises plain `missing_evidence`
  (`evidence.py:997-1000`), and `_fresh` swallows it (`:939-944`). The form must render whatever `code`
  Core returns rather than hard-coding `missing_evidence` (`errors.ts` maps 422 to kind `invalid`,
  `useWriteAction.ts:108` maps that to `refused`), but no negative may require `fingerprint_unavailable`.
* The unsatisfiable `USER_CONFIRMATION`+`PASS` requirement (§3.2, n1) produces branch 3 (`details:
  {requirement_key}`) forever; it is not its own branch.
* The proposed item-5 read side (CR-1) would create new `422` sites only if it reused this vocabulary;
  that is a Core-owner decision, not this slice's.

### 5.2 What the form renders

The existing shared block is already the frozen rendering and is reused, not duplicated: a `refused`
state whose `code === 'missing_evidence'` is drawn as **BLOCKED** with Core's code, HTTP status,
message, `details` and the request id, and with the explicit sentence that no write occurred
(`WritePanel.tsx:258-273`; classification at `useWriteAction.ts:102-110`). The item-5 forms must:

* never render it as a failure and never as a success (taskbook §7 line 135: "UI 不提升
  PASS/VERIFIED/ACCEPTED");
* name the branch from `details` where Core supplied one (`requirement_key`, `rule_id`, `step_id`,
  `task_id`) and say "Core 未给出 requirement_key" where it did not — the confirmation and fingerprint
  branches carry none (§5.1 rows 7-9);
* **for branch 5's `version` key, the guarantee is the raw `details` JSON block**
  (`WritePanel.tsx:270`, `:288`), not the named-field aid: `RefusalFields`' key list contains
  `rule_version`, not `version` (`WritePanel.tsx:384-390`), so branch 5 currently renders only in the
  raw block (rev 2, m5). Adding `version` to `RefusalFields`' keys is the expected implementation
  change, but it is an implementation choice — the raw block is the contract-level guarantee;
* keep the operator's draft and the read basis, and offer the readback (spec §4);
* **not** auto-retry with a fresh `event_id`.

A `422` is **not** a `409`: `invalid_state_transition` and `revision_conflict` are different states
(`useWriteAction.ts:100-109`), and re-reading cannot make an unsatisfied criterion pass, which is why
`missing_evidence` is deliberately kept out of the conflict block (`WritePanel.tsx:253-257`).

### 5.3 "State unchanged" as a falsifiable third-party check

The property is a real property of the code, not an inference from the status code:

* both call sites run the validator **after** `_revision` and the state/role checks but **before**
  `_commit` (`state.py:345-352`, `441-448`);
* the `evidence_ids`/`rule_versions` payload assignments (`state.py:348-351`, `444-447`) mutate only the
  in-memory pending Event spec and execute **before** `_commit`; only `_commit` persists anything
  (`:352`, `:208-213`; rev 2, m3), and the projection `UPDATE`s at `:354-356`/`:450` follow it;
* the whole block is inside `db.transaction`, whose `except BaseException` issues `ROLLBACK` and
  re-raises (`db.py:88-98`) — ADR 0002 §2.1 lines 27/31 and §2.2 lines 36-38; ADR 0006:7 ("Any
  validation or commit failure leaves both unchanged").

So the check a **third party** can run, for a Step form (route 3):

1. **Before:** `GET /v1/steps/{stp}` and `GET /v1/tasks/{tsk}` → record `(status, revision)` for both;
   `GET /v1/steps/{stp}/history` → record the exact set of `event_id` values and the maximum `seq`.
2. **Submit** the transition (with the intended `event_id`).
3. **After:** the same three reads. Assert **all** of:
   * the Step's `(status, revision)` pair is **identical**;
   * the Task's `(status, revision)` pair is **identical** — a Step write bumps both (P14), so checking
     only the Step would miss a partial write;
   * the history `event_id` set and the maximum `seq` are **identical**, i.e. **no command Event was
     appended** — in particular no `step.transitioned` with `payload.to == target`;
   * the response is `422` with `error.code == "missing_evidence"`.
4. For the Task form (route 6): assert the Task's `(status, revision)` unchanged and **no
   `task.accepted`** Event in `GET /v1/tasks/{tsk}/history`.

**The check FAILS if the object changed.** This is the falsification direction and must be written into
the Gate as such: the assertion is a conjunction of equalities, so *any* differing revision, differing
status, new `event_id` or advanced `max(seq)` is a **FAIL** of the "state unchanged" claim — not a
warning, not a re-read. A Gate that reports the `422` and skips the equality half does not test this
contract at all.

**Independence and the audit half.** A browser cannot read `audit_log` (`/v1/audit` is deliberately
absent from the allowlist, `core-paths.json:2`; `tests/p6/test_dashboard_proxy_contract.py:196-204`), so
the strongest form of this check is the SQLite one the delivered validation used
(`P6-02-TASKSTEP-WRITE-COMPLIANCE.md:19,38`; `P6-02-COMPLIANCE.md:224-227`): count `events`,
read `tasks.revision`, `steps.revision`/`status`, and confirm one new `audit_log` row for the submitted
`X-Request-Id`. **A refused request is not the only row it touches:** `dispatch` calls
`core.auth.touch(principal.key_id)` after the scope check and before the handler (`dispatch.py:51`),
which best-effort `UPDATE`s `api_keys.last_used_at` (`auth.py:105-118`) — so a `422` writes
`api_keys` as well as `audit_log`, and a `403` writes only `audit_log` because the scope check raises
first (rev 2, m1). Keep the SQLite assertion to `events`, the Task/Step projections and `audit_log`,
not to an all-tables single-row-change claim. The audit row is consistent with "Truth unchanged":
`dispatch` records it in its own transaction after the handler raised (`dispatch.py:90-102`), `decision`
is `allow` because 422 is neither 401/403 nor ≥500 (`:77`), and `_target_of` is only consulted on the
success path (`:55`), so `target_id` is `null`. The form may not imply it can read that row.

---

## 6. Permissions and roles

| Action | Route scope (dispatch) | Additional in-store gate | Actor kinds allowed |
| --- | --- | --- | --- |
| Step → `VERIFIED` | `state:write` (`handlers.py:123`) | **none**: `state.py:333` gates only `SKIPPED`/`ACCEPTED` targets and a `SKIPPED` source | any key holding `state:write` — including an `agent` (§12 divergence D1) |
| Step → `ACCEPTED` | `state:write` | `can_accept` → `403 forbidden_scope` if the key lacks `state:accept` (`state.py:334-335`); then `_role` → `403 forbidden_actor_kind` (`:163-166`) | human/system |
| Task → `ACCEPTED` | `state:accept` (`handlers.py:120`) | handler passes `can_accept=True` (`:552`); `_role` is the remaining guard (`state.py:429-431`) | human/system |
| Read the matrix | `objects:read` + `state:read` + `evidence:read` + `authority:read` (§3.1) | none | any key holding them |

* Scopes are checked at dispatch, before the handler and before any table is touched
  (`dispatch.py:43-51`), with `403 forbidden_scope` carrying `{required, granted}`
  (`auth.py:34-39`; `errors.py:55-57`).
* **An agent credential must be refused on both ACCEPT paths, by two independent mechanisms:**
  1. **Issuance:** `auth.issue_key` refuses to mint `state:accept` for an `agent` actor
     (`auth.py:63-64`; `ACTOR_KINDS = ("human","agent","system")` at `registry.py:17`). An agent
     therefore *cannot hold* the scope route 6 requires.
  2. **Enforcement:** if an agent key somehow carried it, `_role` still refuses with
     `403 forbidden_actor_kind` (`state.py:164-166`). With only `state:write`, route 3→`ACCEPTED`
     fails at `can_accept` with `403 forbidden_scope` (`state.py:334-335`), proven for the sibling edge
     by `tests/test_state.py:154-157`.
* **The agent boundary on `VERIFIED` is different, and this proposal does not hide it.** →`VERIFIED`
  requires no `state:accept` and calls no `_role`; `EvidenceRequest.actor_kind` is passed
  (`state.py:346`) but `CurrentEvidenceValidator.validate` never reads it (`evidence.py:990-1056`). So
  an agent key holding `state:write` reaches the Evidence validator on →`VERIFIED`. `tests/test_state.py:110-112`
  shows exactly that: an agent token's →`VERIFIED` request gets `422` from the fail-closed validator,
  not a `403`. ADR 0006:13 says "An Agent cannot receive `state:accept` or self-accept" — which the code
  honours on every *accept* path — but the taskbook's broader sentence ("Agent credential … 也应被服务端
  拒绝") is **not** true of →`VERIFIED` in schema 8. This is recorded as divergence **D1** in §12 and as
  coordination request **CQ-1** in §11; the acceptance negative (§8.4) must assert only the code.
* **The dashboard cannot see its own actor kind or scopes (rev 2, M4/CR-7).** No schema-8 route reports
  them (`handlers.py:91-95`; `web/src/app/session.tsx:16-27`; `/v1/auth/*` outside the allowlist), so no
  UI obligation may be written in terms of "an agent-key session".
* **Actor identity is the token, never the body.** Routes 3 and 6 have no `actor_id` field; sending one
  is `400 invalid_request` via `_state_body` (spec §3).
* No scope escalation, no credential swap, no elevated retry on a refusal (`WritePanel.tsx:275-291`).

---

## 7. Audit, Events, and the readback rule

| Submission | Command Event | Identifiers in the response |
| --- | --- | --- |
| Step → `VERIFIED` / `ACCEPTED` | `step.transitioned` with `from`, `to`, `expected_revision`, `reason`, `evidence_ids`, `rule_versions` (`state.py:320-325`, `348-351`) | `step.step_id`, `task_revision`, `evidence_ids`, `rule_versions`, `event.event_id` |
| Task → `ACCEPTED` | `task.accepted` with `expected_revision`, `evidence_ids`, `rule_versions` (`state.py:421-425`, `444-447`) | `task.task_id`, `evidence_ids`, `rule_versions`, `event.event_id` |

* Both types are in the frozen enum (`models.py:39`, `46`, `50`) and in `StateStore.history`
  (`state.py:144-150`), so `GET /v1/tasks/{tsk}/history` and `GET /v1/steps/{stp}/history` are the
  correct readback. `evidence_ids`/`rule_versions` are the code location item 5's "规则版本" display
  **must** come from (ADR 0006:19; §3.6).
* **`rule_versions` is Core's list of applicable Authority Rule versions** in `rule_id` order
  (`evidence.py:1044-1047`), where each entry is exactly `{rule_id, version}` (validated by
  `state.py:222-227`). It is a **record of what was applied**, not a list of every ACTIVE rule — the
  form must not equate it with R-RA's list.
* **Audit:** one `audit_log` row per request, including refusals (`dispatch.py:90-108`), with
  `request_id`, method, path, `decision`, actor id, status, scope, `target_id`, `error_code`, `detail`.
  For these three routes a **success** records `target_id = event_id` (no top-level `task_id`/`step_id`
  and no nested `object` in the response body, so `_target_of` falls through to the nested event,
  `dispatch.py:115-128`; spec §8).
* **No optimistic write.** After a success the UI re-reads the object's own row **and** the history,
  renders Core's re-read values, and refreshes the Task as well as the Step (spec §8; `TaskWrites.tsx`
  `useAutoReread` at `:110-116`). A timeout remains `unknown` with the request id and a readback, never
  success and never failure (spec §5).

---

## 8. Acceptance plan

**This is a proposal for a Gate, not a Gate.** Positive path and evidence-bundle shape are the frozen
spec's §11; this section adds only what item 5 requires. The environment rule is absolute: **any case
whose environment cannot be created through the browser alone is recorded BLOCKED/PARTIAL — never PASS.**

**Positive path (only where the environment permits).** If the isolated Core has a complete current
fingerprint and satisfying Evidence (and, for ACCEPTED, an out-of-band confirmation at the exact
revision, §4.3), record: the `200`, `replayed:false`, `evidence_ids`/`rule_versions` verbatim, the
command Event, the audit row with `target_id = evt_…`, and the re-read Step/Task rows. **If it does not,
record the ACCEPTED cases as BLOCKED and the VERIFIED case as BLOCKED when no satisfying Evidence
exists**, with the exact `422` message/details, the fingerprint state, and the proof that no write
occurred. A Gate that reports these as failures, or omits them, misstates the slice
(`P6-02-TASKSTEP-WRITE-COMPLIANCE.md:76-81`).

**The required negatives.** "PASS requires" is the exact observation that makes the claim true;
"counterexample" is what falsifies it.

1. **Criteria satisfied but the human confirmation missing** (ACCEPTED Step, and separately Task accept).
   * *PASS requires:* `422 missing_evidence` whose `message` is
     `"current human USER_CONFIRMATION is required"` **and whose `details` are empty**, plus §5.3's
     unchanged-state equality for Step and Task and no Event. The branch is only provable when a
     satisfying Evidence set exists, because `satisfy(criteria)` and `satisfy(rule criteria)` run
     **before** the confirmation check (`evidence.py:1043-1053`); without satisfying Evidence the `422`
     comes from the `requirement_key` branch instead.
   * *BLOCKED, not FAIL, when* no satisfying Evidence exists in the environment (then assert the
     `requirement_key`/rule branch instead and say so).
   * *Counterexample:* a `200` (confirmation was not actually required); a `422` whose message or
     `details` name a `requirement_key`/`rule_id` (wrong branch); any revision, status or Event change.
2. **No requirements at all.** A Step and a Task carrying `{"requirements": []}`.
   * *PASS requires:* `422` with `details == {"step_id": …}` for the Step (`state.py:343-344`) and
     `{"task_id": …}` for the Task (`state.py:439-440`), plus unchanged state and no Event.
   * *Counterexample:* `409 invalid_state_transition` (the object was not in the state the route
     requires, so the negative never reached the branch); `details` naming a `requirement_key`; any
     state change. For the Task case, an environment with no ACCEPTED Step answers `409` first
     (`state.py:434-437`) — record **BLOCKED** and create the precondition, or the negative is not run.
3. **No ACCEPTED Step for route 6.**
   * *PASS requires:* `409 invalid_state_transition` whose message is exactly
     `"Task needs an accepted Step and no unfinished Steps"` **and whose `details` are empty**
     (`state.py:437` raises with no keyword arguments; `errors.py:19-22` drops only `None`), plus the
     Task's `(status, revision)` unchanged and no `task.accepted` Event. **Do not require precondition
     fields in `details`** — Core sends none, and `tests/test_state.py:151` confirms the bare
     `409 invalid_state_transition` (rev 2, M3; contrast negative 2, whose `{step_id}`/`{task_id}`
     details are real).
   * *Counterexample:* a `422` (the precondition is no longer checked before the criteria/validator) or
     a `200`; or a `409` with a different message.
4. **An agent key.** Three parts, and **no claim beyond the code**:
   * (a) **Core-side, not browser-drivable (rev 2, M4/m12):** issuing `state:accept` for an `agent`
     actor → `403 forbidden_actor_kind` (`auth.py:63-64`). This needs `POST /v1/auth/keys` with the
     `admin` scope (`handlers.py:93`), which is deliberately absent from the proxy allowlist
     (`core-paths.json:1-2`, `37-49`); a browser attempt is a proxy `404 proxy_path_not_allowed` and
     **must not** be recorded as the assertion. Run it directly against Core.
   * (b) **Browser-drivable:** an agent key holding `state:write` submitting route 6 → `403
     forbidden_scope` at dispatch (`handlers.py:120`); submitting route 3→`ACCEPTED` → `403
     forbidden_scope` (`state.py:334-335`); submitting route 7/5 (criteria) → `403 forbidden_scope` at
     dispatch (`handlers.py:119`, `124`). Assert no `step.transitioned`/`task.accepted` Event and
     unchanged state in each case.
   * (c) **`→VERIFIED`, stated as what it is (rev 2, M4):** assert only that **the form never claims a
     server-side actor-kind refusal on →`VERIFIED`**, and that Core decides the request through the
     Evidence validator (an agent key holding `state:write` reaches it and gets `422`, not `403` —
     §6, `tests/test_state.py:110-112`). **Do not** assert that the UI hides or withholds the action
     from an agent-key session: the dashboard cannot know its own actor kind or scopes (P26/CR-7), and
     any key holding `state:write` legitimately reaches that validator.
   * *PASS requires:* the exact codes above and the absence of any write.
   * *Counterexample:* a `200`, or an accept-path request reaching the validator; a `403` claimed on
     →`VERIFIED`.
5. **A stale `expected_revision`.**
   * *PASS requires:* `409 revision_conflict` with `details` `{expected_revision, current_revision,
     current_snapshot, kind}` (`state.py:158-161`), the draft retained, Core's current
     value/snapshot displayed, and **no second POST** issued by the panel (`useWriteAction.ts:24-33`;
     spec §11 negative 1). On a →`VERIFIED` submission the stale revision must answer **409, not 422**,
     because `_revision` runs before the validator (`state.py:328` vs `:341`).
   * *Counterexample:* a `422` (check ordering changed) or an automatic retry with a new revision.
6. **The Evidence-insufficient case proves state unchanged and no Event.**
   * *PASS requires:* all four equalities of §5.3 (Step and Task `(status, revision)`, history
     `event_id` set, `max(seq)`) plus `422 missing_evidence`, and — for the SQLite half — a `COUNT(*)`
     on `events` identical before and after with exactly one new `audit_log` row for the submitted
     request id (and the `api_keys.last_used_at` touch acknowledged per m1, not counted as Truth).
   * *Counterexample (this is the check's whole point):* **any** difference in revision, status, the
     Event set or `max(seq)` — including a `422` that nevertheless wrote. A Gate that reports the `422`
     without the equalities has not tested the contract.
7. **An applicable ACTIVE `ACCEPTANCE`/`VERIFY` Rule with no `verification_requirements`.** (Reachable
   and distinct — `evidence.py:972-976`; the API doc states the same, `docs/api/core-api-v1.md:283`.)
   * *PASS requires:* `422` with `details == {rule_id, version}` (note the key name; the raw `details`
     block is where it renders, §5.2), unchanged state, no Event. This is the one branch that can make
     an otherwise-satisfied submission fail, so it is the proof that the Rule half of the matrix is
     really checked.
   * *Counterexample:* a `200`, or a `422` blaming a `requirement_key`.
   * **Not a required negative:** branch 6 (no activation Event) — unreachable (§5.1, M2).
8. **`INFO` is never rendered as `PASS`** (taskbook §7 line 135; §3.5 item 5).
   * *PASS requires:* a `USER_CONFIRMATION`/tool row with `status: "INFO"` is shown as `INFO`, and it
     satisfies only a requirement whose `required_result` is `INFO` (`state.py:59`; `evidence.py:951`).
   * *Counterexample:* an `INFO` row rendered green/PASS, or used to satisfy a `PASS` requirement.
   * *Related (n1):* a `USER_CONFIRMATION` requirement authored with `required_result: "PASS"` can
     never be satisfied (`evidence.py:815-816` vs `:951`); the form must show it as a missing item and
     must not present it as an operator mistake.
9. **The out-of-scope Evidence routes stay closed and the confirm control stays absent.**
   * *PASS requires:* from the served dashboard, `POST /v1/evidence/confirm`,
     `POST /v1/workspaces/fingerprint`, `POST /v1/workspaces/compare`, `POST /v1/tool-results`,
     `POST /v1/events` and `POST /v1/auth/keys` each answered by the proxy with `404
     proxy_path_not_allowed` (`refuse()` at `core-proxy.ts:158-172`; the refusal call at `:189-196`),
     never reaching Core; and no confirm control anywhere in the item-5 forms
     (`TaskEvidence.tsx:302-309` already renders the absence).
   * *Counterexample:* any of those POSTs reaching Core, or any UI affordance that could mint a
     confirmation.

**Evidence pack and naming.** Follow the frozen spec's §11 (files, redaction rules, bundle shape) and
its **M6 naming rule**: the token `p6-02b` is reserved for milestone B and must never label this
slice. This proposal suggests the namespace `<stamp>-p6-02-verified-accepted/` with status label
`P6-02 VERIFIED/ACCEPTED forms`; the final token is the parent's/validator's to assign, not this
document's.

---

## 9. Status honesty

* This document is a **proposal at rev 2, awaiting delta re-review**. No screen, matrix, route or Gate
  exists because of it. `P6-02`'s item 5 remains **N** (not implemented) in `P6-REMAINING-TASKS.md:59`.
* **This slice cannot be called P6 or V1 complete.** `P6-02` stays **PARTIAL**, `P6` is not complete,
  and nothing here releases V1 (`P6-02-COMPLIANCE.md:374`; `P6-REMAINING-TASKS.md:65-69`).
* **`evidence/confirm` is out of scope** pending the Core owner's ADR/API decision (ADR 0007:13;
  taskbook §5 item 6, line 96). The forms must not offer it and must not fabricate a `user.prompt`.
* **Every case whose environment cannot be created through the browser alone is BLOCKED/PARTIAL, never
  PASS.** Concretely: any ACCEPTED submission (Step or Task) needs a `USER_CONFIRMATION` at the exact
  revision, and the two routes that could produce one (`POST /v1/evidence/confirm`, `POST /v1/events`
  for the origin `user.prompt`) are both unreachable from the browser (`core-paths.json:20`, `37-49`,
  `50`); any positive VERIFIED submission needs a complete current fingerprint mintable only by
  `POST /v1/workspaces/fingerprint` (also refused). The delivered validation had to prepare those
  out-of-band and said so (`P6-02-TASKSTEP-WRITE-COMPLIANCE.md:76-81`).
* **Missing precondition ⇒ BLOCKED, never PASS and never FAIL** (the spec's §11 M4 rule, inherited).
  In particular: negative 1 without satisfying Evidence, negative 2's Task case without an ACCEPTED
  Step, and any positive path without a passing validator.
* No behaviour is claimed here that was not read in code. No server was contacted.

---

## 10. Frozen once reviewed, and left to implementation

**This proposal asks a reviewer to freeze (on a delta re-review of rev 2):** the read routes that feed
the matrix and the four scopes they need (§3.1); the complete `_fresh` classification F1–F9 and the
per-requirement display states built on it (§3.2); the Rule half, its one reachable and one defensive
refusal, and the readable activation Event (§3.3); the "unknowable here" list and coordination requests
CR-1…CR-7 (§3.4); the never-claim list (§3.5); the route/scope/body/revision reuse for the three
submissions (§4.1); "Core decides legality and sufficiency" (§4.2); the confirmation's exact-revision
binding and the browser boundary (§4.3); the `422` branch/details table with its reachability notes
(§5.1); the BLOCKED rendering and the raw-`details` guarantee (§5.2); the state-unchanged check with
its falsification direction and the `api_keys` qualification (§5.3); the permission table including the
honest `VERIFIED` boundary and the no-actor-kind-visibility rule (§6); the audit/readback rules (§7);
the nine negatives (§8); and §9's status honesty.

**Left to implementation (deliberately not frozen here):** component structure and whether the forms
live on the existing `write` tab (`router.ts:15` — `TaskTab` is already
`'board' | 'history' | 'evidence' | 'write'`) or a new one; whether the matrix is a table, a list or an
extension of `TaskEvidence.tsx`; test ids and wording; whether `RefusalFields` gains `version` (§5.2);
the exact fixture/Gate harness; how the applicable-rule list is presented; and whether the matrix reuses
R-TE's already-loaded rows or fetches per-Evidence rows. Reusing `useWriteAction`/`WritePanel` with
`keyKind='event_id'` and the existing `missing_evidence` block is the expected, but not mandated, route.

**Could not be verified from code (stated rather than assumed):** whether any given operator
environment has a complete current fingerprint, a satisfying Evidence set, or a current human
confirmation. This is environment state; no source read here can establish it, which is why §8 treats
the `422` negatives as the required ones and permits BLOCKED/PARTIAL. `docs/Jasmine-Core-V1-P6-阶段任务书.md`
was read in this worktree at the path named in the brief.

---

## 11. What this proposal deliberately does not decide

* **Whether a web button may ever mint a human `USER_CONFIRMATION`** — the Core owner's ADR/API
  decision (ADR 0007:13; taskbook §5 item 6). This document only specifies what the forms may show
  *about* a confirmation that already exists. Filed as **CR-6** in §3.4.
* **Any new Core route or scope.** The coordination requests of §3.4 (CR-1…CR-7) plus CQ-1 are listed
  here, not implemented:
  * **CQ-1 — the missing actor-kind gate on →`VERIFIED`** (§6, §12 D1): ask the Core interface owner
    whether →`VERIFIED` should require a human/system actor. ADR 0006:13 gates `state:accept` and
    self-acceptance, not `→VERIFIED`, and the frozen P1-02 API table names only `state:write` for the
    Step transition (`docs/api/core-api-v1.md:202`); the taskbook's item-5 sentence assumes more. Until
    answered, the UI must not claim the server refuses an agent on that edge.
  * **CR-1 — a read-only Evidence-matrix / validation-preview route** (§3.4; must satisfy the
    in-transaction assert and needs a new proxy pattern).
  * **CR-2 — a read-only current-fingerprint read or a per-Evidence `freshness` boolean** (§3.4).
  * **CR-3 — the producer-config resolution** (§3.4).
  * **CR-4 — source revalidation exposure** (§3.4).
  * **CR-5 — publish `STEP_EDGES`/`TASK_EDGES`** (inherited; spec §13, `P6-REMAINING-TASKS.md` §6).
  * **CR-6 — the `evidence/confirm` ADR question** (above).
  * **CR-7 — the caller's own actor kind / granted scopes** (§3.4), needed **only** if a future item
    requires the UI to know its identity; this slice does not, and CR-7 is not a licence to invent a UI
    actor-kind rule.
* **Proxy/operator decisions, not Core changes:** whether `POST /v1/workspaces/compare` (already a
  Core route, `fingerprint:read`) is opened to the browser, and whether the dashboard needs a
  documented read-key set that includes `authority:read` for the matrix's Rule half. Both are operator
  decisions with no Core edit; neither is made here.
* **Form design**, tab placement, and the Gate's evidence-bundle format beyond naming it (§10).
* **Anything about the fingerprint lifecycle**, including when a fingerprint is refreshed or whether a
  VERIFIED Step is moved to `STALE` — P1/P7 domain (ADR 0007:15; `evidence.py:326-397`).
* **Re-litigating item 4.** No line of `web/P6-02B-TASK-STEP-WRITE-SPEC.md` is changed or reinterpreted
  here; where this document needs one of its rules it cites the section.

---

## 12. Where the taskbook, the ADRs and the code disagree

Each divergence is named so a reviewer sees it rather than inherits it. There is **no general "code
wins" clause** to appeal to: `docs/Jasmine-Core-V1-P6-阶段任务书.md:16` says only that *design examples*
yield to the *frozen contract* (the ADRs, the API doc and the implementation), which is a different
rule (rev 2, m10). Where a divergence is with the frozen contract itself, it is a Core-owner question
(CQ-1), not something this slice resolves.

* **D1 — agent credentials on `VERIFIED`.** Taskbook §5 item 5 (line 95) says an agent credential must
  be refused by the server "even if the UI shows a human button"; ADR 0006:13 says an Agent cannot
  receive `state:accept` or self-accept. Both hold for `ACCEPTED`/accept (`auth.py:63-64`,
  `state.py:334-335`, `handlers.py:120`), and the frozen P1-02 API table names only `state:write` for
  the Step transition (`docs/api/core-api-v1.md:202`), so the frozen contract does **not** carry an
  actor-kind gate on `→VERIFIED` either. The code matches the frozen contract and not the taskbook's
  broader sentence: →`VERIFIED` requires only `state:write` and calls no `_role` (`state.py:333-341`),
  and the validator never reads `request.actor_kind` (`evidence.py:990-1056`);
  `tests/test_state.py:110-112` shows an agent key reaching the validator on that edge. Filed as
  **CQ-1** — a Core-owner requirement question, not a defect this slice may fix; the acceptance
  negative (§8.4) asserts only the code.
* **D2 — the taskbook's "fingerprint" display is not readable.** Taskbook §5 item 5 requires the form to
  show the fingerprint. F1/F2 mean no read route returns the stored snapshot's identity or the
  **current** fingerprint/completeness (only `POST /v1/workspaces/fingerprint`, mutating and refused,
  and `POST /v1/workspaces/compare`, a POST that is also not in the proxy allowlist and needs two known
  digests). The form may show each Evidence row's stored `fingerprint_sha256` and must say the current
  one is unreadable. Filed as **CR-2**.
* **D3 — the taskbook's "criteria→Evidence matrix" has no read API.** ADR 0006:19 and
  `evidence.py:991-992` place the evaluation inside a state write; there is no read-only matrix route
  in `handlers.py` or `docs/api/core-api-v1.md`. Filed as **CR-1**; §3.6 states exactly what may be
  displayed without computing Core's verdict.
* **D4 — "确认来源" cannot be produced by the slice.** The form can display an existing confirmation's
  source Event (`source_event_id` → R-EV, F9b), but the browser cannot mint that source: `POST /v1/events`
  is GET-only in the allowlist and `POST /v1/evidence/confirm` is absent from `write_paths`
  (`core-paths.json:20`, `37-50`). Recorded, not worked around; it is the item-5/item-6 boundary.
* **D5 — ADR 0002 §2.3's literal SQL is not what runs, on these routes too.** The ADR says the
  optimistic lock is `UPDATE … WHERE revision = :expected` with 0 rows → `409` (`ADR 0002:45-47`). The
  implementation SELECTs the snapshot and compares in Python (`state.py:152-161`), and the projection
  `UPDATE`s carry no `WHERE revision` (`state.py:354`, `387`, `450`). Under `BEGIN IMMEDIATE`
  (`db.py:91`) the two are equivalent for one writer; this proposal follows the code and already
  inherits the same note from the frozen spec §4.
* **D6 — the criteria-structure asymmetry.** `state.validate_criteria` accepts
  `{"requirements": []}` (`state.py:41-44`) while `evidence._requirement_set`, used for Authority rule
  mappings, refuses an empty list with `400 invalid_request` (`evidence.py:66-70`; `authority.py:266-269`).
  No contradiction arises through routes 3/6, because both refuse empty criteria with a `422` before
  calling the validator (`state.py:343-344`, `439-440`); it is recorded so a future reader does not
  "unify" the two and change which error an empty rule mapping produces.
* **D7 — a `USER_CONFIRMATION` requirement can be authored unsatisfiable (n1).** `state.py:59` permits
  `required_result: "PASS"` for `USER_CONFIRMATION` (as does ADR 0006:17), but `record_confirmation`
  always writes `status="INFO"` (`evidence.py:815-816`) and `_match` requires exact equality (`:951`).
  A requirement written that way can never be satisfied through any existing route. It is recorded, not
  resolved: tightening `validate_criteria` would be a Core/schema-facing change and belongs to the Core
  interface owner.
