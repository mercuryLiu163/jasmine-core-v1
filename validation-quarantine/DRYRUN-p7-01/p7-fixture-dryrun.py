#!/usr/bin/env python3
"""Dry-run the rev-2 §2 (discovery) and §3 (manifest/artifact) rules over a
synthetic negative-case tree. /tmp only; nothing in the repo is touched.

Prints, for every attack the acceptance criteria name, the rule that refuses it.
"""
import hashlib, json, os, re, shutil, stat, sys
from pathlib import Path

BASE = Path("/tmp/p7-fixroot")
RUN_RE = re.compile(r"^run-[0-9]{8}T[0-9]{6}Z\.json$")
ART_RE = re.compile(r"^art_[a-z0-9][a-z0-9_.-]{0,63}$")
SCHEMA = "jasmine.validation-bundle.v1"
ALLOWED_TEXT = {"application/json","application/jsonl","application/x-ndjson",
                "application/xml","application/yaml","application/x-yaml"}
MAX_ARTIFACT = 8*1024*1024
MAX_FILES = 128

def build():
    shutil.rmtree(BASE, ignore_errors=True)
    outside = Path("/tmp/p7-outside"); outside.mkdir(exist_ok=True)
    (outside/"loot.txt").write_text("secret outside the root\n")

    def run(run_id, verdict="PASS", cases=None, extra=None):
        return {"run_id": run_id, "stage": "P7", "created_at": "2026-10-08T00:00:01.000000Z",
                "commit": "0"*40, "schema_version": 11, "core_version": "0.1.0",
                "environment": {}, "executor": "fixture", "reviewer": "fixture",
                "verifier": "fixture", "verdict": verdict,
                "cases": cases if cases is not None else [{"case_id":"T-1","name":"c","verdict":"PASS",
                    "user_raw":"hi","agent_response":"ok","tools":[],"state_before":{},
                    "state_after":{},"interpretation":"N/A (P0 not implemented)",
                    "context":"N/A (P0 not implemented)","evidence":["a"]}],
                "notes": [], "not_implemented": []}

    # A: valid run + valid manifest (good / tampered / missing file)
    a = BASE/"p7-ok"/"run-20261008T000001Z"; a.mkdir(parents=True)
    (a/"run-20261008T000001Z.json").write_text(json.dumps(run("run-20261008T000001Z")))
    (a/"artifacts").mkdir()
    good = b"hello bundle\n"; (a/"artifacts"/"good.txt").write_bytes(good)
    tampered = b"original bytes\n"; (a/"artifacts"/"tampered.txt").write_bytes(b"TAMPERED bytes\n")
    manifest = {"schema": SCHEMA, "run_id": "run-20261008T000001Z",
        "frozen_at": "2026-10-08T00:10:00Z", "mode": "component",
        "projects": [], "sources": {"recorder": {"path":"scripts/x.py","sha256":"1"*64}},
        "files": [
          {"artifact_id":"art_good","path":"artifacts/good.txt","bytes":len(good),
           "sha256":hashlib.sha256(good).hexdigest(),"media_type":"text/plain","credential_shaped":False},
          {"artifact_id":"art_tampered","path":"artifacts/tampered.txt","bytes":len(tampered),
           "sha256":hashlib.sha256(tampered).hexdigest(),"media_type":"text/plain","credential_shaped":False},
          {"artifact_id":"art_missing","path":"artifacts/missing.txt","bytes":3,
           "sha256":"2"*64,"media_type":"text/plain","credential_shaped":False},
          {"artifact_id":"art_secret","path":"artifacts/secret.log","bytes":0,"sha256":"3"*64,
           "media_type":"text/plain","credential_shaped":False}]}
    (a/"artifacts"/"secret.log").write_text(
        "2026-10-08 INFO starting\nAuthorization: Bearer ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n")
    sec = (a/"artifacts"/"secret.log").read_bytes()
    manifest["files"][3]["bytes"] = len(sec); manifest["files"][3]["sha256"] = hashlib.sha256(sec).hexdigest()
    assign = b"2026-10-08 INFO db_password=hunter2hunter2 in the log line\n"
    (a/"artifacts"/"assign.log").write_bytes(assign)
    manifest["files"].append({"artifact_id":"art_assign","path":"artifacts/assign.log",
        "bytes":len(assign),"sha256":hashlib.sha256(assign).hexdigest(),
        "media_type":"text/plain","credential_shaped":False})
    (a/"manifest.json").write_text(json.dumps(manifest, indent=1))

    # B: synthetic FAIL run (permanently labelled)
    b = BASE/"p7-fail"/"run-20261008T000002Z"; b.mkdir(parents=True)
    f = run("run-20261008T000002Z", "FAIL", cases=[{"case_id":"F-1","name":"fails","verdict":"FAIL",
        "user_raw":"go","agent_response":"mocked: fixture response","tools":[],
        "state_before":{},"state_after":{},"interpretation":"N/A (P0 not implemented)",
        "context":{"reason":"no context pack"},"failure_class":"UI_FAILURE",
        "failure_output":"expected 200 got 500","reproduction":["GET /x"],"evidence":[],
        "not_applied_reason":None}])
    f["synthetic"] = True; f["synthetic_note"] = "NOT EVIDENCE: synthetic fixture"
    (b/"run-20261008T000002Z.json").write_text(json.dumps(f))

    # C: corrupt record
    c = BASE/"p7-corrupt"; c.mkdir(parents=True)
    (c/"run-20261008T000003Z.json").write_text("{not json at all")

    # D: duplicate stem of A in another directory
    d = BASE/"p7-dup"/"run-20261008T000001Z"; d.mkdir(parents=True)
    (d/"run-20261008T000001Z.json").write_text(json.dumps(run("run-20261008T000001Z")))

    # E: every escape form in one manifest
    e = BASE/"p7-escape"/"run-20261008T000004Z"; e.mkdir(parents=True)
    (e/"run-20261008T000004Z.json").write_text(json.dumps(run("run-20261008T000004Z")))
    (e/"artifacts").mkdir()
    m = {"schema": SCHEMA, "run_id": "run-20261008T000004Z", "mode": "real",
         "projects": ["prj_00000000000000000000000000"],
         "sources": {"recorder": "scripts/x.py@" + "a"*40},
         "files": [
           {"artifact_id":"art_dotdot","path":"../p7-outside/loot.txt","bytes":1,"sha256":"0"*64},
           {"artifact_id":"art_abs","path":"/tmp/p7-outside/loot.txt","bytes":1,"sha256":"0"*64},
           {"artifact_id":"art_drive","path":"C:foo.txt","bytes":1,"sha256":"0"*64},
           {"artifact_id":"art_unc","path":"\\\\host\\share\\f.txt","bytes":1,"sha256":"0"*64},
           {"artifact_id":"art_url","path":"https://evil.example/x.txt","bytes":1,"sha256":"0"*64},
           {"artifact_id":"art_symfile","path":"artifacts/link.txt","bytes":1,"sha256":"0"*64},
           {"artifact_id":"art_symdir","path":"artifacts/sub/loot.txt","bytes":1,"sha256":"0"*64},
           {"artifact_id":"art_baduuid","path":"artifacts/good.txt","bytes":1,"sha256":"0"*64}]}
    (e/"artifacts").mkdir(exist_ok=True)
    os.symlink("/tmp/p7-outside/loot.txt", e/"artifacts"/"link.txt")
    os.symlink("/tmp/p7-outside", e/"artifacts"/"sub")
    (e/"manifest.json").write_text(json.dumps(m))

    # F: an unreadable subtree. Trap: os.walk with the default onerror=None
    # SILENTLY IGNORES a directory it cannot list, so a reader that does not pass
    # onerror can never report index_incomplete.
    u = BASE/"p7-unreadable"/"run-20261008T000007Z"; u.mkdir(parents=True)
    (u/"run-20261008T000007Z.json").write_text(json.dumps(run("run-20261008T000007Z")))
    os.chmod(BASE/"p7-unreadable", 0o000)

    # G: noise the walk must ignore
    (BASE/".hidden").mkdir(); (BASE/".hidden"/"run-20261008T000005Z.json").write_text("{}")
    (BASE/"__pycache__").mkdir(); (BASE/"__pycache__"/"x.pyc").write_text("x")
    os.symlink("/tmp/p7-outside", BASE/"p7-linkdir")
    os.symlink("/tmp/p7-outside/loot.txt", BASE/"run-20261008T000006Z.json")
    (BASE/"notes.md").write_text("not a record")

