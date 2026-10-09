# ADR 0015: Web structured human confirmation

Status: Accepted by the user for implementation ('按方案实现').
Implementation candidate: schema 12; migration `m0012_web_confirmations`.
Independent implementation and browser validation are separate gates.

The frozen full decision is [the approved item 6 ADR](../proposals/p6-02-item6/adr.md),
with its [review manifest](../proposals/p6-02-item6/manifest.json). Its PROPOSED header
records the design delivery time and is preserved unchanged as historical evidence.
The normative API is [Web confirmation v1](../api/web-confirmations-v1.md).

Core prepares immutable complete review material, then a registered human bearer
key submits explicit `affirmed: true`. Core atomically appends a protected structured
source Event, command Event and `USER_CONFIRMATION/INFO` Evidence. State transition
is a separate command and continues to check all existing Evidence requirements.
No `user.prompt` is manufactured, no INFO is promoted to PASS and no Rule is approved.

Authentication proves control of a registered human bearer key; it does not prove
physical click, presence, reading or human intent. Host identifies the Registry
home host. Each preparation binds one actor/key/host and exact Task/Step revisions,
full applicable ACTIVE Rules, selected Rule pair, complete workspace fingerprint,
configuration identity, target status and the fixed Chinese statement.

Preparations expire at 600 seconds. Exact authenticated owner replays return their
original receipts before freshness/expiry checks, both before scanning and again
inside the writer lock. Different command IDs cannot consume the same preparation.
Scans run outside DB transactions with a 15 second monotonic deadline, a 20,000
file and 256 MiB cap, under a 30 second request budget. SQLite keeps its 5 second
busy timeout. Complete coverage exceeding a cap is refused, never accepted as a
sample. No files are read inside the new writer transaction. DB/configuration/identity
drift is refused and review material is never silently regenerated.

Two append-only tables preserve preparation and unique consumption receipts. Shared
integrity verification reparses strict original JSON, hashes every original Event
payload byte, rebuilds semantic requests including the original note, cross-checks
Event/Projection/Evidence bindings and reproduces the immutable receipt. It is used
by status, replay and the fail-closed State validator Web branch. Legacy prompt
confirmation retains its original API and validator contract.

The protected event types and source namespace require an internal capability
in the generic Event store; all external Raw entry paths reject them. No API body
can grant that capability. The product's authorized Raw readback preserves full
notes/rules. Only derived logs/audit/shareable bundles are redacted; original
material and digests remain unchanged. Audit remains a separate best-effort write.

This migration adds two tables only and does not modify published migration bytes,
existing Events/Evidence, hooks, retired V0, trust or global operator configuration.
