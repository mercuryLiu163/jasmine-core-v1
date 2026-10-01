# Jasmine Core V1 — Core API v1 (P0)

Frozen by ADR 0003. Base URL `http://127.0.0.1:8787`; P0 refuses to bind
anything but loopback.

## Conventions

* Request and response bodies are `application/json; charset=utf-8`. Original
  text is stored and returned exactly as written — not escaped, not normalised.
* Every response carries `X-Request-Id`. Send your own to correlate; otherwise
  one is generated. A supplied id that is longer than 128 characters or that
  looks like a credential is replaced with a generated one, because the audit
  log stores it.
* Numeric query parameters are validated, not coerced: `?after_seq=abc` is a
  `400 invalid_request`, never a 500.
* Errors are always `{"error": {"code", "message", "request_id", "details"}}`.
  `code` is stable; branch on it, never on `message`.
* P0 has **no** `PUT` / `PATCH` / `DELETE`. A known path with the wrong method
  returns `405` and lists what is allowed.

## Authentication

```
Authorization: Bearer <token>
```

Tokens come from `jasmine-core bootstrap` or `POST /v1/auth/keys`. Only the
SHA-256 is stored; a token is shown once and cannot be recovered. Scopes:
`admin`, `objects:read`, `objects:write`, `events:read`, `events:write`.

The `actor_id` of a write always comes from the token. A body may name an
`actor_id`, but only as a claim: if it differs from the authenticated actor the
request is refused with `403 actor_mismatch`.

## Endpoints

| Method | Path | Scope | Notes |
| --- | --- | --- | --- |
| GET | `/v1/health` | — | liveness and schema version |
| GET | `/v1/meta/schema` | — | applied migrations with checksums |
| GET | `/v1/hosts` | `objects:read` | registered capture devices |
| GET | `/v1/actors` | `objects:read` | registered identities |
| POST | `/v1/auth/keys` | `admin` | mint a key; the token is returned once |
| GET | `/v1/auth/keys` | `admin` | metadata only, never a token |
| POST | `/v1/auth/keys/{key_id}/revoke` | `admin` | idempotent; the key is then refused with 401 |
| GET | `/v1/audit` | `admin` | metadata only; no prompt text, no token |
| GET | `/v1/hosts`, `/v1/actors` | `objects:read` | registered devices and identities |
| POST | `/v1/events` | `events:write` | append one Raw Event |
| GET | `/v1/events` | `events:read` | filter by `session_id`, `task_id`, `project_id`, `event_type`, `source_system`, `after_seq`, `limit` |
| GET | `/v1/events/{event_id}` | `events:read` | one Event, payload included |
| POST | `/v1/projects` | `objects:write` | creates the object **and** its source Event |
| GET | `/v1/projects`, `/v1/projects/{project_id}` | `objects:read` | list / one |
| POST | `/v1/tasks` | `objects:write` | needs an existing `project_id` |
| GET | `/v1/tasks`, `/v1/tasks/{task_id}` | `objects:read` | list (filter by `project_id`) / one |
| POST | `/v1/sessions` | `objects:write` | `project_id` and `task_id` both optional |
| GET | `/v1/sessions`, `/v1/sessions/{session_id}` | `objects:read` | list / one |

## Creating an object

One request writes two rows in one transaction: the Raw Event, then the
projection. The response carries both.

```jsonc
// POST /v1/projects
{"name": "demo", "host_id": "hst_01K7…", "event_id": "evt_01K7…"}   // event_id optional

// 201 Created (or 200 with "replayed": true on an identical retry)
{
  "object": {"project_id": "prj_01K7…", "name": "demo", "status": "active",
             "revision": 1, "source_event_id": "evt_01K7…",
             "created_at": "2026-09-29T12:00:00.000000Z",
             "updated_at": "2026-09-29T12:00:00.000000Z"},
  "event":  {"event_id": "evt_01K7…", "seq": 1, "event_type": "project.created",
             "actor_id": "act_01K7…", "host_id": "hst_01K7…",
             "payload": {"text": "", "object_kind": "project", "object_id": "prj_01K7…", …},
             "body_sha256": "…"},
  "replayed": false
}
```

`expected_revision` is **rejected** on create (`400 unexpected_expected_revision`).
P0 has no update endpoint; optimistic locking arrives with P1.

## Appending a Raw Event

