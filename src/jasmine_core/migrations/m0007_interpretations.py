"""P2 candidate processing: reservations and immutable outcomes, never Truth."""
NAME = 'm0007_interpretations'
VERSION = 7
STATEMENTS = (
    """CREATE TABLE interpretations (
        interpretation_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL REFERENCES events(event_id),
        project_id TEXT REFERENCES projects(project_id),
        task_id TEXT REFERENCES tasks(task_id), session_id TEXT REFERENCES sessions(session_id),
        host_id TEXT NOT NULL REFERENCES hosts(host_id),
        source_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
        source_actor_kind TEXT NOT NULL CHECK(source_actor_kind IN ('human','agent','system')), source_type TEXT NOT NULL CHECK(source_type IN ('USER_EXPLICIT','SYSTEM_CONFIG','AGENT_PROPOSED')), 
        processor_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
        extractor_id TEXT NOT NULL, extractor_model TEXT NOT NULL,
        extractor_version TEXT NOT NULL, provider TEXT NOT NULL,
        prompt_version TEXT NOT NULL, output_schema_version TEXT NOT NULL,
        schema_digest TEXT NOT NULL, config_digest TEXT NOT NULL,
        input_hash TEXT NOT NULL, config_json TEXT NOT NULL CHECK(json_valid(config_json)),
        parent_interpretation_id TEXT REFERENCES interpretations(interpretation_id),
        created_at TEXT NOT NULL, deadline_at TEXT NOT NULL,
        UNIQUE(processor_actor_id,event_id,input_hash,config_digest)
    )""",
    """CREATE TABLE interpretation_results (
        interpretation_id TEXT PRIMARY KEY REFERENCES interpretations(interpretation_id),
        status TEXT NOT NULL CHECK(status IN ('EXTRACTED','FAILED','INTERRUPTED')),
        raw_result_json TEXT, candidates_json TEXT CHECK(candidates_json IS NULL OR json_valid(candidates_json)), confidence REAL,
        error_code TEXT, completed_at TEXT NOT NULL,
        CHECK((status='EXTRACTED' AND raw_result_json IS NOT NULL AND candidates_json IS NOT NULL AND confidence IS NOT NULL AND confidence BETWEEN 0 AND 1
               AND error_code IS NULL) OR (status!='EXTRACTED' AND candidates_json IS NULL
               AND confidence IS NULL AND error_code IS NOT NULL))
    )""",
    """CREATE TABLE interpretation_request_keys (
        processor_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
        idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
        interpretation_id TEXT NOT NULL REFERENCES interpretations(interpretation_id),
        PRIMARY KEY(processor_actor_id,idempotency_key)
    )""",
    'CREATE INDEX interpretations_context ON interpretations(project_id,task_id,created_at,interpretation_id)',
    'CREATE INDEX interpretations_event ON interpretations(event_id,created_at,interpretation_id)',
    *tuple(f"CREATE TRIGGER {table}_no_{verb.lower()} BEFORE {verb} ON {table} "
           "BEGIN SELECT RAISE(ABORT, 'interpretation chain is immutable'); END"
           for table in ('interpretations','interpretation_results','interpretation_request_keys')
           for verb in ('UPDATE','DELETE')),
)
