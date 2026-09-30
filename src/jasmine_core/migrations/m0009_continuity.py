"""Immutable checkpoint projections and exact historical Resume receipts."""
NAME = "m0009_continuity"
VERSION = 9

_SHA = "length({0})=64 AND {0} NOT GLOB '*[^0-9a-f]*'"
STATEMENTS = (
    """CREATE TABLE checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        command_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        source_event_id TEXT NOT NULL REFERENCES events(event_id),
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        host_id TEXT NOT NULL REFERENCES hosts(host_id),
        actor_id TEXT NOT NULL REFERENCES actors(actor_id),
        session_id TEXT REFERENCES sessions(session_id),
        reason TEXT NOT NULL CHECK(reason IN ('PRE_COMPACT','SESSION_STOP','HANDOFF','BLOCKED','STEP_VERIFIED','MANUAL')),
        trigger_provenance_json TEXT NOT NULL CHECK(json_valid(trigger_provenance_json) AND length(CAST(trigger_provenance_json AS BLOB))<=16384),
        projection_version TEXT NOT NULL CHECK(projection_version='jasmine.task-snapshot.v1'),
        projection_json TEXT NOT NULL CHECK(json_valid(projection_json) AND length(CAST(projection_json AS BLOB))<=262144),
        projection_digest TEXT NOT NULL CHECK(%s),
        task_revision INTEGER NOT NULL CHECK(typeof(task_revision)='integer' AND task_revision BETWEEN 1 AND 9223372036854775807),
        full_context_digest TEXT NOT NULL CHECK(%s),
        workspace_json TEXT NOT NULL CHECK(json_valid(workspace_json) AND length(CAST(workspace_json AS BLOB))<=2097152),
        workspace_digest TEXT NOT NULL CHECK(%s),
        note_json TEXT CHECK(note_json IS NULL OR (json_valid(note_json) AND json_type(note_json,'$.text') IS 'text' AND length(json_extract(note_json,'$.text')) BETWEEN 1 AND 2000 AND length(CAST(note_json AS BLOB))<=16384)),
        created_at TEXT NOT NULL
    )""" % (_SHA.format('projection_digest'), _SHA.format('full_context_digest'), _SHA.format('workspace_digest')),
    """CREATE TABLE checkpoint_request_keys (
        actor_id TEXT NOT NULL REFERENCES actors(actor_id),
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL CHECK(%s),
        checkpoint_id TEXT NOT NULL REFERENCES checkpoints(checkpoint_id),
        PRIMARY KEY(actor_id,idempotency_key)
    )""" % _SHA.format('request_hash'),
    """CREATE TABLE resume_results (
        resume_id TEXT PRIMARY KEY,
        command_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        source_event_id TEXT NOT NULL REFERENCES events(event_id),
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        host_id TEXT NOT NULL REFERENCES hosts(host_id),
        actor_id TEXT NOT NULL REFERENCES actors(actor_id),
        session_id TEXT REFERENCES sessions(session_id),
        checkpoint_id TEXT REFERENCES checkpoints(checkpoint_id),
        request_hash TEXT NOT NULL CHECK(%s),
        result_json TEXT NOT NULL CHECK(json_valid(result_json) AND length(CAST(result_json AS BLOB))<=262144),
        created_at TEXT NOT NULL
    )""" % _SHA.format('request_hash'),
    """CREATE TABLE resume_request_keys (
        actor_id TEXT NOT NULL REFERENCES actors(actor_id),
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL CHECK(%s),
        resume_id TEXT NOT NULL REFERENCES resume_results(resume_id),
        PRIMARY KEY(actor_id,idempotency_key)
    )""" % _SHA.format('request_hash'),
    "CREATE INDEX checkpoints_task_selection ON checkpoints(task_id,command_event_id)",
) + tuple(
    f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
    f"BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END"
    for table in ('checkpoints','checkpoint_request_keys','resume_results','resume_request_keys')
    for operation in ('UPDATE','DELETE')
) + tuple(
    f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "
    f"WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) "
    f"BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END"
    for table,condition in (
        ('checkpoints','checkpoint_id=NEW.checkpoint_id'),
        ('resume_results','resume_id=NEW.resume_id'),
        ('checkpoint_request_keys','actor_id=NEW.actor_id AND idempotency_key=NEW.idempotency_key'),
        ('resume_request_keys','actor_id=NEW.actor_id AND idempotency_key=NEW.idempotency_key'),
    )
)
