# ADR 0019: Device-level hook registration and device binding

Status: Proposed. Amends ADR 0017 sections 6 and 16. **Provides no proof of native effect.**
Date: 2026-10-13. Schema: unchanged. Decided by the user on 2026-10-13: hooks are managed per device, not per project.

## 1. Decision

* One set of hooks per agent per machine, registered in the agent's own user-level configuration
  (`~/.claude/settings.json`, `~/.codex/hooks.json`, `~/.grok/hooks/jasmine-core.json`).
* The registered command names a **device binding** (`--device-binding FILE`): Core address, `host_id`, key files,
  `on_unavailable`, `spool_dir`, `timeout_seconds`, default `inject_context`. Same field rules as before; no
  project/task/step fields.
* A **project file** (`<project>/.jasmine/host-binding.json`, found by the existing upward search from the agent's
  reported working directory) shrinks to ownership data: `version`, `project_id`, `task_id`, `step_id`,
  optional `inject_context`. When a device binding applies, the project file may **not** set any other field
  (`BindingError`, the call is treated like any unusable binding, so `tool.before` is denied under the default
  policy). A project may set `inject_context: false`; it cannot force injection on.
* **No project file found: the hook does nothing** and prints the agent's empty response. A directory that has not
  joined Jasmine is not affected by the global hook at all, not even a spool entry.
* Project file present, Core unreachable or the device file unusable: the device `on_unavailable` policy applies
  (default deny for `tool.before`), unchanged from ADR 0017.
* Full project bindings (everything in one file, project-level registration) keep working.
* Spooled events remember the device file, so `hook replay --device-binding FILE` and the automatic drain resend through it.

## 2. Registration (`jasmine-core hooks ... --scope user`)

* `--scope user` is never the default. Install needs `--confirm-user-scope` (it edits the user's own agent
  configuration for every project on the machine) and `--device-binding FILE`, which must load.
* The target is the single file the dialect names (`INSTALL["user"]`), honouring the agent's own config-dir variable
  (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `GROK_HOME`) so that tests and gate runs can redirect it. A symlinked file or
  directory is refused.
* Before changing an existing file the installer copies it, byte for byte, to `<file>.jasmine-backup-<UTC stamp>`
  (0600, never overwritten). Uninstall also leaves a backup. Grok's own file did not exist before: no backup.
* Only entries the installer owns (command `... hook --agent <name> --binding|--device-binding <path>`) are added,
  replaced or removed. The user's own hooks, other tools' hooks and unrelated keys are untouched and keep their order;
  ours is appended.
* Install is idempotent. `status --scope user` reports events, the device binding and binding problems; it reads
  the file only and does not show that the agent loads it.

## 3. Coexistence with the user's own hooks

Claude Code, Codex and Grok Build all run every matching hook of every registered source (user, project, plugin); none
replaces another. Ours neither rewrites tool input nor stops others. Consequences:

* A deny from any hook wins, so another hook allowing a call does not override ours (and ours does not override theirs).
* Both may add context; the agent delivers both.
* Cost: one extra process per hook event per project that has a project file; one cheap process (no network) elsewhere.
* Agents that load another agent's hook file: Grok Build reads `~/.claude/settings.json`. With both registered the
  Claude Code command also starts under Grok; its dialect does not recognise Grok's payload and records nothing.
  `hooks install --scope user` prints a note when it detects both.
* Codex re-asks for trust when a hook's content hash changes; user-level registration needs one review. Grok trusts user hooks
  always. Claude Code has no per-hook review.

## 4. Risks

* A repository can ship a `.jasmine/host-binding.json`. With the device form it can only choose which Project/Task the
  events are attributed to **in the user's own Core**; it cannot change Core, keys or policy. Attribution is still
  chosen by a file inside the repository: do not open untrusted repositories in directories where you also run Jasmine
  bound tasks, and treat the project file like any other committed configuration. (A device-side directory map would
  remove this; not done.)
* A global hook makes the failure policy global for bound projects: a stopped Core denies tool calls in every bound project.
* Nothing here shows that any agent loads user-level registrations the way the documentation says; see the results documents.
