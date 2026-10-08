# ADR 0015: P7-01 Validation read contract

Status: implementation candidate. **No P7 Gate and no acceptance criterion is
claimed by this ADR.** It freezes the read-only contract and records what remains
BLOCKED. The normative prose is `web/P7-01-CONTRACT-PROPOSAL.md` revision 5,
independently approved with no mandatory changes
(`validation-quarantine/REVIEW-p7-01-proposal/RE-REVIEW-4-findings.md`); this ADR
records the decisions an implementer and a reviewer must be able to point at.

Source requirements: the approved proposal §1–§11; the P7 taskbook's T06 chain
(`Raw → Extraction → Resolution → Context → Agent → Tool → Evidence → State`);
the P4 authorisation mechanism frozen by ADR 0014; ADR 0003 for the error shape.

## 1. Scope

Five `GET` routes read the run records `src/jasmine_core/validation/recorder.py`
already wrote. The surface never writes, never runs a scenario, never re-signs or
upgrades a verdict, never resolves a caller-named path, and never renders HTML.
The root is operator configuration only (`JASMINE_CORE_VALIDATION_RUNS`, no
default); a request can never name or extend it.

## 2. The seven new codes, their triggers and the negatives that exercise them

No existing code is redefined. `NotFound(kind, id)` produces the two not-found
codes; it never emits a bare `not_found`.

| Code | Status | Trigger | Acceptance negative |
| --- | --- | --- | --- |
| `validation_not_configured` | 503 | root unset / missing / not a directory / symlinked / unreadable (`details.reason ∈ {unset, missing, not_a_directory, symlinked_root, root_unreadable}`); a knowingly incomplete index (`details.reason: "index_incomplete"`); a declared artifact that exists but cannot be read (`details.reason: "artifact_unreadable"`) | root unset; symlinked root; `chmod 000` subtree |
| `validation_index_too_large` | 503 | the walk exceeded `MAX_SCANNED_ENTRIES = 20_000` or `MAX_RUN_SHAPED = 500` | (reported, never silently truncated) |
| `validation_run_corrupt` | 409 | `single_user` mode only: a run-shaped record is unparseable, has the wrong `run_id`, or lacks a required field | corrupt fixture record |
| `validation_run_ambiguous` | 409 | `single_user` mode only: two run-shaped files share this stem | duplicate-stem pair |
| `validation_manifest_corrupt` | 409 | `single_user` mode only: `manifest.json` is present and breaks a §3 rule (`details.rule` names it) | `../`, absolute, `C:`, UNC and URL declared paths |
| `artifact_modified` | 409 | served bytes disagree with the declared `sha256`/`bytes` | byte-flipped file |
| `original_withheld` | 403 | `form=original` requested while the masker is not a no-op, the manifest declares `credential_shaped: true`, or the bytes are not strict UTF-8 (`details.reason ∈ {credential_shaped_declared, mask_not_noop, not_utf8}`, `details.masked_sha256`) | assignment-shaped secret |

Reused codes: `invalid_request` 400, `unauthenticated` 401, `forbidden_scope`
403, `payload_too_large` 413, `unsupported_media_type` 415, and the existing
`memory_deployment_not_ready` 503. `schema_version_unsupported` and
`database_busy` are deliberately **not** listed: no Validation route can produce
them.

**Route-scoped precedence, first matching row wins.** Stage A (authentication,
scope) precedes stage B (root, index completeness, deployment readiness, caps,
request validity), which precedes stage C (resource resolution and content). On
detail/manifest/artifact/content the root is opened first (operator
configuration, not attacker-controlled), so a malformed id is answered before any
*record* file is read; on list the completeness check precedes readiness and the
caps. In `acl` mode a run the caller may not read is reported with the **same**
`404 validation_run_not_found` as a run that does not exist, including a
corrupt/duplicate record, and `ProjectAccess.require` raising
`NotFound("project", …)` is translated to the run-level code so **no Validation
route ever emits `project_not_found`**.

## 3. Discovery and ordering

The root is read lazily and cached **against the raw configured string**, so a new
value is never masked by a stale cache and a request can never name the path. The
root is opened `O_DIRECTORY|O_NOFOLLOW`, so a symlinked root is refused. The walk
is recursive to four directory levels, prunes dot-directories, `__pycache__` and
every symlinked directory, and considers only regular files whose basename matches
`^run-[0-9]{8}T[0-9]{6}Z\.json$`; a symlinked `run-*.json` is not a record.
`os.walk(..., onerror=...)` is required: the default `onerror=None` silently
swallows an unlistable directory, which would make `index_incomplete` unreachable.

