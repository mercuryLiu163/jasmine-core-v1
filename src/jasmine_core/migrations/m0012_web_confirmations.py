"""Append-only Web structured human confirmation provenance (ADR 0015)."""
NAME = "m0012_web_confirmations"
VERSION = 12
STATEMENTS = (
    """CREATE TABLE web_confirmation_preparations (
      preparation_id TEXT PRIMARY KEY REFERENCES events(event_id),
      actor_id TEXT NOT NULL REFERENCES actors(actor_id),
      key_id TEXT NOT NULL REFERENCES api_keys(key_id),
      host_id TEXT NOT NULL REFERENCES hosts(host_id),
      project_id TEXT NOT NULL REFERENCES projects(project_id),
      task_id TEXT NOT NULL REFERENCES tasks(task_id),
      step_id TEXT REFERENCES steps(step_id),
      expected_task_revision INTEGER NOT NULL CHECK(expected_task_revision>0),
      expected_step_revision INTEGER CHECK(expected_step_revision>0),
      target_status TEXT NOT NULL CHECK(target_status IN ('VERIFIED','ACCEPTED')),
      confirmed_rule_id TEXT REFERENCES rules(rule_id),
      confirmed_rule_version INTEGER CHECK(confirmed_rule_version>0),
      fingerprint_sha256 TEXT NOT NULL REFERENCES workspace_fingerprints(fingerprint_sha256),
      review_json TEXT NOT NULL CHECK(json_valid(review_json)),
      review_sha256 TEXT NOT NULL CHECK(length(review_sha256)=64),
      semantic_request_sha256 TEXT NOT NULL CHECK(length(semantic_request_sha256)=64),
      created_at TEXT NOT NULL, expires_at TEXT NOT NULL, sampled_at TEXT NOT NULL,
      response_json TEXT NOT NULL CHECK(json_valid(response_json)),
      CHECK((step_id IS NULL AND expected_step_revision IS NULL AND target_status='ACCEPTED') OR
            (step_id IS NOT NULL AND expected_step_revision IS NOT NULL)),
      CHECK((confirmed_rule_id IS NULL AND confirmed_rule_version IS NULL) OR
            (confirmed_rule_id IS NOT NULL AND confirmed_rule_version IS NOT NULL)),
      FOREIGN KEY(confirmed_rule_id,confirmed_rule_version) REFERENCES rule_versions(rule_id,version)
    )""",
    """CREATE TABLE web_confirmation_submissions (
      command_event_id TEXT PRIMARY KEY REFERENCES events(event_id),
      preparation_id TEXT NOT NULL UNIQUE REFERENCES web_confirmation_preparations(preparation_id),
      source_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
      evidence_id TEXT NOT NULL UNIQUE REFERENCES evidence(evidence_id),
      actor_id TEXT NOT NULL REFERENCES actors(actor_id),
      key_id TEXT NOT NULL REFERENCES api_keys(key_id),
      host_id TEXT NOT NULL REFERENCES hosts(host_id),
      semantic_request_sha256 TEXT NOT NULL CHECK(length(semantic_request_sha256)=64),
      request_id TEXT NOT NULL, response_json TEXT NOT NULL CHECK(json_valid(response_json)),
      created_at TEXT NOT NULL,
      CHECK(command_event_id<>preparation_id AND source_event_id<>preparation_id AND source_event_id<>command_event_id)
    )""",
) + tuple(statement for table, primary in (
    ("web_confirmation_preparations", "preparation_id"),
    ("web_confirmation_submissions", "command_event_id"),
) for statement in (
    f"CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END",
    f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END",
    f"CREATE TRIGGER trg_{table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {primary}=NEW.{primary}) BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END",
))
