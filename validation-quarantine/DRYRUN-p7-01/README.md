# P7-01 pre-implementation dry runs (retained evidence)

These two read-only scripts validate the proposal's rules **before** any implementation exists. They were
run against the gitignored real records and against a synthetic adversarial tree, and their raw output is
captured verbatim next to them. They are not part of the delivered reader.

## Inputs (full SHAs)

| Input | Value |
| --- | --- |
| P7 worktree HEAD when first captured (rev 4) | `1b9791a125617deb85b84a565ccb61e4d95fe145` |
| proposal baseline | `039f5a3e7800704e5f0478d01992f00eeb2ca3f1` |
| source repo HEAD (holds the records) | see the command below; recorded at capture time in this file's history |

The digest over the nine records is computed by this exact command, run in the source repo
(`jasmine-core-v1`):

```sh
find validation-runs -name 'run-*.json' | sort | xargs shasum -a 256 | shasum -a 256
```

## How to re-run

```sh
python3 validation-quarantine/DRYRUN-p7-01/p7-real-records-dryrun.py
PYTHONPATH=src python3 validation-quarantine/DRYRUN-p7-01/p7-fixture-dryrun.py
```

The second script recreates `/tmp/p7-fixroot` and `/tmp/p7-outside` on every run and restores the
permissions of its unreadable fixture directory in a `finally` block; it touches nothing inside the
repository. The first reads only the gitignored records.

Both scripts implement the **rev-5** rules, and each is a plain, readable transcription of the relevant
contract section, not a copy of the future reader.

## What each script checks

`p7-real-records-dryrun.py` — the §2 discovery walk (depth cap 4, dot-dirs and `__pycache__` excluded,
symlinks never traversed), the §1.1 presence rules **including** the `present` / `context_pack_present`
split, the §4.1 chain links (Extraction and Resolution have no recorded source at all; `case.interpretation`
is deliberately **not** mapped to Resolution), the §4.2 grade ladder, and the recorder's own worst-case
verdict rule recomputed per run.

`p7-fixture-dryrun.py` — the §2 status precedence (`corrupt > duplicate > ok`), the §3 manifest and path
rules, and the §5 content gate, over a synthetic tree:

* a good file, a **tampered** file, a **missing** file, a **symlinked** file and a **symlinked intermediate
  directory**;
* manifest paths using `../`, an absolute path, a `C:` drive-relative form, a UNC form and a URL;
* a **corrupt** record and a **duplicate** stem;
* an **unreadable subtree** (`chmod 000`), the negative for §2's `index_incomplete` rule — which also
  demonstrates the trap that `os.walk(onerror=None)` silently ignores a directory it cannot list, so a
  reader must pass `onerror` to be able to report it;
* a synthetic FAIL record with `synthetic: true`, exercised through the §4.2 grade ladder;
* a `text/plain` log whose only secret is **provider-shaped** (`Authorization: Bearer ghp_…`) while the
  manifest declares `credential_shaped: false`;
* a `text/plain` log whose only secret is **assignment-shaped** (`db_password=hunter2hunter2`), which
  `looks_like_credential` does **not** match.

## Results (the `.txt` files hold the raw output)

* **Real tree**: 9 run-shaped records found from one root; every walk entry not treated as a record is
  listed with its reason; `verdict_consistent = True` for all nine; chain links missing: `extraction` 9/9,
  `resolution` 9/9, **`context` 9/9**, `agent` 6/9 (the six BLOCKED runs); every run graded `unknown`
  because no manifest exists.
* The Context link is missing for all 63 cases in **three** distinct recorded forms — `reason_only` 27,
  `recorded_no_pack` 30 (run details such as `db_path`/`processes`, recorded but holding no pack),
  `sentinel` 6 — which is exactly the distinction §1.1 turns into `context_pack_present`.
* **Adversarial tree**: `../`, absolute, `C:`, UNC and URL declared paths all refuse the manifest with the
  offending entry named; the symlinked file and the symlinked intermediate directory pass the string
  grammar and are refused at open; tamper → `mismatch`, missing → `missing`; a single bad declared path
  makes the whole manifest corrupt; the unreadable subtree yields `unreadable_entries = 1`, which §2 turns
  into `503 index_incomplete` rather than a page that silently omits rows.
* **The security property, demonstrated**: for the provider-shaped log `looks_like_credential = True`; for
  the assignment-shaped log `looks_like_credential = False` — so a scan built only on that function would
  **serve** it — while `canonical.redact_text(text) == text` is `False`, so the §5 gate withholds it as
  `403 original_withheld` and `form=masked` serves `db_password=[REDACTED]`. The gate is therefore strictly
  stronger than the scan it replaces, which is the property the third and fourth reviews required.
