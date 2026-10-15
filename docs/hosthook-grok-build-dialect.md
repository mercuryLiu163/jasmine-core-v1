# Grok Build dialect (`--agent grok-build`)

Status: **constructed from documentation; no native verification.** Sources: the hooks guide installed with grok 1.0.41
(`~/.grok/docs/user-guide/10-hooks.md`, read in full on 2026-10-13, also published at <https://docs.x.ai/build/features/hooks>)
and the open-source tool taxonomy (`xai-org/grok-build`, `xai-grok-tools/src/tool_taxonomy.rs`). The machine running this
work was not signed in to Grok, so no model turn has been run (`docs/hosthook-h4-results.md`).

## Registration

`jasmine-core hooks install --agent grok-build --project-dir DIR --binding FILE` writes **one file it owns**,
`DIR/.grok/hooks/jasmine-core.json`, and never touches the other files in `.grok/hooks/`. Handlers carry only the documented
fields (`type`, `command`, `timeout`). Events: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact,
PostCompact, Stop, SessionEnd. Timeouts are explicit (observe events default to 5 s in Grok): PreToolUse 10, SessionEnd 1,
others 30.

Project hooks are skipped until the folder is trusted (`/hooks-trust` or `--trust`), which Grok records in its own trust
store. Installing never touches `~/.grok`, and refuses to.

**Cross-reading.** Grok also loads `~/.claude/settings.json`, `<project>/.claude/settings.json` and `.cursor/hooks.json`
(`[compat.claude] hooks`, `[compat.cursor] hooks`, default on). If a project has Jasmine hooks installed for Claude Code
as well, Grok runs those commands too. The Claude Code dialect cannot read Grok's camelCase payload (`session_id` is
missing) and passes through, so there is no double record, but the commands do start. Prefer one agent per project
directory, or switch the compat cell off.

## Field mapping (documented)

| Neutral | Grok native |
| --- | --- |
| session id | `sessionId` |
| turn id | `promptId` (absent on session-scoped events; `prompt.submit` falls back to a content hash) |
| `tool.*` name / input / call id | `toolName` / `toolInput` / `toolUseId` |
| `tool.after` result | `toolResult` (tagged object such as `{"type":"Bash","exit_code":0,...}`; a plain string when truncated) |
| event name | `hook_event_name` (PascalCase); `hookEventName` is Grok's own snake_case |
| prompt text | `prompt` |
| Stop | `reason == "end_turn"` only; `lastAssistantMessage`, `stopHookActive` |

Not this session's, therefore ignored: `Stop` with another `reason` (an extra one fires at session teardown), anything with
`subagentType` for Stop / UserPromptSubmit / SessionEnd. Subagent **tool** calls are still checked.
`StopCancelled`, `StopFailure`, `Notification`, `PermissionDenied`, `PostToolUseFailure`, `SubagentStart/Stop` are not registered.

## Tool categories (`tool` in the payload)

Built from the open-source wire names; the toolset in use changes the spelling (`run_terminal_command`, `run_terminal_cmd`,
`bash`; `search_replace`, `edit`, `hashline_edit`, `apply_patch`, `write`, `write_file`; `read_file`, `grep`, `list_dir`, ...).
MCP calls arrive as `server__tool`. The file operand is read from the first of `path`, `file_path`, `target_file`, `filePath`,
`file` — **the hooks guide only shows `command` for the shell tool; the file keys are unconfirmed**. If none matches, no path is
sent and Guard answers `confirm` for rules that need one (fail toward asking, not toward allowing). A real run must record the
actual keys, and this table must then be corrected. First real run (grok 1.0.52, [H5](hosthook-h5-results.md)): `grep` sends
`{pattern, glob, head_limit}` with no path (the dialect then scopes it to the cwd); the keys of `read_file`/`write`/`edit` are still unobserved.

## Decisions and output

* `deny` and `ask` → `hookSpecificOutput.permissionDecision` (Grok supports `ask` natively). `allow` → silence.
* Grok is fail-open on every hook error; only an explicit deny blocks. Core unreachable → the runtime emits an **explicit deny**
  (`on_unavailable=deny`, the default). A crashed or timed-out hook process is still allowed by Grok: this is a property of the agent.
* No context at `SessionStart` / `UserPromptSubmit` (stdout is discarded). `inject_mode: "deferred"`: the pack is delivered as
  `PostToolUse` `additionalContext` after the first tool call, once per pack (ADR 0018).
* Registered `PostCompact` without `deliver_context=false`, so Core builds a `POST_COMPACT` pack that is deferred the same way.

## Not verified (see the results document)

Everything above about native behaviour: that the hooks load and fire in `-p` mode, the tool input key names, whether
`PostToolUse` `additionalContext` reaches the model's next step, exit-code presence in `toolResult`, and compaction events.
