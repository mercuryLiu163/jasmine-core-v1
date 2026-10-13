# ADR 0017: Host Hook Protocol (agent-neutral capture and guard events)

Status: Proposed. Written before the implementation; the code in
`src/jasmine_core/hosthook/` follows it. This ADR provides **no proof of native
effect** (see section 12).
Date: 2026-10-12. Schema: 12 -> 13 (one new forward-only migration, `m0013_host_hooks`).
Related: ADR 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0011, 0012, 0013.
Inputs: `TRELLIS-HOST-HOOKS-TASKBOOK.md` (plan only), `JASMINE-MESH-OVERVIEW.md` section 4.

## 1. Purpose and boundary

A neutral protocol through which any coding agent that offers command hooks can
(1) have the user's words stored verbatim, (2) have a tool call checked by
Authority/Guard before it runs, (3) have its tool result stored as Evidence with
a recorded trust level, (4) receive a Context Pack, (5) trigger Checkpoints.

Core knows nothing about particular agents. All agent-specific work lives in a
*dialect* (section 9). Core code under `src/jasmine_core/hosthook/` (except
`dialects/`) and the Core endpoint contain no agent names; an agent name is only
ever a data value (`agent.name`).

The existing P1-P3 Codex hooks (`capture/`, `adapter/`, `.codex/hooks.json`,
`scripts/*hook*`) are historical validation paths. They are not modified and this
protocol does not replace their ADRs' claims.

## 2. Neutral events

| Event | Required of an adapter | Core handling (reuse) | Response |
| --- | --- | --- | --- |
| `session.start` | no | Raw Event `session.started`; if Task bound, Context Pack `SESSION_START` | `inject_context` |
| `prompt.submit` | no | Raw Event `user.prompt`, `payload.text` verbatim (ADR 0004); if Task bound and caller is human, Context Pack `USER_PROMPT` | `inject_context` |
| `tool.before` | yes | `authority.guard` per resolved path; mapped to a decision | `decision`, `reason` |
| `tool.after` | yes | `evidence.record_tool_result` (ADR 0007 exit-code rules unchanged) | none |
| `context.compact` | no | `phase=before`: Raw Event + Checkpoint `PRE_COMPACT`; `phase=after`: Raw Event + Context Pack `POST_COMPACT` | `inject_context` (after) |
| `turn.stop` | no | Raw Event + Checkpoint `SESSION_STOP` (Task bound only) | none |
| `session.end` | no | Raw Event `session.ended` | none |

Only `tool.before` and `tool.after` are assumed for every agent.

## 3. Endpoint

`POST /v1/host-hooks/events`, scope `hosthook:report`, JSON body (max 1 MiB, ADR 0003).
Success: `201` for first processing, `200` for an idempotent replay. Unknown
top-level request fields are `400 invalid_request`.

### 3.1 Request

```
protocol_version  "1"                                   (else 400 host_hook_protocol_unsupported)
event             one of the seven events
delivery_id       string 1..200 chars, [A-Za-z0-9_.:-]  idempotency key
agent             {name: string 1..64, version?: string<=64}
adapter           {name: string 1..64, version: string<=64, capabilities_sha256: 64 hex}
host_id           hst_...
binding           {project_id?: prj_, task_id?: tsk_, step_id?: stp_}
native            {session_id: string 1..256, turn_id?: string|null, call_id?: string|null}
trust             NATIVE_HOOK | PROXY_OBSERVED | AGENT_REPORTED
occurred_at       optional RFC 3339 (observation time; used for Raw Event occurred_at)
payload           object, per event (3.3)
```

### 3.2 Response

```
protocol_version  "1"
event, delivery_id  echoed
decision          allow | deny | ask | none     ("none" except for tool.before)
reason            string, mandatory for deny/ask, else null
inject_context    {text, context_pack_id} | null
recorded          {event_ids:[evt_...], evidence_ids:[evd_...], checkpoint_id: ckp_|null}
degraded          [string]     codes for what could not be done, e.g. no_task_binding
guard             tool.before only: {decision, capability:"ADVISORY", request_sha256, matched:[...]}
trust             echoed, as recorded
replayed          boolean (transport flag; not part of the stored response)
```

