"""Long-term Memory: items, durable async jobs, query traces, engine status.

This migration follows m0008_resolutions (P2-02). It was cut as
m0008_memory and renumbered to m0009 because P2 claimed m0008 first. It creates only new P4
tables. It does not touch the P0/P1/P2/P3 truth tables, does not reinterpret a
single historical Event payload, and does not create any table owned by P3
(Checkpoint / Context Pack) or P6 (Dashboard).

Design notes that the schema itself has to enforce, because a CHECK is the only
refusal an out-of-band writer cannot route around:

* `memory_items` rows are never deleted. `delete` is a lifecycle transition that
  leaves a tombstone plus its provenance, so `trg_memory_items_no_delete` fires
  on a direct `DELETE` exactly as it does on the P1 Evidence tables.
* Identity columns on `memory_items` and `memory_jobs` are immutable after
  insert. A retry or a lifecycle change may move status, revision, lease, retry
  counters, receipt and timestamps; it may not re-point a job at a different
  source, bank, document or content hash, which is what would let a "retry"
  smuggle new content past the enqueue-time eligibility check.
* `status` on a job is the frozen `PENDING/RUNNING/DONE/FAILED` vocabulary from
  the design. `kind` is `retain`, `delete` or `invalidate`; there is one table and
  one queue, not a parallel delete queue. `invalidate` is the engine's own soft
  retire, which is what design §9's `obsolete` is.
* `(source_event_id, kind)` is unique, so re-running the recovery scan over the
  same committed Event can never create a second job for it.
* Traces and attempts are append-only, so `last_error_code` on the job row is a
  summary, never the only record of what happened.
"""

from __future__ import annotations

NAME = "m0011_memory"
VERSION = 11

#: Frozen by ADR 0014 §2. Repeated here because SQLite cannot CHECK against a
#: constant declared in Python: the constraint has to live in the table.
_MEMORY_TYPES = ("'CONFIRMED_DECISION'", "'STABLE_PROJECT_FACT'", "'RESOLVED_ISSUE'",
                 "'VERIFIED_OUTCOME'", "'FAILURE_LESSON'")
_MEMORY_TYPE_LIST = ", ".join(_MEMORY_TYPES)
#: Reused by `memory_items` and `memory_jobs`; kept here so the two can never
#: drift into accepting different vocabularies for the same concept.
_MEMORY_TYPE_CHECK = (f"CHECK(memory_type IN ({_MEMORY_TYPE_LIST}))")

