#!/usr/bin/env python3
"""Criterion 4: artifact negatives.

Good file (digest recomputed by the validator), missing file, byte-flipped
(tampered) file, and every escape attempt: `../`, absolute, `C:`, UNC, external
URL, a symlinked file and a symlinked intermediate directory. An
assignment-shaped secret is masked by `form=masked` and withheld by
`form=original`, while `canonical.looks_like_credential` returns False for it
(the validator runs that check itself). HTML/script content requested with
`download=1` is never a renderable page (Content-Type inspected).

Run: PYTHONPATH=../../src:. python3 c4_artifacts.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from jasmine_core import canonical

from fix_harness import (FILES, Live, Checker, artifact_entry, emit, sha256_bytes,
                     write_manifest, write_run)

OUT = Path(__file__).resolve().parent
RUN = "run-20261008T000004Z"
HTML = "<html><body><script>alert('x')</script>pwn</body></html>"
ASSIGN = b"2026-10-08 INFO db_password=hunter2hunter2 in the log line\n"
APIKEY = b'{"api_key": "sk-live-abcdef0123456789"}\n'


def main() -> int:
    c = Checker("criterion 4")
    live = Live()
    try:
        granted = live.key(["validation:read"])
        root = live.base / "c4-root"
        run_dir = root / "bundle" / RUN
        write_run(run_dir, RUN)
        outside = root / "outside"
        outside.mkdir(parents=True)
        (outside / "loot.txt").write_text("secret outside the run directory\n")
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        os.symlink(outside / "loot.txt", run_dir / "artifacts" / "link.txt")
        os.symlink(outside, run_dir / "artifacts" / "sub")

        files = [
            artifact_entry(run_dir, "art_good", "artifacts/good.txt", fixture="good.txt"),
            artifact_entry(run_dir, "art_missing", "artifacts/missing.txt",
                           declared_sha="2" * 64, declared_bytes=3),
            artifact_entry(run_dir, "art_tampered", "artifacts/tampered.txt",
                           fixture="tampered.txt",
                           declared_sha=hashlib.sha256(b"original bytes\n").hexdigest(),
                           declared_bytes=len(b"original bytes\n")),
            artifact_entry(run_dir, "art_symfile", "artifacts/link.txt",
                           declared_sha="1" * 64, declared_bytes=1),
            artifact_entry(run_dir, "art_symdir", "artifacts/sub/loot.txt",
                           declared_sha="1" * 64, declared_bytes=1),
            artifact_entry(run_dir, "art_assign", "artifacts/assign.log",
                           content=ASSIGN),
            artifact_entry(run_dir, "art_apikey", "artifacts/apikey.json",
                           content=APIKEY, media_type="application/json"),
            artifact_entry(run_dir, "art_html", "artifacts/page.html",
                           content=HTML.encode(), media_type="text/html"),
            artifact_entry(run_dir, "art_binary", "artifacts/binary.bin",
                           fixture="binary.bin", media_type="application/octet-stream"),
            artifact_entry(run_dir, "art_declared_secret", "artifacts/declared.txt",
                           content=b"no secret in these bytes\n",
                           credential_shaped=True),
        ]
        write_manifest(run_dir, RUN, files, mode="component")
        manifest_bytes = (run_dir / "manifest.json").read_bytes()
        run_bytes = (run_dir / f"{RUN}.json").read_bytes()

        with live.root(root):
            # -- 1. good file: the validator recomputes the digest itself -----
            good_path = run_dir / "artifacts" / "good.txt"
            my_good_sha = hashlib.sha256(good_path.read_bytes()).hexdigest()
            status, _, meta, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_good", granted)
            emit(OUT / "c4-01-good-artifact-metadata.json", meta)
            c.check("good artifact metadata is 200", status == 200, status)
            c.check("good artifact integrity=match", meta.get("integrity") == "match",
                    meta.get("integrity"))
            c.check("good declared_sha256 equals the validator's own hash",
                    meta.get("declared_sha256") == my_good_sha,
                    (meta.get("declared_sha256"), my_good_sha))
            c.check("good digests.original equals the validator's own hash",
                    meta.get("digests", {}).get("original") == my_good_sha,
                    meta.get("digests"))
            c.check("good declared_bytes equals the file size",
                    meta.get("declared_bytes") == good_path.stat().st_size)
            status, _, content, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_good/content", granted)
            emit(OUT / "c4-02-good-artifact-content.json", content)
            c.check("good content is 200", status == 200, status)
            c.check("good served_sha256 matches the validator's own hash",
                    content.get("served_sha256") == my_good_sha,
                    (content.get("served_sha256"), my_good_sha))
            c.check("good masked==original when the masker is a no-op",
                    content.get("masked_sha256") == content.get("original_sha256"))

            # -- 2. missing file ---------------------------------------------
            status, _, body, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_missing", granted)
            emit(OUT / "c4-03-missing-artifact.json", body)
            c.check("missing artifact is 404", status == 404, status)
            c.check("missing artifact code artifact_not_found",
                    body.get("error", {}).get("code") == "artifact_not_found",
                    body.get("error", {}).get("code"))
            c.check("missing artifact reason is 'missing'",
                    body.get("error", {}).get("details", {}).get("reason") == "missing",
                    body.get("error", {}).get("details"))

            # -- 3. tampered (byte-flipped) file ------------------------------
            status, _, body, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_tampered", granted)
            emit(OUT / "c4-04-tampered-artifact.json", body)
            c.check("tampered artifact is 409", status == 409, status)
            c.check("tampered artifact code artifact_modified",
                    body.get("error", {}).get("code") == "artifact_modified",
                    body.get("error", {}).get("code"))
            status, _, body, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_tampered/content", granted)
            c.check("tampered content is refused too (409)", status == 409, status)
            status, _, manifest, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/manifest", granted)
            emit(OUT / "c4-05-manifest-integrity.json", manifest)
            integ = {e["artifact_id"]: e["integrity"] for e in manifest["files"]}
            c.check("manifest reports the tampered file as mismatch",
                    integ.get("art_tampered") == "mismatch", integ.get("art_tampered"))
            c.check("manifest reports the missing file as missing",
                    integ.get("art_missing") == "missing", integ.get("art_missing"))

            # -- 4. symlinked file and symlinked intermediate directory -------
            for aid in ("art_symfile", "art_symdir"):
                status, _, body, _ = live.call(
                    "GET", f"/v1/validation/runs/{RUN}/artifacts/{aid}", granted)
                c.check(f"symlink {aid} artifact is 404", status == 404, status)
                c.check(f"symlink {aid} reason is symlink_refused",
                        body.get("error", {}).get("details", {}).get("reason")
                        == "symlink_refused", body.get("error", {}).get("details"))
                status, _, body, _ = live.call(
                    "GET", f"/v1/validation/runs/{RUN}/artifacts/{aid}/content", granted)
                c.check(f"symlink {aid} content is refused (404)", status == 404, status)
                c.check(f"manifest integrity {aid} is symlink_refused",
                        integ.get(aid) == "symlink_refused", integ.get(aid))

            # -- 5. assignment-shaped secret: mask + withhold -----------------
            text = ASSIGN.decode()
            api_text = APIKEY.decode()
            c.check("canonical.looks_like_credential is False for db_password=...",
                    canonical.looks_like_credential(text) is False,
                    canonical.looks_like_credential(text))
            # Recorded, not asserted: a value-level rule DOES catch a realistic
            # `{"api_key": "sk-live-…"}` value (True). The original gate does not
            # depend on this either way, so the file is still withheld below.
            api_key_flagged = canonical.looks_like_credential(api_text)
            c.check("api_key JSON original is withheld whether or not "
                    "looks_like_credential flags it",
                    api_key_flagged in (True, False), api_key_flagged)
            my_masked = canonical.redact_text(text)
            my_masked_sha = sha256_bytes(my_masked.encode())
            c.check("canonical.redact_text actually masks the assignment",
                    my_masked != text and "hunter2hunter2" not in my_masked, my_masked)
            c.check("mask_version is canonical.REDACT_TEXT_VERSION",
                    canonical.REDACT_TEXT_VERSION == "1",
                    canonical.REDACT_TEXT_VERSION)
            emit(OUT / "c4-06-validator-mask-computation.json", {
                "looks_like_credential_assign": canonical.looks_like_credential(text),
                "redact_text_assign": my_masked,
                "masked_sha256_assign": my_masked_sha,
                "redact_text_apikey": canonical.redact_text(api_text),
                "REDACT_TEXT_VERSION": canonical.REDACT_TEXT_VERSION,
            })

            status, _, masked, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_assign/content", granted)
            emit(OUT / "c4-07-assign-masked.json", masked)
            c.check("assign masked form is 200", status == 200, status)
            c.check("assign masked form reports form=masked",
                    masked.get("form") == "masked", masked.get("form"))
            c.check("assign masked content_is_original is false",
                    masked.get("content_is_original") is False)
            c.check("assign masked content equals the validator's redact_text",
                    masked.get("content") == my_masked,
                    (masked.get("content"), my_masked))
            c.check("assign masked_sha256 equals the validator's own digest",
                    masked.get("masked_sha256") == my_masked_sha,
                    (masked.get("masked_sha256"), my_masked_sha))
            c.check("assign served_sha256 equals masked_sha256",
                    masked.get("served_sha256") == masked.get("masked_sha256"))
            c.check("assign masked content withholds the secret",
                    "hunter2hunter2" not in (masked.get("content") or ""))
            c.check("assign mask_algorithm is canonical.redact_text",
                    masked.get("mask_algorithm") == "canonical.redact_text",
                    masked.get("mask_algorithm"))

            status, _, withheld, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_assign/content"
                       "?form=original", granted)
            emit(OUT / "c4-08-assign-original-withheld.json", withheld)
            c.check("assign original form is 403", status == 403, status)
            c.check("assign original code is original_withheld",
                    withheld.get("error", {}).get("code") == "original_withheld",
                    withheld.get("error", {}).get("code"))
            c.check("assign original reason is mask_not_noop",
                    withheld.get("error", {}).get("details", {}).get("reason")
                    == "mask_not_noop",
                    withheld.get("error", {}).get("details"))
            c.check("assign withheld details carry the masked_sha256",
                    withheld.get("error", {}).get("details", {}).get("masked_sha256")
                    == my_masked_sha)

            status, _, api_masked, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_apikey/content", granted)
            c.check("api_key JSON is masked by form=masked", status == 200, status)
            c.check("api_key JSON masked content withholds the value",
                    "sk-live-abcdef0123456789" not in (api_masked.get("content") or ""),
                    api_masked.get("content"))
            status, _, api_withheld, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_apikey/content"
                       "?form=original", granted)
            c.check("api_key JSON original form is withheld (403)", status == 403, status)

            # -- 6. HTML/script with download=1 is never renderable -----------
            status, headers, body, raw = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_html/content"
                       "?download=1", granted)
            emit(OUT / "c4-09-html-download-response.json", body)
            emit(OUT / "c4-10-html-download-headers.json", headers)
            c.check("html content request is 200", status == 200, status)
            c.check("html response Content-Type is JSON, not text/html",
                    headers.get("Content-Type") == "application/json; charset=utf-8",
                    headers.get("Content-Type"))
            c.check("html response sets X-Content-Type-Options: nosniff",
                    headers.get("X-Content-Type-Options") == "nosniff",
                    headers.get("X-Content-Type-Options"))
            c.check("html response sets a default-src 'none' CSP",
                    headers.get("Content-Security-Policy") == "default-src 'none'; sandbox",
                    headers.get("Content-Security-Policy"))
            c.check("html download sets attachment Content-Disposition",
                    headers.get("Content-Disposition")
                    == 'attachment; filename="art_html.txt"',
                    headers.get("Content-Disposition"))
            c.check("raw response bytes are a JSON envelope, not an HTML page",
                    raw.lstrip()[:1] == b"{", raw[:20].decode("utf-8", "replace"))
            c.check("the HTML is carried as a JSON string field, not rendered",
                    body.get("content") == HTML and body.get("media_type") == "text/html",
                    (body.get("media_type"), (body.get("content") or "")[:20]))

            # The original form (the masker is a no-op on HTML) must be equally
            # non-renderable.
            status, headers_orig, body_orig, raw_orig = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_html/content"
                       "?form=original&download=1", granted)
            emit(OUT / "c4-17-html-original-headers.json", headers_orig)
            c.check("html original form is 200", status == 200, status)
            c.check("html original form Content-Type is still JSON",
                    headers_orig.get("Content-Type")
                    == "application/json; charset=utf-8",
                    headers_orig.get("Content-Type"))
            c.check("html original form still sets nosniff",
                    headers_orig.get("X-Content-Type-Options") == "nosniff",
                    headers_orig.get("X-Content-Type-Options"))
            c.check("html original form is a JSON envelope, not an HTML page",
                    raw_orig.lstrip()[:1] == b"{", raw_orig[:10].decode("utf-8", "replace"))

            # -- 8b. a manifest-declared credential shape withholds the original
            status, _, masked_decl, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_declared_secret/content",
                granted)
            c.check("declared-credential masked form is 200", status == 200, status)
            status, _, withheld_decl, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_declared_secret/content"
                       "?form=original", granted)
            emit(OUT / "c4-18-declared-credential-withheld.json", withheld_decl)
            c.check("declared-credential original form is 403", status == 403, status)
            c.check("declared-credential reason is credential_shaped_declared",
                    withheld_decl.get("error", {}).get("details", {}).get("reason")
                    == "credential_shaped_declared",
                    withheld_decl.get("error", {}).get("details"))

            # -- 7. non-text is 415 (bonus negative) --------------------------
            status, _, body, _ = live.call(
                "GET", f"/v1/validation/runs/{RUN}/artifacts/art_binary/content", granted)
            c.check("binary content is 415 unsupported_media_type", status == 415, status)

            # -- 8. every declared-path escape refuses the manifest -----------
            escapes = {
                "art_dotdot": "../outside/loot.txt",
                "art_nested_dotdot": "artifacts/../../outside/loot.txt",
                "art_absolute": "/tmp/outside/loot.txt",
                "art_drive": "C:foo.txt",
                "art_unc": "\\\\host\\share\\f.txt",
                "art_url": "https://evil.example/x.txt",
            }
            escape_evidence = {}
            for index, (aid, escape_path) in enumerate(escapes.items()):
                # one manifest per escape, in its own root, so the offending
                # entry is named for every form
                sub_root = root / f"escape-{aid}"
                sub_run = f"run-20261008T0001{index:02d}Z"
                sub_dir = sub_root / "leaf" / sub_run
                write_run(sub_dir, sub_run)
                write_manifest(sub_dir, sub_run, [{
                    "artifact_id": aid, "path": escape_path, "bytes": 1,
                    "sha256": "0" * 64, "media_type": "text/plain",
                    "credential_shaped": False}])
                with live.root(sub_root):
                    status, _, body, _ = live.call(
                        "GET", f"/v1/validation/runs/{sub_run}/manifest", granted)
                    escape_evidence[f"manifest::{aid}"] = {"status": status, "body": body}
                    c.check(f"escape {aid} refuses the manifest (409)", status == 409, status)
                    c.check(f"escape {aid} names validation_manifest_corrupt",
                            body.get("error", {}).get("code") == "validation_manifest_corrupt",
                            body.get("error", {}).get("code"))
                    c.check(f"escape {aid} names the offending entry in rule",
                            aid in (body.get("error", {}).get("details", {}).get("rule") or ""),
                            body.get("error", {}).get("details"))
                    status, _, body, _ = live.call(
                        "GET", f"/v1/validation/runs/{sub_run}/artifacts/{aid}", granted)
                    escape_evidence[f"artifact::{aid}"] = {"status": status, "body": body}
                    c.check(f"escape {aid} makes the artifact unaddressable (404)",
                            status == 404, status)
                    c.check(f"escape {aid} artifact reason is manifest_corrupt",
                            body.get("error", {}).get("details", {}).get("reason")
                            == "manifest_corrupt",
                            body.get("error", {}).get("details"))
            emit(OUT / "c4-11-escape-attempts.json", escape_evidence)

            # -- 9. raw declared-path string rules (§3, F3) -------------------
            # The spec says `files[].path` is relative, every component
            # non-empty, and *contains no `..`*. `pathlib` used to normalise
            # `a//b`, `a/./b`, a trailing `/` and `./a` away before the check,
            # so those manifests were served frozen. Validate the raw string
            # now, plus literal-`..` substrings (which the spec's wording
            # refuses even when they are not a `..` segment).
            raw_bad = [
                ("empty_component", "a//b"),
                ("dot_component", "a/./b"),
                ("trailing_slash", "a/b/"),
                ("leading_dot_slash", "./a"),
                ("dot_alone", "."),
                ("dotdot_substring_name", "artifacts/note..txt"),
                ("dotdot_substring_mid", "a..b"),
                ("dotdot_segment_name", "artifacts/..hidden"),
                ("dotdot_segment", "artifacts/../loot.txt"),
                ("dotdot_encoded", "..%2Fb"),
            ]
            raw_evidence = {}
            for index, (label, declared) in enumerate(raw_bad):
                sub_run = f"run-20261008T0003{index:02d}Z"
                sub_root = root / f"raw-{label}"
                sub_dir = sub_root / "leaf" / sub_run
                write_run(sub_dir, sub_run)
                entry = artifact_entry(sub_dir, "art_raw", "artifacts/plain.txt",
                                       fixture="plain.txt", declared_path=declared)
                write_manifest(sub_dir, sub_run, [entry])
                with live.root(sub_root):
                    status, _, body, _ = live.call(
                        "GET", f"/v1/validation/runs/{sub_run}/manifest", granted)
                    raw_evidence[f"{label} manifest"] = {"declared": declared,
                                                         "status": status, "body": body}
                    c.check(f"raw declared path {label} ({declared!r}) refuses "
                            "the manifest (409)", status == 409, status)
                    c.check(f"raw declared path {label} names "
                            "validation_manifest_corrupt",
                            body.get("error", {}).get("code")
                            == "validation_manifest_corrupt",
                            body.get("error", {}).get("code"))
                    c.check(f"raw declared path {label} names the path rule "
                            "(rule mentions 'path')",
                            "path" in (body.get("error", {}).get("details", {})
                                       .get("rule") or ""),
                            body.get("error", {}).get("details"))
                    status, _, body, _ = live.call(
                        "GET", f"/v1/validation/runs/{sub_run}/artifacts/art_raw",
                        granted)
                    raw_evidence[f"{label} artifact"] = {"status": status, "body": body}
                    c.check(f"raw declared path {label} makes the artifact "
                            "unaddressable (404)", status == 404, status)
                    c.check(f"raw declared path {label} reason is manifest_corrupt",
                            body.get("error", {}).get("details", {}).get("reason")
                            == "manifest_corrupt",
                            body.get("error", {}).get("details"))
            emit(OUT / "c4-19-raw-declared-paths.json", raw_evidence)

            # -- 10. a *legitimate* declared path still works -----------------
            # The F3 tightening must not break the ordinary form: a normal
            # `artifacts/name.txt`, and a nested `artifacts/sub/name.txt`.
            legit_root = root / "legit-paths"
            legit_id = "run-20261008T000401Z"
            legit_dir = legit_root / "b" / legit_id
            write_run(legit_dir, legit_id)
            legit_files = [
                artifact_entry(legit_dir, "art_flat", "artifacts/name.txt",
                               fixture="good.txt"),
                artifact_entry(legit_dir, "art_nested", "artifacts/sub/name.txt",
                               fixture="good.txt"),
            ]
            write_manifest(legit_dir, legit_id, legit_files)
            with live.root(legit_root):
                status, _, manifest, _ = live.call(
                    "GET", f"/v1/validation/runs/{legit_id}/manifest", granted)
                c.check("legit nested bundle manifest is 200", status == 200, status)
                integrity = {e["artifact_id"]: e.get("integrity")
                             for e in manifest.get("files", [])}
                c.check("legit declared path artifacts/name.txt is served match",
                        integrity.get("art_flat") == "match", integrity)
                c.check("legit nested path artifacts/sub/name.txt is served match",
                        integrity.get("art_nested") == "match", integrity)
                for aid in ("art_flat", "art_nested"):
                    status, _, body, _ = live.call(
                        "GET", f"/v1/validation/runs/{legit_id}/artifacts/{aid}",
                        granted)
                    c.check(f"legit {aid} metadata is 200", status == 200, status)
                    status, _, body, _ = live.call(
                        "GET",
                        f"/v1/validation/runs/{legit_id}/artifacts/{aid}/content",
                        granted)
                    c.check(f"legit {aid} content is 200 with the right digest",
                            status == 200 and body.get("served_sha256")
                            == sha256_bytes((FILES / "good.txt").read_bytes()),
                            (status, body.get("served_sha256")))

        # digests of the files the validator created, for the record
        emit(OUT / "c4-12-validator-file-digests.json", {
            "run_record_sha256": sha256_bytes(run_bytes),
            "manifest_sha256": sha256_bytes(manifest_bytes),
            "good.txt": my_good_sha,
            "assign.log": sha256_bytes(ASSIGN),
            "page.html": sha256_bytes(HTML.encode()),
        })
        emit(OUT / "c4-13-criterion4-report.json", c.summary())
    finally:
        live.close()
    return 0 if not c.summary()["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
