#!/usr/bin/env python3
"""G04's real multiuser-ACL evidence, over P4's own authenticated HTTP API.

The taskbook asks for more than "an ungranted actor is refused":

    启用真实multiuser ACL时另验证无权actor直接访问A后端拒绝，并明确mode；
    不是只检查前端显示

So this drives the real server with real keys and records, per check, the method,
the path, who acted, whether they were granted, the status the API actually
returned, and the error code. Two properties make the refusals mean something:

* the deployment mode is read back **from inside the server process**, so the
  evidence cannot claim `acl` while running `single_user` -- where
  `single_user` deliberately treats one operator as trusted across their own
  projects and no refusal is expected at all;
* every refusal is paired with a *success by the same key on a project it does
  own*. A 404 from a key that gets 404 everywhere is a broken route, not an
  authorization decision, and ADR 0003 freezes the refusal as `not_found`
  precisely so a refusal cannot become a project-enumeration oracle.

Keys are issued, used and discarded in process. Nothing here prints or returns a
token value: only key ids, labels and scopes.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from http.server import ThreadingHTTPServer  # noqa: E402

from jasmine_core import SCHEMA_VERSION, auth, db, ids, registry  # noqa: E402
from jasmine_core.api.server import Application, _Handler  # noqa: E402
from jasmine_core.migrations import migrate  # noqa: E402
from jasmine_core.models import NewObject  # noqa: E402
from jasmine_core.objects import ObjectStore  # noqa: E402


def _issue(conn: sqlite3.Connection, *, actor_id: str, label: str,
           scopes: list[str]) -> dict[str, Any]:
    """A real key through P4's own issuer; the token never leaves this function."""
    issued = auth.Auth(conn).issue_key(actor_id=actor_id, label=label, scopes=scopes)
    return {"token": issued["token"], "key_id": issued.get("key_id"),
            "label": label, "scopes": scopes, "actor_id": actor_id}


def _call(port: int, token: str, method: str, path: str) -> tuple[int, Any]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                     headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"null")
        except ValueError:
            return exc.code, {"raw": raw[:200].decode("utf-8", "replace")}


def _error_code(body: Any) -> str | None:
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"].get("code")
    return None