### 3.3 Payloads

* `session.start`: `{source?: string}`
* `prompt.submit`: `{text: string}` (verbatim; non-string is 400; over 256 KiB is `413 payload_too_large`, never truncated)
* `tool.before`: `{tool: category, native_tool: string, input: object, action?: string, path?: abs path, paths?: [abs path] (max 8)}`
* `tool.after`: `{tool, native_tool, input: object, response: any JSON, exit_code?: int, is_error?: bool}`
* `context.compact`: `{phase: "before"|"after", trigger?: "auto"|"manual", deliver_context?: bool}`
* `turn.stop`: `{last_message_sha256?: 64 hex}` (assistant text is not sent)
* `session.end`: `{reason?: string}`

Tool categories (neutral, closed set): `shell`, `file.read`, `file.write`,
`file.edit`, `web`, `mcp`, `other`. The native name stays in `native_tool`.
`action`/`path(s)` are resolved on the adapter side (section 8), because ADR 0005
makes the *caller* responsible for resolving a deterministic tool/action/path.

## 4. Idempotency

A new table `host_hook_deliveries` (append-only, triggers as for `evidence`) stores
`delivery_id` (PK), `request_sha256` (SHA-256 of the canonical request body, which
includes `occurred_at`), the final `response_json`, `actor_id`, `trust`, `created_at`.
On a request:

1. same `delivery_id`, same hash -> stored response, `200`, `replayed=true`;
2. same `delivery_id`, different hash -> `409 host_hook_delivery_conflict`, nothing written;
3. otherwise process, then store the receipt.

The sub-operations are individually idempotent (deterministic `evt_` IDs derived from
`delivery_id`; `source_system="host-hook-v1"` + `source_event_id=delivery_id` unique index
per ADR 0003; deterministic Checkpoint/Context `idempotency_key`; the tool-result seed of
ADR 0007). A crash between the sub-operations and the receipt therefore re-executes safely
on retry. The receipt stores the response of the first processing; a replayed
`inject_context` is the *original* pack and is **not** a fresh admission (ADR 0012); an
adapter must not treat a replay as a new injection decision. `delivery_id` construction is
the dialect's duty (section 9): deterministic from native session/turn/call where those exist.

## 5. Ownership: no guessing (ADR 0002)

`binding` is supplied by the trusted project binding file (section 10), never by the model
or tool arguments. Without `binding.task_id`: Raw Events are still appended (with
`project_id` if known), no Context Pack is built, no Checkpoint is created, no Evidence is
written (a `tool.result` Raw Event is appended instead), and `degraded` contains
`no_task_binding`. A stated host/project/task/step that does not exist is `404`; a task
outside the stated project or a step outside the stated task is `400 invalid_request`.

## 6. Mapping to existing semantics

### 6.1 Raw Events

All Raw Events written here use `source_system="host-hook-v1"`, `source_event_id=<delivery_id>`
(a delivery that writes several Events suffixes `#<role>`), the authenticated actor and kind, and
carry in `payload`: `text` (empty except `user.prompt`), `agent`, `adapter`, `native`
(`session_id`, `turn_id`, `call_id`), `trust`, `delivery_id`, `binding_step_id`. They never set
`events.session_id` (no Core `ses_` object is created; open question 1). Two event types are added
to `models.EVENT_TYPES`: `host.turn_stopped` and `host.compaction_reported`, so that a Checkpoint has
a genuine Task-scoped source Event.

### 6.2 Guard (`tool.before`)

The Guard body is `{project_id?, task_id?, tool: native_tool, action?, path?}`. `tool` is the
**native** tool name (existing Rules use `Bash`); open question 2 covers matching on the neutral
category. When `paths` has several entries Guard runs once per path and the strictest result wins
(deny > confirm > verify > allow); with no path it runs once without. Mapping:

