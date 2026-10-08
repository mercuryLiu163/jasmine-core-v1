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

## `p6_pending_check.mjs` — "未接入" honesty

Walks every placeholder route in a real browser and asserts that each unfrozen
area (P2 interpretation, P3 checkpoint/context, P4 memory, P7-01 validation, P5
registry online, P5/P7 backup) renders a `未接入` panel naming its owner and the
contract it waits for, that an unknown placeholder key is reported rather than
silently rendered, and — the important half — that the page never even attempts
a request to an unfrozen endpoint.

```bash
node scripts/p6/p6_pending_check.mjs --ui http://127.0.0.1:5180 \
  --token-file /private/tmp/p6-scratch/tokens.json --token-key read \
  --out <dir> --task tsk_…
```

Exit code is non-zero if any area is unmarked, an unknown key is silently
rendered, or an unfrozen path is requested.

## `p6_p2_fixture.py` — P1 scenario + real P2 rows

Composes on `p6_scratch.Scratch` (so the Task board has Steps) and then produces
real P2 data through the real pipeline — `InterpretationStore.process`,
`ResolverStore.resolve`, `ReviewStore` — with a component-simulation provider.
It seeds an applied resolution, a `PENDING_REVIEW` resolution with its review
row, a `correct`ed child interpretation and a rejected one, plus enough
interpretations to page.

```bash
python3 scripts/p6/p6_p2_fixture.py build --out /private/tmp/p6-p2
python3 scripts/p6/p6_p2_fixture.py serve --out /private/tmp/p6-p2 --port 8795
```

`tokens.json` then also carries `p2_read`, a token with exactly the P6-02 read
scopes. The descriptor records the provider as a simulation, not a model run.

## `p6_lazy_recovery_probe.py` — the GET that writes

Core's interpretation and review reads call `maintenance.recover` /
`InterpretationStore._recover`, which can write. This probe proves it instead of
assuming it: it copies a fixture, inserts the crashed-provider precondition (an
interpretation past its deadline with no result row — the chain is append-only by
trigger), serves that copy itself, issues a plain
`GET /v1/interpretations?limit=50`, and reports the before/after row counts.

```bash
python3 scripts/p6/p6_lazy_recovery_probe.py --db /private/tmp/p6-p2/core.db \
  --workspace /private/tmp/p6-p2/workspace \
  --token-file /private/tmp/p6-p2/tokens.json --token-key p2_read \
  --out validation-runs/p6/<run-id>
```

Observed: `interpretation_results` 8 → 9 with
`status=INTERRUPTED, error_code=provider_interrupted`. Therefore P6-02 describes
these endpoints as "GET, but not guaranteed side-effect free".

## `p4_write_fixture.py` — the **simulated** P4 write Core (P6-03b2)

P4 is unmerged, so the six Memory writes have nothing real to run against. This fixture
subclasses `p4_read_fixture.Handler` (so the shell routes and the seven reads are the same code)
and adds `do_POST` for exactly the frozen contract's six writes, plus a by-id read override so a
mutation is visible on readback and `GET /v1/events/{evt}` so the audit readback has something to
read.

```bash
python3.13 scripts/p6/p4_write_fixture.py --port 8918 \
  --token <human>  --scopes "objects:read,memory:read,memory:manage,memory:reflect" \
  --token <agent>  --scopes "objects:read,memory:read,memory:manage,memory:reflect,actor:agent" \
  --token <reader> --scopes "objects:read,memory:read"
```

It stamps `X-P6-Fixture: simulated-p4-writes` on every reply, asserts every recorded response
shape against the frozen contract before serving, and says in its banner which parts follow the
pinned `errors.py`/`memory/jobs.py` and which are invented. Its docstring lists what it cannot
reproduce — no Truth store, engine or real jobs, a scope stand-in for actor kind, a replay
identity looser than P4's command hash, a fixture-invented nested `event`, `job: null` on a
lifecycle reply, and readback on the by-id routes only.

Four `/v1/_p6_fixture/` controls exist for the Gate and are **not** product surface (no token, not
in any allowlist, called directly on the fixture port): `state` (the simulation's command log and
revisions), `drift` (another actor moved the item), `drop-next` (apply the next transition and then
fail the reply), `set-job-status` (reach `DONE`/`RUNNING`/`PENDING`).

## `run_p4_write_gate.sh` — the P6-03b2 Gate, end to end

```bash
scripts/p6/run_p4_write_gate.sh [--keep]
```

It archives `HEAD` with `git archive`, borrows `web/node_modules` read-only, builds with
`vite build`, starts its own fixture and **two** proxies on free-chosen ports (writes on, writes
off), records per-run throwaway tokens in the temporary tree only, runs
`browser_trace.mjs --p4-writes` twice with the fixture restarted in between (the Gate mutates it)
and once with `--p4-writes-off`, requires the two ON runs to describe identical step sequences and
every record to pass, writes the bundle README from the run's own click-trace files, and freezes
the bundle. It runs against a Core **it started**: each host's startup line is required in its own
log, because the first version used fixed ports and one run talked to a stale dashboard from an
earlier session.

## `freeze_bundle.py` — write once, and prove it

```bash
python3.13 scripts/p6/freeze_bundle.py --freeze <bundle> --commit <sha> --kind <what> \
  [--asset index-*.js --asset-sha256 … --tree-sha256 … --fixture-value … --dependency-json …]
python3.13 scripts/p6/freeze_bundle.py --verify <bundle>     # exit 1 on any change, 2 with no manifest
```

`--freeze` hashes every file in the bundle (except the manifest itself) and records the binding
facts; `--verify` re-hashes and reports changed, added and missing files, exiting **2** when there
is no manifest at all so "could not look" is never read as "verified". `tests/p6/test_freeze_bundle.py`
covers each of those, and also verifies the real P6-03b1 bundle — which was frozen by hand before
this tool existed — so the tool is checked against evidence it did not produce.
