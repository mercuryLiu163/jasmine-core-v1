# ADR 0012 — Exact Context Builder

Status: Accepted implementation contract; native emission validation belongs to P3-03.

## Snapshot and persistence

Schema 10 adds only append-only `context_packs` and `context_request_keys`. A genuine `context.built` Event and the complete immutable response commit in one transaction. Historical idempotency replay returns that original response before current binding/configuration reads; it is not permission to inject an old pack.

Builder reads the shared complete Truth snapshot and auxiliary checkpoint selector, scans the workspace outside database transactions, optionally recalls Memory, then reads again. The writer rechecks complete membership, Truth digest, selector and source; one retry is allowed. Total budget is 30 seconds, scan 10, Memory 3, and database busy wait 3. Filesystem calls have postchecks rather than a claimed hard kernel deadline.

## Exact rendering

All ACTIVE Rules, their complete origin/version references, current Task and Step state, criteria and safety requirements are mandatory. HARD content is never truncated. The complete rendered text, including header and separators, must fit 2,500 tokens. Overflow returns `AUTHORITY_TOO_LARGE` or `CONTEXT_MANDATORY_TOO_LARGE`; optional Evidence bodies, note and Memory are removed with source-linked omission receipts.

Renderer `jasmine.context.v1` supports the explicitly tagged Evidence reference
encoding `columns-rows.dictionary-columns.v1`. It contains the complete ordered
column names and one row per original reference. The explicit
`dictionary_columns.fingerprint_sha256` contains every full string fingerprint
in deterministic first occurrence order; that column's row values are zero-based
dictionary ordinals. All other values, nulls, types and row order remain unchanged.
Nonuniform keys or nonstring fingerprints retain the original JSON list; the
table is selected only when its exact selected-encoding count is smaller.
Complete Truth and its digest are unchanged. No Rule, Evidence ref or mandatory
State is omitted. Rule rendering and both overflow checks retain their existing
semantics; the 2,500-token limit is unchanged. Existing pack bytes remain immutable;
renderer code fingerprints invalidate current admission after this format change.

The pinned tokenizer is tiktoken 0.14.0, explicit `o200k_base`, using `encode_ordinary`. Qualification is `EXPLICIT_ENCODING_MODEL_UNVERIFIED`, target model gpt-6.1-sol, scope `RENDERED_PACK_ENCODING_ONLY`. This does not establish the platform's hidden prompt count or model encoding. Offline asset SHA256 is `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`. Missing or changed dependencies fail explicitly; loading never downloads inside a transaction. The macOS arm64 dependency hashes are in `requirements-context-macos-arm64.lock`.

## Read-only Memory

Memory defaults off. Configured recall runs an owned, bounded worker with interpreter/script hashes and a fixed environment included in configuration identity. Every requested bank must have an explicit status. Failed banks never contribute items; cleanup denial/incompletion remains observable without claiming descendants were killed.

The interface preserves the five canonical Jasmine memory types and global/project banks. Initial qualification supports project-bank VERIFIED_OUTCOME and FAILURE_LESSON; other types/global provenance are explicitly filtered. Same-project historical Tasks are eligible using their actual source Task and Evidence lineage. VERIFIED_OUTCOME requires the genuine VERIFIED transition and Evidence relationship. Only Core-derived fact data is rendered, labeled historical and nonauthoritative; provider prose cannot become Truth. Duplicate and current-state-shadowed facts are filtered mechanically.

## Fresh admission

`check-current` requires the stored actor, exact source/host/session/Step binding, current server configuration, renderer/tokenizer identity, complete Truth and latest checkpoint selector. It is read-only. Configuration or state changes reject admission even though historical key replay remains available.

The component emission adapter persists an exact request, removes obsolete P2 snapshot fields, validates stored byte/hash/token identity, calls Core fresh comparison and rechecks every protected lease binding and generation before READY. It sends the exact text without an added newline. This is component evidence only: P3-03 must establish actual current-turn native injection, protected caller lock and typed execution barrier. Frozen P2 hooks and trust artifacts remain unchanged.
