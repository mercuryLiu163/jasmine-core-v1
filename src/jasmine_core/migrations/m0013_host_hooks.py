"""Host Hook Protocol delivery receipts (ADR 0017).

Forward-only. One append-only table: the idempotency receipt of a delivery,
holding the request hash, the response returned for it, and the recorded trust
level. It changes no existing table.
"""
NAME = "m0013_host_hooks"
VERSION = 13

STATEMENTS = (
    """CREATE TABLE host_hook_deliveries (
        delivery_id TEXT PRIMARY KEY CHECK(length(delivery_id) BETWEEN 1 AND 200),
        request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
        event TEXT NOT NULL CHECK(event IN ('session.start','prompt.submit','tool.before',
            'tool.after','context.compact','turn.stop','session.end')),
        agent_name TEXT NOT NULL,
        host_id TEXT NOT NULL REFERENCES hosts(host_id),
        actor_id TEXT NOT NULL REFERENCES actors(actor_id),
        trust TEXT NOT NULL CHECK(trust IN ('NATIVE_HOOK','PROXY_OBSERVED','AGENT_REPORTED')),
        response_json TEXT NOT NULL CHECK(json_valid(response_json)),
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX ix_host_hook_deliveries_created ON host_hook_deliveries(created_at)",
    """CREATE TRIGGER trg_host_hook_deliveries_immutable_update BEFORE UPDATE ON host_hook_deliveries
        BEGIN SELECT RAISE(ABORT,'host_hook_deliveries are append-only'); END""",
    """CREATE TRIGGER trg_host_hook_deliveries_immutable_delete BEFORE DELETE ON host_hook_deliveries
        BEGIN SELECT RAISE(ABORT,'host_hook_deliveries are append-only'); END""",
    """CREATE TRIGGER trg_host_hook_deliveries_no_replace BEFORE INSERT ON host_hook_deliveries
        WHEN EXISTS(SELECT 1 FROM host_hook_deliveries WHERE delivery_id=NEW.delivery_id)
        BEGIN SELECT RAISE(ABORT,'host_hook_deliveries are append-only'); END""",
)