```jsonc
// POST /v1/events
{
  "event_type": "user.prompt",          // user.prompt | assistant.message |
                                        // session.started | session.ended |
                                        // project.created | task.created | project.notes
  "source_system": "codex",             // free text; scopes source_event_id
  "source_event_id": "<session>:<turn>",// optional; unique per source_system
  "host_id": "hst_01K7…",
  "event_id": "evt_01K7…",              // optional; the idempotency key
  "occurred_at": "2026-09-29T12:00:00Z",// optional, see below
  "session_id": "ses_01K7…",            // all optional: an Event may precede
  "project_id": "prj_01K7…",            // any Truth (ADR 0002 §2.4)
  "task_id":    "tsk_01K7…",
  "payload": {"text": "the original words"}
}
```

`payload.text` is required and is stored byte for byte. A reference you state
must resolve (`404 …_not_found`); a reference you omit is simply absent.

The Codex capture entry deliberately sends **no** `cwd` and **no** `occurred_at`.
Neither is part of a turn's identity: `cwd` is reported inconsistently between
deliveries of the same turn, and an invented timestamp would make every retry a
different body. The device is recorded as `host_id`.

**`occurred_at` is optional on purpose.** A source with no timestamp of its own
— the Codex hook — omits it, the Core records its own acceptance time, and the
omission keeps the field out of the idempotency hash so a retried delivery is
recognised as a replay. Supplying it makes it part of the hashed content.

### Idempotency

`event_id` is the key.

| Situation | Result |
| --- | --- |
| new `event_id` | `201` |
| same `event_id`, same content | `200`, the stored object/Event, `replayed: true`, nothing written |
| same `event_id`, different content | `409 event_id_conflict` with `existing_body_sha256` and `request_body_sha256` |
| same `(source_system, source_event_id)`, new `event_id` | `409 source_event_duplicate` |
| same `source_event_id`, different `source_system` | allowed |

## Errors

| HTTP | `code` | When |
| --- | --- | --- |
| 400 | `invalid_request` | bad JSON, wrong type, unknown field, bad timestamp, `NaN`/`Infinity` |
| 400 | `unexpected_expected_revision` | `expected_revision` on a create |
| 400 | `missing_expected_revision` | reserved for P1 updates |
| 401 | `unauthenticated` | missing, unknown or revoked token |
| 403 | `forbidden_scope` | scope missing; nothing was written |
| 403 | `actor_mismatch` | the body names a different actor than the token |
| 404 | `project_not_found`, `task_not_found`, `session_not_found`, `event_not_found`, `host_not_found`, `actor_not_found`, `route_not_found` | target or reference does not exist |
| 404 | `key_not_found` | revoking a key that does not exist |
| 405 | `method_not_allowed` | known path, wrong method; `details.allowed` lists the rest |
| 409 | `event_id_conflict`, `source_event_duplicate` | see the table above |
| 409 | `revision_conflict` | reserved for P1 updates |
| 413 | `payload_too_large` | body over 1 MiB |
| 415 | `unsupported_media_type` | not `application/json` |
| 500 | `internal_error` | no exception type, message or stack is ever returned |
| 503 | `database_busy`, `schema_version_unsupported`, `migration_conflict` | see the ADRs |

A refusal never writes anything — not to `events`, the object tables, or
`api_keys`. `last_used_at` is only recorded after the scope check passes, so a
refused caller leaves no trace beyond the audit row.

That audit row holds metadata and lengths only. `path`, the caller's
`X-Request-Id` and any `details` value are all passed through redaction before
they are stored, so a client that puts a token in a URL cannot land it on disk.
Field values are length-clipped, so an audit row cannot grow with the body.

## P1-01 Authority extension

See [ADR 0005](../adr/0005-p1-authority-and-guard.md) for version, scope,
permission, Guard and idempotency semantics. These paths extend the P0 API;
the P0 endpoints and error codes above remain valid.