A run-shaped file is one status: `corrupt > duplicate > ok`. `corrupt` is kept and
listed, never dropped and never reported as an empty list; `duplicate` considers
only parseable records, because a file that cannot be parsed has no `run_id` to
compare. The single ordering authority is
`(status_rank asc: ok=0, duplicate=1, corrupt=2, created_at_key desc, run_id_key
desc, relative_path asc)`, where `created_at_key` is the recorded `created_at` for
a parseable record and `""` for a corrupt one, and `relative_path` is the unique
final tie-break, so pages cannot overlap or skip. `total` counts the filtered
**visible** records; a corrupt row matches no positive filter. An incomplete
index is never paginated: `unreadable_entries > 0` returns
`503 validation_not_configured` with `details.reason: "index_incomplete"` and its
first 20 `{path, error}` samples.

## 4. The manifest convention

`<run_dir>/manifest.json` may declare `schema`, `run_id`, `frozen_at`, `mode`,
`projects`, `sources` and `files[]`. It is `frozen`, `absent` or `corrupt`; a
manifest that breaks any grammar rule (schema, `run_id`, unique
`artifact_id` matching `^art_[a-z0-9][a-z0-9_.-]{0,63}$`, a relative `path` with
no `..`/backslash/absolute/`C:`/UNC/URL/NUL, 64-lowercase-hex `sha256`, a
non-negative integer `bytes`, ≤ 128 files, ≤ 1 MiB) is wholly corrupt and none of
its artifacts is even partially trusted. **`manifest_authentication` is
`"unauthenticated"`**: `frozen_at`, `mode` and `credential_shaped` are
self-declared and unsigned, and the reader never calls them verified.

`mode` is derived, not trusted: `real` qualifies only with non-empty `projects`
and recorder provenance (`{path, sha256}` or `"<relative path>@<64 hex>"`),
`component`/`simulated` are as declared, and everything else is `unknown`. The
evidence grade is marker-based (`synthetic`/`simulated`/`fixture`/`mock` →
`simulated`, `component` → `component`) with **no substring search over free
text**; the run grade is the least trustworthy of the effective mode and every
case grade, and the section grade is the same `min` against the case grade, so no
section can exceed its case. An `unknown` mode is never improved by a case grade.
The residual — an *unmarked* mocked `agent_response` under `mode: real` — cannot
be detected by a read-only contract and is closed only by a P7 recorder change,
which is explicitly outside this slice.

## 5. Permissions

The staged check is: dispatch authenticates and requires `validation:read` before
the handler runs; the handler resolves the id grammars
(`^run-[0-9]{8}T[0-9]{6}Z$`, `^art_[a-z0-9][a-z0-9_.-]{0,63}$`, matched against
the raw, non-percent-decoded route capture) and reads the record; project grants
are checked last, and only then is the response built.

Grants reuse the frozen P4 mechanism with nothing parallel invented: the
deployment mode is `memory.scope.deployment_mode()`, grants are rows in
`memory_project_actors`, and the check is `memory.scope.ProjectAccess.require`
constructed from the same live connection. `require_mode_ready()` is called
first, so an `acl` deployment with no grants refuses `503
memory_deployment_not_ready` rather than serving everything. In `acl` every
project in the effective `projects` list is checked (**all**, never just the
first); an unattributed run (including all nine real records) additionally needs
`validation:unattributed`, so reading evidence with no project fence is an
explicit operator grant. `validation:read` is read-only and may be carried by any
actor; `validation:unattributed` is refused at key issuance for `agent` keys, the
same `ForbiddenActorKind` guard already used for `memory:manage` and
`memory:reflect`.

### 5.0 Two documented divergences from the frozen proposal's wording

The independent implementation review (F5, F6) found two places where the code
answers more honestly than the proposal's prose. In both the code is kept and the
divergence is recorded here, because changing the code would state something
false.

* **F5 — rows 4 and 6 also fire on the by-id routes.** The proposal scopes
  `index_incomplete` and `validation_index_too_large` to the list route (`L`),
  and says by-id reads "need no index". That is true for a record that can be
  found: a known id is still served while a sibling subtree is unreadable. It is
  not true for an id that resolves to **nothing** when the walk was knowingly
  incomplete: answering `404 validation_run_not_found` would assert that the
  record does not exist when the reader in fact could not look. The by-id routes
  therefore return the same row 4 / row 6 refusal as the list when they cannot
  rule the id out. This is not an existence oracle: the same refusal is returned
  whether the id is hidden or absent.
* **F6 — `acl` plus `validation:unattributed` reaches the `409` family.** The
  proposal's §7 says the `409`s are `single_user`-only because in `acl` mode an
  unreadable record "has already been answered by row 8". That is true only while
  the caller lacks the explicit `validation:unattributed` grant: with it, a
  corrupt or duplicate record is no longer hidden by row 8, and the honest answer
  is `409 validation_run_corrupt` / `409 validation_run_ambiguous`. Without the
  grant the caller still receives the single indistinguishable
  `404 validation_run_not_found`, so the isolation property is unchanged.

