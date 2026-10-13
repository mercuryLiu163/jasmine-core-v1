# Claude Code dialect for the Host Hook Protocol

Module: `src/jasmine_core/hosthook/dialects/claude_code.py` (agent name `claude-code`); capability declaration:
`src/jasmine_core/hosthook/dialects/claude_code.capabilities.json`. Fixtures: `fixtures/hosthook/claude-code/`
(constructed from docs, labelled as such in their README).

## Source, date, and how reliable this is

Official page: `https://code.claude.com/docs/en/hooks`, read on **2026-10-12** (no redirect occurred; the URL answered
directly on the official Claude domain). The page shows no version or date of its own. **The fetch tool returns summaries
produced by a small model, not the raw page**; I made two fetches with targeted questions. Everything below is "as
summarised" and must be re-read against the page before a real conversation. No Claude Code session was run, and no `claude`
process was started by this work.

Quoted by the summary (treated as documented): the common input fields; `source` on SessionStart; `tool_name`, `tool_input`,
`tool_use_id` on PreToolUse; the PreToolUse `hookSpecificOutput` fields `permissionDecision` (`allow`/`deny`/`ask`/`defer`),
`permissionDecisionReason`, `updatedInput`, `additionalContext`; `hookSpecificOutput.additionalContext` on SessionStart; the
statement that for `UserPromptSubmit`, `UserPromptExpansion`, `SessionStart` and `PostModelSwitch` "Claude Code adds
plain-text stdout as context that Claude can see"; `decision: "block"` + `reason` for UserPromptSubmit and PreCompact; exit
code 0 = success (stdout parsed as JSON if a valid object), exit 2 = blocking error, other = non-blocking error; settings
files `~/.claude/settings.json`, `.claude/settings.json` (project, committable), `.claude/settings.local.json`; handler fields
`type: "command"`, `command`, `timeout` ("Seconds before canceling"), `statusMessage`; `${CLAUDE_PROJECT_DIR}`; PreCompact
matchers `manual`/`auto`; SessionStart matchers `startup|resume|clear|compact|fork`; tool names `Bash`, `Write`, `Edit`,
`MultiEdit`, `Read`, `Glob`, `Grep`, `WebFetch`, `mcp__server__tool`; `last_assistant_message` on Stop and the mention of
`stop_hook_active`.

**Fields I could not confirm** (assumed by the dialect, each marked in the fixtures README):

* `prompt` as the field holding the user's text on `UserPromptSubmit` (the summary said the input schema was not listed).
* `reason` on `SessionEnd` (the page lists matcher values `clear`, `resume`, `logout`, `prompt_input_exit`, `other`; no field table).
* `trigger` as the input field name on `PreCompact` (the matcher values are documented; `custom_instructions` was not found).
* Any key inside `tool_response` for PostToolUse. The summary guessed `stdout`, `stderr`, `exit_code`, `interrupted` and flagged
  the guess. **Whether Claude Code reports an integer exit status at all is unknown.**
* Whether `hookSpecificOutput.additionalContext` (as opposed to plain stdout) is honoured on `UserPromptSubmit`: documented for
  SessionStart and generically in the universal output table; the plain-stdout route is documented for UserPromptSubmit. The
  taskbook says to use `additionalContext`, so the dialect does. If a real run shows it is not delivered, switch that event to
  plain stdout (a one-line change in `_inject`).
* What `permissionDecision: "ask"` does in a headless `-p` session (probably cannot prompt), and what `deny` does for the model
  (documented only as denying the call).
* Whether hooks run in `-p` mode, and whether project `.claude/settings.json` hooks need prior workspace trust. The summary
  quoted that "a `-p` session doesn't count as accepting" workspace trust in another context; this is why the Gate's
  `--prepare` asks for one interactive run in the scratch project and `--run` reports BLOCKED, not FAIL, when no hook fires.
* `PostToolUseFailure` and similar events exist in current Claude Code as far as general knowledge goes but were not in the
  summary; the dialect does not handle them (so a failed tool call may produce no Evidence).

## Native events handled

| Native event | Neutral event | Input fields used | Output |
| --- | --- | --- | --- |
| `SessionStart` | `session.start` | `session_id`, `cwd`, `source` | `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"<pack>"}}` or empty. `source: "compact"` is the post-compaction injection point. |
| `UserPromptSubmit` | `prompt.submit` | `session_id`, `prompt_id`, `prompt` (assumed), `cwd` | `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"<pack>"}}` or empty |
| `PreToolUse` | `tool.before` | `session_id`, `prompt_id`, `tool_name`, `tool_use_id`, `tool_input`, `cwd` | deny: `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"..."}}`; ask: same with `"ask"`; allow: **empty**, exit 0 |
| `PostToolUse` | `tool.after` | same plus `tool_response` | empty (never `decision: block`, never `updatedToolOutput`) |
| `PreCompact` | `context.compact` phase `before` | `session_id`, `prompt_id`, `trigger` (assumed) | empty (never blocks compaction) |
| `Stop` | `turn.stop` | `session_id`, `prompt_id`, `stop_hook_active`, `last_assistant_message` | empty (never `decision: block`, which would continue the turn) |
| `SessionEnd` | `session.end` | `session_id`, `reason` (assumed) | empty |