| Method | Path | Scope | Result |
| --- | --- | --- | --- |
| GET | `/v1/rules/active?project_id=&task_id=` | `authority:read` | ACTIVE rules applicable to context |
| GET | `/v1/rules/{rule_id}` | `authority:read` | Current Rule projection and content |
| GET | `/v1/rules/{rule_id}/history` | `authority:read` | Lifecycle snapshots by revision |
| POST | `/v1/rules/proposals` | `authority:propose` | New PROPOSED Rule; 201, or 200 replay |
| POST | `/v1/rules/{rule_id}/approve` | `authority:manage` plus human/system | Activate proposal; 200 |
| POST | `/v1/rules/{rule_id}/supersede` | `authority:manage` plus human/system | Same-scope new version; 200 |
| POST | `/v1/rules/{rule_id}/retire` | `authority:manage` plus human/system | Retire active Rule; 200 |
| POST | `/v1/guard/check` | `guard:check` | Advisory `allow/deny/confirm/verify`; 200 |

Proposal body: `rule_key`, `kind`, `severity`, `enforcement`, `content`,
`matcher`, `scope`, `origin_event_id`, `host_id`, optional `event_id`.
Approve/retire body: `host_id`, positive integer `expected_revision`, optional
`event_id`. Supersede also carries replacement `kind/severity/enforcement/content/matcher`,
the *same* `scope`, and `origin_event_id`. Guard body may carry `project_id`,
`task_id`, `tool`, `action`, `path`; omitted context can produce `confirm`.

New errors: `403 forbidden_actor_kind`, `409 rule_key_conflict`; existing
`revision_conflict` and `event_id_conflict` apply. The `replayed` response flag
means the original command Event and Rule snapshot were returned without a new
Authority write. Guard HTTP 200 does not authorize a tool action by itself;
inspect `decision` and `capability`.

## P1-02 Task/Step State extension

See [ADR 0006](../adr/0006-task-step-state-and-evidence-boundaries.md) for the
state machine, revision ownership, acceptance criteria and Evidence gate. State
scopes are `state:read`, `state:write`, and `state:accept`; Agent keys cannot
carry `state:accept`.

| Method | Path | Scope | Purpose |
| --- | --- | --- | --- |
| POST | `/v1/tasks/{task_id}/steps` | `state:write` | Create PLANNED Step; lock Task revision |
| GET | `/v1/tasks/{task_id}/steps` | `state:read` | List Steps |
| GET | `/v1/steps/{step_id}` | `state:read` | Current Step |
| GET | `/v1/tasks/{task_id}/history` | `state:read` | State Events in sequence |
| GET | `/v1/steps/{step_id}/history` | `state:read` | Step Events in sequence |
| POST | `/v1/steps/{step_id}/transition` | `state:write` | Lock Step revision; advance Step and Task revisions |
| POST | `/v1/steps/{step_id}/criteria` | `state:accept` | Human/system criteria update; lock Step revision |
| POST | `/v1/tasks/{task_id}/transition` | `state:write` | Lock Task revision |
| POST | `/v1/tasks/{task_id}/criteria` | `state:accept` | Human/system criteria update; lock Task revision |
| POST | `/v1/tasks/{task_id}/accept` | `state:accept` | Human/system acceptance; lock Task revision |

Every write takes `host_id` and a positive integer `expected_revision` (except
`POST /v1/tasks`, which is a create). `event_id` is optional and supports exact
replay. A Step transition body uses `status` and, when required, `reason`.
The response includes the new Step revision and `task_revision`. A rejected
write leaves Truth unchanged. `VERIFIED` and `ACCEPTED` return
`422 missing_evidence` when the P1-03 validator finds an unsatisfied current requirement.

## P1-03 Evidence and workspace extension

See [ADR 0007](../adr/0007-p1-evidence-fingerprint-and-codex-hook.md). The server
must be started with an operator-owned `JASMINE_CORE_WORKSPACE_ROOT`; no request
body may select or claim a workspace fingerprint. New `evd_` IDs identify
immutable Evidence. All Evidence writes keep source and command Events distinct.

| Method | Path | Scope and Registry actor | Purpose |
| --- | --- | --- | --- |
| POST | `/v1/codex-exec-observations` | `evidence:attest`, system | Independent native CLI result linked to immutable Post INFO |
| POST | `/v1/tool-results` | `evidence:write`, system | Tool call/result Raw Events and Evidence in one transaction |
| POST | `/v1/evidence/confirm` | `evidence:confirm`, human | Explicit confirmation with genuine current human prompt source |
| GET | `/v1/evidence/{evidence_id}` | `evidence:read` | Evidence plus current reference status |
| GET | `/v1/tasks/{task_id}/evidence` | `evidence:read` | Task Evidence list |
| POST | `/v1/workspaces/fingerprint` | `fingerprint:scan`, system | Server scan; persist invalidated VERIFIED Steps as STALE |
| POST | `/v1/workspaces/compare` | `fingerprint:read` | Compare two stored fingerprint SHA-256 IDs |

