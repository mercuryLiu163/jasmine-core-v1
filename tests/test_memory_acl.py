"""P4: the multiuser ACL the Gate's G04 leg asserts, tested below the HTTP layer.

`scripts/p4-memory-acl.py` drives the real API and the Gate runs it end to end.
These tests pin the two properties that make its evidence mean anything, because a
battery that passes for the wrong reason is worse than no battery:

  * `acl` is what actually refuses an ungranted actor, and `single_user` is what
    does not -- so a refusal cannot be attributed to the route, the key, or the
    project not existing;
  * the refusal is `not_found`, not `forbidden`, so it cannot be turned into a
    project-enumeration oracle (ADR 0003).
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from support import DbTestCase  # noqa: E402

from jasmine_core import errors, ids, registry  # noqa: E402
from jasmine_core.memory import scope  # noqa: E402
from jasmine_core.models import NewObject  # noqa: E402
from jasmine_core.objects import ObjectStore  # noqa: E402

SCHEMA = 11
STRANGER = "act_01K742SG00CBKP25A9ETPBTRMJ"


class AclFixture(DbTestCase):
    """Two projects, two actors, one grant -- the shape the battery needs."""

    def setUp(self) -> None:
        super().setUp()
        self.host = ids.new_id("hst")
        self.owner = ids.new_id("act")
        with self.conn:
            reg = registry.Registry(self.conn)
            reg.upsert_host(self.host, display_name="acl test host")
            reg.upsert_actor(self.owner, kind="human", home_host_id=self.host)
            reg.upsert_actor(STRANGER, kind="human", home_host_id=self.host)
        objects = ObjectStore(self.conn, schema_version=SCHEMA)
        self.project = objects.create(
            NewObject.project({"name": "A", "host_id": self.host}),
            actor_id=self.owner)["object"]["project_id"]
        self.other = objects.create(
            NewObject.project({"name": "B", "host_id": self.host}),
            actor_id=self.owner)["object"]["project_id"]
        # `project.created` carries no project_id, so each project's grant needs an
        # Event that belongs to it.
        for project_id in (self.project, self.other):
            objects.create(NewObject.task({"project_id": project_id, "title": "t",
                                           "host_id": self.host}), actor_id=self.owner)

    def _grant(self, project_id: str, actor_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO memory_project_actors (project_id, actor_id,"
                " granted_by_event_id, created_at) VALUES (?,?,?,?)",
                (project_id, actor_id,
                 self.conn.execute(
                     "SELECT event_id FROM events WHERE project_id=? ORDER BY seq LIMIT 1",
                     (project_id,)).fetchone()["event_id"],
                 "2026-01-01T00:00:00.000000Z"))

    def _access(self, mode: str) -> scope.ProjectAccess:
        return scope.ProjectAccess(self.conn, mode=mode)


class TheDeploymentModeDecidesWhoIsRefused(AclFixture):
    def test_an_ungranted_actor_is_refused_under_acl(self) -> None:
        with self.assertRaises(errors.NotFound) as caught:
            self._access(scope.ACL).require(project_id=self.project, actor_id=STRANGER)
        # `not_found`, not `forbidden`: ADR 0003 freezes 403 as a decision about
        # authority that the audit log records, and a 403 here would tell a stranger
        # the project exists.
        self.assertEqual(caught.exception.code, "project_not_found")

    def test_the_same_request_is_allowed_under_single_user(self) -> None:
        # Which is why the Gate's battery runs it twice: a refusal that also happened
        # under `single_user` would be about something other than authorization.
        self._access(scope.SINGLE_USER).require(project_id=self.project, actor_id=STRANGER)

    def test_a_grant_is_what_changes_the_answer(self) -> None:
        self._grant(self.project, STRANGER)
        self._access(scope.ACL).require(project_id=self.project, actor_id=STRANGER)

    def test_a_grant_for_another_project_does_not_help(self) -> None:
        self._grant(self.other, STRANGER)
        access = self._access(scope.ACL)
        access.require(project_id=self.other, actor_id=STRANGER)
        with self.assertRaises(errors.NotFound):
            access.require(project_id=self.project, actor_id=STRANGER)

    def test_may_read_agrees_with_require(self) -> None:
        access = self._access(scope.ACL)
        self.assertFalse(access.may_read(project_id=self.project, actor_id=STRANGER))
        self._grant(self.project, STRANGER)
        self.assertTrue(access.may_read(project_id=self.project, actor_id=STRANGER))

    def test_absent_project_is_refused_in_both_modes(self) -> None:
        # So a refusal alone never proves an authorization decision -- which is why
        # the battery also shows the same key succeeding on a project it owns.
        for mode in (scope.ACL, scope.SINGLE_USER):
            with self.subTest(mode=mode):
                with self.assertRaises(errors.NotFound):
                    self._access(mode).require(project_id=ids.new_id("prj"),
                                               actor_id=self.owner)


class TheModeIsReadFromTheEnvironment(unittest.TestCase):
    def test_the_environment_decides_it(self) -> None:
        with patch.dict(os.environ, {"JASMINE_MEMORY_DEPLOYMENT_MODE": "acl"}):
            self.assertEqual(scope.deployment_mode(), scope.ACL)
        with patch.dict(os.environ, {"JASMINE_MEMORY_DEPLOYMENT_MODE": "single_user"}):
            self.assertEqual(scope.deployment_mode(), scope.SINGLE_USER)
        # Absent means the documented trust premise, not an error.
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(scope.deployment_mode(), scope.SINGLE_USER)

    def test_an_unknown_mode_is_refused_rather_than_guessed(self) -> None:
        with patch.dict(os.environ, {"JASMINE_MEMORY_DEPLOYMENT_MODE": "everyone"}):
            with self.assertRaises(errors.InvalidRequest):
                scope.deployment_mode()


class AnAclDeploymentWithNoGrantsIsNotReady(AclFixture):
    def test_require_mode_ready_refuses_it(self) -> None:
        # `require_mode_ready` exists for exactly this: an `acl` deployment with an
        # empty grant table would otherwise lock out the operator who deployed it.
        with self.assertRaises(errors.MemoryDeploymentNotReady):
            self._access(scope.ACL).require_mode_ready()
        self._grant(self.project, self.owner)
        self._access(scope.ACL).require_mode_ready()