### 5.1 Two isolation invariants (closing the approving review's AD1 and AD2)

* **AD1 — `empty_reason` is computed over the visible set, never the raw walk.**
  When the visible run-shaped count is zero the reason is `no_run_shaped_files`;
  when visible records exist but the caller's filters exclude them all it is
  `all_filtered_out`. Because the raw walk is never consulted, an `acl` caller
  cannot use `empty_reason` as a one-bit existence oracle for a hidden run.
  Implemented in `reads.list_runs` (the `visible` list, not `walk.paths`, is what
  the reason is derived from).
* **AD2 — in `acl` mode `ignored_samples[]` is empty and only the
  `ignored_entries` count is reported.** An ignored relative path can name
  operator directory structure, and a refused symlinked `run-*.json` can carry a
  run-id-shaped filename. In `single_user` mode (one trusted operator, whose own
  tree this is) up to 20 `{relative_path, reason}` samples are returned.
  Implemented in `reads.list_runs` (samples are populated only when the
  deployment mode is not `acl`).

`discovery.run_shaped_files` also follows the visible set; the remaining
`discovery` counters (`scanned_dirs`, `scanned_files`, `unreadable_entries`,
`capped`) and `ignored_entries` are raw root diagnostics and are not per-project
information.

### 5.2 Bounded reads

`MAX_MANIFEST_BYTES` (1 MiB) is the proposal's §8 limit and is checked with a
`stat` **before** the bytes are read, so an oversized manifest is reported
`corrupt` without ever being made resident (and is then reported with no
`manifest_sha256`, because a file that was not read has no digest). §8 names no
record size but does require that no route read an unbounded amount of data, and
the list route classifies every discovered record on every request; a run record
over `MAX_RECORD_BYTES` (16 MiB, far above any real record — the nine are a few
kilobytes) is therefore reported `corrupt` with `record_sha256: ""` rather than
read. Both bounds are implementation limits, not contract vocabulary: no response
field names them, and a record between the two bounds is still read normally.

### 5.3 Two deliberate over-rejections, and the non-regular-file rule

* The declared-path rule refuses **any literal `..` substring**, not only a `..`
  path segment, so a legitimate name such as `artifacts/note..txt` is refused.
  That is the proposal's literal wording ("contains no `..`") and it fails
  closed; the alternative — a per-component test — would have to reason about
  which reader later normalises the path, which is exactly the assumption this
  slice refuses to make.
* A run-shaped entry that is not a **regular file** (a FIFO, socket or device) is
  not treated as a record: it is counted in `ignored_entries` with reason
  `not_regular_file`. This is §2's "only regular files are considered" and it
  also means an open is never attempted on something that could block forever.
* `discovery.duplicate rows` carry no `mode`/`attribution`/`evidence_grade`,
  because a stem with more than one parseable member is refused rather than
  resolved; a `mode`/`attribution`/`project` filter therefore never matches a
  duplicate row. That follows from the fail-closed rule, not from a missing field.

### 5.4 Four invariants against a swapped or hostile filesystem

An independent review of the delivered candidate showed that reading a file
"carefully" is not enough if the check and the read are separate operations, so
four invariants are now explicit and tested. They matter because the run root is
a real directory an operator (or anything else on the host) can change while the
Core is serving.

1. **One manifest read per request.** `_locate` returns both the record and the
   manifest it read, and the run-detail, manifest and both artifact routes all
   use that one object. Re-reading the manifest in the handler let a manifest swapped between
   the two reads decide the **permissions** while a different manifest decided the
   **bytes**. The grant check and the payload now provably come from one read.
2. **One rooted, bounded read path.** Records, manifests and artifacts are read
   through `_open_regular_under` + `_read_capped`:
   * the root is re-opened `O_DIRECTORY|O_NOFOLLOW` on **every** call, so content
     is never served through a root that is currently a symlink;
   * every path component is opened descriptor-relatively with `O_NOFOLLOW`, so a
     swapped ancestor cannot redirect the read;
   * `O_NONBLOCK` plus an `fstat` `S_ISREG` test means a FIFO, socket or device is
     refused rather than opened and blocked on;
   * the size is checked on the open descriptor **and again inside the read loop**
     (one extra byte is requested and the read is refused as too large), so a file
     that grows after the check cannot be read past the cap, and a record over
     `MAX_RECORD_BYTES` is never made resident.
   A `stat` followed by `read_bytes()` satisfied none of these, and a FIFO named
   `run-*.json` made every request block until the client timed out.
3. **The root is re-validated on every use.** The resolved path used to be cached
   per configured string, so the `O_NOFOLLOW` check ran once and later requests
   followed a root that had since been replaced by a symlink. Nothing about the
   root is cached now; the check runs per request, and invariant 2 re-checks it
   again at every read.
