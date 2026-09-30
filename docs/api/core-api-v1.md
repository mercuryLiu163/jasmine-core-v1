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
`supports_search_tool: false`. This release supports only the independently
verified CLI0.159.0 binary fingerprint recorded in the provider. Other binaries
or missing configuration return a recorded `FAILED/provider_unavailable`.
The request body cannot alter this profile. Model weights/build version is not
attested by this CLI; extractor_version records the CLI version and its digest.
