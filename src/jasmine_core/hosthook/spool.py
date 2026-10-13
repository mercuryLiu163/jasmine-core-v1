"""Private on-disk spool for deliveries Core could not accept (ADR 0017 section 10).

Directory 0700, files 0600, atomic rename. A spooled request is resent unchanged,
so replay is exactly idempotent through `delivery_id`. A `tool.before` that could
not be checked leaves a *gap record* (hashes only, never the tool input) which is
reported but not sent.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .. import clock
from ..canonical import canonical_json, sha256_hex


class SpoolError(Exception):
    pass


class Spool:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _ensure(self) -> None:
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.directory.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                os.chmod(self.directory, 0o700)
        except OSError as exc:
            raise SpoolError("spool directory is unavailable") from exc

    def _write(self, directory: Path, name: str, document: dict[str, Any]) -> Path:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        final = directory / name
        temp = directory / ("." + name + ".tmp")
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            os.write(descriptor, canonical_json(document).encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temp, final)
        return final

    def _name(self, delivery_id: str) -> str:
        return f"{time.time_ns():020d}-{sha256_hex(delivery_id)[:12]}.json"

    def put_request(self, request: dict[str, Any], binding_path: Path, reason: str) -> Path:
        self._ensure()
        return self._write(self.directory, self._name(request["delivery_id"]),
                           {"kind": "request", "request": request, "binding_path": str(binding_path),
                            "queued_at": clock.now_rfc3339(), "reason": reason})

    def put_gap(self, request: dict[str, Any], reason: str) -> Path:
        self._ensure()
        payload = request.get("payload", {})
        return self._write(self.directory, self._name(request["delivery_id"]),
                           {"kind": "unchecked_gap", "delivery_id": request["delivery_id"],
                            "event": request["event"], "native_tool": payload.get("native_tool"),
                            "input_sha256": sha256_hex(payload.get("input")),
                            "queued_at": clock.now_rfc3339(), "reason": reason})

    def entries(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(p for p in self.directory.iterdir() if p.is_file() and p.suffix == ".json" and not p.name.startswith("."))

    @staticmethod
    def read(path: Path) -> dict[str, Any]:
        try:
            document = json.loads(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise SpoolError("spool entry is unreadable") from exc
        if not isinstance(document, dict) or document.get("kind") not in ("request", "unchecked_gap"):
            raise SpoolError("spool entry is malformed")
        return document

    def remove(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    def reject(self, path: Path, error: str) -> None:
        try:
            document = self.read(path)
        except SpoolError:
            document = {"kind": "malformed"}
        document["rejected_reason"] = error[:200]
        self._write(self.directory / "rejected", path.name, document)
        self.remove(path)
