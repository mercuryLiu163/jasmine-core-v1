# Jasmine Core V1 — P0-02 Event Store and object projections

P0-02 implements the append-only Raw Event Store and the three minimal
projections on top of the P0-01 contracts. It adds no HTTP surface: the API in
`src/jasmine_core/api/` arrives in P0-03.

## What is implemented

| Capability | Where |
| --- | --- |
| Immutable, idempotent Event Store | `src/jasmine_core/events.py` |
| `project` / `task` / `session` creation, atomically paired with a source Event | `src/jasmine_core/objects.py` |
| Host and actor registry (foreign-key targets and provenance) | `src/jasmine_core/registry.py` |
| Request validation and the idempotency hash inputs | `src/jasmine_core/models.py` |
| Storage-level tests for P0-T02 / T04 / T05 and restart recovery | `tests/test_event_store.py` |

## Write path

```
db.transaction()            # BEGIN IMMEDIATE
  EventStore.append()       # 1. INSERT events  (immutable, idempotent)
  _insert_projection()      # 2. INSERT projects|tasks|sessions
COMMIT                      # or ROLLBACK: neither row exists
```

`ObjectStore.create()` is the only public entry point for creating an object and
it owns the transaction, so a caller cannot write an Event without its
projection or a projection without its Event. The projection's
`source_event_id` is `UNIQUE` and its foreign key is not deferred, so two
objects can never claim the same Event and a projection can never precede the
Event that caused it.

## Idempotency

`event_id` is the idempotency key. The body hash covers exactly the fields
listed in ADR 0003 §1.3, plus two refinements this stage had to fix:

* **`occurred_at` is hashed only when the client stated it.** For an object
  create without an explicit `occurred_at` the server fills in the acceptance
  time; hashing that would make every retry a "different content" conflict.
* **The server-assigned `object_id`, `object_kind` and `name_sha256` are
  excluded from the hash of the stored payload.** The authoritative `object_id`
  always comes from the *stored* Event on a replay, never from the retry.

| Situation | Result |
| --- | --- |
| unseen `event_id` | `201`, object and Event created |
| same `event_id`, same hash | replay: the originally stored object and Event are returned, nothing is written |
| same `event_id`, different hash | `409 event_id_conflict`, existing row untouched |
| same `(source_system, source_event_id)`, different `event_id` | `409 source_event_duplicate` |
| same `source_event_id` from a different `source_system` | allowed — different agents, different conversations |

## Deliberately not implemented

No update or delete endpoint, no Task state machine, no `expected_revision`
optimistic locking (that lands with P1's update endpoints), no Evidence, no
Interpreter. `status` and `revision` exist as columns and are asserted to start
at their initial values; nothing transitions them.

## Tests

`tests/test_event_store.py` runs against real SQLite with real transactions.
The fault-injection tests replace the projection insert with a function that
raises *after* the Event insert and then assert that the event count, the
object count, `PRAGMA integrity_check` and `PRAGMA foreign_key_check` are all
unchanged — that is the P0-T05 shape, not an assertion restating the code.