4. **The masked form is measured.** `canonical.redact_text` can *expand* content
   (`token=x` becomes `token=[REDACTED]`), so a raw artifact under the cap can
   produce a masked body over it. The masked bytes are measured and a body over
   `MAX_ARTIFACT_BYTES` is refused `413 payload_too_large` (with `details.limit`
   and `details.original_bytes`) instead of being returned successfully.

## 6. Artifact reads and the masker revision

A declared path is opened with the repo's per-component descriptor-relative
`O_NOFOLLOW` walk, mirrored locally from `fingerprint.hash_workspace_file` because
that helper returns only `(digest, length)`. Each component is opened with
`dir_fd`+`O_NOFOLLOW`; the file is read **once** into a capped buffer, the digest
of *that buffer* is verified, and exactly that buffer is served — it is never
re-opened. Final-component `O_NOFOLLOW` plus an `lstat`/inode comparison cannot
see a symlinked ancestor, so it is not sufficient (an `lstat` per component is
used only as a supplementary classifier on platforms where an intermediate
symlinked directory fails with `ENOTDIR`). `realpath` containment is kept as a
second, non-primary check, because it is a check-then-open and therefore races.
A test asserts the mirror and `hash_workspace_file` return the same digest.

`form=masked` (the default) serves `canonical.redact_text` and reports
`mask_algorithm: "canonical.redact_text"` and
`mask_version: canonical.REDACT_TEXT_VERSION`. **`REDACT_TEXT_VERSION` must be
bumped whenever any pattern table in `canonical.py` is added to, removed or
changed**, because `masked_sha256` is reproducible only from
`(algorithm, version, input)`. It is a masker revision, not the package version:
`__version__` is `"0.1.0"` and does not track `canonical.py`. `form=original` is
served only when the bytes were read once under the cap and hash to the declared
`sha256`/`bytes`, decode as strict UTF-8, the manifest does not declare
`credential_shaped: true`, **and** `redact_text(text) == text`; the last gate is
what closes the assignment-shaped-secret hole (`db_password=…`,
`{"api_key": "…"}`), which `looks_like_credential` alone does not match. The
content response is a JSON envelope with `served_sha256` (never a bare `sha256`)
plus `nosniff` and a sandbox CSP, so HTML or script can never be served as a
renderable page.

## 7. Fixtures are never evidence

The synthetic FAIL fixture lives under `tests/fixtures/p7/` and in
validator-created temporary roots, **never** in the operator's configured run
root. Every fixture record labels itself `synthetic: true`, a marker the API
surfaces, and no fixture result is counted toward any acceptance criterion. A
synthetic record's case grade is `simulated`; its run grade is the least
trustworthy of the effective bundle mode (`unknown`, absent a manifest) and its
case grades, so it is `unknown` and can never be reported as `real`.

## 8. Acceptance criterion 1 is BLOCKED

Criterion 1 ("read an existing real FAIL Run, classified, raw output traceable")
is **BLOCKED**. The nine real run records are PASS(3) / BLOCKED(6) at run level
and PASS(57) / BLOCKED(6) at case level; there is no FAIL case, no
`failure_class` and no `failure_output` on disk. The FAIL code path is exercised
end to end on the clearly-labelled synthetic fixture, which is not evidence.

**Unblock action:** the P7 owner records one genuinely failing scenario with
`recorder.py` into the configured run root (`validation-runs/p7-<scenario>/`),
after which criterion 1 is re-validated against that real record. Until then
`P6-T06`/P6-03c cannot close. All other criteria (2–6) are demonstrable on the
real records plus the fixture negatives.

## 9. Contract generation

`web/contracts/p7-validation-read.v1.json` is derived by `scripts/p7/p7_contract.py`
from `src/jasmine_core/validation/reads.py` by AST parsing: the five route
patterns, their route-table scope and each route's top-level response keys.
`--check` re-derives and compares; where the source ref (or `reads.py` at it) is
absent it **exits 2 with a loud message**, never 0. `--derived-at <ref>` records
the integration provenance stamp through `scripts/p6/contract_provenance.py`. The
default source is the working tree, because P7-01 is this checkout's own code
rather than a pinned external dependency.

## 10. Shared-file changes

`src/jasmine_core/errors.py` (+7 classes), `src/jasmine_core/auth.py` (the two
scopes and one actor-kind guard), `src/jasmine_core/canonical.py`
(`REDACT_TEXT_VERSION`, additive), `src/jasmine_core/api/handlers.py` (the route
tuple appended). No migration, no schema change, no new Core public ID prefix:
artifact ids are bundle-scoped by §4 and are deliberately not Core ids. The
dashboard proxy's P7 opt-in (`P6_ENABLE_P7_READS`, `p7_pending_paths`) is P6-03c's
change, not this slice's.