STATEMENTS = (
    # -- Memory items ------------------------------------------------------
    # `origin_event_id` is the Event the *content* came from, which is not always
    # `source_event_id`: for a Decision the latter is the `rule.approved` Event that
    # created the row, while the former is the Event the Rule version itself was
    # written from. Storing only `source_event_id` meant every Decision's provenance
    # pointed at the approval rather than at where the decision came from, and the
    # worker papered over the gap by substituting `source_event_id` — so an Inspector
    # walking back from a Decision always stopped one Event too late.
    """
    CREATE TABLE memory_items (
        memory_id            TEXT PRIMARY KEY,
        bank_id              TEXT NOT NULL,
        scope_kind           TEXT NOT NULL CHECK(scope_kind IN ('global','project')),
        project_id           TEXT REFERENCES projects(project_id),
        task_id              TEXT REFERENCES tasks(task_id),
        memory_type          TEXT NOT NULL CHECK(memory_type IN (
                                'CONFIRMED_DECISION','STABLE_PROJECT_FACT','RESOLVED_ISSUE',
                                'VERIFIED_OUTCOME','FAILURE_LESSON')),
        document_id          TEXT NOT NULL UNIQUE,
        source_event_id      TEXT NOT NULL UNIQUE REFERENCES events(event_id),
        evidence_ids_json    TEXT NOT NULL CHECK(json_valid(evidence_ids_json)),
        origin_rule_id       TEXT REFERENCES rules(rule_id),
        origin_rule_version  INTEGER,
        origin_event_id      TEXT REFERENCES events(event_id),
        content_sha256       TEXT NOT NULL CHECK(length(content_sha256)=64),
        sanitized_text       TEXT NOT NULL,
        status               TEXT NOT NULL CHECK(status IN ('ACTIVE','OBSOLETE','DELETED')),
        pinned               INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
        revision             INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
        engine_state         TEXT NOT NULL DEFAULT 'PENDING_RETAIN' CHECK(engine_state IN (
                                'PENDING_RETAIN','RETAINED','RETAIN_FAILED',
                                'DELETE_PENDING','DELETED','DELETE_FAILED')),
        receipt_json         TEXT CHECK(receipt_json IS NULL OR json_valid(receipt_json)),
        last_error_code      TEXT,
        safe_error_summary   TEXT,
        remote_deleted_at    TEXT,
        lifecycle_reason     TEXT,
        last_recalled_at     TEXT,
        recall_count         INTEGER NOT NULL DEFAULT 0 CHECK(recall_count>=0),
        created_at           TEXT NOT NULL,
        updated_at           TEXT NOT NULL,
        -- A global memory has no project and no task; a project memory names
        -- its project. Without this, a job could be enqueued into
        -- `jasmine-global` from a task-scoped source and bypass project
        -- isolation entirely (design §7: global holds only explicitly
        -- authorised global content).
        CHECK ((scope_kind='global'  AND project_id IS NULL     AND task_id IS NULL) OR
               (scope_kind='project' AND project_id IS NOT NULL)),
        CHECK ((origin_rule_id IS NULL AND origin_rule_version IS NULL) OR
               (origin_rule_id IS NOT NULL AND origin_rule_version IS NOT NULL)),
        FOREIGN KEY(origin_rule_id, origin_rule_version)
            REFERENCES rule_versions(rule_id, version)
    )
    """,
    "CREATE INDEX ix_memory_items_project ON memory_items(project_id, status, created_at, memory_id)",
    "CREATE INDEX ix_memory_items_bank ON memory_items(bank_id, status, created_at)",
    "CREATE INDEX ix_memory_items_type ON memory_items(memory_type, status, created_at)",
    "CREATE INDEX ix_memory_items_recall ON memory_items(status, pinned, last_recalled_at)",
    # Supporting sources. One retained document may be backed by more than one
    # committed Event, and ADR 0014 §5 requires a de-duplicated recall hit to
    # keep *all* of its legal provenance rather than whichever one won the merge.
    """
    CREATE TABLE memory_item_sources (
        memory_id      TEXT NOT NULL REFERENCES memory_items(memory_id),
        source_event_id TEXT NOT NULL REFERENCES events(event_id),
        role           TEXT NOT NULL CHECK(role IN ('PRIMARY','SUPPORTING')),
        created_at     TEXT NOT NULL,
        PRIMARY KEY (memory_id, source_event_id)
    )
    """,
    "CREATE INDEX ix_memory_item_sources_event ON memory_item_sources(source_event_id)",
    # -- Durable async jobs ------------------------------------------------
    """
    CREATE TABLE memory_jobs (
        job_id             TEXT PRIMARY KEY,
        kind               TEXT NOT NULL CHECK(kind IN ('retain','delete','invalidate')),
        memory_id          TEXT NOT NULL REFERENCES memory_items(memory_id),
        source_event_id    TEXT NOT NULL REFERENCES events(event_id),
        project_id         TEXT REFERENCES projects(project_id),
        task_id            TEXT REFERENCES tasks(task_id),
        memory_type        TEXT NOT NULL CHECK(memory_type IN (
                             'CONFIRMED_DECISION','STABLE_PROJECT_FACT','RESOLVED_ISSUE',
                             'VERIFIED_OUTCOME','FAILURE_LESSON')),
        bank_id            TEXT NOT NULL,
        document_id        TEXT NOT NULL,
        content_sha256     TEXT NOT NULL CHECK(length(content_sha256)=64),
        status             TEXT NOT NULL CHECK(status IN ('PENDING','RUNNING','DONE','FAILED')),
        retry_count        INTEGER NOT NULL DEFAULT 0 CHECK(retry_count>=0),
        max_attempts       INTEGER NOT NULL CHECK(max_attempts>=1),
        next_attempt_at    TEXT NOT NULL,
        lease_owner        TEXT,
        lease_expires_at   TEXT,
        -- Monotonic claim generation: bumped on every claim and never reset.
        -- `retry_count` is the *budget* and `request_retry` zeroes it, so a
        -- `(lease_owner, retry_count)` fence cannot tell two claims by the same
        -- worker apart once the budget has been rewound — the older claim then
        -- passes and can commit the newer attempt's job with the older receipt.
        claim_generation   INTEGER NOT NULL DEFAULT 0,
        last_error_code    TEXT,
        safe_error_summary TEXT,
        receipt_json       TEXT CHECK(receipt_json IS NULL OR json_valid(receipt_json)),
        created_at         TEXT NOT NULL,
        updated_at         TEXT NOT NULL,
        completed_at       TEXT,
        -- A lease always has an expiry: a claimed job with no deadline is a job
        -- that is lost forever if the worker dies.
        CHECK (lease_owner IS NULL OR lease_expires_at IS NOT NULL),
        CHECK (status <> 'DONE' OR completed_at IS NOT NULL)
    )
    """,
    # One job per committed source Event per kind. This is the uniqueness that
    # makes the recovery scan idempotent: a crash between "Truth committed" and
    # "job inserted", or a re-scan of the same Event, cannot produce a duplicate.
    """
    CREATE UNIQUE INDEX ux_memory_jobs_source ON memory_jobs(source_event_id, kind)
    """,
    "CREATE INDEX ix_memory_jobs_due ON memory_jobs(status, next_attempt_at, job_id)",
    "CREATE INDEX ix_memory_jobs_lease ON memory_jobs(status, lease_expires_at, job_id)",
    "CREATE INDEX ix_memory_jobs_project ON memory_jobs(project_id, status, created_at, job_id)",
    "CREATE INDEX ix_memory_jobs_memory ON memory_jobs(memory_id, kind, created_at)",
    # -- Append-only job history ------------------------------------------
    """
    CREATE TABLE memory_job_attempts (
        attempt_id         TEXT PRIMARY KEY,
        job_id             TEXT NOT NULL REFERENCES memory_jobs(job_id),
        attempt_number     INTEGER NOT NULL CHECK(attempt_number>=1),
        phase              TEXT NOT NULL CHECK(phase IN (
                               'CLAIMED','RETAINED','DELETED','INVALIDATED','FAILED','ABANDONED')),
        outcome            TEXT NOT NULL CHECK(outcome IN ('SUCCESS','RETRY','FAILED')),
        error_code         TEXT,
        safe_error_summary TEXT,
        receipt_json       TEXT CHECK(receipt_json IS NULL OR json_valid(receipt_json)),
        lease_owner        TEXT NOT NULL,
        started_at         TEXT NOT NULL,
        finished_at        TEXT,
        UNIQUE (job_id, attempt_number, phase)
    )
    """,
    "CREATE INDEX ix_memory_job_attempts_job ON memory_job_attempts(job_id, attempt_number)",
    # -- Recall / Reflect traces ------------------------------------------
    """
    CREATE TABLE memory_query_traces (
        trace_id            TEXT PRIMARY KEY,
        mode                TEXT NOT NULL CHECK(mode IN ('recall','reflect')),
        status              TEXT NOT NULL CHECK(status IN ('ok','empty','degraded','skipped')),
        project_id          TEXT REFERENCES projects(project_id),
        task_id             TEXT REFERENCES tasks(task_id),
        session_id          TEXT REFERENCES sessions(session_id),
        actor_id            TEXT NOT NULL REFERENCES actors(actor_id),
        query_sha256        TEXT NOT NULL CHECK(length(query_sha256)=64),
        query_length        INTEGER NOT NULL CHECK(query_length>=0),
        requested_types_json TEXT NOT NULL CHECK(json_valid(requested_types_json)),
        banks_json          TEXT NOT NULL CHECK(json_valid(banks_json)),
        filtered_json       TEXT NOT NULL CHECK(json_valid(filtered_json)),
        budget_tokens       INTEGER CHECK(budget_tokens IS NULL OR budget_tokens>=0),
        used_tokens         INTEGER NOT NULL DEFAULT 0 CHECK(used_tokens>=0),
        authority_refs_json TEXT NOT NULL CHECK(json_valid(authority_refs_json)),
        state_revision      INTEGER,
        error_code          TEXT,
        safe_error_summary  TEXT,
        created_at          TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_memory_query_traces_project ON memory_query_traces(project_id, created_at, trace_id)",
    "CREATE INDEX ix_memory_query_traces_task ON memory_query_traces(task_id, created_at, trace_id)",
    """
    CREATE TABLE memory_query_trace_items (
        result_id            TEXT PRIMARY KEY,
        trace_id             TEXT NOT NULL REFERENCES memory_query_traces(trace_id),
        memory_id            TEXT REFERENCES memory_items(memory_id),
        bank_id              TEXT,
        engine_result_id     TEXT,
        document_id          TEXT,
        memory_type          TEXT,
        scope_kind           TEXT,
        project_id           TEXT REFERENCES projects(project_id),
        source_event_ids_json TEXT NOT NULL CHECK(json_valid(source_event_ids_json)),
        evidence_ids_json    TEXT NOT NULL CHECK(json_valid(evidence_ids_json)),
        origin_rule_id       TEXT,
        origin_rule_version  INTEGER,
        rank                 INTEGER NOT NULL CHECK(rank>=0),
        token_estimate       INTEGER NOT NULL CHECK(token_estimate>=0),
        text_sha256          TEXT NOT NULL CHECK(length(text_sha256)=64),
        included             INTEGER NOT NULL CHECK(included IN (0,1)),
        historical           INTEGER NOT NULL CHECK(historical IN (0,1)),
        obsolete             INTEGER NOT NULL CHECK(obsolete IN (0,1)),
        filtered_reason      TEXT,
        created_at           TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_memory_query_trace_items_trace ON memory_query_trace_items(trace_id, rank)",
    "CREATE INDEX ix_memory_query_trace_items_memory ON memory_query_trace_items(memory_id, created_at)",
    # -- Engine status (no credentials, ever) -----------------------------
    """
    CREATE TABLE memory_engine_status (
        engine_id          TEXT PRIMARY KEY CHECK(engine_id='hindsight'),
        origin_host        TEXT NOT NULL,
        configured         INTEGER NOT NULL CHECK(configured IN (0,1)),
        api_version        TEXT,
        features_json      TEXT CHECK(features_json IS NULL OR json_valid(features_json)),
        last_check_ok      INTEGER CHECK(last_check_ok IS NULL OR last_check_ok IN (0,1)),
        last_attempt_at    TEXT,
        last_success_at    TEXT,
        last_error_code    TEXT,
        safe_error_summary TEXT,
        updated_at         TEXT NOT NULL
    )
    """,
    # -- Project authorisation (only populated in `acl` deployment mode) ---
    """
    CREATE TABLE memory_project_actors (
        project_id          TEXT NOT NULL REFERENCES projects(project_id),
        actor_id            TEXT NOT NULL REFERENCES actors(actor_id),
        granted_by_event_id TEXT NOT NULL REFERENCES events(event_id),
        created_at          TEXT NOT NULL,
        PRIMARY KEY (project_id, actor_id)
    )
    """,
    "CREATE INDEX ix_memory_project_actors_actor ON memory_project_actors(actor_id)",
    # -- Triggers ---------------------------------------------------------
    # A tombstone is the point of `delete`: the row, its provenance and its
    # source Events outlive the remote document.
    """
    CREATE TRIGGER trg_memory_items_no_delete
    BEFORE DELETE ON memory_items
    BEGIN
        SELECT RAISE(ABORT, 'memory items are never deleted; use the DELETED tombstone state');
    END
    """,
    """
    CREATE TRIGGER trg_memory_items_no_replace
    BEFORE INSERT ON memory_items
    WHEN EXISTS (SELECT 1 FROM memory_items WHERE memory_id = NEW.memory_id)
    BEGIN
        SELECT RAISE(ABORT, 'memory items are append-only: memory_id already exists');
    END
    """,
    # `content_sha256` is pinned as well as the text: a retry that silently
    # changed the body would keep the same document_id and overwrite the remote
    # document with content the eligibility check never saw.
    """
    CREATE TRIGGER trg_memory_items_identity_immutable
    BEFORE UPDATE OF memory_id, bank_id, scope_kind, project_id, task_id, memory_type,
                     document_id, source_event_id, content_sha256, sanitized_text,
                     evidence_ids_json, created_at
    ON memory_items
    BEGIN
        SELECT RAISE(ABORT, 'memory item identity is immutable');
    END
    """,
    """
    CREATE TRIGGER trg_memory_jobs_no_delete
    BEFORE DELETE ON memory_jobs
    BEGIN
        SELECT RAISE(ABORT, 'memory jobs are never deleted; FAILED is the terminal state');
    END
    """,
    """
    CREATE TRIGGER trg_memory_jobs_no_replace
    BEFORE INSERT ON memory_jobs
    WHEN EXISTS (SELECT 1 FROM memory_jobs WHERE job_id = NEW.job_id)
    BEGIN
        SELECT RAISE(ABORT, 'memory jobs are append-only: job_id already exists');
    END
    """,
    """
    CREATE TRIGGER trg_memory_jobs_identity_immutable
    BEFORE UPDATE OF job_id, kind, memory_id, source_event_id, project_id, task_id,
                     memory_type, bank_id, document_id, content_sha256, max_attempts, created_at
    ON memory_jobs
    BEGIN
        SELECT RAISE(ABORT, 'memory job identity is immutable; a retry may not change what is retained');
    END
    """,
    """
    CREATE TRIGGER trg_memory_job_attempts_immutable_update
    BEFORE UPDATE ON memory_job_attempts
    BEGIN
        SELECT RAISE(ABORT, 'memory job attempts are append-only');
    END
    """,
    """
    CREATE TRIGGER trg_memory_job_attempts_immutable_delete
    BEFORE DELETE ON memory_job_attempts
    BEGIN
        SELECT RAISE(ABORT, 'memory job attempts are append-only');
    END
    """,
    """
    CREATE TRIGGER trg_memory_job_attempts_no_replace
    BEFORE INSERT ON memory_job_attempts
    WHEN EXISTS (SELECT 1 FROM memory_job_attempts WHERE attempt_id = NEW.attempt_id)
    BEGIN
        SELECT RAISE(ABORT, 'memory job attempts are append-only: attempt_id already exists');
    END
    """,
    """
    CREATE TRIGGER trg_memory_query_traces_immutable_update
    BEFORE UPDATE ON memory_query_traces
    BEGIN
        SELECT RAISE(ABORT, 'memory query traces are append-only');
    END
    """,
    """
    CREATE TRIGGER trg_memory_query_traces_immutable_delete
    BEFORE DELETE ON memory_query_traces
    BEGIN
        SELECT RAISE(ABORT, 'memory query traces are append-only');
    END
    """,
    """
    CREATE TRIGGER trg_memory_query_traces_no_replace
    BEFORE INSERT ON memory_query_traces
    WHEN EXISTS (SELECT 1 FROM memory_query_traces WHERE trace_id = NEW.trace_id)
    BEGIN
        SELECT RAISE(ABORT, 'memory query traces are append-only: trace_id already exists');
    END
    """,
    """
    CREATE TRIGGER trg_memory_query_trace_items_immutable_update
    BEFORE UPDATE ON memory_query_trace_items
    BEGIN
        SELECT RAISE(ABORT, 'memory query trace items are append-only');
    END
    """,
    """
    CREATE TRIGGER trg_memory_query_trace_items_immutable_delete
    BEFORE DELETE ON memory_query_trace_items
    BEGIN
        SELECT RAISE(ABORT, 'memory query trace items are append-only');
    END
    """,
    """
    CREATE TRIGGER trg_memory_query_trace_items_no_replace
    BEFORE INSERT ON memory_query_trace_items
    WHEN EXISTS (SELECT 1 FROM memory_query_trace_items WHERE result_id = NEW.result_id)
    BEGIN
        SELECT RAISE(ABORT, 'memory query trace items are append-only: result_id already exists');
    END
    """,
    """
    CREATE TRIGGER trg_memory_item_sources_immutable_update
    BEFORE UPDATE ON memory_item_sources
    BEGIN
        SELECT RAISE(ABORT, 'memory item sources are append-only');
    END
    """,
    """
    CREATE TRIGGER trg_memory_item_sources_immutable_delete
    BEFORE DELETE ON memory_item_sources
    BEGIN
        SELECT RAISE(ABORT, 'memory item sources are append-only');
    END
    """,
)