`POST /v1/tool-results` accepts exact fields `task_id`, optional `step_id`,
`host_id`, optional Core `session_id`, `codex_session_id`, `turn_id`,
`tool_use_id`, `tool_name`, `tool_input` (object), `tool_response` (value),
optional `kind` (default `COMMAND_RESULT`), and optional `artifact_uri` of the
form `workspace:/relative/path`. The source Event IDs derive from the Codex
session/turn/tool-use triple. The same delivery returns the original projection
with `replayed: true`. An explicit integer zero exit code gives PASS; nonzero
or `is_error: true` gives FAIL; absent exit code gives INFO. A specialized kind
requires a matching private operator producer mapping as described in ADR 0007.
`reference_status` is rechecked on GET and validation: `NONE`, `OK` or
`BROKEN_REFERENCE`. `BROKEN_REFERENCE` cannot satisfy criteria.

`POST /v1/codex-exec-observations` accepts exactly `task_id`, `step_id`,
`host_id`, `origin_prompt_event_id`, `related_posttool_event_id`, `codex_jsonl`,
`codex_jsonl_sha256`, `hook_trace_jsonl`, `hook_trace_sha256`, and
`codex_executable` with exact `path`, `version`, `sha256` fields. Sources are
complete UTF-8 captures with matching lowercase SHA-256 digests; executable
path is resolved and absolute. The trusted local runner attests that both
sources came from the same invocation. Core checks a unique completed native
command and complete three-record prompt/Pre/Post trace against stored Events,
original INFO Evidence, unchanged revisions and complete workspace fingerprint.
The original Post remains INFO. A separate native result yields PASS only for
strict integer zero and FAIL for other integers. New writes return 201 with
`evidence`, `result_event`, `related_posttool_event_id`, `replayed=false`;
identical replay by the same actor returns the original snapshot with 200 and
`replayed=true`. Changed observations for the same Post return 409. JSONL and
trace limits are 192 KiB and 32 KiB; full text is retained in the immutable
Event. See ADR 0007 for exact lifecycle and shell wrapper restrictions.

`POST /v1/evidence/confirm` takes `task_id`, optional `step_id`, `host_id`,
`origin_event_id`, positive integer `expected_revision`, optional `event_id`,
and optional paired `confirmed_rule_id`/`confirmed_rule_version`. The origin
must be a same-task human `user.prompt` Event from the authenticated actor,
after the relevant current State Event; a Step confirmation also requires the
prompt payload to name that Step. The command Event carries the actor's
structured confirmation, exact revision, fingerprint and optional Rule version.
A human may replay an identical `event_id` to get its original Evidence; old
prompts cannot be reused for a later revision. Agent keys cannot carry
`evidence:confirm`, `evidence:write`, or `evidence:attest`.

`POST /v1/workspaces/fingerprint` takes only `project_id` and `host_id`; the
response includes `fingerprint_sha256`, server `snapshot`, and `staled_steps`.
`POST /v1/workspaces/compare` takes exact lowercase 64-hex `left_sha256` and
`right_sha256`; it returns `SAME`, `MISMATCH` or `UNKNOWN`. Partial scans never
return SAME. GET endpoints do not perform stale writes.

Authority proposal and same-scope supersede bodies now accept optional
`verification_requirements` with the exact `acceptance_criteria` structure
from ADR 0006. The mapping is immutable content of the new Rule version, is
included in its idempotency digest, and appears in Rule GET/history. ACTIVE
ACCEPTANCE/VERIFY Rules without a mapping fail `VERIFIED`/`ACCEPTED` with
`422 missing_evidence`; they must be superseded by an authorized new version.

The P1 Codex hook installer adds only project-local entries. Its bound
PreToolUse supports simple canonical Bash actions only and emits the official
native deny response for Guard DENY/CONFIRM and unresolved calls; VERIFY lets
the tool execute but does not mark the Step VERIFIED. Unbound sessions receive
advisory `{}`. Hook trust and real-tool coverage are decided by the separate
P1-T10 Gate, never inferred from a successful API call or synthetic test.

## P2-01 Interpretation candidate extension