def classify_path(path):
    """§3 path rules, in order. Returns (ok, rule)."""
    if not isinstance(path, str) or not path: return False, "empty"
    if "\x00" in path: return False, "NUL"
    if path.startswith("/") or path.startswith("\\"): return False, "absolute/UNC prefix"
    if re.match(r"^[A-Za-z]:", path): return False, "drive-relative"
    if "\\" in path: return False, "backslash"
    if re.search(r"^[A-Za-z][A-Za-z0-9+.-]*://", path): return False, "URL scheme"
    parts = Path(path).parts
    if any(p in ("..", "", ".") for p in parts): return False, "dot segment"
    return True, "ok"

def load_manifest(run_dir, run_id):
    mf = run_dir/"manifest.json"
    if not mf.exists(): return "absent", {"reason":"no manifest.json on disk"}
    try:
        m = json.loads(mf.read_text())
    except Exception as exc:
        return "corrupt", {"rule": f"unparseable: {exc}"}
    if not isinstance(m, dict): return "corrupt", {"rule":"not an object"}
    if m.get("schema") != SCHEMA: return "corrupt", {"rule":"schema is not "+SCHEMA}
    if m.get("run_id") != run_id: return "corrupt", {"rule":"run_id does not match the record"}
    files = m.get("files")
    if not isinstance(files, list): return "corrupt", {"rule":"files is not a list"}
    if len(files) > MAX_FILES: return "corrupt", {"rule":f"files exceeds {MAX_FILES}"}
    ids = set()
    for f in files:
        if not isinstance(f, dict): return "corrupt", {"rule":"file entry is not an object"}
        aid = f.get("artifact_id")
        if not isinstance(aid,str) or not ART_RE.match(aid):
            return "corrupt", {"rule":f"artifact_id {aid!r} fails the grammar"}
        if aid in ids: return "corrupt", {"rule":f"duplicate artifact_id {aid}"}
        ids.add(aid)
        ok, rule = classify_path(f.get("path"))
        if not ok: return "corrupt", {"rule":f"{aid}: {rule} path {f.get('path')!r}"}
        if not isinstance(f.get("sha256"),str) or not re.fullmatch(r"[0-9a-f]{64}", f.get("sha256") or ""):
            return "corrupt", {"rule":f"{aid}: sha256 is not 64 hex"}
        if not isinstance(f.get("bytes"),int) or f["bytes"] < 0:
            return "corrupt", {"rule":f"{aid}: bytes is not a non-negative int"}
    return "frozen", m

