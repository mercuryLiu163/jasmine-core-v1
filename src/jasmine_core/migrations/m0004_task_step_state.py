"""Task/Step state projections and P0 open -> ACTIVE upgrade (ADR 0006)."""

NAME = "m0004_task_step_state"
VERSION = 4

STATEMENTS = (
    "ALTER TABLE tasks ADD COLUMN acceptance_criteria_json TEXT NOT NULL DEFAULT '{\"requirements\":[]}' CHECK (json_valid(acceptance_criteria_json))",
    "UPDATE tasks SET status = 'ACTIVE' WHERE status = 'open'",
    """CREATE TRIGGER trg_tasks_status_insert BEFORE INSERT ON tasks
       WHEN NEW.status NOT IN ('ACTIVE','BLOCKED','ACCEPTED','CANCELLED')
       BEGIN SELECT RAISE(ABORT, 'invalid task status'); END""",
    """CREATE TRIGGER trg_tasks_status_update BEFORE UPDATE OF status ON tasks
       WHEN NEW.status NOT IN ('ACTIVE','BLOCKED','ACCEPTED','CANCELLED')
       BEGIN SELECT RAISE(ABORT, 'invalid task status'); END""",
    """CREATE TABLE steps (
       step_id TEXT PRIMARY KEY,
       task_id TEXT NOT NULL REFERENCES tasks(task_id),
       title TEXT NOT NULL,
       description TEXT NOT NULL DEFAULT '',
       status TEXT NOT NULL DEFAULT 'PLANNED' CHECK (status IN
         ('PLANNED','IN_PROGRESS','EXECUTED','VERIFIED','ACCEPTED','FAILED','BLOCKED','STALE','SKIPPED')),
       revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
       acceptance_criteria_json TEXT NOT NULL CHECK (json_valid(acceptance_criteria_json)),
       source_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
       created_at TEXT NOT NULL,
       updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX ix_steps_task ON steps(task_id, created_at)",
    """CREATE TRIGGER trg_steps_source_event_kind BEFORE INSERT ON steps
       WHEN (SELECT event_type FROM events WHERE event_id = NEW.source_event_id) IS NOT 'step.created'
       BEGIN SELECT RAISE(ABORT, 'a step must reference a step.created event'); END""",
)
