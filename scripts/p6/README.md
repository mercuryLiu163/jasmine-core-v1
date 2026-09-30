# P6 scripts

Two local-only tools for the P6 dashboard. Neither is a product feature and
neither is evidence of a passing Gate on its own.

## `p6_scratch.py` — throwaway Core with realistic data

Builds a private scratch database (schema v6) and workspace, mints a human /
agent / system actor plus a **read-only** token, and seeds projects, Tasks,
Steps in every state, sessions, raw Events, an ACTIVE and a PROPOSED Rule,
PASS/FAIL Evidence and one confirmed Evidence — all through the real HTTP API of
the real Core build.

```bash
python3 scripts/p6/p6_scratch.py build --out /private/tmp/p6-scratch
python3 scripts/p6/p6_scratch.py serve --out /private/tmp/p6-scratch --port 8789
```

`build` writes `descriptor.json` and `tokens.json` (mode 0600) under `--out`.
It never touches the operator's database, hooks, accounts or global config, and
`descriptor.json` is what the verification steps below read for IDs.

## `browser_trace.mjs` — real Chrome walkthrough

Drives headless Chrome over the DevTools Protocol against a running dashboard:
sign-in, Overview → Project → Task board, keyboard activation of a source link,
deep-link refresh, Rule and Evidence detail, the explicit `未接入` panels, an
unknown-route check, an accessibility name scan, and sign-out.

```bash
node scripts/p6/browser_trace.mjs \
  --ui http://127.0.0.1:5180 \
  --token-file /private/tmp/p6-scratch/tokens.json --token-key read \
  --project prj_… --task tsk_… --rule rul_… --evidence evd_… \
  --out validation-runs/p6/<run-id>
```

It writes `click-trace.jsonl`, `browser-requests.jsonl` (Authorization recorded
only as `present(redacted)`/`absent`), `screenshots/`, `chrome-stderr.log` and
`browser-trace.md`. The Chrome profile is created in the system temp directory
and deleted afterwards, so cookies and history never enter the evidence bundle.

Exit code is non-zero if any step failed, which is what makes the script usable
as a Gate command.

## `p6_verify.py` — assemble the §8 evidence bundle

Runs the browser trace, snapshots the Core objects it touches **before and
after** (to show no Truth was written), records the raw `tsc` / `vitest` /
`vite build` output, and writes `manifest.json`, `scenario.md`,
`api-requests.jsonl`, `api-responses.jsonl`, `truth-before-after.json` and
`result.md`.

```bash
python3 scripts/p6/p6_verify.py \
  --ui http://127.0.0.1:5180 --core http://127.0.0.1:8789 \
  --token-file /private/tmp/p6-scratch/tokens.json --token-key read \
  --project prj_… --task tsk_… --rule rul_… --evidence evd_… \
  --out validation-runs/p6/<run-id>
```

Exit code is non-zero when a browser step failed or when any before/after Core
read differs — a Truth write during a read-only Gate is a failure, not a detail.

The `result.md` it writes is the **implementer's** result. An independent
reviewer and an independent validator must add their own reports next to it
before the run counts as a reviewed Gate.


## `p6_t07_checks.mjs` — the T07 actions a single trace does not cover

Headless-Chrome phases, one per run, against an isolated fixture:

```bash
node scripts/p6/p6_t07_checks.mjs --phase paginate --ui http://127.0.0.1:5190 \
  --token-file /private/tmp/p6-scratch-t07/tokens.json --token-key read --out <dir>
node scripts/p6/p6_t07_checks.mjs --phase longtext --event evt_… (same flags)
node scripts/p6/p6_t07_checks.mjs --phase accept --task tsk_… --event evt_… --evidence evd_… (same flags)
# offline/recover are orchestrated: the phase writes `ready-for-core-stop`, the
# caller stops Core and touches `core-is-down`, then restarts it and touches
# `core-is-up` before the recover phase runs.
```

- `paginate` — pages through a 259-Event list and asserts no duplicate and no
  missing `seq`;
- `longtext` — a 216,015-character Event must render collapsed without hiding the
  source controls;
- `offline` / `recover` — with Core stopped, the failure is visible and is never
  an empty list; after restart the UI reads Core Truth again;
- `accept` — a real `task.accepted` must appear as a gate record with the
  `evidence_ids` Core recorded.

## `p6_accept_fixture.py` — produce a real `task.accepted`

Drives one Task to acceptance through the frozen API (respecting the ADR 0006/
0007 confirmation rules) so the `accept` phase above has something real to read:

```bash
python3 scripts/p6/p6_accept_fixture.py /private/tmp/p6-scratch-accept 8899
```
