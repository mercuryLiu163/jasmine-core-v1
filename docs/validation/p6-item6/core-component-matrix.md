# Item 6 Core component evidence matrix

Implementation authorization: user “按方案实现”. Integrated baseline:
`4d7eda79327c6f563f86ee2f1eaa5d8f7cd3f05f`, schema 11 → 12.
These results cover the implementation candidate before root freezes a commit.
The final integrated commit/tree identity is recorded by the integration owner.

Qualification: **component_simulation** for registered actors, synthetic prompts
used only by legacy/Rule compatibility fixtures, criteria, injected failures and
clock/deadline boundary controls. HTTP tests use actual loopback Core sockets,
actual SQLite and filesystem scans. Concurrent tests use real independent HTTP
requests and SQLite connections. Crash tests terminate actual child processes
using `os._exit(73/74/75/76)` and recover through fresh connections. No physical
human click, browser Gate, current user database, service, global trust or V0
behavior is proved by this matrix.

## Commands and receipts

Run from the isolated `_worktrees/p6-item6` checkout:

```sh
.venv/bin/python -W ignore::ResourceWarning -m unittest discover -s tests -p 'test_web_confirmations*.py' -q
.venv/bin/python -W ignore::ResourceWarning -m unittest discover -s tests -q
```

The local canonical `.venv` installs this repository editable and its pinned
context extra (`tiktoken==0.14.0`). It does not change a global interpreter.

* [Core component test log](core-web-confirmations-tests.log): **37 tests / PASS**.
* [Full regression log](regression-final.log): **1421 tests / PASS, 11 skipped**;
  1410 executed tests had no failure/error. This run predates the final three literal
  combination tests and crash receipt prints; production code did not change afterward.
  The final 37-test targeted run includes those three additions. The integration owner
  performs final frozen full regression independently. The remaining skips are below.
* [Initial regression](regression-initial-failures.log): preserved **FAIL** run
  under a noncanonical system interpreter: 16 failures, 5 errors, 11 skips.
  Actual regressions corrected were migration expectation lists and runtime
  default fingerprint caps. Other failures required canonical local interpreter,
  editable package/context dependency, and P6 strict route sample updates.
* [Intermediate canonical regression](regression-venv-intermediate.log): preserved
  **FAIL** run with one wrong tuple dictionary key introduced when updating a
  migration assertion. The exact memory migration assertions were corrected and
  their 20 tests passed before the final complete regression.
* [Skip inventory](skips-inventory.json): all 13 tests with possible skip sites
  rerun to record exact runtime reasons; 11 skips, zero failures/errors.
* [Isolated dependency installation](isolated-venv-install.log).

The logs include intentionally exercised failure responses and a historical P1
runner error-fixture output. Those are expected assertions inside passing unit
tests; they are not new claims that a functional Gate passed.

## C01–C24 mapping

Names refer to the tests in `tests/test_web_confirmations.py` (`HTTP`),
`tests/test_web_confirmations_integrity.py` (`Integrity`) and
`tests/test_web_confirmations_rules_schema.py` (`RulesSchema`). “PASS” means the
specified test assertions passed. It does not imply all combinations listed in
the broader preregistered row were exhaustively executed; explicit gaps are
retained in the final column.

