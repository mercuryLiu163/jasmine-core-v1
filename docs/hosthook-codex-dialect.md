# Codex dialect for the Host Hook Protocol

Module: `src/jasmine_core/hosthook/dialects/codex.py`; capability declaration:
`src/jasmine_core/hosthook/dialects/codex.capabilities.json`.

**Source and date.** Constructed from the Codex hooks documentation at
`https://developers.openai.com/codex/hooks`, read on **2026-10-12**. That URL answered with a
308 redirect to `https://learn.chatgpt.com/docs/hooks`, which is the page actually read. The page
carries no version or date of its own ("current release behavior"). The fetch tool summarised the page
(it does not return raw HTML), so details below are as summarised and should be re-checked against the
page before a real conversation test. No native Codex session was run. The existing P1-P3 hooks were not
consulted for field shapes beyond confirming the older `permissionDecision: "deny"` output form is
already used there.

## Native events handled

| Native event | Neutral event | Input fields used (documented) | Output |
| --- | --- | --- | --- |
| `SessionStart` | `session.start` | `session_id`, `cwd`, `source` | `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"<pack>"}}` or empty |
| `UserPromptSubmit` | `prompt.submit` | `session_id`, `turn_id`, `prompt` | `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"<pack>"}}` or empty |
| `PreToolUse` | `tool.before` | `session_id`, `turn_id`, `tool_name`, `tool_use_id`, `tool_input`, `cwd` | deny: `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"..."}}`; allow: empty, exit 0 |
| `PostToolUse` | `tool.after` | same plus `tool_response` | empty (never `decision: block`) |
| `PreCompact` | `context.compact` phase `before` | `session_id`, `turn_id`, `trigger` | empty |
| `PostCompact` | `context.compact` phase `after` | same | empty |
| `Stop` | `turn.stop` | `session_id`, `turn_id`, `stop_hook_active`, `last_assistant_message` | `{}` (never `decision: block`, which would continue the turn) |
| `SessionEnd` | `session.end` | `session_id`, `reason` | empty |

Not handled (ignored, pass-through output): `PermissionRequest`, `SubagentStart/Stop`, `Interrupt`.
`SessionEnd` is in the documentation but not in the taskbook's list; it is included because it costs nothing.

## Decisions taken from the documentation

* **ask is unsupported for PreToolUse** (documented as parsed but treated as a failed hook). Core's `ask`
  is turned into a `deny` whose reason starts "confirmation required (this agent cannot ask)".
* **Exit code and stdout.** Output is JSON on stdout with exit 0. Exit code 2 (documented as blocking) is
  not used. `Stop` must emit JSON on exit 0, hence `{}`.
* **Context injection** only on `SessionStart` and `UserPromptSubmit`; the documentation lists no injection
  output for `PreCompact`/`PostCompact`, so the dialect sets `deliver_context=false` for `PostCompact` and Core
  does not build a pack that could not be delivered. Documented limit: oversized output spills to disk
  (`additionalContextLimit`, about 2,500 tokens), the same size as the Context Pack cap.
* **Tool names.** `Bash` -> `shell`; `apply_patch` -> `file.edit` (affected files read from the `*** Add/Update/Delete File:`
  and `*** Move to:` lines of `tool_input.command`); `mcp__*` -> `mcp`; anything else -> `other`. Guard `tool` is the native name.
* **Exit codes.** The documentation's example `tool_response` is `{"stdout","stderr","returncode"}`; the dialect reads an
  integer `exit_code`, `exitCode` or `returncode` and passes it as `exit_code`. A response without one (for example a
  plain string) yields no `exit_code`, so Core records `INFO`, never `PASS`. Whether real Codex puts a numeric status in
  `tool_response` for Bash is **unverified**.
* **Turn and call ids.** Codex supplies `turn_id` and `tool_use_id`; if `tool_use_id` is missing the dialect substitutes a
  SHA-256 of tool name and input.
* **delivery_id** = `codex:<event>:<sha256(parts)[:40]>`; parts are session id, turn id, call id (tools), prompt hash (prompt),
  phase/trigger (compact), `stop_hook_active` (stop). `SessionStart`/`SessionEnd` add the UTC second of the invocation.
* **Trust.** Always `NATIVE_HOOK` as sent by this dialect, which is a statement about the channel, not a verification.

## Config

H2 added `jasmine-core hooks install --agent codex --project-dir <dir> --binding <file>`, which writes handlers for
`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PreCompact`, `PostCompact`, `Stop` and `SessionEnd` into
`<dir>/.codex/hooks.json` only (ADR 0017 section 16.1). Hooks still require explicit user trust in Codex (`/hooks`); the
installer cannot grant it. Not verified: that Codex loads these entries or accepts the `SessionEnd`/`PostCompact` names.
Before H2, the command line to register was `jasmine-core hook --agent codex --binding <project>/.jasmine/host-binding.json`.

## Unverified

Everything in the tables above is from documentation. Not observed: that Codex invokes these hooks with these fields, that a
`deny` stops the tool, that `additionalContext` reaches the model, the real shape of `tool_response` for each tool, the
behaviour of `apply_patch` hooks, hook timeouts and the latency of the runtime under Codex.
