#!/usr/bin/env python3
"""Prove the P2 read paths are not strictly side-effect free.

The handoff for the P6-02 read slice says explicitly: "GET 方法不是零副作用证明
... 接入前核对恢复语义，使用真实 Core 前后状态检查，不能把未知恢复动作悄悄提升为
严格只读".

Core's interpretation reads call `InterpretationStore._recover` (list and detail)
and `MaintenanceStore.recover` (interpretation detail, review detail, resolution
preview). `_recover` marks an interpretation whose provider deadline passed
without a result row as `INTERRUPTED`, which is an INSERT. This probe reproduces
that write on a **copy** of a fixture database:

1. copy the fixture DB to a scratch path;
2. create the precondition the API cannot create — an interpretation with a past
   deadline and no result row (as a crashed provider would leave behind);
3. digest `interpretation_results` and `interpretations`;
4. perform a plain `GET /v1/interpretations` with a read-only token;
5. digest again and report exactly which rows appeared.

It never touches the operator's Core: only the copy it makes itself.

Usage::

    python3 scripts/p6/p6_lazy_recovery_probe.py \
      --db /private/tmp/p6-p2/core.db \
      --core http://127.0.0.1:8796 \
      --token-file /private/tmp/p6-p2/tokens.json --token-key p2_read \
      --out validation-runs/p6/<run-id>
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _serve_copy(db_path: Path, workspace: str | None):
    """Serve `db_path` on a free loopback port so the read hits the mutated copy."""
    import os
    import socket
    import threading
    from http.server import ThreadingHTTPServer

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "src"))
    from jasmine_core.api.server import Application, _Handler

    if workspace:
        os.environ["JASMINE_CORE_WORKSPACE_ROOT"] = workspace
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    app = Application(db_path, log=lambda _line: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    httpd.app = app  # type: ignore[attr-defined]
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02},
                     daemon=True).start()
    return f"http://127.0.0.1:{port}", httpd


def digest(conn: sqlite3.Connection, table: str) -> dict[str, object]:
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    return {"rows": len(rows), "sample": [dict(row) for row in rows[:3]]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p6_lazy_recovery_probe")
    parser.add_argument("--db", required=True, help="source fixture db (never opened read-write)")
    parser.add_argument("--core", help="optional: an already-running Core serving this exact copy")
    parser.add_argument("--workspace", help="workspace root the probe-Core should report")
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--token-key", default="p2_read")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    copy = out / "recovery-probe.db"
    shutil.copyfile(args.db, copy)

    token = json.loads(Path(args.token_file).read_text(encoding="utf-8"))[args.token_key]
    lines: list[str] = [
        "# Lazy-recovery probe: a GET that writes",
        "",
        f"- source fixture: {args.db}",
        f"- working copy:   {copy} (the probe only ever touches this copy)",
        f"- Core under test: {args.core or 'started in-process by this probe'}",
        f"- at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
    ]

    core_url = args.core
    server = None
    if core_url is None:
        core_url, server = _serve_copy(copy, args.workspace)
        lines.append(f"- probe Core started in-process at {core_url} over the copy")
        lines.append("")

    conn = sqlite3.connect(copy)
    conn.row_factory = sqlite3.Row
    try:
        # 1. Precondition: an interpretation whose provider deadline passed and
        #    whose result row does not exist - what a crashed provider leaves
        #    behind. Both tables are append-only by trigger (UPDATE/DELETE are
        #    refused), so the probe INSERTS a derived row rather than editing one.
        import hashlib
        import sys as _sys

        _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
        from jasmine_core import ids as core_ids

        source = conn.execute(
            "SELECT * FROM interpretations ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if source is None:
            print("fixture has no interpretation to derive from", file=sys.stderr)
            return 2
        data = dict(source)
        ident = core_ids.new_id("int")
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
            "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        data.update(
            interpretation_id=ident,
            deadline_at=past,
            created_at=past,
            parent_interpretation_id=None,
            # The unique key is (processor_actor_id, event_id, input_hash,
            # config_digest); a fresh input_hash keeps the derived row legal.
            input_hash=hashlib.sha256(f"p6-lazy-recovery-probe-{ident}".encode()).hexdigest(),
        )
        with conn:
            conn.execute(
                f"INSERT INTO interpretations ({', '.join(data)}) "
                f"VALUES ({', '.join('?' for _ in data)})",
                tuple(data.values()),
            )
        lines += [
            "## Precondition created by INSERT (the API cannot create it)",
            f"- derived interpretation: `{ident}` (same event/project/task as `{source['interpretation_id']}`)",
            f"- `deadline_at` = {past}, and no `interpretation_results` row exists for it",
            "- the chain is append-only by trigger, so this is an INSERT, not an edit",
            "",
        ]

        before = {table: digest(conn, table) for table in ("interpretation_results", "interpretations")}
        lines += ["## Before the GET", "```json",
                  json.dumps(before, ensure_ascii=False, indent=1), "```", ""]

        # 2. A plain authenticated read, exactly what the dashboard issues.
        request = urllib.request.Request(
            f"{core_url}/v1/interpretations?limit=50",
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                status, body = response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            status, body = error.code, json.loads(error.read())
        returned = [item["interpretation_id"] for item in body.get("items", [])]
        rewound = next((item for item in body.get("items", [])
                        if item["interpretation_id"] == ident), None)
        lines += [
            "## The GET",
            f"- `GET /v1/interpretations?limit=50` -> HTTP {status}, {len(returned)} items",
            f"- the rewound row now reports status `{rewound['status'] if rewound else 'absent'}` "
            f"with error_code `{rewound['error_code'] if rewound else '-'}`",
            "",
        ]

        after = {table: digest(conn, table) for table in ("interpretation_results", "interpretations")}
        lines += ["## After the GET", "```json",
                  json.dumps(after, ensure_ascii=False, indent=1), "```", ""]

        appeared = after["interpretation_results"]["rows"] - before["interpretation_results"]["rows"]
        row = conn.execute(
            "SELECT interpretation_id, status, error_code, completed_at FROM interpretation_results "
            "WHERE interpretation_id=?", (ident,)
        ).fetchone()
        verdict = (
            "WRITE CONFIRMED: the GET inserted an `INTERRUPTED` result row"
            if appeared == 1 and row is not None and row["status"] == "INTERRUPTED"
            else "no write observed"
        )
        lines += [
            "## Verdict",
            f"- `interpretation_results` rows: {before['interpretation_results']['rows']} -> "
            f"{after['interpretation_results']['rows']} (+{appeared})",
            f"- inserted row: `{dict(row) if row else None}`",
            f"- **{verdict}**",
            "",
            "## What this means for the dashboard",
            "- The dashboard issues only GET, and the UI never promotes this recovery to a",
            "  user action or a new fact; the row it returns is what Core already decided.",
            "- It is nevertheless a Core-side write performed by a read, so P6-02 does not",
            "  describe these endpoints as strictly read-only.",
            "- On an intact fixture (`/private/tmp/p6-p2`) the before/after digests of every",
            "  P2 table EXCEPT the authentication bookkeeping (`audit_log`, and",
            "  `api_keys.last_used_at`) are identical: Core audits every request and stamps the",
            "  key that authenticated it, so those always change even for reads. The recovery",
            "  write above needs the crashed-provider precondition.",
            "",
        ]
    finally:
        conn.close()
        if server is not None:
            server.shutdown()
            server.server_close()

    (out / "lazy-recovery-proof.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[-14:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
