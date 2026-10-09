# P6-02 item 6 Web consumer implementation

This document describes the consumer implementation. It does not inherit item 5's historical
PASS, assert a Core validator verdict, or stand in for the independent real-browser Gate.
The approved contract is `docs/proposals/p6-02-item6/api.md` with `web_confirmation_v1`.

## Enable and navigate

Both the Vite host and `node server/serve.mjs` read `P6_ENABLE_WEB_CONFIRMATIONS` at startup.
Default is off; `1`, `true`, or `yes` explicitly enables only these method/path pairs:

| Method | Path |
| --- | --- |
| GET | `/v1/auth/me` |
| GET | `/v1/evidence/confirmations/evt_{26 Crockford characters}` |
| POST | `/v1/evidence/confirmations/prepare` |
| POST | `/v1/evidence/confirmations/submit` |

The UI checks Core's own current identity and capability. An off proxy, older Core or missing
capability stays `DEPENDENCY_PENDING`; agent/unbound/missing-scope identity cannot prepare.
Old `/evidence/confirm`, Raw Events, tool-results, fingerprint scan, key management and audit
remain absent. Both proxies reject a non-same-origin Origin on confirmation POST before
forwarding. No Cookie/Origin/arbitrary headers are sent upstream; the existing bearer,
JSON, Content-Length and correlation headers remain the only forwardable request headers.

Task → **人工确认**, or `#/task/{task_id}/confirmation`, opens the form. Choose Task/Step,
VERIFIED/ACCEPTED and optional single Rule pair, then explicitly prepare. Core supplies
versions, criteria, the entire applicable Rule set, fingerprint coverage/completeness,
identity, time/expiry, fixed statement and sufficiency=`NOT_EVALUATED`. The entire document
is escaped plain text with no masking or truncation; a digest alone is never offered for
signing. Synthetic secret-pattern content remains visible here because it is authorized
review material. Sharing screenshots/evidence still needs independently derived masking.

Affirmation starts unchecked. A human explicitly selects it before submit. note is preserved
as entered, including whitespace, and UTF-8 byte length gates submission at 4096. Switching
object/target/Rule clears preparation and affirmation; any retained note explicitly names
its original Task/Step. Changing token or route invalidates in-flight response display.

A successful submit displays INFO and issues three independent GETs for source Event,
command Event and Evidence. An unreadable/inconsistent GET remains unverified, even when
a submit receipt was returned. Event/Evidence links point at existing read routes. No State
request is issued: Task → 写入 is the existing separate State flow. After a State transition,
return to 人工确认 to read the new Task/Step versions and explicitly prepare new materials.

## Unknown outcome recovery

prepare/submit are tracked before transmission in `SessionProvider` memory. This survives
route changes and appears as a recovery link in global navigation, but is never written to
browser storage. Network failures, 5xx and malformed successful responses remain
`UNKNOWN_OUTCOME`; duplicate submissions are blocked. Explicit exact replay sends the
original event_id/body, regardless of later note edits. Current me.key_id must match the
pending owner; recovery must return to the same Task/Step before displaying results.

Status is explicit and read-only. OPEN/404 does not prove an in-flight command failed.
CONSUMED with another command ID is identified as another command, never the pending
command's success. Historical readback is labelled historical and is not freshness proof.
Refresh clears credentials/drafts; the preparation ID field permits history/status lookup,
without auto-submitting. 409 and other determinate refusals preserve note and require new
explicit prepare/review, with no automatic revision or digest update/retry.

## Item 5 boundary and tests

The existing matrix can display `human.confirmation_submitted` from
`core-web-confirmation-v1` alongside legitimate human prompt sources. It continues to show
only candidate status and residual unknown conditions. It does not verify the immutable
preparation/submission projection, semantic/Event digests, current fingerprint or Core's
acceptance predicate. INFO still cannot match a PASS requirement.

`web/tests/webConfirmations.test.tsx` exercises mock UI contracts, raw escaped material,
exact request bodies, separate State actions, conflicts, UNKNOWN, exact replay, cross-key
and object invalidation, different-consumer status and failed readback. These are
component simulations, not real Core acceptance. `web/tests/webConfirmationProxy.test.ts`
uses real local sockets with a stub upstream for both hosts, exact allowlist parity and
Origin/header forwarding. `tests/p6/test_web_confirmation_proxy_contract.py` independently
checks the JSON contract's opt-in and method/path separation. Full web suite, TypeScript
and production build are required; the separate integration owner freezes actual
Chrome+Core receipts and independent audit/readback for the real Gate.

Stable browser selectors: `confirmation-panel`, `confirmation-step`, `confirmation-target`,
`confirmation-rule`, `confirmation-prepare`, `confirmation-review`, `confirmation-affirm`,
`confirmation-note`, `confirmation-submit`, `confirmation-message`, `confirmation-receipt`,
`confirmation-readback`, `confirmation-status-id`, `confirmation-status-read`,
`confirmation-status`, `confirmation-pending-{command_id}`,
`confirmation-recovery-status-{command_id}`, `confirmation-replay-{command_id}`.
