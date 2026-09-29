"""Immutable product Evidence bound to server-computed workspace snapshots.

This migration follows m0004_task_step_state. It does not modify the P0/P1
truth tables or reinterpret historical Event payloads.
"""

NAME = "m0005_evidence_fingerprint"
VERSION = 5

STATEMENTS = (
    """CREATE TABLE workspace_fingerprints (
        fingerprint_sha256 TEXT PRIMARY KEY CHECK(length(fingerprint_sha256)=64),
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        root_path TEXT NOT NULL,
        snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),
        complete INTEGER NOT NULL CHECK(complete IN (0,1)),
        source_event_id TEXT NOT NULL REFERENCES events(event_id),
        first_captured_at TEXT NOT NULL
    )""",
    "CREATE INDEX ix_fingerprints_project ON workspace_fingerprints(project_id)",
    """CREATE TABLE evidence (
        evidence_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        step_id TEXT REFERENCES steps(step_id),
        kind TEXT NOT NULL CHECK(kind IN ('COMMAND_RESULT','BUILD','TEST','DEVICE_TEST',
            'FILE_CHANGE','ARTIFACT','REVIEW','USER_CONFIRMATION')),
        status TEXT NOT NULL CHECK(status IN ('PASS','FAIL','INFO')),
        result_json TEXT NOT NULL CHECK(json_valid(result_json)),
        source_event_id TEXT NOT NULL REFERENCES events(event_id),
        change_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        fingerprint_sha256 TEXT NOT NULL REFERENCES workspace_fingerprints(fingerprint_sha256),
        artifact_uri TEXT,
        artifact_sha256 TEXT,
        actor_id TEXT NOT NULL REFERENCES actors(actor_id),
        host_id TEXT NOT NULL REFERENCES hosts(host_id),
        session_id TEXT REFERENCES sessions(session_id),
        turn_id TEXT,
        tool_use_id TEXT,
        tool_name TEXT,
        command_sha256 TEXT,
        producer_config_sha256 TEXT,
        confirmed_rule_id TEXT REFERENCES rules(rule_id),
        confirmed_rule_version INTEGER,
        task_revision INTEGER NOT NULL CHECK(task_revision>=1),
        step_revision INTEGER CHECK(step_revision>=1),
        created_at TEXT NOT NULL,
        CHECK ((step_id IS NULL AND step_revision IS NULL) OR
               (step_id IS NOT NULL AND step_revision IS NOT NULL)),
        CHECK ((confirmed_rule_id IS NULL AND confirmed_rule_version IS NULL) OR
               (confirmed_rule_id IS NOT NULL AND confirmed_rule_version IS NOT NULL)),
        FOREIGN KEY(confirmed_rule_id,confirmed_rule_version)
            REFERENCES rule_versions(rule_id,version)
    )""",
    "CREATE INDEX ix_evidence_task ON evidence(task_id,created_at)",
    "CREATE INDEX ix_evidence_step ON evidence(step_id,created_at)",
    "CREATE INDEX ix_evidence_source ON evidence(source_event_id)",
    """CREATE TABLE rule_verification_requirements (
        rule_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        requirements_json TEXT NOT NULL CHECK(json_valid(requirements_json)),
        source_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        actor_id TEXT NOT NULL REFERENCES actors(actor_id),
        created_at TEXT NOT NULL,
        PRIMARY KEY(rule_id,version),
        FOREIGN KEY(rule_id,version) REFERENCES rule_versions(rule_id,version)
    )""",
    """CREATE UNIQUE INDEX ux_evidence_tool_use ON evidence(session_id,turn_id,tool_use_id,kind)
        WHERE tool_use_id IS NOT NULL""",
    """CREATE TRIGGER trg_evidence_immutable_update BEFORE UPDATE ON evidence
        BEGIN SELECT RAISE(ABORT,'evidence is append-only'); END""",
    """CREATE TRIGGER trg_evidence_immutable_delete BEFORE DELETE ON evidence
        BEGIN SELECT RAISE(ABORT,'evidence is append-only'); END""",
    """CREATE TRIGGER trg_evidence_no_replace BEFORE INSERT ON evidence
        WHEN EXISTS(SELECT 1 FROM evidence WHERE evidence_id=NEW.evidence_id)
        BEGIN SELECT RAISE(ABORT,'evidence is append-only'); END""",
    """CREATE TRIGGER trg_fingerprint_immutable_update BEFORE UPDATE ON workspace_fingerprints
        BEGIN SELECT RAISE(ABORT,'workspace_fingerprints are append-only'); END""",
    """CREATE TRIGGER trg_fingerprint_immutable_delete BEFORE DELETE ON workspace_fingerprints
        BEGIN SELECT RAISE(ABORT,'workspace_fingerprints are append-only'); END""",
    """CREATE TRIGGER trg_fingerprint_no_replace BEFORE INSERT ON workspace_fingerprints
        WHEN EXISTS(SELECT 1 FROM workspace_fingerprints
                    WHERE fingerprint_sha256=NEW.fingerprint_sha256)
        BEGIN SELECT RAISE(ABORT,'workspace_fingerprints are append-only'); END""",
    """CREATE TRIGGER trg_rule_requirements_immutable_update BEFORE UPDATE ON rule_verification_requirements
        BEGIN SELECT RAISE(ABORT,'rule_verification_requirements are append-only'); END""",
    """CREATE TRIGGER trg_rule_requirements_immutable_delete BEFORE DELETE ON rule_verification_requirements
        BEGIN SELECT RAISE(ABORT,'rule_verification_requirements are append-only'); END""",
    """CREATE TRIGGER trg_rule_requirements_no_replace BEFORE INSERT ON rule_verification_requirements
        WHEN EXISTS(SELECT 1 FROM rule_verification_requirements
                    WHERE rule_id=NEW.rule_id AND version=NEW.version)
        BEGIN SELECT RAISE(ABORT,'rule_verification_requirements are append-only'); END""",
)
