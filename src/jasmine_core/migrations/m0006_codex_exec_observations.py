"""Link immutable CLI JSONL observations to the original PostToolUse Evidence.

The native PostToolUse record remains unchanged. A separate system observation
may supply a result only when Core validates the complete second source.
"""

NAME = "m0006_codex_exec_observations"
VERSION = 6

STATEMENTS = (
    "ALTER TABLE evidence ADD COLUMN related_posttool_event_id TEXT REFERENCES events(event_id)",
    "DROP INDEX ux_evidence_tool_use",
    """CREATE UNIQUE INDEX ux_evidence_native_tool_use
        ON evidence(session_id,turn_id,tool_use_id,kind)
        WHERE tool_use_id IS NOT NULL AND related_posttool_event_id IS NULL""",
    """CREATE UNIQUE INDEX ux_evidence_observed_post
        ON evidence(related_posttool_event_id)
        WHERE related_posttool_event_id IS NOT NULL""",
)
