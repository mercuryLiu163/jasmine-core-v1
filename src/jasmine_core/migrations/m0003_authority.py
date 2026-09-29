"""Versioned Authority content and its current lifecycle projection."""

NAME = "m0003_authority"
VERSION = 3

STATEMENTS = (
    """CREATE TABLE rules (
        rule_id TEXT PRIMARY KEY,
        rule_key TEXT NOT NULL,
        scope_kind TEXT NOT NULL CHECK(scope_kind IN ('global','project','task')),
        project_id TEXT REFERENCES projects(project_id),
        task_id TEXT REFERENCES tasks(task_id),
        current_version INTEGER NOT NULL CHECK(current_version >= 1),
        status TEXT NOT NULL CHECK(status IN ('PROPOSED','ACTIVE','SUPERSEDED','RETIRED')),
        revision INTEGER NOT NULL CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(rule_id,current_version) REFERENCES rule_versions(rule_id,version)
            DEFERRABLE INITIALLY DEFERRED,
        CHECK ((scope_kind='global' AND project_id IS NULL AND task_id IS NULL)
            OR (scope_kind='project' AND project_id IS NOT NULL AND task_id IS NULL)
            OR (scope_kind='task' AND project_id IS NOT NULL AND task_id IS NOT NULL))
    )""",
    """CREATE TABLE rule_versions (
        rule_id TEXT NOT NULL REFERENCES rules(rule_id),
        version INTEGER NOT NULL CHECK(version >= 1),
        kind TEXT NOT NULL CHECK(kind IN ('RULE','DECISION','ACCEPTANCE')),
        severity TEXT NOT NULL CHECK(severity IN ('HARD','NORMAL')),
        enforcement TEXT NOT NULL CHECK(enforcement IN ('CONTEXT','DENY','CONFIRM','VERIFY')),
        content TEXT NOT NULL,
        matcher_json TEXT NOT NULL CHECK(json_valid(matcher_json)),
        origin_event_id TEXT NOT NULL REFERENCES events(event_id),
        change_event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        created_at TEXT NOT NULL,
        PRIMARY KEY(rule_id, version)
    )""",
    """CREATE TABLE rule_changes (
        change_event_id TEXT PRIMARY KEY REFERENCES events(event_id),
        rule_id TEXT NOT NULL REFERENCES rules(rule_id),
        version INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('PROPOSED','ACTIVE','SUPERSEDED','RETIRED')),
        revision INTEGER NOT NULL CHECK(revision >= 1),
        superseded_version INTEGER,
        changed_at TEXT NOT NULL,
        FOREIGN KEY(rule_id, version) REFERENCES rule_versions(rule_id, version),
        FOREIGN KEY(rule_id, superseded_version) REFERENCES rule_versions(rule_id, version)
    )""",
    "CREATE INDEX ix_rules_scope ON rules(scope_kind, project_id, task_id, status)",
    "CREATE UNIQUE INDEX ux_rules_global_key ON rules(rule_key) WHERE scope_kind='global'",
    "CREATE UNIQUE INDEX ux_rules_project_key ON rules(project_id, rule_key) WHERE scope_kind='project'",
    "CREATE UNIQUE INDEX ux_rules_task_key ON rules(task_id, rule_key) WHERE scope_kind='task'",
    "CREATE INDEX ix_rule_changes_rule ON rule_changes(rule_id, revision)",
    """CREATE TRIGGER trg_rule_versions_immutable_update BEFORE UPDATE ON rule_versions
        BEGIN SELECT RAISE(ABORT, 'rule_versions are append-only'); END""",
    """CREATE TRIGGER trg_rule_versions_immutable_delete BEFORE DELETE ON rule_versions
        BEGIN SELECT RAISE(ABORT, 'rule_versions are append-only'); END""",
    """CREATE TRIGGER trg_rule_versions_no_replace BEFORE INSERT ON rule_versions
        WHEN EXISTS(SELECT 1 FROM rule_versions WHERE rule_id=NEW.rule_id AND version=NEW.version)
        BEGIN SELECT RAISE(ABORT, 'rule_versions are append-only'); END""",
    """CREATE TRIGGER trg_rule_changes_immutable_update BEFORE UPDATE ON rule_changes
        BEGIN SELECT RAISE(ABORT, 'rule_changes are append-only'); END""",
    """CREATE TRIGGER trg_rule_changes_immutable_delete BEFORE DELETE ON rule_changes
        BEGIN SELECT RAISE(ABORT, 'rule_changes are append-only'); END""",
    """CREATE TRIGGER trg_rule_changes_no_replace BEFORE INSERT ON rule_changes
        WHEN EXISTS(SELECT 1 FROM rule_changes WHERE change_event_id=NEW.change_event_id)
        BEGIN SELECT RAISE(ABORT, 'rule_changes are append-only'); END""",
)