Not handled (ignored, empty output): `SubagentStop`, `Notification`, `PostToolUseFailure`, `PermissionRequest`, anything else.

## Decisions

* **ask is native.** Core `ask` (from Guard `confirm`, for example an unparsable shell command) becomes `permissionDecision: "ask"`
  with the Core reason. This differs from the Codex dialect, which must downgrade to deny.
* **allow is silence.** The dialect never emits `permissionDecision: "allow"`, because it would skip Claude Code's own permission
  check. A Core `allow`/`verify` leaves the normal flow untouched.
* **Fail-closed.** If Core cannot be reached (or refuses), `PreToolUse` emits `deny` with a reason that names `on_unavailable=deny`,
  unless the binding opts into `allow` (ADR 0017 section 10). Capture events never block.
* **Context injection** on `SessionStart` and `UserPromptSubmit` only. PreCompact has no documented injection output, so nothing
  is requested there; post-compaction context arrives through `SessionStart(source=compact)`.
* **Tool categories.** `Bash` -> `shell`; `Read`, `Glob`, `Grep` -> `file.read`; `Write` -> `file.write`; `Edit`, `MultiEdit`,
  `NotebookEdit` -> `file.edit`; `WebFetch`, `WebSearch` -> `web`; `mcp__*` -> `mcp`; else `other`. Guard `tool` is the native name,
  so a Rule written for `Bash` does not constrain `Write` (ADR 0017 section 15, question 2).
* **Exit codes.** The dialect reads an integer `exit_code`, `exitCode` or `returncode` from `tool_response` and `is_error`/`isError`;
  `interrupted: true` becomes `is_error: true`. With none of them present Core records `INFO`, never `PASS`. If Claude Code
  indeed reports no integer status, every Bash Evidence from this agent is `INFO`: it is still captured with trust `NATIVE_HOOK`,
  but it cannot satisfy a `PASS` acceptance requirement until the response shape is known. This is a known limitation.
* **Trust.** Always `NATIVE_HOOK` as sent by this dialect: a statement about the channel, not a verification.

## Turn id and replay stability

Claude Code is documented (as summarised) to put `prompt_id` in every hook's common input: a UUID for the user prompt being processed,
matching the `prompt.id` of its OpenTelemetry events, absent until the first user input.

* Present: `native.turn_id = prompt_id` on prompt, tool, compact, stop and session-end events. The `delivery_id` of a prompt, stop or
  compaction is built from it, so re-running the same hook for the same prompt is an exact duplicate (Core returns the stored receipt).
* Absent: only `prompt.submit` gets a substitute, `cc-` + SHA-256(`session_id` NUL `prompt`)[:16]. It is a function of content only, so
  it is identical on every re-run and across processes, and it is **the same for two identical prompts in one session**. Because of
  that the delivery id additionally carries the UTC second of the invocation; typing the same prompt twice therefore yields two
  events, while a duplicate firing in the same second would collapse (rare; documented limitation). Other events carry no turn id.
* Tool events do not depend on turn ids: their `delivery_id` is `claude-code:<event>:SHA-256(session, tool_use_id)`; if `tool_use_id`
  is missing the call id is SHA-256(tool name + canonical input).
* `session.start`/`session.end` add the invocation second (no native identity); `stop` without `prompt_id` also uses it.
* A **spool replay** resends the stored request unchanged (the stored `delivery_id` is used as is), so replay is exactly idempotent
  whatever the above.

## Installing

`jasmine-core hooks install --agent claude-code --project-dir <dir> --binding <file>` writes one handler per event into
`<dir>/.claude/settings.json` (see ADR 0017 section 16.1). Not verified: that Claude Code loads these entries, applies workspace
trust to them, or runs them in `-p` mode.

## Unverified (all of it, natively)

Everything in the tables above is from documentation. Not observed: that Claude Code invokes these hooks with these fields, that a
`deny` stops the tool, that `ask` prompts, that `additionalContext` reaches the model on either event, the shape of `tool_response`
for any tool, hook latency under Claude Code, or `-p` behaviour.
