# Host Hook Protocol v1

Status: draft, published with ADR 0017 (`docs/adr/0017-host-hook-protocol.md`, which is
normative where the two differ). Version string: `"1"`.

This is the public contract between an **adapter** (a thin program that sits behind an
agent's command hooks) and a **Core** that implements `POST /v1/host-hooks/events`.
It is agent-neutral. Nothing in it says an agent will honour a hook output: that is a
property of the agent, established only by a real-conversation test (section 9).

Machine-readable schemas: `spec/schemas/host-hook-request.schema.json`,
`host-hook-response.schema.json`, `host-hook-capabilities.schema.json`.

## 1. Events

| Event | Meaning | Adapter requirement | Core may answer |
| --- | --- | --- | --- |
| `session.start` | a session starts or resumes | optional | context text |
| `prompt.submit` | the user submitted a prompt | optional | context text |
| `tool.before` | a tool is about to run | **required** | `allow` / `deny` / `ask` |
| `tool.after` | a tool finished | **required** | nothing |
| `context.compact` | before or after context compaction | optional | context text (after) |
| `turn.stop` | an agent turn ended | optional | nothing |
| `session.end` | the session ended | optional | nothing |

An adapter declares which of these it implements and what the agent can do (block,
ask, inject) in a **capability declaration**; its SHA-256 (over canonical JSON) is sent
with every request.

## 2. Request

`POST /v1/host-hooks/events`, `Authorization: Bearer <token>`, `Content-Type: application/json`.

```json
{
  "protocol_version": "1",
  "event": "tool.before",
  "delivery_id": "myagent:tool.before:3f2a...",
  "agent":   {"name": "myagent", "version": "1.2"},
  "adapter": {"name": "myagent", "version": "1", "capabilities_sha256": "<64 hex>"},
  "host_id": "hst_...",
  "binding": {"project_id": "prj_...", "task_id": "tsk_...", "step_id": "stp_..."},
  "native":  {"session_id": "abc", "turn_id": "t1", "call_id": "c9"},
  "trust": "NATIVE_HOOK",
  "occurred_at": "2026-10-12T08:30:15.000000Z",
  "payload": {"tool": "shell", "native_tool": "Bash", "input": {"command": "rm /x/y"},
              "action": "rm", "paths": ["/x/y"]}
}
```

* `delivery_id`: 1-200 chars of `[A-Za-z0-9_.:-]`. Deterministic from the agent's own
  session/turn/call identifiers where they exist, so a retry of the same native event
  has the same id. Reusing an id with different content is an error.
* `binding` comes from trusted configuration, never from model output. Omit `task_id`
  and Core only records Raw Events (no context, checkpoint or Evidence).
* `native.turn_id` and `native.call_id` may be absent; an adapter for an agent without
  them documents its substitute.
* `trust`: `NATIVE_HOOK` (an in-agent hook or plugin observed it), `PROXY_OBSERVED`
  (observed on the model API path), `AGENT_REPORTED` (the agent says so). Recorded only.
* Tool categories (`payload.tool`): `shell`, `file.read`, `file.write`, `file.edit`,
  `web`, `mcp`, `other`. `native_tool` keeps the agent's own name.
* `action` / `path` / `paths` on `tool.before` are resolved by the adapter: Core cannot
  see the filesystem the tool runs on. A command that cannot be resolved is sent without
  them and Core's Guard answers `ask` (insufficient context), never an implicit allow.

Payloads per event are in the request schema.

## 3. Response

```json
{
  "protocol_version": "1", "event": "tool.before", "delivery_id": "...",
  "decision": "deny", "reason": "matched rul_... version 1",
  "inject_context": null,
  "recorded": {"event_ids": [], "evidence_ids": [], "checkpoint_id": null},
  "degraded": [],
  "guard": {"decision": "deny", "capability": "ADVISORY", "request_sha256": "...", "matched": []},
  "trust": "NATIVE_HOOK", "replayed": false
}
```

`decision` is `none` except for `tool.before`. `degraded` lists what Core could not do
(for example `no_task_binding`, `context_unavailable:<code>`); the Raw Event is still
stored. `ask` means "have the user confirm": an adapter for an agent that cannot ask must
turn it into `deny` and say so in the reason. A `verify` Guard result is returned as
`allow` (it does not mark any Step verified).

## 4. Status codes

`201` new, `200` replay; `400 invalid_request` / `host_hook_protocol_unsupported`;
`401`, `403 forbidden_scope` / `forbidden_actor_kind` / `actor_mismatch` / `host_hook_namespace_reserved` (other clients writing `host.*` events or the `host-hook-v1` source); `404 <kind>_not_found`;
`409 host_hook_delivery_conflict`; `413 payload_too_large`; `503 database_busy`.

## 5. Adapter behaviour when Core is unreachable

* Capture events (`prompt.submit`, `tool.after`, `session.*`, `turn.stop`, `context.compact`): store the
  exact request in a private spool (directory mode 0700, files 0600) and return an empty native response so
  the agent is not blocked. Re-send later unchanged; Core de-duplicates by `delivery_id`.
* `tool.before`: **fail-closed by default** (the adapter emits its native deny), write a visible line to stderr and
  a gap record that holds only hashes; a binding may opt in to letting the tool run with `on_unavailable: "allow"`.
* Retry only on connection failure, timeout, 429 and 5xx. A 4xx is a permanent rejection.

## 6. Binding file (reference runtime)

`<project>/.jasmine/host-binding.json`, found by `--binding`, `JASMINE_HOST_BINDING`, or an upward
search from the agent-reported working directory. Fields: `version` (1), `core_url` (loopback
`http://HOST:PORT`), `host_id`, `project_id`, optional `task_id`, `step_id`, `api_key_file`,
optional `human_api_key_file`, `on_unavailable` (`allow`|`deny`), `spool_dir`, `timeout_seconds`, and
`inject_context` (boolean, default `true`; `false` withholds the Context Pack from the agent while Core still
records it, for enforcement tests).
With a device binding (`hook --device-binding FILE`, ADR 0019) the project file holds only `project_id`, `task_id`,
`step_id` and optionally `inject_context`; the rest comes from the device file. No project file means no action.
It never holds a token; key files must be private (owner only, mode 0600). Without a binding the
runtime prints an empty native response and exits 0.

The human key, when configured, is used only for `prompt.submit`, so that the user's words are
attributed to the human actor; every other event uses the system key.

## 7. Idempotency and replay semantics

A replay returns the **original** response. In particular a replayed `inject_context` is the old
pack: an adapter must not present it as a fresh decision about the current state.

## 8. Writing a dialect

A dialect translates one agent's native hook JSON to the events above and the response back to the
agent's native stdout and exit code. It owns: native event names, field names, the tool-category
mapping, `delivery_id` construction, the native deny/ask/inject output shapes, and the capability
declaration. An agent that cannot take context at the event Core produces it on declares `inject_mode: "deferred"`:
the runtime holds the pack per native session and delivers it once at the next `tool.after` (ADR 0018). Rules meant to
hold across agents use `matcher.tool_category` (the neutral category from `payload.tool`), not the native `tool` name.
See `docs/hosthook-codex-dialect.md` for a worked example (constructed from the
agent's documentation, not verified natively).

## 9. What conformance does and does not show

Passing the schemas, the component tests and a local integration test shows that an adapter and Core
exchange well-formed messages and record what they claim to record. It does **not** show that the agent
calls the hook, trusts it, passes the fields the dialect expects, stops a tool after `deny`, or shows
`inject_context` to the model. Those require a real conversation under the operator's authorisation, with
the denied tool's side effect checked afterwards. Until then a deployment must describe the guard as
advisory.