See [ADR 0008](../adr/0008-p2-interpretation-candidate-chain.md). Candidate
processing never applies Truth. Scopes are `interpretations:read` and
`interpretations:process`; both require `events:read` on these routes, and
process also requires read. These remain instance-wide scopes, not project ACLs.

| Method | Path | Scope | Result |
| --- | --- | --- | --- |
| POST | `/v1/interpret` | process + read + events:read | 201 new interpretation (including recorded failure), 200 replay |
| GET | `/v1/interpretations` | read + events:read | items, next_after_id |
| GET | `/v1/interpretations/{interpretation_id}` | read + events:read | provenance, processing status and terminal result |

POST exact fields: `event_id`, `idempotency_key`, optional `extractor_id`
(default `codex-local-v1`). No source text, model, credentials or scope override.
Response: `interpretation`, `replayed`. Status is `PROCESSING`, `EXTRACTED`,
`FAILED` or `INTERRUPTED`; EXTRACTED remains a non-authoritative candidate.
The same key and changed request returns 409 `interpretation_idempotency_conflict`.
Same actor/event/config processing with another key replays the original record.
Failures return a fixed error_code in the record, preserving the source Event.
Lists accept `event_id`, `project_id`, `task_id`, `status`, `after_id`, `limit`.
Source type/actor identity derive from stored Event, never from model claims.

The exact model output contract is [interpretation-v1 JSON Schema](../schemas/interpretation-v1.json).
`source_span.start/end` are zero-based Unicode code points with an exclusive end;
`quote` must equal that exact source slice. IDs in candidate scope can only refer
to the source Event's current project/task. Candidate task_id may be null for a
new Task proposal. Status recovery of expired PROCESSING appends an immutable
INTERRUPTED outcome during an authorized read/replay; it does not retry a model.

The local provider is opt-in: the operator sets absolute
`JASMINE_CORE_INTERPRETER_CODEX` and `JASMINE_CORE_INTERPRETER_CATALOG` paths.
The catalog must retain the actual `gpt-6.1-sol` entry and have
`apply_patch_tool_type: null`, `experimental_supported_tools: []`, and
`supports_search_tool: false`, and `tool_mode: null`. This release supports only the independently
verified CLI0.159.0 binary fingerprint recorded in the provider. Other binaries
or missing configuration return a recorded `FAILED/provider_unavailable`.
The request body cannot alter this `no-execution.v2` profile or its default
non-Plan execution mode. The CLI may declare one Plan-only question meta tool;
independent forced-call verification must prove it is unavailable in this mode.
This is not a claim that the platform declares zero tools. Model weights/build version is not
attested by this CLI; extractor_version records the CLI version and its digest.

The local provider fixes official saved-auth Responses HTTP/SSE transport using
`interpreter-openai` as a local configuration name, `requires_openai_auth=true`
and `supports_websockets=false`. It supplies no custom base URL or API key.
The 120-second deadline is unchanged; network failures remain recorded failures.

## P2-02 Resolver / Review / Maintenance (ADR0009, Schema8)

All reads require events:read and interpretations:read plus the named read scope. Writes reject query parameters and unknown/nested fields. Additional actor rules and bounds are in ADR0009; admin does not substitute for required scopes. Operator executor configuration is not an HTTP body field.

|Endpoint|Scope|Body / result|
|---|---|---|
|GET /v1/resolutions/preview?interpretation_id=int_|resolutions:read|Consistent plan, policy_version, expected_revisions, expected_context_digest, maintenance_head, source_application|
|POST /v1/resolve/{int_id}|resolutions:process + resolutions:read|idempotency_key, host_id, policy_version, expected_revisions, expected_context_digest → resolution snapshot|
|GET /v1/resolutions/{res_id}|resolutions:read|Immutable command/plan/result, source/binding and manual application history|
|GET /v1/reviews/pending|reviews:read|limit1..200, after_id, project_id, task_id; stable created_at/id cursor|
|GET /v1/reviews/{rvw_id}|reviews:read|CAS revision, pending Resolution, history, current preview or explicit preview_error|
|POST /v1/reviews/{rvw_id}/approve|reviews:manage + reviews:read + action P1 scopes|idempotency_key, host_id, reason, expected_revision, expected_revisions, expected_context_digest, actions|
|POST /v1/reviews/{rvw_id}/reject|reviews:manage + reviews:read|idempotency_key, host_id, reason, expected_revision|
|POST /v1/interpretations/{int_id}/correct|interpretations:manage|idempotency_key, host_id, reason, expected_revision, result (complete interpretation-v1 schema against original Raw) → immutable manual child|
|POST /v1/interpretations/{int_id}/reject|interpretations:manage|idempotency_key, host_id, reason, expected_revision; keeps Raw/Truth|
|POST /v1/interpretations/{int_id}/rerun|interpretations:manage + interpretations:process|same reject body; bounded real provider, child/operation refs, default no Truth|
|POST /v1/resolutions/{canonical_res_id}/manual-reapply|reviews:manage + interpretations:manage + resolutions:read + action P1 scopes|idempotency_key, host_id, reason, current_interpretation_id, expected_head_revision, expected_latest_resolution_id, expected_revisions, expected_context_digest, actions|

