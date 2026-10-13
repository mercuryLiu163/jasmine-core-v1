# Claude Code hook fixtures (constructed from docs)

These native payloads were **constructed by hand from the Claude Code hooks documentation**
(https://code.claude.com/docs/en/hooks, read on 2026-10-12 through a summarising fetch tool, not
raw HTML). They were **not captured from a real Claude Code session**.

Documented (as summarised): the common fields (`session_id`, `prompt_id`, `transcript_path`, `cwd`,
`permission_mode`, `hook_event_name`), `source` on SessionStart, `tool_name`/`tool_input`/`tool_use_id`
on tool events, `tool_response` on PostToolUse (shape not shown), `last_assistant_message` and
`stop_hook_active` (mentioned) on Stop, `trigger` values `manual`/`auto` on PreCompact.

Not confirmed from the page and therefore **assumed**: the `prompt` field name on UserPromptSubmit,
the `reason` field on SessionEnd, `custom_instructions` on PreCompact, and every key inside
`tool_response` (Bash may carry `stdout`/`stderr`/`interrupted`; an integer exit code is not known to exist,
`post_tool_use_bash_with_exit_code` is a hypothetical shape). A real session may differ; replace with captured
fixtures after the real-conversation gate.
