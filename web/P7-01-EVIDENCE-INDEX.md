# P7-01 evidence index

Everything below is retained in the worktree `_worktrees/p7-01` on branch `p7-01-validation-read`. The
**final candidate** is `0ae80411824739b11485c61ed388d86093f2be7c`; the derived contract pins
`46a33d78d03e4d8d9711c96ded62072de8a276be`, the commit it was derived from.

Every review and validation ran in its own agent context on `deepseek-flash`; each report states its model
and baseline at the top.

## 1. Specifications and contracts (tracked)

| Path | What it is |
| --- | --- |
| `web/P7-01-CONTRACT-PROPOSAL.md` | the frozen specification, revision 5, approved by the fifth review |
| `web/contracts/p7-validation-read.v1.json` | the derived, machine-checked contract (`--check` verified at the pin) |
| `docs/adr/0015-p7-validation-read-contract.md` | the seven new codes, discovery and ordering rules, the manifest convention, the permission model, AD1/AD2, the documented F5/F6 divergences, and the bounded-read limits |
| `web/P7-01-PHASE-REPORT.md` | this phase's report: deliverables, acceptance results, follow-ups, residual risk |

## 2. Contract-proposal reviews (5 rounds, `validation-quarantine/REVIEW-p7-01-proposal/`)

| File | Round | Verdict |
| --- | --- | --- |
| `findings.md` | rev 1 `3ebe46c` | APPROVE WITH NAMED MANDATORY CHANGES (12) |
| `RE-REVIEW-findings.md` | rev 2 `f397213` | APPROVE WITH CHANGES (11 items, N1–N10) |
| `RE-REVIEW-2-findings.md` | rev 3 `c8fd597` | APPROVE WITH CHANGES; 6 new defects D1–D6 |
| `RE-REVIEW-3-findings.md` | rev 4 `1b9791a` | APPROVE WITH CHANGES; 4 mandatory items + A1–A10 |
| `RE-REVIEW-4-findings.md` | rev 5 `0f7b2ec` | **APPROVE (freeze as-is)** — no mandatory changes |

## 3. Pre-implementation dry runs (`validation-quarantine/DRYRUN-p7-01/`)

Read-only scripts that validated the proposal's rules against the nine real records and an adversarial
synthetic tree before any code existed, with their raw output and a re-run recipe.

| File | What it shows |
| --- | --- |
| `README.md` | inputs with SHAs, the exact digest command, how to re-run, and the results |
| `p7-real-records-dryrun.py` / `.txt` | 9 run-shaped records from one root; `verdict_consistent` for all nine; the three Context forms; the chain links |
| `p7-fixture-dryrun.py` / `.txt` | escape refusals, symlink-at-open, tamper/missing, the unreadable subtree (including that `os.walk(onerror=None)` silently swallows it), and the proof that `looks_like_credential` alone would have served the `db_password=` log |

## 4. Implementation reviews (3 rounds, `validation-quarantine/REVIEW-p7-01-implementation/`)

| File | Candidate | Verdict |
| --- | --- | --- |
| `findings.md` | `1f7b0af` | APPROVE WITH NAMED MANDATORY CHANGES (F1 mandatory; F2–F9) |
| `RE-REVIEW-findings.md` | `a252097` | **APPROVE**; five non-blocking advisories N1–N5 |
| `FINAL-REVIEW-findings.md` | `0ae8041` | **APPROVE** |
| `FIX-REVIEW-findings.md` | `f0aea88` | **APPROVE** — the four operator-reported defects reproduced on `0ae8041` and confirmed closed; the behavioural candidate |

## 5. Acceptance validations (`validation-quarantine/VALIDATE-p7-01/`)

| File | Candidate | Result |
| --- | --- | --- |
| `findings.md` | `1f7b0af` | criteria 2–6 PASS (760 checks), criterion 1 BLOCKED |
| `RE-VALIDATION-findings.md` | `a252097` | criteria 2–6 PASS (879 checks), criterion 1 BLOCKED |
| `FINAL-VALIDATION-findings.md` | `0ae8041` | criteria 2–6 PASS (904/904 checks), criterion 1 BLOCKED |
| `FIX-VALIDATION-findings.md` | `f0aea88` | **criteria 2–6 PASS (904/904 checks) plus 100/100 defect probes with 365 timed requests and 0 hangs; negative controls against `0ae8041` detected all four defect classes** — the behavioural candidate |

The validator's own harness and per-criterion scripts, with all raw output, are retained beside the
reports:

| Path | What it holds |
| --- | --- |
| `harness.py`, `c1_fail.py` … `c6_values.py`, `extra_probes.py`, `analysis_real_records.py` | the six criterion scripts and the supplementary probes |
| `evidence/` (68 files) | raw output for the first validation round |
| `evidence-revalidation/` (87 files) | raw output for the second round |
| `evidence-final/` (99 files) | raw output for the final round |
| `evidence-fix/` | raw output for the fix round: the six criteria, the four defect classes under a changing filesystem, the negative controls against `0ae8041`, and per-request timings |

## 6. How to re-check the delivered candidate

```sh
cd "_worktrees/p7-01"
git rev-parse HEAD                                   # 477e17fd71161b31e1920b7306dc70760ddd50b4
python3.13 scripts/p7/p7_contract.py --check         # matches e79c1172…   (exit 0; exit 2 if it cannot look)
PYTHONPATH="src:tests" python3.13 -m unittest discover -s tests -t tests -p "test_validation_read.py"
PYTHON=python3.13 ./scripts/run-tests.sh             # full suite; only the pre-existing P4 failures
```

The four defects the operator's re-check found are described in the phase report §4b, with the
before-and-after measurements and the regression tests that pin each fix.

The three real-record tests read the operator's gitignored tree; point `P7_REAL_RUNS_ROOT` at a tree of
real records elsewhere, or they skip with a message rather than pretending to have run.