expected_revisions is an exact array of `{object_type:project|task|step|rule,object_id,revision}`. actions is an exact array `{candidate_index,action,payload}`. NO_ACTION/CREATE_TASK/ATTACH_TASK payload is `{}`. CREATE_RULE accepts `{}` for NORMAL/CONTEXT semantic mapping or complete P1 `{kind,severity,enforcement,content,matcher}` (content equals candidate). SUPERSEDE_RULE requires those rule fields plus target_rule_id/expected_revision. Criteria actions require target_id/expected_revision/acceptance_criteria. Scope and origin are server-derived from the candidate and trusted source bindings; caller cannot supply other Task/Project. CREATE_RULE requires authority:propose+authority:manage, supersede authority:manage, criteria state:accept, Task creation objects:write. Human/system role is checked again in the business store.

New command201; exact historical replay200; source replay200. Review source-race loser is SOURCE_REPLAYED and preserves original applied action refs. Bad syntax400, wrong role/scope403, missing resource404, stale head409 interpretation_not_current, unready processing409 interpretation_not_ready, key conflict409 idempotency_conflict, stale full context409 context_conflict, latest application conflict409 application_conflict. Existing GET Interpretation adds maintenance_head/history/operation_completions while preserving original processing fields. Unsupported PATH/TOOL manual mappings return400 and keep pending; no silently changed scope or claimed application.

Review APPROVE may map a CORRECTION candidate to SUPERSEDE_RULE targeting a different-source Rule only in the same Task and exact scope, with authority:manage, human/system identity and the complete revision/context snapshot. This exception is unavailable to ordinary manual-reapply. The immutable action result preserves previous_origin_event_id, previous_rule_version, previous_source_event_id plus the new change_event_ids.

## P3-01 Checkpoint and Resume (Schema 9)

These endpoints store deterministic continuity receipts; they do not attest actual compact/Stop hooks or restore Truth. All require `objects:read`, `state:read`, `authority:read`, `evidence:read`, and `events:read`, in addition to the endpoint scope.

| Endpoint | Scope | Result |
| --- | --- | --- |
| POST `/v1/checkpoints` | `checkpoint:write` | 201 new / 200 exact replay |
| GET `/v1/checkpoints/{ckp}` | `checkpoint:read` | exact immutable checkpoint |
| GET `/v1/tasks/{tsk}/checkpoints/latest` | `checkpoint:read` | latest checkpoint or null |
| POST `/v1/tasks/{tsk}/resume` | `resume:build`, `checkpoint:read` | 201 new / 200 exact replay |
| GET `/v1/resumes/{rms}` | `resume:read`, `checkpoint:read` | exact immutable Resume |

Checkpoint body: `{task_id,host_id,source_event_id,reason,idempotency_key,session_id?,current_step_id?,note?}`. Note is `{text,source_event_id}`, bounded to 2000 characters. Reasons: MANUAL, HANDOFF, BLOCKED, STEP_VERIFIED, PRE_COMPACT, SESSION_STOP. Native lifecycle reasons are caller requests labeled UNVERIFIED_REQUEST; they are not native platform proof.

Resume body: `{host_id,source_event_id,idempotency_key,session_id?,checkpoint_id?}`. Null/omitted checkpoint selects the latest matching Task and optional Core session; explicit checkpoint must belong to that Task. Optional identities can be null; required identities cannot. Latest GET accepts only a single `session_id` query. All other endpoints accept no query. Unknown/duplicate fields, malformed IDs, nonfinite or invalid Unicode JSON fail 400.

