"""Exact rendered Context packs and immutable actor-key receipts."""
NAME = 'm0010_context'
VERSION = 10

_SHA = "length({0})=64 AND {0} NOT GLOB '*[^0-9a-f]*'"
_JSON = "json_valid({0}) AND length(CAST({0} AS BLOB))<={1}"
STATEMENTS = (
    """CREATE TABLE context_packs (
        context_pack_id TEXT PRIMARY KEY,
        command_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        source_event_id TEXT NOT NULL REFERENCES events(event_id),
        actor_id TEXT NOT NULL REFERENCES actors(actor_id),
        host_id TEXT NOT NULL REFERENCES hosts(host_id),
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        session_id TEXT REFERENCES sessions(session_id),
        current_step_id TEXT REFERENCES steps(step_id),
        reason TEXT NOT NULL CHECK(reason IN ('SESSION_START','USER_PROMPT','POST_COMPACT','HANDOFF','MANUAL')),
        trigger_provenance_json TEXT NOT NULL CHECK(%s),
        projection_version TEXT NOT NULL CHECK(projection_version='jasmine.task-snapshot.v1'),
        projection_json TEXT NOT NULL CHECK(%s),
        truth_digest TEXT NOT NULL CHECK(%s),
        selector_checkpoint_id TEXT REFERENCES checkpoints(checkpoint_id),
        selector_event_seq INTEGER CHECK(selector_event_seq IS NULL OR (typeof(selector_event_seq)='integer' AND selector_event_seq BETWEEN 1 AND 9223372036854775807)),
        resume_comparison_json TEXT NOT NULL CHECK(%s),
        workspace_json TEXT NOT NULL CHECK(%s),
        workspace_digest TEXT NOT NULL CHECK(%s),
        sample_window_json TEXT NOT NULL CHECK(%s),
        config_digest TEXT NOT NULL CHECK(%s),
        renderer_version TEXT NOT NULL CHECK(renderer_version='jasmine.context.v1'),
        rendered_content TEXT NOT NULL CHECK(length(CAST(rendered_content AS BLOB)) BETWEEN 1 AND 131072),
        rendered_sha256 TEXT NOT NULL CHECK(%s),
        rendered_utf8_bytes INTEGER NOT NULL CHECK(typeof(rendered_utf8_bytes)='integer' AND rendered_utf8_bytes BETWEEN 1 AND 131072),
        tokenizer_json TEXT NOT NULL CHECK(%s),
        token_count INTEGER NOT NULL CHECK(typeof(token_count)='integer' AND token_count BETWEEN 0 AND 2500),
        section_metrics_json TEXT NOT NULL CHECK(%s),
        omissions_json TEXT NOT NULL CHECK(%s),
        memory_json TEXT NOT NULL CHECK(%s),
        full_result_json TEXT NOT NULL CHECK(%s),
        created_at TEXT NOT NULL,
        CHECK((selector_checkpoint_id IS NULL AND selector_event_seq IS NULL) OR (selector_checkpoint_id IS NOT NULL AND selector_event_seq IS NOT NULL))
    )""" % (
        _JSON.format('trigger_provenance_json',16384),_JSON.format('projection_json',262144),
        _SHA.format('truth_digest'),_JSON.format('resume_comparison_json',262144),
        _JSON.format('workspace_json',2097152),_SHA.format('workspace_digest'),
        _JSON.format('sample_window_json',16384),_SHA.format('config_digest'),_SHA.format('rendered_sha256'),
        _JSON.format('tokenizer_json',16384),_JSON.format('section_metrics_json',262144),
        _JSON.format('omissions_json',262144),_JSON.format('memory_json',65536),
        _JSON.format('full_result_json',4194304)),
    """CREATE TABLE context_request_keys (
        actor_id TEXT NOT NULL REFERENCES actors(actor_id),
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL CHECK(%s),
        context_pack_id TEXT NOT NULL REFERENCES context_packs(context_pack_id),
        PRIMARY KEY(actor_id,idempotency_key)
    )""" % _SHA.format('request_hash'),
) + tuple(
    f"CREATE TRIGGER {table}_no_{op.lower()} BEFORE {op} ON {table} "
    f"BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END"
    for table in ('context_packs','context_request_keys') for op in ('UPDATE','DELETE')
) + tuple(
    f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "
    f"WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) "
    f"BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END"
    for table,condition in (
        ('context_packs','context_pack_id=NEW.context_pack_id'),
        ('context_request_keys','actor_id=NEW.actor_id AND idempotency_key=NEW.idempotency_key'),
    )
)