| Case | Component observation | Executable evidence | Result / coverage boundary |
| --- | --- | --- | --- |
| C01 | Step EXECUTED→VERIFIED-target confirmation; VERIFIED→ACCEPTED-target fresh confirmation; final Task confirmation. INFO records preserve State until separate transitions. | HTTP `test_c01_c24_full_step_and_task_info_state_separation` | PASS; complete positive Core chain through actual HTTP. |
| C02 | Preparation alone creates no Evidence. Non-true affirmation values refuse; no consumption. | HTTP `test_c02_c17_ineligible_and_affirmation` | PASS for false/1/string/null; missing-field rejection follows exact field schema but is not a separate missing-affirmed HTTP assertion. |
| C03 | admin-only and ordinary system key fail scope; forged agent and separately forged system confirm grants fail role even with all required scopes. | HTTP `test_c03_c04_identity_scopes_and_host`, `test_c03_system_with_forced_confirm_scope_is_still_forbidden_actor` | PASS agent and system role assertions, without any preparation. |
| C04 | me actual actor/key/kind/host/capability; host null yields identity-unbound without registration. | HTTP `test_c03_c04_identity_scopes_and_host` | PASS null host; nonexistent registered-host reference is enforced by Registry/FK and is NOT separately injected. |
| C05 | Other human, same human other key, body actor/host/source spoofing refuse; generic HTTP and internal Raw store reject reserved type OR namespace OR structured source discriminator. | HTTP `test_c05_reserved_raw_and_same_actor_other_key`, `test_c04_c05_c10_registered_host_drift_and_other_actor` | PASS; current-host replay drift also returns identity conflict. |
| C06 | Missing Task/Step/Rule, stale revisions, unpaired Rule fields; cross-Task Step refuses with no preparation. | HTTP `test_c06_c07_reference_revision_and_rule_pair`, `test_c06_cross_task_step_is_400_without_a_partial_preparation`; RulesSchema `test_c06_active_other_project_rule_is_not_applicable_to_this_step` | PASS references, Rule pairing, cross-Task Step and actual ACTIVE other-project Rule refusal with no preparation/Evidence. |
| C07 | Actual HTTP criteria write while preparation scan paused; actual ACTIVE Rule added during scan, newly added/retired Rule rejects old material. | Integrity `test_c07_writer_phase_detects_actual_state_edit_during_scan`; RulesSchema `test_c07_rules_added_retired_superseded_and_writer_drift`, `test_c14_rule_version_drift_requires_new_exact_confirmation` | PASS DB/Rule drift; supersession freshness is tested after submission, not every waiting-lock timing permutation. |
| C08 | Actual bytes changed, symlink never accepted, extra-path configuration drift, actual Git HEAD changed without changing files, actual >20,000 file tree, bounded bytes refusal. | HTTP `test_c08_real_files_symlink_config_and_partial`; RulesSchema `test_c08_real_git_head_coverage_and_twenty_thousand_file_limit` | PASS; no STALE side effect; cap test does not promote truncated coverage. |
| C09 | Exact 600s expiry and clock-before-creation controlled clock checks; zero/expired monotonic budgets; final writer expiry after scan; actual competing SQLite writer lock. | HTTP `test_c09_expiry_clock_and_budget`; RulesSchema `test_c09_scan_timeout_final_expiry_and_database_busy` | PASS; clock/deadline boundary controls are simulation, not a 600 second wall-clock Gate. DB busy test shortens only its disposable connection wait to 20ms. |
| C10 | Exact prepare/submit replay returns first receipt after workspace/clock drift without scanning. Same ID altered body or different nonexistent preparation is conflict; revocation still 401. | HTTP `test_c10_c22_exact_replays_after_drift_and_revocation`; Integrity `test_c11_barrier_concurrent_prepare_and_submit_same_id` | PASS early and writer replay; unrelated non-Web Event global ID collision is covered by generic Event regressions, not its own Web-specific fixture. |
| C11 | Barrier forces both requests to miss early lookup and complete actual scans before writer. Same prepare ID and submit ID produce 201+200; different submit IDs produce 201+409 with one Evidence. | Integrity `test_c11_barrier_concurrent_prepare_and_submit_same_id`, `test_c11_different_command_ids_only_one_consumes` | PASS real concurrency. Network outcome recovery is represented by actual process death/retry below; browser timeout remains a separate Gate. |
| C12 | Failure after each prepare Event/snapshot/projection and submit source/command/Evidence/projection rolls everything back. Actual child crashes before/after both command commits recover with correct first/replay receipt and one side effect. | Integrity `test_c12_each_submit_write_rolls_back`, `test_c12_real_process_crash_before_and_after_commit`, `test_c12_prepare_each_write_rolls_back_and_process_crash_recovers` | PASS; actual exit codes 73/74/75/76 asserted, fresh connection restart readback asserted. No synthetic Exception is represented as a process crash. |
| C13 | Temporary DB backups damaged independently at source text/hash, prepare material, command semantic, Evidence result, receipt and submission reference. A second case modifies original note, recomputes source hash, updates receipt mirror but retains original command/submission semantic. | Integrity `test_c13_each_original_record_tamper_refuses_read_replay_and_state` | PASS API read/replay integrity refusal and State missing_evidence; original-body hash and semantic-note checks exercised independently. |
| C14 | Step restart revision/cycle makes old confirmation insufficient; actual Rule POST supersede v1→v2 requires a new exact pair. Final Task has ACCEPTED+SKIPPED Steps, records Task confirmation, then skipped Step restarts; Task cannot accept and old Evidence fails `_fresh`. Historical receipts remain readable. | RulesSchema `test_c14_revision_cycles_prevent_old_web_confirmation`, `test_c14_rule_version_drift_requires_new_exact_confirmation`, `test_c14_task_confirmation_invalid_after_skipped_step_restarts`; HTTP C01/C24 | PASS Step/Task/Rule freshness chains with literal actual State/API changes. |
| C15 | Plain confirmation cannot satisfy two Rule-pair requirements. Each Rule receives its own confirmation. INFO never satisfies USER_CONFIRMATION/PASS or rewrites criteria. | RulesSchema `test_c15_plain_vs_exact_multiple_rule_pairs_and_info_not_pass`, `test_c15_c24_user_confirmation_pass_requirement_stays_unsatisfied` | PASS actual State refusals and final success after exact per-Rule evidence. |
| C16 | Legacy same-task human prompt confirm/API shape still permits State verification. Web prepare/source/command sequence order is checked separately. | RulesSchema `test_c16_legacy_prompt_path_still_valid_and_web_seq_strict`; full legacy regressions | PASS; published migrations unchanged. |
| C17 | Ineligible target/current state, final Task with no accepted Step, empty criteria, skipped-only Task refuse. | HTTP `test_c02_c17_ineligible_and_affirmation`; RulesSchema `test_c17_empty_criteria_and_skipped_only_tasks_are_ineligible` | PASS. |
| C18 | Unicode digest matches existing canonical bytes; duplicate JSON keys, nonfinite values, invalid Unicode, unknown fields/query, wrong media type/16KiB body refuse; illegal derived snapshot types refuse; complete legal Rules exceeding review cap refuse; synthetic secret-pattern note readback stays original. | HTTP `test_c18_strict_request_and_material_types`, `test_c18_c20_raw_note_and_best_effort_audit`, `test_c18_illegal_current_server_snapshot_returns_integrity_error`; RulesSchema `test_c18_complete_review_oversize_is_refused_not_truncated` | PASS backend assertions; browser execution/storage and derived HAR/share bundle hygiene are separate Web/Gate obligations. |
| C19 | Prefix schemas 1–11 built using shipped historical migrations upgrade continuously to 12, preserving historical Event bytes/checksums; repeat is no-op; simulated schema-11 build refuses newer schema; UPDATE/DELETE/REPLACE guards refuse both tables. | RulesSchema `test_c19_migrate_all_supported_prefixes_old_bytes_and_append_only`; migration/full suite | PASS. Old-build refusal uses the same version checker with its older supported-version constant; it does not claim an independently installed old binary was launched. |
| C20 | Normal audit row locates successful submit and excludes note; injected audit outage leaves complete committed receipt. Refusals preserve Truth. | HTTP `test_c18_c20_raw_note_and_best_effort_audit`; full Auth/audit regressions | PASS best-effort boundary; no promise of durable audit during outage. |
| C21 | Actual HTTP writer creates a different project and reader reads Step while confirmation scan is paused outside transactions. | Integrity `test_c21_scan_allows_another_actual_http_writer_and_reader`; C07 actual State writer | PASS; no new write transaction spans scan. File-after-prepare conflict and State fingerprint revalidation are covered by C08/full evidence regressions. |
| C22 | Revoked key cannot replay; historical confirmation still permits actual State verification using another currently authorized key. | HTTP `test_c22_revoked_confirmation_key_does_not_erase_historical_evidence`, `test_c10_c22_exact_replays_after_drift_and_revocation` | PASS; historical key is not retrospectively invalidated. |
| C23 | Disposable DB key scope downgrade makes status/prepare replay 403; same-key submit replay still returns only the own original receipt without review material. | HTTP `test_c23_current_read_scope_rechecks`; C05 same-key ownership tests | PASS; all additional read scopes are checked before auth.touch through route required_scopes. |
| C24 | Explicit USER_CONFIRMATION/INFO allows separate State VERIFIED; revision growth makes old confirmation insufficient for ACCEPTED; new targeted confirmation succeeds; PASS demand remains unsatisfied. | HTTP `test_c01_c24_full_step_and_task_info_state_separation`; RulesSchema `test_c15_c24_user_confirmation_pass_requirement_stays_unsatisfied` | PASS actual HTTP/Core transitions. |