| Guard | Response `decision` | Note |
| --- | --- | --- |
| `deny` | `deny` | `reason` = Guard reason |
| `confirm` | `ask` | dialect downgrades to deny if the agent cannot ask, and says so in the reason |
| `verify` | `allow` | Step is not marked VERIFIED; `guard.decision="verify"` is returned |
| `allow` | `allow` | |

`capability` stays `ADVISORY` (ADR 0005). HTTP 200 and a `deny` decision do not show that a tool was
blocked; only a native Gate can (section 12). The audit row (ADR 0004) records the mapped decision and
rule hits (as the dispatcher does for `/v1/guard/check`), never tool arguments.

### 6.3 Tool results (`tool.after`)

Reuses `EvidenceStore.record_tool_result` for kind `COMMAND_RESULT`: one `tool.call`, one `tool.result`,
one `evidence.recorded` Event and the Evidence projection in one transaction. The neutral payload is
converted to the ADR 0007 response shape `{exit_code?, is_error?, native_response}`; `PASS` iff integer
`exit_code == 0` and `is_error` is not true, `FAIL` on nonzero/`is_error`, otherwise `INFO` (rules
unchanged). The caller must be a Registry `system` actor (existing rule). `native.turn_id` absent ->
`"none"`; `native.call_id` absent -> the `delivery_id`. Additive changes to `evidence.py` (no behaviour
change for existing callers): optional parameters `source_system` (default `codex-posttool`) and
`payload_extra`, and an accepted body alias `native_session_id` for the legacy field of the same
meaning. The stored `tool.call`/`tool.result` payload still uses the frozen legacy key (Deviation D3).
If the workspace root is not configured (`fingerprint_unavailable`) Evidence cannot be built; the handler
falls back to a `tool.result` Raw Event and `degraded: ["evidence_unavailable:fingerprint_unavailable"]`.
After a successful Evidence write the handler makes a best-effort workspace refresh
(`EvidenceStore.capture`, as the existing PostToolUse path does) so a changed workspace stales VERIFIED
Steps; failure is `degraded: ["fingerprint_refresh_failed"]`.

### 6.4 Context (`session.start`, `prompt.submit`, `context.compact` after)

`ContextBuilder.build` with reason `SESSION_START`, `USER_PROMPT`, `POST_COMPACT`; `source_event_id` is the
Raw Event written in the same delivery; `idempotency_key` = `hh-` + first 40 hex of
SHA-256(`delivery_id` + reason). `inject_context.text` is `rendered_content` exactly. A builder failure
(for example unavailable tokenizer, ADR 0012) does not fail the delivery:
`degraded: ["context_unavailable:<code>"]`. ADR 0012 requires `USER_PROMPT` sources to be human-authored:
when the authenticated actor is not `human`, the prompt is still recorded (attributed to the real actor,
never impersonating a human) and `degraded: ["prompt_not_human_attributed"]` is returned with no context.
`context.compact` honours `payload.deliver_context=false` (set by the runtime when the agent cannot inject
at that event) by skipping the build.

### 6.5 Checkpoints

