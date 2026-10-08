# P7-01 pre-implementation dry runs (retained evidence)

These two read-only scripts validate the proposal's rules **before** any implementation
exists. They were run against the gitignored real records and against a synthetic
adversarial tree, and their raw output is captured verbatim next to them.

## Inputs (full SHAs)

| Input | Value |
| --- | --- |
| P7 worktree HEAD at capture | `c8fd597ac6f6617072a1772595c54ec141d9c01b` |
| proposal baseline | `039f5a3e7800704e5f0478d01992f00eeb2ca3f1` |
| source repo HEAD (holds the records) | `7016dabb8926d6bd6599f1f56b8f39cbda253ebf` |
| sha256 over the sorted sha256 list of the nine run records | `1c9deec38ccf0015269f61fd3f3517aa7eb93327b86f75ffc78c3b1ee6032464` |

## What each script checks

* `p7-real-records-dryrun.py` — the §2 discovery walk (depth cap 4, excludes dot-dirs and
  `__pycache__`, never traverses symlinks) over `validation-runs/`, the §1.1 presence rules,
  the §4.1 chain links and the §4.2 grade defaults, plus the recorder's own worst-case verdict
  rule recomputed per run.
* `p7-fixture-dryrun.py` — the §2 status/precedence rules and the §3 manifest and path rules
  over a synthetic tree built in `/tmp`: a good file, a tampered file, a missing file, a
  symlinked file, a symlinked intermediate directory, `../`/absolute/`C:`/UNC/URL declared
  paths, a corrupt record, a duplicate stem, a synthetic FAIL record, and a "text/plain" log
  whose only secret is provider-shaped while the manifest declares `credential_shaped: false`.
  It also exercises the §5 scanning rule against `canonical.redact_text` / `looks_like_credential`.

## How to re-run

```sh
python3 validation-quarantine/DRYRUN-p7-01/p7-real-records-dryrun.py
PYTHONPATH=src python3 validation-quarantine/DRYRUN-p7-01/p7-fixture-dryrun.py
```

The second script recreates `/tmp/p7-fixroot` and `/tmp/p7-outside` on every run; it touches
nothing inside the repository. Neither script is part of the delivered reader.

## Results (see the .txt files for the raw output)

* real tree: **9 run-shaped records found from one root**, 179 ignored walk entries with
  attributable reasons, `verdict_consistent = True` for all nine; chain links missing:
  extraction 9/9, resolution 9/9, **context 9/9** and agent 6/9 (the six BLOCKED runs).
  The context link is missing for all 63 cases in **three** distinct recorded forms —
  `reason_only` 27, `recorded_no_pack` 30 (run details such as `db_path`/`processes`, recorded but
  no pack), `sentinel` 6 — which is the distinction §1.1 turns into `context_pack_present`;
  every case graded `unknown` because no manifest exists.
* adversarial tree: `../`, absolute, `C:` and UNC and URL declared paths all refuse the
  manifest with the offending entry named; the symlinked file and the symlinked intermediate
  directory pass the string grammar and are refused at open; tamper → `mismatch`, missing →
  `missing`; a single bad declared path makes the whole manifest corrupt; the log with
  `credential_shaped: false` is still withheld as original (the masker is not a no-op).