## Eleven skips

The exact IDs and reasons are in `skips-inventory.json`:

* Four `test_context.Context` tests, one `test_context.Emission` test, four
  `test_context.Tokens` tests, one `test_memory_gate_runner` real Context-pack
  comparison: `pinned tokenizer or encoding asset is unavailable` (10 total).
  The pinned Python library exists, but an offline encoding cache was not
  configured in these commands. No asset was downloaded or global setting changed.
* One historical `p6.test_freeze_bundle` test: `the evidence index is not present
  in this checkout`. This is an unavailable historical bundle verification,
  not a failure of the new item 6 Core contract.

No new `test_web_confirmations*.py` test skipped. All four actual crash receipts
(point, returncode, sanitized child stdout, restart availability/replayed, ID and
receipt digest) are retained as JSON lines at the end of the 37-test component log.

## Delivery and limits

Production files: `src/jasmine_core/web_confirmations.py`, migration
`m0012_web_confirmations.py`, metadata/models/events/fingerprint/evidence changes,
and API request/dispatch/handler/server additions. Documentation: ADR 0015,
`docs/api/web-confirmations-v1.md`, additions to ADR 0007/Core API. Necessary
existing migration expectations were updated in API/CLI/migration/State/Memory
schema tests. No released migration changed.

The implementer did not edit Web/P6 implementation files or commit this tree.
Independent code review and actual browser/Core validation are separate owners.
Rows with explicit unexecuted combinations remain coverage gaps; this document
never promotes those combinations or the eleven skips into a PASS claim.