New commands use server-owned complete snapshots and bounded outside-transaction fingerprint scans. Scan timeout/failure/output cap return 503 `checkpoint_scan_timeout`, `checkpoint_scan_failed`, or `checkpoint_scan_too_large`. Concurrent Truth/selection changes after one retry return 409 `checkpoint_context_conflict` or `resume_context_conflict`. Corrupt/ahead checkpoint contents return 409 `checkpoint_integrity_error`. Oversized complete context returns 409 `checkpoint_context_too_large`. Exact actor/key replay returns original stored receipt contents even after subsequent business changes; a changed request body with the same key returns 409 `idempotency_conflict`.

See [ADR 0011](../adr/0011-checkpoint-resume-snapshot.md) for integrity, provenance, budget and non-Authority note boundaries.

## P3-02 Context packs (Schema 10)

- `POST /v1/context/build`: exact fields `task_id`, `host_id`, `source_event_id`, `idempotency_key`, `reason`; optional `session_id`, `current_step_id`. Reasons: SESSION_START, USER_PROMPT, POST_COMPACT, HANDOFF, MANUAL. USER_PROMPT requires a genuine human prompt source. Requires `context:build` plus checkpoint and baseline read scopes. New receipt is 201; historical exact-key replay is 200.
- `GET /v1/context/{ctx}`: immutable full receipt, requiring `context:read` plus baseline/checkpoint reads. Unknown query fields are rejected.
- `POST /v1/context/{ctx}/check-current`: exact `host_id`, `source_event_id`, optional `session_id`, `current_step_id`; requires build/read scopes and the original actor. Returns current comparison, not a regenerated pack. Binding, configuration or Truth/selector mismatch returns 409; actor mismatch 403. No query fields are accepted.

Receipts include the genuine command Event, complete snapshot/selector, exact rendered content/hash/byte count, pinned tokenizer identity, total/section counts, omission provenance and read-only Memory diagnostics. A 2,500-token rendered-pack bound uses explicit o200k_base; model encoding remains unverified. Mandatory overflow is `AUTHORITY_TOO_LARGE` or `CONTEXT_MANDATORY_TOO_LARGE`. Missing tokenizer is an explicit dependency failure. See ADR 0012 for offline setup and admission boundaries. Context generation and its component adapter do not establish actual native hook injection or tool execution.

## Native adapter (P3-03)

Schema remains 10. These routes require the exact configured system key and
Registry home-host binding; `adapter:report`, `adapter:attest` and `adapter:read`
are purpose scopes, in addition to the existing business read/write scopes.

| Route | Purpose |
| --- | --- |
| POST `/v1/adapter/lifecycle/report` | Exact trusted callback report; no native completion claim |
| POST `/v1/adapter/lifecycle/attest` | Immutable post-collection native lifecycle proof |
| POST `/v1/adapter/operations/reserve` | One reservation per native thread/turn/call |
| GET `/v1/adapter/operations/{evt}` | Historical snapshot; UNKNOWN_OUTCOME if effect started without terminal |
| POST `/v1/adapter/operations/{evt}/check-current` | Read-only fresh admission check; historical receipt is not authorization |
| POST `/v1/adapter/operations/{evt}/complete` | Terminal once; stale effects do not qualify Evidence |
| POST `/v1/adapter/requirements/bind` | Explicit P2 Rule-to-P1 criterion binding with exact revisions |
| POST `/v1/evidence/codex-dynamic-observation` | Read the already committed original dynamic Evidence |

All routes reject query parameters and unknown body fields. GET/observation use
`adapter:read`; mutations use report or attest. Exact-key replay returns the
original snapshot (200); fresh immutable mutations return 201. Changed native-call
arguments or reused keys conflict (409). Private receipt/config input is supplied
by the adapter deployment, not by model arguments or public Raw Events.

The complete exact request fields are frozen in the implementation's `REPORT`,
`RESERVE`, `CHECK` and `BIND` constants and the lifecycle/complete validators.
Operations are the four fixed `jasmine_read`, `jasmine_patch`, `jasmine_test` and
`jasmine_playwright` names. Guard confirmation/precondition outcomes are blocked
unless the reviewed path can establish the necessary precondition; no automatic
confirmation or acceptance is implied. See [ADR 0013](../adr/0013-native-lifecycle-and-typed-execution.md).
