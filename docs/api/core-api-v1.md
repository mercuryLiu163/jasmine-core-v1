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
`422 missing_evidence` until a real Evidence validator is available.
