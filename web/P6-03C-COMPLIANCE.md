# P6-03c compliance — the Validation Inspector and its real-browser drill-down

**Status: IMPLEMENTED, independently reviewed and independently validated; PARTIAL / DEPENDENCY_PENDING.**
The read slice it consumes (P7-01) exists on its own branch and is not merged; **P7-01 acceptance
criterion 1 remains BLOCKED** because no real P7-format FAIL run exists on disk. Nothing here makes the
P6 or P7 phase PASS.

This file is the implementation and validation record. The API it is measured against is the frozen
P7-01 contract, copied verbatim into this worktree as `contracts/p7-validation-read.v1.json`; the
decisions behind that API are in the P7-01 worktree's `docs/adr/0015-p7-validation-read-contract.md`.

## Models, stated rather than omitted

`gpt-6.1-sol` is unreachable in this environment, so every role ran on **`deepseek-flash`**: the
Inspector implementation, the drill-down harness, two independent reviews and two independent
validations, each in its own agent context. Each report states its model at the top.

## What was delivered, and at which commit

| Commit | What |
| --- | --- |
| `f811f58` | the proxy allowlist: `p7_pending_paths` (five routes) plus `P6_ENABLE_P7_READS`, gated identically in `server/core-proxy.ts` and `server/serve.mjs`, with the frozen contract copied across and `tests/p6/test_p7_proxy_paths.py` pinning the five patterns to the contract's routes |
| `3577987` | the Inspector (`web/src/views/ValidationViews.tsx`, `ValidationDependency.tsx`, routes `#/validation/runs`, `/{run}`, `/{run}/manifest`, `/{run}/artifacts/{artifact_id}`, 37 tests) and the drill-down harness (`scripts/p6/p7_realcore_{env,ui}.py`, `p7_realcore_ui_trace.mjs`) |
| `de1b058` | the first review's two MAJOR findings: `empty_reason` is computed over the **visible** set, so the empty-state text must not claim the run root is empty; and a `duplicate` row is parseable, so its note must not deny the verdict it displays |
| `b1a9658` | the round-2 re-review's three residuals: an unreported `context_pack_present` no longer renders as a negative measurement; the fixture-census check reads the measured overlap instead of a literal `True`; the token scan names the three files it cannot cover |

**Final candidate: `b1a965872dcec9a1b3d979f017d95730500c0f45`** (branch tip, clean tree).

## What the Inspector shows

The run list with Core's own pagination and the five filters, corrupt and duplicate rows kept and
marked unusable with their reasons rather than hidden; the detail with the eight T06 chain links
(`raw`, `extraction`, `resolution`, `context`, `agent`, `tool`, `evidence`, `state`) each with its
verbatim `missing_reason`, the seven-section `present_kind` table with `context_pack_present`, and the
case-level evidence grade; the manifest with its `declared_mode` vs derived `mode`, sources,
`manifest_sha256`, per-file integrity, and the `manifest_authentication: "unauthenticated"` warning;
and the artifact metadata and content with `digests.original` and `digests.masked` as separate labelled
values and content rendered as text only, never as HTML.

Every refusal reads as its own condition — 401, 403 `forbidden_scope`, both 404s, the four 409s, 413,
415, and 503 `validation_not_configured` with its `details.reason` — and a refusal is never rendered as
"no data". The `original` control is a labelled probe: `forms` is the contract's answer to whether the
original is available, so when it lacks `original` the control says so and still asks Core once, so the
real `403 original_withheld` reason is displayed rather than inferred.

## The drill-down, and what it proves

`scripts/p6/p7_realcore_ui.py` builds an isolated Core from the P7-01 branch tip with `git archive`,
starts four of them (real root / fixture root / unset root / real root in `acl`), builds `web/dist`,
serves it through the dashboard's own `serve.mjs` with `P6_ENABLE_P7_READS=1`, and drives
**Chrome over CDP** through that proxy. The operator's records tree is read-only throughout.

Final run: **24 browser checks PASS / 0 FAIL**, **5 python checks PASS / 0 FAIL / 1 BLOCKED**. The
BLOCKED entry is the criterion-1 census, which is the finding rather than a failure.

What it establishes, on the nine real P0 records:

* the chain is real: Chrome → `serve.mjs` → a Core extracted from `00d6040` (its `reads.py` hash equals
  `git show 00d6040:…`) → the records on disk;
