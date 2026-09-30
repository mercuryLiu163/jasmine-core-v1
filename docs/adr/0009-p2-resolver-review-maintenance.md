# ADR 0009 — Atomic Resolver, Review and Interpretation Maintenance

Status: Accepted contract. Implementation and independent P2-02 validation are in progress. This does not assert the P2 capture Gate or P3 acceptance.

## 1. Policy and identities

`jasmine.resolution.v1` uses immutable Event actor/source from ADR0008. Model impact and certainty remain unchanged in storage and reporting. Ordered rules:

1. New operations require the current maintenance head, not REJECTED/RERUN_PENDING, and EXTRACTED. Other processing states return 409 interpretation_not_ready; old/rejected heads return 409 interpretation_not_current.
2. NO_STRUCTURE means NO_ACTION even when HIGH/tentative; it never confirms completion.
3. Non-EXPLICIT certainty requires review.
4. AGENT_PROPOSED and SYSTEM_CONFIG require review; neither becomes USER_EXPLICIT through model text.
5. GLOBAL/PROJECT/HIGH/PATH/TOOL candidates require review.
6. USER_EXPLICIT EXPLICIT LOW/MEDIUM TASK TASK_CREATE_OR_ATTACH may bind the source Task or create one Task in the source Project. REQUIRED_CAPABILITY may add a NORMAL/CONTEXT semantic Rule with matcher `{}`. Task-null requirements require a Task-create candidate in the same plan.
7. CORRECTION always requires review: its current schema cannot prove additive intent or name a supersede target. RULE/DECISION/ACCEPTANCE and every remaining candidate require review.

If any candidate requires review, the **whole plan** is PENDING_REVIEW, with no partial Truth or source application. Automatic capability application grants no execution permission, produces no DENY/HARD matcher, Evidence PASS, VERIFIED or ACCEPTED state. MEDIUM is not rewritten as LOW.

The automatic executor is an operator-configured actor/host (`JASMINE_CORE_RESOLVER_ACTOR_ID`, `JASMINE_CORE_RESOLVER_HOST_ID`); HTTP cannot override it. Registry records must exist, actor.kind=system, and nonempty actor.home_host_id must equal the configured host. Caller and source actors/hosts must exist and have matching home_host_id, and their host must equal this executor host. Registry has no lifecycle/status columns; this ADR introduces none. Manual changes also validate the source actor/home-host association; a mismatched source returns403 actor_mismatch with no Truth. They use the authenticated human/system actor and its own host, plus the actual P1 permissions. Agent keys cannot acquire reviews:manage or interpretations:manage. Filters are instance-wide read filters, not project ACLs.

## 2. Transaction composition and source graph

`db.unit_of_work(conn)` owns exactly one BEGIN IMMEDIATE/COMMIT and issues an internal registered, connection-bound, active token. ObjectStore.create, AuthorityStore.propose/transition and StateStore.set_task_criteria/set_step_criteria accept an internal keyword token. Their original actor, scope, revision, history, state and Evidence checks remain. Default nested transactions still fail; forged, inactive and cross-connection tokens fail. Resolver never writes P1 Truth directly and never invokes a model/network under a write transaction.

Source read, complete context revalidation, Resolution command Event, P1 commands/Truth, review changes, result snapshots and idempotency keys commit together. Any intermediate or COMMIT error rolls back the whole operation. An actual lost HTTP response after COMMIT is tested separately: reopen Application/DB and replay identical actor/key/body into the original immutable snapshot, without duplicate effects.

Project-known/task-null source remains immutable. The plan creates one Task through ObjectStore, then a system `interpretation.bound` Event scoped to that Task and an immutable resolution_source_bindings row. Rule origin points to this scoped Event; Authority._origin remains strict. Source graph retains the human Raw Event, original task-null Interpretation, Resolution and new Task. Multiple task-create candidates bind the same Task and each has an action result. Later manual plans may use only this trusted source binding, not a body-selected other Task. Context includes the bound Task, Steps and Rules. Raw text, scope and source spans are never rewritten.

Human approval of GLOBAL/PROJECT/HIGH candidates uses a genuine scoped `review.approved` authorization Event referencing the original Raw, Interpretation, Resolution and candidate. The action cannot exceed candidate scope. GLOBAL approval may originate from project-scoped Raw; its approval Event is explicitly global and human/system authored. PATH/TOOL execution matcher mappings are currently explicitly rejected as unsupported; they remain pending and can be rejected, without silent scope conversion. This limitation is exposed through 400, not reported as applied.

## 3. Context, idempotency and source dedup

Preview uses a consistent DEFERRED read snapshot, not a write lock. Context includes Project/Task/Step revisions, all applicable global/project/task Rules with version/status/content, criteria, maintenance head and source binding. Sorted revision arrays are exact sets: no missing, extra or duplicate members. Inside the write transaction they are recomputed and compared with expected_context_digest.

Idempotency is actor/key plus normalized exact request, checked before current head/revision preconditions. Committed exact-key history replays even after later changes; different body is 409 idempotency_conflict. New requests obey current-head admission. Canonical source replay skips stale context comparison but still validates the complete request syntax and bounds.

source_applications uniquely binds an Event to its first successful complete APPLIED plan across processing actors/configurations. Ordinary Resolve never reapplies after Correct/Rerun. Concurrent pending-review approvals recheck this binding inside the same outer transaction: exactly one applies; the loser becomes SOURCE_REPLAYED, appends review history and returns the original **applied** action snapshot, without Truth changes. Original pending Resolution results remain immutable; the canonical applied snapshot is read from successful review_changes when applicable.

