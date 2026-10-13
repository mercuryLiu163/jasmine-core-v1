"""The published JSON Schemas (spec/schemas) agree with the implementation.

A small stdlib validator covers the schema keywords the spec files use, so real
requests built by the runtime and real responses from Core can be checked
without a third-party dependency.
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hosthook_support import HostHookHttp

from jasmine_core.hosthook import protocol, runtime
from jasmine_core.hosthook.dialects import codex
from test_hosthook_dialect_codex import fixture

SCHEMAS = Path(__file__).resolve().parents[1] / "spec" / "schemas"
NOW = datetime(2026, 10, 12, 8, 30, 15, tzinfo=timezone.utc)


def load(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text())


def _is(kind: str, value) -> bool:
    return {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str),
            "boolean": isinstance(value, bool), "null": value is None,
            "integer": isinstance(value, int) and not isinstance(value, bool)}[kind]


def validate(value, schema: dict, path: str = "$") -> list[str]:
    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum")
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is(kind, value) for kind in kinds):
            errors.append(f"{path}: wrong type")
            return errors
    if "not" in schema and not validate(value, schema["not"], path):
        errors.append(f"{path}: matches a forbidden schema")
    if "oneOf" in schema and sum(not validate(value, option, path) for option in schema["oneOf"]) != 1:
        errors.append(f"{path}: not exactly one oneOf option")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: pattern")
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 10 ** 9):
            errors.append(f"{path}: length")
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", 10 ** 9):
            errors.append(f"{path}: too many items")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            errors.append(f"{path}: duplicates")
        for index, item in enumerate(value):
            if "items" in schema:
                errors += validate(item, schema["items"], f"{path}[{index}]")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            errors += [f"{path}: unexpected {key}" for key in value if key not in properties]
        for key, sub in properties.items():
            if key in value:
                errors += validate(value[key], sub, f"{path}.{key}")
    for clause in schema.get("allOf", []):
        if "if" in clause:
            if not validate(value, clause["if"], path):
                errors += validate(value, clause["then"], path)
        else:
            errors += validate(value, clause, path)
    return errors


class SchemaMatchesImplementation(unittest.TestCase):
    def test_enums_and_fields_match_the_protocol_module(self) -> None:
        request = load("host-hook-request.schema.json")
        self.assertEqual(request["properties"]["event"]["enum"], list(protocol.EVENTS))
        self.assertEqual(request["properties"]["trust"]["enum"], list(protocol.TRUST_LEVELS))
        self.assertEqual(set(request["properties"]), protocol.TOP_FIELDS)
        self.assertEqual(set(request["required"]), protocol.TOP_REQUIRED)
        tool_enum = request["allOf"][2]["then"]["properties"]["payload"]["properties"]["tool"]["enum"]
        self.assertEqual(tool_enum, list(protocol.TOOL_CATEGORIES))
        response = load("host-hook-response.schema.json")
        self.assertEqual(response["properties"]["decision"]["enum"], list(protocol.DECISIONS))

    def test_the_codex_capability_declaration_validates(self) -> None:
        self.assertEqual(validate(codex.CAPABILITIES, load("host-hook-capabilities.schema.json")), [])
        broken = {**codex.CAPABILITIES, "can_ask": "no"}
        self.assertTrue(validate(broken, load("host-hook-capabilities.schema.json")))

    def test_the_claude_code_capability_declaration_validates_and_requests_conform(self) -> None:
        from datetime import datetime, timezone
        from jasmine_core import ids
        from jasmine_core.hosthook.binding import Binding
        from jasmine_core.hosthook.dialects import claude_code
        from test_hosthook_dialect_claude_code import ALL_FIXTURES, fixture as cc_fixture
        self.assertEqual(validate(claude_code.CAPABILITIES, load("host-hook-capabilities.schema.json")), [])
        binding = Binding(path=Path("/x/.jasmine/host-binding.json"), core_url="http://127.0.0.1:1",
                          host_id=ids.new_id("hst"), project_id=ids.new_id("prj"), task_id=ids.new_id("tsk"),
                          step_id=None, api_key_file=Path("/x/k"), human_api_key_file=None,
                          on_unavailable="deny", spool_dir=Path("/x/s"), timeout_seconds=None)
        now = datetime(2026, 10, 12, 8, 30, 15, tzinfo=timezone.utc)
        for name in ALL_FIXTURES:
            with self.subTest(name):
                request = runtime.build_request(claude_code, claude_code.translate_in(cc_fixture(name), now), binding, now)
                self.assertEqual(validate(request, load("host-hook-request.schema.json")), [])

    def test_every_runtime_request_validates_and_bad_ones_do_not(self) -> None:
        from jasmine_core import ids
        from jasmine_core.hosthook.binding import Binding
        schema = load("host-hook-request.schema.json")
        binding = Binding(path=Path("/x/b"), core_url="http://127.0.0.1:1", host_id=ids.new_id("hst"),
                          project_id=ids.new_id("prj"), task_id=ids.new_id("tsk"), step_id=ids.new_id("stp"),
                          api_key_file=Path("/x/k"), human_api_key_file=None, on_unavailable="allow",
                          spool_dir=Path("/x/s"), timeout_seconds=None)
        names = ["session_start", "session_end", "user_prompt_submit", "pre_tool_use_bash", "pre_tool_use_bash_compound",
                 "pre_tool_use_apply_patch", "pre_tool_use_mcp", "post_tool_use_bash", "post_tool_use_bash_failed",
                 "post_tool_use_unstructured", "pre_compact", "post_compact", "stop"]
        for name in names:
            with self.subTest(name):
                request = runtime.build_request(codex, codex.translate_in(fixture(name), NOW), binding, NOW)
                self.assertEqual(validate(request, schema), [])
        good = runtime.build_request(codex, codex.translate_in(fixture("pre_tool_use_bash"), NOW), binding, NOW)
        for mutate in (lambda r: r.update(trust="X"), lambda r: r["payload"].update(tool="bash"),
                       lambda r: r.update(extra=1), lambda r: r["payload"].pop("input"),
                       lambda r: r["payload"].update(paths=["rel"])):
            bad = json.loads(json.dumps(good))
            mutate(bad)
            with self.subTest(bad=bad):
                self.assertTrue(validate(bad, schema))


class ResponsesValidate(HostHookHttp):
    def test_core_responses_conform_for_every_event(self) -> None:
        schema = load("host-hook-response.schema.json")
        self.rule("no-rm", "DENY", {"tool": "Bash", "action": "rm", "path_prefix": "/protected"})
        sends = [
            self.request("session.start", {"source": "startup"}),
            self.request("prompt.submit", {"text": "hi"}),
            self.request("tool.before", {"tool": "shell", "native_tool": "Bash", "input": {}, "action": "rm", "paths": ["/protected/x"]}),
            self.request("tool.after", {"tool": "shell", "native_tool": "Bash", "input": {}, "response": {}, "exit_code": 0}),
            self.request("context.compact", {"phase": "before"}),
            self.request("context.compact", {"phase": "after"}),
            self.request("turn.stop", {}),
            self.request("session.end", {}),
            self.request("session.end", {}, task=False),
        ]
        for body in sends:
            with self.subTest(event=body["event"]):
                code, out = self.send(body, token=self.human_hook_token if body["event"] != "tool.after" else None)
                self.assertIn(code, (200, 201), out)
                self.assertEqual(validate(out, schema), [], out)
                code, again = self.send(body, token=self.human_hook_token if body["event"] != "tool.after" else None)
                self.assertEqual((code, again["replayed"]), (200, True))
                self.assertEqual(validate(again, schema), [])


if __name__ == "__main__":
    unittest.main()