`ContinuityStore.create` with reason `PRE_COMPACT` (before) or `SESSION_STOP` (`turn.stop`); source = the
Raw Event of the delivery; key as above. Per ADR 0011 these are `UNVERIFIED_REQUEST` provenance: *reported*
lifecycle, not attested native lifecycle (ADR 0013's separate mechanism). A scan or conflict failure is
reported in `degraded` (`checkpoint_unavailable:<code>`); the Raw Event stays.

## 7. Trust levels

`trust` in {`NATIVE_HOOK`, `PROXY_OBSERVED`, `AGENT_REPORTED`} is **recorded only**. It is persisted in
(a) the payloads of the Raw Events of the delivery (for tool results this includes the `tool.result` Event
that is the Evidence's `source_event_id`, so Evidence -> source Event -> `trust`), and (b) the
`host_hook_deliveries` row. The `evidence` table is append-only and unchanged; no column is added to it. No
acceptance threshold, `CurrentEvidenceValidator` rule or State rule consults `trust`. Gating on trust needs a
separate ADR. A `trust` value outside the three is `400`.

## 8. Adapter-side resolution

`hosthook/resolve.py` (agent-neutral) resolves a simple shell command to `action` (basename of the first
word) and canonical absolute `paths`. It resolves nothing (so Guard answers `confirm`, ADR 0005) for a
command containing `; & | > < \` $ ( ) { }`, a newline, an unparsable quote, a leading `VAR=` word, or more
than 8 path-like words; relative words are resolved against the agent-reported cwd; each candidate is also
resolved through the filesystem (`realpath` of the existing parent) and both forms are sent. Wildcard and
`~` words are not paths. This is a heuristic, not a shell parser.

## 9. Dialects and capability declaration

Registry in `src/jasmine_core/hosthook/dialects/`; one module per agent exposing `NAME`, `CAPABILITIES`
(also stored as `<name>.capabilities.json`), `translate_in(native, ctx)` and `translate_out(event, response, ctx)`.
Capability fields: `agent`, `events`, `can_block_tool`, `can_ask`, `can_inject_context`, `inject_events`,
`has_turn_id`, `has_call_id`, plus `dialect_version` and `documentation` (source URL, date read).
`capabilities_sha256` = SHA-256 of the canonical JSON of that declaration, sent with every request and written
into the Raw Events. Core does not interpret it beyond format.

`delivery_id`: dialect-built, `"<agent>:<kind>:" + sha256(parts)[:40]`, parts from native session/turn/call plus
a discriminator (e.g. the prompt hash). Events with no call-like identity (`session.start`, `session.end`) add
the UTC second of the invocation: the agent re-running the same hook in another second yields a distinct delivery
(a duplicate `session.started`, not a lost event), while a *spool replay* resends the stored request unchanged
and is exactly idempotent. A dialect for an agent without turn IDs must state its substitute.

## 10. Hook runtime, binding file, Core unavailability

`jasmine-core hook --agent <name> [--binding FILE]` reads native JSON from stdin, picks the dialect,
translates, calls Core, translates back, prints the native stdout and returns the dialect's exit code.
`jasmine-core hook replay [--binding FILE | --spool-dir DIR]` re-sends spooled deliveries.

Binding file `<project>/.jasmine/host-binding.json` (found via `--binding`, else `JASMINE_HOST_BINDING`, else
upward search from the agent-reported `cwd`):

```
{"version":1,"core_url":"http://127.0.0.1:PORT","host_id":"hst_..","project_id":"prj_..",
 "task_id":"tsk_.."|null,"step_id":"stp_.."|null,
 "api_key_file":"/abs/private/system.key","human_api_key_file":"/abs/private/human.key"|null,
 "on_unavailable":"allow"|"deny","spool_dir":"/abs/dir"|null,"timeout_seconds":number|null}
```

No token is stored in the file. Key files must be regular, owned by the user, with no group/other permission
bits, else the key is treated as unavailable. `core_url` must be loopback HTTP (ADR 0004; anything else needs a
later ADR). No binding file -> empty native output, exit 0, no error.

Key use: `prompt.submit` uses `human_api_key_file` when present (so the prompt is attributed to the human
actor, as ADR 0007's P1 path does), else the system key; all other events use `api_key_file` (a `system`
actor). This keeps `hosthook:report` away from agents and avoids a system key minting human-attributed prompts.

Unavailability (connection refused, timeout, HTTP 5xx/429): capture events go to the private spool (directory
0700, files 0600, atomic rename) and the hook prints an empty native response; `tool.before` is **fail-open** by
default (stderr line "tool call was not checked" plus a spool gap record holding only hashes), and
`on_unavailable: "deny"` makes the dialect's deny output instead. A permanent 4xx is never spooled; it is logged
on stderr, and `tool.before` treats it like unavailable under the same policy since no decision was obtained.
After a successful non-`tool.before` delivery the runtime opportunistically drains up to 10 spooled entries.
Latency goal: `tool.before` p95 <= 300 ms including process start (not measured in H1); client timeout default
2 s for `tool.before`, 5 s otherwise; the installer (H2) writes the native hook timeout.

## 11. Authorization and errors

New scope `hosthook:report` in `auth.SCOPES`; `issue_key` refuses it for `agent` actors
(`403 forbidden_actor_kind`); `human` and `system` may hold it. The rule that agent keys cannot write evidence
is untouched. `tool.after` requires a `system` actor. The actor's `home_host_id` must equal `host_id`.

| Condition | Status / code |
| --- | --- |
| bad shape / field / enum / unknown field | 400 `invalid_request` |
| `protocol_version != "1"` | 400 `host_hook_protocol_unsupported` |
| text over limit | 413 `payload_too_large` |
| unauthenticated / missing scope | 401 `unauthenticated` / 403 `forbidden_scope` |
| actor kind not allowed for the event | 403 `forbidden_actor_kind` |
| unknown host/project/task/step | 404 `<kind>_not_found` |
| delivery id reused with different content | 409 `host_hook_delivery_conflict` |
| database busy | 503 `database_busy` |

Context/Checkpoint/Evidence failures that leave the Raw Event committed are not errors; they are `degraded`
entries (section 6).

## 12. What this ADR does not provide

No proof that any agent honours a hook output: that a `deny` stops a tool, that an `inject_context` reaches the
model, that a hook fires at all, or that the native payload has the fields a dialect expects. Component and local
integration tests show that Core and the runtime behave as specified given constructed inputs. Native effect is
established only by a real-conversation Gate under the user's authorisation (taskbook H3), which this change does
not run. Core never reports `ENFORCED`.

## 13. Relationship to earlier ADRs

* 0005: Guard unchanged and advisory.
* 0007: Evidence rules unchanged; additive parameters only (6.3). The P1 hooks' "Pre failures deny" belongs to
  those bound hooks; this protocol's default for Core-unreachable `tool.before` is fail-open with explicit opt-in
  deny (taskbook 4.4). Different adapter, different policy (D1).
* 0011/0012: Checkpoint/Context reused as-is, including `UNVERIFIED_REQUEST` and the "replay is not admission" caveat.
* 0013: adapter lifecycle attestation is a different, stricter mechanism; not used, not relaxed.

## 14. Deviations from the taskbook (existing ADRs win)

* D1: taskbook 4.4 fail-open `tool.before` differs from the P1 hooks' deny-on-failure (ADR 0007); kept as a
  per-binding choice, default per taskbook.
* D2 (ADR 0012 + 0007): taskbook maps `prompt.submit` to Context `USER_PROMPT` with a system-held scope. ADR 0012
  requires a human-authored source and ADR 0007 keeps human attribution away from system keys. So the binding may hold
  a separate human key used only for `prompt.submit`, `hosthook:report` may be held by human or system actors, and
  with only a system key no prompt-time pack is built.
* D3 (ADR 0007): tool-result events keep the frozen `codex_session_id` payload key; they are tagged
  `source_system="host-hook-v1"` via a new optional parameter. The Core hosthook module itself uses the alias
  `native_session_id`.
* D4: `session.start` does not create a Core `ses_` projection.
* D5: two event types added (6.1).
* D6: `deliver_context` and `occurred_at` added to the taskbook draft; `paths` plural for Guard.

## 15. Open questions (conservative choice made)

1. Map each native session to a Core `ses_` object? (H1: no.)
2. Should Rules match the neutral category instead of the native tool name? (H1: native name.)
3. Re-validate `inject_context` with `context/{id}/check-current` before delivery? (H1: no.)
4. Trust-level acceptance thresholds: a later ADR.
5. Should `events:write` clients be blocked from writing the `host.*` event types and `host-hook-v1` source? (H1: no, same as other types.)
6. Non-loopback `core_url` / TLS: unsupported.