Explicit manual-reapply requires human/system, reviews:manage, interpretations:manage and each action's P1 scopes. It targets the current same-source head, requires expected_head_revision and expected_latest_resolution_id, complete revisions/context, reason and exact actions. source_application_changes.previous_resolution_id is the transactionally read latest successful application; UNIQUE(source_event_id,previous_resolution_id) prevents forks. Ordinary Resolve cannot trigger this path. Reapplication targets existing Rule/criteria through explicit supersede/criteria commands rather than duplicating Task/Rule creation.

## 4. Review plans and immutable results

Manual actions are an exact candidate-index set, each index once: NO_ACTION, CREATE_TASK, ATTACH_TASK, CREATE_RULE, SUPERSEDE_RULE, SET_TASK_CRITERIA, SET_STEP_CRITERIA. Unknown or duplicate indices/fields fail before any P1 mutation. New Task/attach cannot choose another Task; criteria must target source/bound Task or its Step. Supersede must target a Rule in the same source application graph and exact candidate scope, with its revision. The narrow exception is a human/system review APPROVE of a CORRECTION candidate: it may explicitly target an existing Rule from another source in the same Task and exact scope, with authority:manage and the complete current Task/Rule/ancestor revision context. Automatic CORRECTION remains pending; ordinary manual-reapply still requires the same source application graph. Cross-Task or cross-scope targets are refused. Its immutable action snapshot includes previous_origin_event_id, previous_rule_version and previous_source_event_id alongside the new source/Interpretation/review command/supersede Event chain. Rule payload uses the original P1 content/matcher validation and content must equal the candidate content. Approval may not add candidates, expand candidate scope, or bypass P1 scopes. USER_CONFIRMED exists only in a real authorized approval/manual command's provenance; it is not model-settable Raw source identity.

reviews is a CAS projection with PENDING/APPROVED/REJECTED/SOURCE_REPLAYED and positive revision. review_changes and request keys are immutable. Approval rechecks that its Interpretation is still current/non-rejected/non-pending and EXTRACTED; old pending reviews cannot bypass Correct/Reject. Reject does not retract previously applied Truth.

Each immutable action result contains candidate_index, action, target_type/target_id, before_revision/after_revision, rule_version, change_event_ids, binding_event_id (nullable fields explicit). Resolution/review reads expose source_event_id, interpretation_id, command_event_id, execution/review actor refs, canonical refs, bindings and history. Snapshot replay does not reconstruct results from current Truth.

## 5. Correct, Reject, Rerun and recovery

Schema7 interpretations/results stay append-only. maintenance_heads is a CAS projection; interpretation_changes and operation completions are immutable. Logical initial head is revision1. Correct creates a strictly source-bound manual child with manual-correction-v1 provider/config/digest, parent and correction Event. It does not claim to be an LLM result or change Truth. Reject appends history and rejects the head without deleting old data or retracting Truth. Applied content changes use explicit manual-reapply/P1 versioned commands.

Rerun reserves a child and operation Event/head/request key in one short transaction, commits, then runs ADR0008's bounded provider outside locks. Server-owned operation_context `{version:1,type:rerun,parent_interpretation_id,operation_event_id,actor_id}` enters the actual LLM input and input_hash; public POST /interpret rejects it. Original source text and source span quote/index rules remain unchanged. Schema7 parent/input uniqueness supports this without changing m0001–7.

Start sets RERUN_PENDING with operation and child refs. Completion appends one interpretation_operation_completions record. Only matching head revision + pending operation + pending child allows a short CAS to NORMAL/current child and RERUN_COMPLETE history. EXTRACTED/FAILED/INTERRUPTED remain their actual processing status. Later Correct/Reject may supersede pending work: completion then records head_applied=false and cannot overwrite that head.

Controlled GET Interpretation/Review and new business operations recover unfinished rerun history. First ADR0008 deadline recovery records INTERRUPTED outside the maintenance transaction; then a short transaction records unique completion and triple CAS. Recovery scans immutable unfinished RERUN operations even when manual changes cleared the current pending head. It neither reruns a model nor leaves a head permanently pending after deadline.

## 6. Schema8 and limits

m0008 only adds resolutions/results/request keys, source_applications/source_application_changes/source_bindings, reviews/review_changes/request keys, maintenance_heads/interpretation_changes/operation_completions/request keys. Foreign keys and JSON/enums/positive revision checks apply. Immutable tables have UPDATE/DELETE triggers, including REPLACE protection via existing recursive_triggers. Old m0001–7 checksums are unchanged; old v7 binaries reject v8. Public IDs add res_/rvw_.

Requests/context/results: 128KiB, candidates/actions maximum16, reason1..2000 Unicode codepoints, revision set maximum512, positive integer revisions <=2**63-1 (bool rejected). Too-large context is409 context_too_large, never truncated. Canonical UTF8 JSON is ensure_ascii=False, sort_keys=True, separators=(',',':'), allow_nan=False; revision arrays sort by type/id, Rules by ID. Digests are SHA256 of exact bytes. Invalid Unicode/nonfinite/nested unknown fields are400. Context mismatch409 context_conflict; reference absence404; role/scope403. See core-api-v1 for exact endpoints.