def art_media_ok(mt):
    if not isinstance(mt,str): return False
    return mt.startswith("text/") or mt in ALLOWED_TEXT

def main():
    build()
    runs, ignored, unreadable = [], [], []

    def on_error(exc):
        unreadable.append((str(getattr(exc, "filename", "?")), type(exc).__name__))

    for dirpath, dirnames, filenames in os.walk(BASE, onerror=on_error):
        rel = Path(dirpath).relative_to(BASE)
        if len(rel.parts) >= 4:
            ignored.append((str(rel),"depth_cap")); dirnames[:]=[]; continue
        keep=[]
        for d in dirnames:
            if d.startswith(".") or d=="__pycache__":
                ignored.append((str(rel/d),"excluded_dir")); continue
            if (Path(dirpath)/d).is_symlink():
                ignored.append((str(rel/d),"symlinked_directory")); continue
            keep.append(d)
        dirnames[:]=keep
        for f in filenames:
            p=Path(dirpath)/f
            if not RUN_RE.match(f): ignored.append((str(rel/f),"not_run_shaped")); continue
            if p.is_symlink(): ignored.append((str(rel/f),"symlinked_file")); continue
            runs.append(p)
    recs=[]
    for p in runs:
        rel=str(p.relative_to(BASE))
        try: d=json.loads(p.read_text())
        except Exception as exc:
            recs.append({"rel":rel,"status":"corrupt","reason":f"unparseable: {exc}"}); continue
        if not isinstance(d,dict) or any(k not in d for k in ("run_id","stage","created_at","verdict","cases")) \
           or d.get("run_id")!=p.stem:
            recs.append({"rel":rel,"status":"corrupt","reason":"required field or run_id mismatch"}); continue
        recs.append({"rel":rel,"status":"ok","data":d})
    stems={}
    for r in recs:
        if r["status"]=="ok": stems.setdefault(Path(r["rel"]).stem,[]).append(r["rel"])
    for r in recs:
        if r["status"]=="ok" and len(stems[Path(r["rel"]).stem])>1:
            r["status"]="duplicate"; r["reason"]=f"{len(stems[Path(r['rel']).stem])} files share this stem"
    print(f"discovered {len(recs)}  ignored {len(ignored)}  unreadable {len(unreadable)}")
    print(f"ignored reasons {sorted({x for _,x in ignored})}")
    if unreadable:
        print("unreadable_entries > 0 => the list MUST be 503 validation_not_configured "
              "(details.reason=index_incomplete), never a page that omits rows:")
        print(f"   {unreadable}")
    else:
        print("unreadable_entries == 0 => the index is complete and may be paged")
    for r in sorted(recs,key=lambda r:(0 if r["status"]=="ok" else 1, r["rel"])):
        print(f"  {r['status']:9s} {r['rel']}  {r.get('reason','')}")
    print()
    dup = [r for r in recs if r["status"]=="duplicate"]
    if dup: print(f"detail on a duplicate stem -> 409 validation_run_ambiguous ({dup[0]['reason']})")
    alt = [r for r in recs if r["status"]=="corrupt"]
    print(f"corrupt rows stay listed: {len(alt)}; total includes them when unfiltered")
    print()
    a = BASE/"p7-ok"/"run-20261008T000001Z"
    status, m = load_manifest(a, "run-20261008T000001Z")
    print(f"A manifest_status={status}")
    if status=="frozen":
        for f in m["files"]:
            p = a/f["path"]
            if not p.exists(): integ="missing"
            elif not stat.S_ISREG(os.stat(p).st_mode): integ="unreadable"
            else:
                b=p.read_bytes()
                integ = "match" if hashlib.sha256(b).hexdigest()==f["sha256"] else "mismatch"
            print(f"   {f['artifact_id']:14s} integrity={integ:9s} media_ok={art_media_ok(f.get('media_type'))}")
            if f["artifact_id"]=="art_secret":
                from jasmine_core.canonical import looks_like_credential, redact_text
                txt=b.decode()
                print(f"      original scan (independent of credential_shaped=False): "
                      f"looks_like_credential={looks_like_credential(txt)} -> 403 original_withheld")
                masked=redact_text(txt)
                print(f"      masked differs: {masked!=txt}  masked line: "
                      f"{[l for l in masked.splitlines() if 'REDACTED' in l or 'Bearer' in l]}")
    print()
    e = BASE/"p7-escape"/"run-20261008T000004Z"
    status, m = load_manifest(e, "run-20261008T000004Z")
    print(f"E manifest_status={status}  rule={m.get('rule')}")
    print("   (a single broken path makes the whole manifest corrupt, so no artifact is partially trusted)")
    print()
    print("individual path verdicts:")
    for f in json.loads((e/"manifest.json").read_text())["files"]:
        ok, rule = classify_path(f["path"])
        print(f"   {f['artifact_id']:12s} {str(f['path'])[:44]:46s} {'OK' if ok else 'REFUSED: '+rule}")
    a_status, a_m = load_manifest(a, "run-20261008T000001Z")
    if a_status == "frozen":
        from jasmine_core.canonical import looks_like_credential, redact_text
        for f in a_m["files"]:
            if f["artifact_id"] != "art_assign":
                continue
            txt = (a/f["path"]).read_text(); masked = redact_text(txt)
            print(f"   {f['artifact_id']:12s} original gate: looks_like_credential="
                  f"{looks_like_credential(txt)} (alone it would SERVE the file); "
                  f"redact_text==input: {masked==txt} -> "
                  f"{'serve original' if masked==txt else '403 original_withheld (mask is not a no-op)'}")
            print(f"      masked -> {masked.strip()}")
    print()
    print("grade ladder on the synthetic FAIL record (rev 5 §4.2):")
    fd = json.loads((BASE/"p7-fail"/"run-20261008T000002Z"/"run-20261008T000002Z.json").read_text())
    for c in fd["cases"]:
        g = "simulated" if fd.get("synthetic") is True else "unknown"
        basis = "the run record marks itself synthetic" if fd.get("synthetic") is True else "?"
        print(f"   {c['case_id']}: evidence_grade={g}  grade_basis={basis}")
    print("   run grade = least trustworthy of (bundle mode unknown, case grades) = unknown")
    print()
    from jasmine_core.canonical import looks_like_credential
    print("declared mode=real with projects and recorder provenance -> effective real")
    m2 = json.loads((e/"manifest.json").read_text())
    eff = "real" if (m2.get("projects") and m2.get("sources",{}).get("recorder")) else "unknown"
    print(f"   effective mode = {eff}")
    m3 = dict(m2); m3.pop("projects")
    eff3 = "real" if (m3.get("projects") and m3.get("sources",{}).get("recorder")) else "unknown"
    print(f"   without projects           -> effective mode = {eff3} (mode_basis names the missing provenance)")

try:
    main()
finally:
    u = BASE/"p7-unreadable"
    if u.exists():
        os.chmod(u, 0o700)