* all nine run ids appear, walked across pages with no repeats or omissions, and each displayed verdict
  equals the file's — the run, the API list, the API detail, the DOM and a paginated fetch all agree,
  and no PASS appears where a file says BLOCKED;
* `extraction` and `resolution` are present=false with their verbatim reasons, `context` is absent in
  all nine across the three recorded forms (27 reason-only, 30 recorded-but-no-pack, 6 sentinel, 0
  packs), and `agent` is present=false in all six BLOCKED runs and true in the three PASS runs;
* a real run's manifest reports `status: absent` with the reason that its history is **not
  reconstructed**, and the unsigned-manifest warning is shown;
* in `acl` mode the list is grant-filtered both ways, an ungranted run and a nonexistent run get the
  same one-code `404 validation_run_not_found`, and a key without `validation:read` is refused
  `403 forbidden_scope` **in the browser** rather than shown an empty list;
* on the labelled fixture root only: the masked content matches an independent
  `canonical.redact_text` recomputation, `digests.original` differs from `digests.masked`, the original
  is withheld with reason `mask_not_noop` while the masked body stays on screen, and the HTML artifact
  is rendered as text with nothing parsed into the page and the global it tries to set undefined;
* an unset run root is `503 validation_not_configured (details.reason: unset)`, not an empty list.

## Criterion 1 is BLOCKED, and nothing here pretends otherwise

Independent census of the operator's tree: nine records, run verdicts PASS 3 / BLOCKED 6 / **FAIL 0**;
63 cases, PASS 57 / BLOCKED 6 / **FAIL 0**; **0 `failure_class`, 0 `failure_output`**. The FAIL path is
exercised only by the fixture run that labels itself `synthetic: true`, which is not evidence and is
counted toward no criterion — the harness now *measures* that disjointness
(`fixture-census.json.fixture_ids_inside_census`) instead of asserting it, and the validator falsified
that gate end to end by planting the fixture id in a synthetic census.

**Unblock action: the P7 owner records one genuinely failing scenario with `recorder.py` into the
configured run root**, after which criterion 1 is re-validated against that real record.

## Independent gates

| Round | Candidate | Result |
| --- | --- | --- |
| Review 1 | `3577987` | APPROVE WITH NAMED MANDATORY CHANGES — two MAJOR honesty findings (F1, F2) plus F3–F12 |
| Re-review | `de1b058` | **APPROVE** — both mandatory items closed, verified by the reviewer's own rendered-DOM probe and by mutation for the two test-only negatives |
| Validation 1 | `3577987` | ten claims PASS, no counterexample |
| Final validation | `b1a9658` | **ten claims PASS on a fresh run**; the harness-honesty finding closed, with F6 falsified end to end |
| Residual confirmation | `b1a9658` | the three residuals (F3, F6, F7) and A6 **CONFIRMED CLOSED** |

All four reports are in `validation-runs/p6/` (gitignored), together with the harness bundles.

## Advisory follow-ups (recorded, not blocking)

* With `visibility` absent — a Core older than the frozen contract — the empty-state text falls back to
  the root-empty wording. Unreachable against a conformant Core, which always sets `visibility`.
* An unknown `record_status` (the contract pins `ok` / `corrupt` / `duplicate`) falls into the
  "unavailable" branch and would deny fields a row displays.
* `fixture-census.json`'s `measurement` prose is a static string, so it would contradict the flag in a
  deliberately falsified bundle.
* The harness's process exit code is not driven by `python_checks`: a failing check is recorded in
  `result.md` and `manifest.json`, but the driver still exits 0.
* `manifest.json`'s `dashboard.dirty_note` says the views were uncommitted; it is true of the run that
  happened before `3577987` was committed and stale afterwards.
* Cosmetics: two literal `**` markers reach the DOM in the duplicate note; `token-scan.json.not_scanned`
  is a literal list; one `_LOG.append` is now redundant.

## Note on the evidence directory

The two review directories and the validation directory were accidentally deleted by the coordinator
during a tidy-up, because the cleanup rule ("no `manifest.json` and no `BUNDLE-FROZEN.json` ⇒ aborted
harness bundle") also matched report directories. The reports were **re-emitted by the same independent
agents** — and for the validators, the raw evidence was **regenerated by re-running the tools**, not
reconstructed from memory — with each file carrying a header saying so. The harness bundles themselves
survived the deletion untouched. Recorded here rather than hidden.
