# ADR 0018: Neutral tool-category matching and deferred context delivery

Status: Proposed (written with the Grok Build dialect, the third agent). Amends ADR 0005 (matcher keys) and ADR 0017
(sections 9 and 15.2). **Provides no proof of native effect**: see `docs/hosthook-h4-results.md`.
Date: 2026-10-13. Schema: unchanged (13). No migration: `rule_versions.matcher_json` already stores the matcher as JSON text.

## 1. Why

ADR 0017 section 15.2 recorded the user's decision to keep matching the native tool name *until a third agent is added*,
because native names differ (`Bash`, `run_terminal_command`, `apply_patch`, `search_replace`, `Write`, ...). A rule
`tool: Bash` constrains Claude Code and Codex but not Grok Build. The third agent is now being added, so that condition is met.

Separately, Grok Build discards stdout of `SessionStart` and of an allowing `UserPromptSubmit` hook (hooks guide, 1.0.41), so
a Context Pack cannot be injected at the events Core produces it on.

## 2. Decision A: `matcher.tool_category`

* Rule `matcher` accepts a fourth key, `tool_category`, one of the neutral categories already used by the protocol:
  `shell`, `file.read`, `file.write`, `file.edit`, `web`, `mcp`, `other`. Any other value is `400 invalid_request`
  (`field: matcher.tool_category`). The single definition of the list is `authority.TOOL_CATEGORIES`; `hosthook.protocol`
  re-exports it.
* `POST /v1/guard/check` accepts an optional `tool_category` alongside `tool`, `action`, `path`. Matching stays an AND over
  the keys the matcher names, using the ADR 0005 rules: a key present in the matcher but absent in the request makes the
  rule *uncertain* (answer `confirm`), never a silent allow.
* `tool` keeps its meaning (exact native name) and keeps working. `tool_category` is added, not substituted; nothing
  stored changes meaning. A request that carries only `tool_category` (no `tool`) no longer counts as "unresolved tool".
* The Host Hook service sends both `tool` (= `native_tool`) and `tool_category` (= payload `tool`) to Guard, so
  `request_sha256` of host-hook guard answers changes from this version on.
* `action` is already neutral where it is derived by the adapter (`rm`, `touch`, ... from the shell command's first word;
  `read`/`write`/`edit` for file tools), so `{tool_category: shell, action: rm, path_prefix: /x}` reads the same for every agent.
* A rule that names a native `tool` stays agent-specific. Use `tool_category` for rules meant to hold across agents.
* The Dashboard rule form accepts the key (`web/src/views/RuleWrites.tsx`).

Limits, stated plainly: the category comes from each dialect's tool table. A tool a dialect does not know is `other`, and
a table can be wrong for a tool name nobody has observed (Grok Build's table is built from open-source names and is not
yet confirmed on a real conversation). The shell `action` is a heuristic (ADR 0017 section 8) and compound commands
resolve to nothing, so they come back `confirm`.

## 3. Decision B: `inject_mode: "deferred"`

* Capability declaration gains optional `inject_mode`: `direct` (default; the pack is returned on the event that produced
  it) or `deferred`. `inject_events` may now contain `tool.after`.
* For a `deferred` dialect the hook runtime keeps the `inject_context` text Core returns for `session.start`,
  `prompt.submit` and a post-compact `context.compact` in the spool directory (`deferred/<sha256(session)[:32]>.json`, 0600
  in a 0700 directory), newest wins, and hands it to the next `tool.after` of the **same native session**, once. Expired
  after one hour. The dialect puts it where the agent documents it (Grok: `PostToolUse` `additionalContext`).
* Consequences, not hidden: the model sees the pack only after its first tool call of a turn, so a turn with no tool call
  gets none, and the model's first decision in a turn is made without it. Hard guarantees therefore rest on `tool.before`
  Guard, not on the pack.
* `binding.inject_context=false` still withholds the pack (nothing is stored). Core records the pack either way.
* Delivery is a local file operation: an already-held pack is delivered even if Core is down at that moment.

## 4. Decision C: shared dialect helpers

`dialects/_common.py` holds hashing, defensive field readers, the exit-code reader and the two output envelopes shared
by the agents' documented formats. Native names, tool tables and event maps stay in each dialect.