def collect_acl_evidence(*, db_path: Path, bank_suffix: str) -> dict[str, Any]:
    """Every check, with the observation behind it. No key value is returned."""
    db_path = Path(db_path).resolve()
    conn = db.connect(db_path)
    migrate(conn)
    host = ids.new_id("hst")
    owner = ids.new_id("act")
    outsider = ids.new_id("act")
    agent_actor = ids.new_id("act")
    with db.transaction(conn):
        reg = registry.Registry(conn)
        reg.upsert_host(host, display_name="p4 acl gate host")
        reg.upsert_actor(owner, kind="human", display_name="owner of A", home_host_id=host)
        reg.upsert_actor(outsider, kind="human", display_name="no grant", home_host_id=host)
        reg.upsert_actor(agent_actor, kind="agent", display_name="agent", home_host_id=host)

    objects = ObjectStore(conn, schema_version=SCHEMA_VERSION)
    project_a = objects.create(NewObject.project({"name": f"{bank_suffix}-A",
                                                  "host_id": host}),
                               actor_id=owner)["object"]["project_id"]
    project_b = objects.create(NewObject.project({"name": f"{bank_suffix}-B",
                                                  "host_id": host}),
                               actor_id=owner)["object"]["project_id"]
    # A real Event to attribute the grant to, because
    # `memory_project_actors.granted_by_event_id` references one and a row with no
    # provenance is not a grant. `project.created` carries no `project_id`, so the
    # provenance is the first Event that does belong to project A.
    objects.create(NewObject.task({"project_id": project_a, "title": "acl subject",
                                   "host_id": host}), actor_id=owner)
    grant_event = conn.execute(
        "SELECT event_id FROM events WHERE project_id=? ORDER BY seq LIMIT 1",
        (project_a,)).fetchone()["event_id"]
    with db.transaction(conn):
        conn.execute("INSERT INTO memory_project_actors (project_id, actor_id, "
                     "granted_by_event_id, created_at) VALUES (?,?,?,?)",
                     (project_a, owner, grant_event, "2026-01-01T00:00:00.000000Z"))
    owner_grant = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_project_actors WHERE project_id=? AND actor_id=?",
        (project_b, owner)).fetchone()["n"]
    outsider_grant = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_project_actors WHERE actor_id=?",
        (outsider,)).fetchone()["n"]

    keys = {
        "owner": _issue(conn, actor_id=owner, label="owner-key",
                        scopes=["memory:read"]),
        "outsider": _issue(conn, actor_id=outsider, label="outsider-key",
                           scopes=["memory:read"]),
        "agent": _issue(conn, actor_id=agent_actor, label="agent-key",
                        scopes=["memory:read"]),
    }
    # An agent key asking to curate Memory is refused at issuance, and that refusal
    # is part of the ACL story rather than a separate concern.
    agent_manage_refused = False
    agent_manage_error = None
    try:
        _issue(conn, actor_id=agent_actor, label="agent-manage-key",
               scopes=["memory:read", "memory:manage"])
    except Exception as exc:
        agent_manage_refused = True
        agent_manage_error = f"{type(exc).__name__}: {exc}"[:200]

    # The mode the *server* will see. Set before the server starts, because
    # `scope.deployment_mode()` reads the environment on every construction.
    previous_mode = os.environ.get("JASMINE_MEMORY_DEPLOYMENT_MODE")
    os.environ["JASMINE_MEMORY_DEPLOYMENT_MODE"] = "acl"
    app = Application(str(db_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.app = app
    server.daemon_threads = True
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()

    checks: list[dict[str, Any]] = []

    def record(check_id: str, *, who: str, method: str, path: str, granted: bool,
               status: int, body: Any, expect_status: int,
               expect_code: str | None, note: str) -> None:
        code = _error_code(body)
        checks.append({
            "check": check_id, "actor": who, "method": method, "path": path,
            "granted": granted, "status": status, "error_code": code,
            "expected_status": expect_status, "expected_error_code": expect_code,
            "passed": status == expect_status and (code == expect_code
                                                   if expect_code else code is None),
            "note": note,
        })

    try:
        # The mode the server itself resolves, asked for inside the handler's own
        # view rather than read from the caller's environment -- otherwise the
        # evidence could claim `acl` while the server ran `single_user`.
        status, body = _call(port, keys["owner"]["token"], "GET", "/v1/memory/health")
        deployment = body.get("deployment") if isinstance(body, dict) else None
        server_mode = deployment.get("mode") if isinstance(deployment, dict) else None
        acl_enforced = bool(deployment.get("acl_enforced")) if isinstance(deployment, dict) else False
        record("server-reports-acl-mode", who="owner", method="GET",
               path="/v1/memory/health", granted=True, status=status,
               body={"mode": server_mode, "acl_enforced": acl_enforced},
               expect_status=200, expect_code=None,
               note="the running server's own `describe()`, not the caller's environment")
        record("server-resolved-acl", who="server", method="GET", path="/v1/memory/health",
               granted=True, status=200 if (server_mode == "acl" and acl_enforced) else 500,
               body={"mode": server_mode, "acl_enforced": acl_enforced},
               expect_status=200, expect_code=None,
               note="without this the rest of the evidence would be meaningless: under "
                    "`single_user` no refusal is expected at all")

        status, body = _call(port, keys["owner"]["token"], "GET",
                             f"/v1/memory/items?project_id={project_a}")
        record("owner-reads-own-project", who="owner", method="GET",
               path=f"/v1/memory/items?project_id={project_a}", granted=True,
               status=status, body=body, expect_status=200, expect_code=None,
               note="the same key that is refused below succeeds here")

        status, body = _call(port, keys["outsider"]["token"], "GET",
                             f"/v1/memory/items?project_id={project_a}")
        record("ungranted-actor-refused-project-a", who="outsider", method="GET",
               path=f"/v1/memory/items?project_id={project_a}", granted=False,
               status=status, body=body, expect_status=404,
               expect_code="project_not_found",
               note="ADR 0003: a 404, not 403, so a refusal is not an enumeration oracle")

        status, body = _call(port, keys["owner"]["token"], "GET",
                             f"/v1/memory/items?project_id={project_b}")
        record("owner-cross-project-isolation", who="owner", method="GET",
               path=f"/v1/memory/items?project_id={project_b}", granted=False,
               status=status, body=body, expect_status=404,
               expect_code="project_not_found",
               note="a project the actor may not use is refused even for the owner of A")

        status, body = _call(port, keys["agent"]["token"], "GET",
                             f"/v1/memory/items?project_id={project_a}")
        record("agent-read-refused-without-grant", who="agent", method="GET",
               path=f"/v1/memory/items?project_id={project_a}", granted=False,
               status=status, body=body, expect_status=404,
               expect_code="project_not_found",
               note="an agent with memory:read still needs a grant")

        # And a route that does not exist, so the refusals above are distinguishable
        # from a broken path.
        status, body = _call(port, keys["owner"]["token"], "GET", "/v1/memory/nonexistent")
        record("unknown-route-is-distinguishable", who="owner", method="GET",
               path="/v1/memory/nonexistent", granted=True, status=status, body=body,
               expect_status=404, expect_code="route_not_found",
               note="a 404 that is not `project_not_found` proves the refusals are "
                    "authorization decisions, not routing accidents")

        # The strongest read-back available: run the whole battery once more under
        # `single_user`, where `ProjectAccess.require` returns immediately for every
        # project in the database. The outsider must then be *allowed*, which is what
        # makes the refusals above attributable to `acl` rather than to the route, the
        # key, or the project not existing.
        previous = os.environ.get("JASMINE_MEMORY_DEPLOYMENT_MODE")
        os.environ["JASMINE_MEMORY_DEPLOYMENT_MODE"] = "single_user"
        single_app = Application(str(db_path))
        single_server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        single_server.app = single_app
        single_server.daemon_threads = True
        single_port = single_server.server_address[1]
        single_thread = threading.Thread(target=single_server.serve_forever,
                                         kwargs={"poll_interval": 0.02}, daemon=True)
        single_thread.start()
        try:
            status, body = _call(single_port, keys["outsider"]["token"], "GET",
                                 f"/v1/memory/items?project_id={project_a}")
            record("single-user-allows-the-same-request", who="outsider", method="GET",
                   path=f"/v1/memory/items?project_id={project_a}", granted=False,
                   status=status, body=body, expect_status=200, expect_code=None,
                   note="the identical request under `single_user`; so the refusal above "
                        "is the deployment mode and nothing else")
        finally:
            single_server.shutdown()
            single_server.server_close()
            single_thread.join(timeout=5)
            single_app.close()
            os.environ["JASMINE_MEMORY_DEPLOYMENT_MODE"] = previous or "acl"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        app.close()
        if previous_mode is None:
            os.environ.pop("JASMINE_MEMORY_DEPLOYMENT_MODE", None)
        else:
            os.environ["JASMINE_MEMORY_DEPLOYMENT_MODE"] = previous_mode
        conn.close()

    return {
        "deployment_mode": "acl",
        "server_saw_mode": server_mode,
        "projects": {"A": project_a, "B": project_b},
        "grants": {"owner_on_A": 1, "owner_on_B": owner_grant,
                   "any_for_outsider": outsider_grant},
        "keys": {name: {"label": k["label"], "scopes": k["scopes"],
                        "key_id": k["key_id"]} for name, k in keys.items()},
        "agent_manage_key_refused_at_issuance": agent_manage_refused,
        "agent_manage_key_error": agent_manage_error,
        "checks": checks,
        "all_passed": all(c["passed"] for c in checks) and agent_manage_refused,
    }


def main() -> int:
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="existing database; a temporary one is made if absent")
    parser.add_argument("--namespace", default="p4acl")
    args = parser.parse_args()
    if args.db:
        evidence = collect_acl_evidence(db_path=Path(args.db), bank_suffix=args.namespace)
    else:
        with tempfile.TemporaryDirectory(prefix="p4-acl-") as tmp:
            evidence = collect_acl_evidence(db_path=Path(tmp) / "core.db",
                                            bank_suffix=args.namespace)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
