# ADR 0013: Native lifecycle reports and typed execution

Status: Accepted implementation contract; native phase acceptance pending.
Schema: 10. No migration or additional public identifier prefix.

## Contract and trust boundary

The configured adapter is a Registry system actor with a matching home host and
an exact purpose-bound key. Public Raw Events cannot claim `adapter.*` command
types or the `core-native-adapter` namespace. Read, report and attestation scopes
remain separate. Hook trust uses the ordinary Codex interface. Installation
changes only the new fixture's reviewed definitions and never writes trust hashes
or global configuration. Private file modes are hygiene; actual native permission
and undeclared-tool boundary proofs are required independently.

The native main process has four typed dynamic tools, read-only fixture/code
permissions, no command network permission and no approval escalation. It cannot
read or modify adapter credentials, Core databases, receipts or leases. The host
adapter owns effects behind the Core Guard. Platform hook timeout/error behavior
is not an absolute execution barrier. PreToolUse is an additional advisory check;
the native tool catalog and client/Core barrier enforce the reviewed path.

## Lifecycle

A hook first records its exact original stdin bytes and a trusted report. A
PreCompact or Stop report can produce a checkpoint, labeled as a trusted adapter
report rather than a proved native lifecycle. The runner later submits a separate
immutable attestation from actual `hook/started`, `hook/completed` and same-thread,
same-turn native completion notifications. Compact ACK `{}` alone proves nothing.
Stop completion must occur after its hook completion. No future notification is
invented while the callback is running.

PostCompact records a report; it does not promise undocumented stdout context
injection. The next genuine UserPromptSubmit captures its Raw Event first, admits
its interpretation/resolution, then emits the sole exact Core Context pack through
the documented typed hook output. A new native session uses an explicit trusted
BINDING_ONLY seed after current same-project Task/Step reads. It inherits no old
Raw, Core session, requests, READY state or context. The first new prompt creates
its own Raw source and Task-bound Core session. Old lease bytes are archived.

## Operations and external effects

A native `(thread, turn, call)` has one deterministic Event identifier, independent
of arguments and idempotency key. UUIDv5-derived bits use the existing 26-character
Crockford `evt_` encoding. A changed argument body conflicts; additional keys replay
the original result. Events preserve reservations, denials, aliases and one terminal
completion. An effect-started receipt without a terminal is UNKNOWN_OUTCOME and
cannot authorize another execution.

All protected filesystem receipts and dependency identities are read outside DB
write transactions. The runner holds the same lease lock from effect admission to
completion. Core checks configuration, exact pack bytes/count, current Truth and
checkpoint selector, Rule versions, native identity, generation and deadlines.
After scans and producer reads it samples the lease again before the writer, then
checks cheap monotonic time after acquiring the writer. This is an explicit sample
window, not a cross-filesystem/database atomicity claim.

The callback has a 90-second budget beginning at the transport's actual inbound
read timestamp, before receipt callbacks or queued dispatch. Fixed executors have
5/30/60-second caps. Cleanup denial remains observable and cannot turn a timeout
into success. Fixed worker argv, environment, raw file or bounded dependency-tree
identities and static stdin are part of execution provenance. Directory identities
cover at most 20,000 members and 256 MiB; they do not claim every OS runtime library.

Read/test/browser effects require semantic input fingerprints to remain SAME.
Patches require the exact old SHA and the permitted target's actual before/after
bytes; unrelated workspace changes make the completion stale. A stale, failed,
unknown or incomplete-cleanup effect produces no qualifying Evidence or automatic
State advance. Actual artifact bytes are read back and hashed.

## Playwright requirement and Evidence

An explicit authorized system fixture operation binds an actual applied P2 Rule
to existing P1 Step criteria, preserving prior criteria. The callback scenario's
fixed command SHA is required, so a smoke test or curl does not satisfy it. A
reviewed same-Task CORRECTION requires rebinding the current Rule version; old
bindings cannot verify State after supersession. No P3 text parser creates Truth.

Specialized TEST Evidence uses the real `jasmine_playwright` or `jasmine_test`
producer, its configured command identity, actual worker/artifact receipts and the
P1 CurrentEvidenceValidator. Browser behavior uses a fresh owned Chrome and actual
navigation/assertion/trace. The fixed unit test only checks source structure and
never evaluates patched JavaScript in a privileged Node process. The local
Playwright SKILL input must be proved loaded by native current-turn input records.
Having the library installed is not skill execution proof.

## Acceptance boundary

Component tests, synthetic protocol receipts, actual isolated browser checks and
native phase Gates remain distinct. Only actual current-turn nonassistant typed
input proves Context/Skill transmission. The explicit o200k_base count is exact
for the selected pack encoding and is MODEL_UNVERIFIED for hidden model prompts.
The seven native Gates require an independent final manifest. No CI or individual
PR is required; the user's latest instruction is one publication after all P3 work.

Preparation disables Python bytecode writes in the parent and installer child.
The deployment digest and per-file manifest cover the same complete copied file
set; generated bytecode is rejected, and the digest is checked again after
installation. A mismatched preparation is not eligible for user trust.
