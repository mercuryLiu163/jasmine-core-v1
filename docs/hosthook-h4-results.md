# Host Hook H4: third agent (Grok Build) and neutral rule matching — results

Date: 2026-10-13. Branch `hosthook-grok` (from `hosthook-h2`; not pushed, not merged). Component level.

## What was built

| Item | Where |
| --- | --- |
| `matcher.tool_category` + Guard `tool_category` ([ADR 0018](adr/0018-neutral-tool-category-and-deferred-context.md)) | `authority.py`, `hosthook/service.py`, Dashboard rule form |
| Shared dialect helpers; Codex and Claude Code dialects now use them (no behaviour change) | `hosthook/dialects/_common.py` |
| Grok Build dialect, fixtures, capability file | `hosthook/dialects/grok_build.py`, `fixtures/hosthook/grok-build/` |
| Deferred context delivery (`inject_mode: deferred`) | `hosthook/runtime.py`, `hosthook/spool.py` |
| Installer: Grok's own hooks file; optional `statusMessage` | `hosthook/install.py` |
| Gate script knows `grok-build`; its deny rule is now agent-neutral (`tool_category: shell`) | `scripts/hosthook-real-conversation.py` |
| Dialect document | [hosthook-grok-build-dialect.md](hosthook-grok-build-dialect.md) |

## What the tests show (component level)

* The same category rule, written once, denies the same `rm /protected/...` through the Codex, Claude Code and Grok Build
  dialects, in-process against a real HTTP Core (`tests/test_tool_category_matching.py`). A native-name rule (`tool: Bash`)
  still constrains only the agent that spells the tool that way, and a non-matching case stays silent (no false allow claims).
* Grok dialect: translation of every documented event on hand-built fixtures, ignored session-teardown / subagent events,
  deny/ask/allow shapes, no output where Grok discards stdout, `PostToolUse` `additionalContext` shape.
* Deferred context: delivered once after the first tool call, newest pack wins, per session, survives a Core outage,
  expires after an hour, files 0600/0700, withheld when the binding disables injection, post-compact pack deferred.
* Installer: owns one file, leaves neighbouring hook files untouched, refuses `~` and `~/.grok`.
* Schemas: capability declaration (with `inject_mode`) and every Grok request validate.
* Regression: Codex and Claude Code behaviour unchanged (their existing suites pass).
* Frontend: `tsc` clean; 540 existing tests pass. No test was added for the new `tool_category` form validation.

## What was NOT done: native verification

`grok login` has not been done on this machine (`grok -p` answers "Not signed in"), so **no Grok Build conversation has been
run**. All Gates for Grok Build (G-PROMPT, G-DENY, G-INJECT, G-EVIDENCE, G-CHECKPOINT, compaction) are **unrun**. The
script is ready: `--agent grok-build --prepare` then `--run`; the first real run is also where the unconfirmed facts get
settled (tool input keys, `toolResult` shape, hook firing in `-p`, whether deferred `additionalContext` reaches the model,
which of `run_terminal_command` / `run_terminal_cmd` the toolset uses).

## Known limits introduced

* Deferred delivery means no pack before the first tool call of a turn (ADR 0018 section 3).
* Tool tables are per dialect; an unseen tool name falls to `other`.
* Grok is fail-open on hook crashes/timeouts; only the explicit deny blocks.
* `request_sha256` of host-hook Guard answers changed (adds `tool_category`).

## Addendum: device-level registration ([ADR 0019](adr/0019-device-level-hook-registration.md))

Requested by the user on 2026-10-13 (hooks per device, not per project). Built and component-tested:

* `hook --device-binding FILE`; the project file shrinks to ownership data and cannot override the device's Core, keys or policy;
  no project file means no action; replay goes through the device file.
* `hooks install|uninstall|status --scope user [--device-binding FILE] --confirm-user-scope`: one owned entry set per agent in the
  user-level file, the user's own hooks untouched, timestamped backup, redirectable through `CLAUDE_CONFIG_DIR` / `CODEX_HOME` /
  `GROK_HOME`, note when Grok and Claude Code are both registered.
* The gate script accepts `--hook-scope user --confirm-user-scope`.

Tests (`tests/test_hosthook_device_scope.py`, gate-prepare): 22 cases on scratch directories with HOME and the config-dir variables
redirected; the real home directory stays empty in every case. They cover coexistence with pre-existing user hooks, idempotence,
backups, symlink refusal, a bare directory being left alone, fail-closed behaviour for a bound directory, the same device binding
serving all three dialects against a real HTTP Core, and replay through the device file.

Not done: no real agent was started with a user-level registration, and the real `~/.claude`, `~/.codex`, `~/.grok` were not touched.
Whether each agent honours the config-dir variable and loads the user file is exactly what the first real run will show.
