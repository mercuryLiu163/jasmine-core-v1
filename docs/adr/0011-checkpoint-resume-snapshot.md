# ADR 0011 — coherent Checkpoint and Resume receipts

Status: Accepted contract; implementation candidate under independent review.
Scope: P3-01 / Schema 9. Later Context Builder and actual native compact Gate remain separate milestones. The user requires local review and validation, whole-stage publication, and no CI gate.

## 1. Truth and selection

`task_snapshot.read_task_snapshot` reads Project, Task, every Step, applicable current Rule versions/status/provenance, complete immutable Evidence rows and derived source bindings in one SQLite DEFERRED transaction. It also returns a latest checkpoint selector, ordered by the server creation Event sequence. The selector and every checkpoint/resume/audit tail are excluded from the canonical Truth projection/digest. Creating a checkpoint cannot change its captured business Truth.

All applicable Rules remain members; only ACTIVE Rule contents are effective Authority. Evidence uses existing PASS/FAIL/INFO records and real task/step revision references, with no invented Evidence revision or version. Every immutable Evidence body participates in the full digest. A projection exceeding 512 object members or 256 KiB is rejected; no silently partial STATE_EQUAL result is permitted.

## 2. Finite outside-transaction scan and writer comparison

A fresh operation samples coherent snapshot A, scans the server-configured workspace outside all database transactions, then samples snapshot B. Exact full Truth and selector equality is required. BEGIN IMMEDIATE rechecks both operands under the writer transaction before the Event-first immutable receipt. One retry is allowed, then `checkpoint_context_conflict` or `resume_context_conflict`.

The server budget is 30 monotonic seconds. An owned fingerprint subprocess is limited to min(10 seconds, remaining), its stdout to 2 MiB, and SQLite lock acquisition to min(3 seconds, remaining). Existing fingerprint file/Git limits are additional bounds. Startup, malformed output, timeout and output cap fail with stable 503 scan codes before any receipt or command commits. Partial fingerprints remain UNKNOWN. Owned process cleanup is bounded; a permission denial is observable and does not replace the primary failure. No hard kernel filesystem real-time guarantee is claimed.

Remaining time is checked before/after durable commit. A response lost or expired after successful commit leaves the immutable receipt recoverable using the exact original actor/key/body. Resume never calls P1 mutating fingerprint refresh, changes a Task or Step status, restores old Truth, or grants Evidence validity.

## 3. Schema and replay

Migration m0009 creates only append-only `checkpoints`, `checkpoint_request_keys`, `resume_results`, and `resume_request_keys`. Public identities are `ckp` and `rms`. Each receipt references a genuine server-created `checkpoint.created` or `resume.built` Event, committed in the same transaction. Public Raw Events with these names cannot create a receipt.

The complete Resume response is persisted as result JSON. Strict finite bounded Unicode JSON syntax and identity validation precede exact key lookup. Replay occurs before current source/binding/revision/scan reads; a different body for the same key conflicts. Authentication and read scopes still apply. Replays return original stored contents plus a transport `replayed` flag, never a regenerated current snapshot.

## 4. Source and lifecycle boundary

Caller and source actors must exist, have nonempty home-host bindings matching the requested host, and the source actor kind must match its Event envelope. Sources must belong to the exact Task and Project, or have an original canonical successful Resolver application with an immutable binding Event referenced by its committed action snapshot; automatic binding uses the configured system executor, while an approved pending plan uses the genuine human/system review principal and that review Event host. Arbitrary client Task IDs cannot replace an original Task-null Raw binding. Optional Core session and current Step must agree with Task/Project/host; external Codex session IDs are not Core sessions.

Notes preserve their original text and eligible source, remain historical non-Authority data, and are never executed as instructions. A requested PRE_COMPACT or SESSION_STOP reason is labeled UNVERIFIED_REQUEST. The P3-01 public API does not attest native lifecycle activity. Future adapter receipts require a separately reviewed protected system identity and actual native Gate evidence.

STEP_VERIFIED requires an existing internal P1 `step.transitioned` command with `to=VERIFIED`, the actual task/step/revision and Evidence lineage, and the internal original-request hash shape. This hash is integrity metadata, not a secret signature. Public Raw event hashing cannot substitute for the business record. Later legitimate State transitions do not erase the historical verified record.

## 5. Resume semantics

Checkpoint identity, projection version and stored digests are checked before comparison. An absent checkpoint yields ABSENT. A checkpoint Task/object/Rule revision ahead of current Truth is an integrity conflict. Lower Task revision yields STALE. Equal Task revision with any member/content/Evidence change yields CONTEXT_CHANGED. Only complete exact business Truth yields STATE_EQUAL.

Removed old members are reported as removals; they do not alone imply an ahead revision. Fingerprint SAME/MISMATCH/UNKNOWN is independent of state comparison. Unknown or mismatching workspace requires reconciliation. Deterministic next actions derive from current factual Step statuses, with no model inference or command execution. P3-02 must perform a fresh comparison and final revision check before building its sole context injection.
