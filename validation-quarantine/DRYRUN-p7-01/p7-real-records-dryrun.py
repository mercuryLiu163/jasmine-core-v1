#!/usr/bin/env python3
"""Read-only dry run of the rev-2 proposal rules over the nine real records.

Not an implementation: no routes, no repo files touched. It answers
"do the proposed rules classify real data sanely?"
"""
import hashlib, json, os, re, sys
from pathlib import Path

ROOT = Path("/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1/validation-runs")
RUN_RE = re.compile(r"^run-[0-9]{8}T[0-9]{6}Z\.json$")
SENTINEL = "N/A (P0 not implemented)"
SECTIONS = ("user_raw","agent_response","tools","state_before","state_after","interpretation","context")
REASON_ONLY_KEYS = {"reason","note","detail","not_applied_reason"}
CONTEXT_PACK_KEYS = {"context_id","pack","sections","rendered","content","tokens","parts"}
REQUIRED = ("run_id","stage","created_at","verdict","cases")
MAX_DEPTH = 4

def walk(root: Path):
    run_shaped, ignored = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        depth = len(rel.parts)
        if depth >= MAX_DEPTH:
            ignored.append((str(rel), "depth_cap")); dirnames[:] = []; continue
        keep = []
        for d in dirnames:
            if d.startswith(".") or d == "__pycache__":
                ignored.append((str(rel/d), "excluded_dir")); continue
            if (Path(dirpath)/d).is_symlink():
                ignored.append((str(rel/d), "symlinked_directory")); continue
            keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            p = Path(dirpath)/f
            if not RUN_RE.match(f):
                ignored.append((str(rel/f), "not_run_shaped")); continue
            if p.is_symlink():
                ignored.append((str(rel/f), "symlinked_file")); continue
            run_shaped.append(p)
    return run_shaped, ignored

def presence(value, *, context=False):
    """§1.1: present_kind records whether the SECTION was recorded.
    The context CHAIN LINK is stricter and also needs context_pack_present."""
    if value is _MISSING: return {"present": False, "present_kind": "absent",
        "missing_reason": "the record has no such field"}
    if value == SENTINEL: return {"present": False, "present_kind": "sentinel",
        "missing_reason": "recorded as N/A (P0 not implemented)"}
    if isinstance(value, dict) and set(value) <= REASON_ONLY_KEYS:
        return {"present": False, "present_kind": "reason_only",
                "recorded_reason": str(value.get("reason") or value)[:70]}
    if isinstance(value, str) and value.strip() == "":
        return {"present": False, "present_kind": "absent",
                "missing_reason": "recorded as an empty string"}
    if context:
        pack = (isinstance(value, dict) and bool(set(value) & CONTEXT_PACK_KEYS)) or \
               (isinstance(value, list) and bool(value))
        return {"present": True, "present_kind": "value", "context_pack_present": bool(pack)}
    return {"present": True, "present_kind": "value"}

def chain(case):
    sec = {s: presence(case.get(s, _MISSING), context=(s=="context")) for s in SECTIONS}
    return {
        "raw": sec["user_raw"]["present"],
        "extraction": False,
        "resolution": sec["interpretation"]["present"],
        "context": sec["context"]["present"] and sec["context"].get("context_pack_present", False),
        "agent": sec["agent_response"]["present"],
        "tool": bool(case.get("tools")),
        "evidence": bool(case.get("evidence")),
        "state": sec["state_before"]["present"] or sec["state_after"]["present"],
    }

def worst(cases):
    if any(c.get("verdict")=="FAIL" for c in cases): return "FAIL"
    if any(c.get("verdict")=="BLOCKED" for c in cases): return "BLOCKED"
    return "PASS"

class _M: pass
_MISSING = _M()

runs, ignored = walk(ROOT)
recs = []
for p in runs:
    rel = str(p.relative_to(ROOT))
    try:
        d = json.loads(p.read_text())
    except Exception as exc:
        recs.append({"rel": rel, "status": "corrupt", "corrupt_reason": f"unparseable: {exc}"}); continue
    if not isinstance(d, dict) or any(k not in d for k in REQUIRED) or d.get("run_id") != p.stem:
        recs.append({"rel": rel, "status": "corrupt", "corrupt_reason": "required field or run_id mismatch"}); continue
    recs.append({"rel": rel, "status": "ok", "data": d, "sha": hashlib.sha256(p.read_bytes()).hexdigest()})

print(f"discovered run-shaped: {len(recs)}   ignored: {len(ignored)}")
print("ignored reasons:", sorted({r for _, r in ignored}))
print()
class Desc:
    __slots__ = ("v",)
    def __init__(self, v): self.v = v
    def __lt__(self, other): return self.v > other.v

order = sorted(recs, key=lambda r: (0 if r["status"]=="ok" else 1,
                                    Desc(r["data"]["created_at"]) if r["status"]=="ok" else Desc(""),
                                    Desc(r["data"]["run_id"]) if r["status"]=="ok" else Desc(""),
                                    r["rel"]))
print("page order (rank, created_at desc, run_id desc, path asc):")
for r in order:
    if r["status"] != "ok":
        print(f"  CORRUPT {r['rel']}"); continue
    d = r["data"]
    cs = d["cases"]
    cc = {"PASS":0,"FAIL":0,"BLOCKED":0}
    for c in cs: cc[c.get("verdict","?")] = cc.get(c.get("verdict","?"),0)+1
    links = [chain(c) for c in cs]
    ever = {k: any(l[k] for l in links) for k in links[0]}
    missing_links = [k for k,v in ever.items() if not v]
    print(f"  {d['run_id']}  {d['created_at']}  verdict={d['verdict']:7s} consistent={d['verdict']==worst(cs)!s:5s}"
          f" cases={len(cs)} {cc} run_grade=unknown(mode=unknown, no manifest)"
          f" missing_links={','.join(missing_links) if missing_links else '(none)'}")
    forms = {}
    for c in cs:
        pr = presence(c.get("context", _MISSING), context=True)
        k = pr["present_kind"] if not pr["present"] else (
            "recorded_no_pack" if not pr.get("context_pack_present") else "pack")
        forms[k] = forms.get(k, 0) + 1
    if not forms.get("pack"):
        print(f"      context link missing for all {len(cs)} cases; forms={forms}")
    else:
        print(f"      context forms={forms}")
