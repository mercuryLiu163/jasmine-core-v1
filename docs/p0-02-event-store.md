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
  ObjectStore.create()      # resolves references, checks the authenticated actor
  EventStore.append()       # 1. INSERT events  (immutable, idempotent)
  _insert_projection()      # 2. INSERT projects|tasks|sessions
COMMIT                      # or ROLLBACK: neither row exists
```

`ObjectStore.create()` owns the transaction for object creation, so the two
halves of an object creation are never separable. A Raw Event on its own is
deliberately allowed — `POST /v1/events` appends one with no projection, which is
what ADR 0002 §2.4 requires so that real input is never dropped for lack of Truth
context — and `EventStore` is public for that reason. It does not open a
transaction of its own, so a caller must supply one.

The projection's `source_event_id` is `UNIQUE` **per table**, which alone would
let a project and a task claim the same Event. The `trg_*_source_event_kind`
triggers close that: each projection may only reference its own creating event
type, and an Event has exactly one type, so at most one object of any kind can
claim a given Event.

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
| same `event_id`, different `actor_id` or `host_id` | `409 event_id_conflict` — a different actor is different content, not a replay |
| same `(source_system, source_event_id)`, different `event_id` | `409 source_event_duplicate` |
| same `source_event_id` from a different `source_system` | allowed — different agents, different conversations |
| body `actor_id` different from the authenticated actor | `403 actor_mismatch`, nothing written |
| `host_id` or `actor_id` not registered | `404 host_not_found` / `404 actor_not_found` |

## Timestamps and identity

* `recorded_at` is sampled per write, not once per store instance, and the
  projections' `created_at` / `updated_at` / `started_at` all come from the Event
  they were created with, so a row can never disagree with its Event.
* The creating Event carries the same `project_id` / `task_id` columns as the
  object it creates, so `GET /v1/events?project_id=...` (ADR 0003 §1.2) finds a
  `task.created` event. A `project.created` event cannot name its own project:
  the row does not exist until after the Event is written and the Event is
  append-only. A session that names only a task gets the project derived from
  that task.
* `actor_kind` is read from the `actors` registry, never taken from the request,
  so an unfixable append-only row cannot misstate who acted.

## Deliberately not implemented

No update or delete endpoint, no Task state machine, no `expected_revision`
optimistic locking (that lands with P1's update endpoints), no Evidence, no
Interpreter. `status` and `revision` exist as columns and are asserted to start
at their initial values; nothing transitions them.

## Tests

`tests/test_event_store.py` runs against real SQLite with real transactions.
The fault-injection tests replace the projection insert with a function that
raises *after* the Event insert and then assert that the event count, the object
count, `PRAGMA integrity_check`, `PRAGMA foreign_key_check` and
`conn.in_transaction` are all unchanged — that is the P0-T05 shape, not an
assertion restating the code.

P0-T02 in the taskbook is defined over the **Core API**, which lands in P0-03.
What P0-02 covers here is the storage behaviour underneath it: object/Event
pairing, provenance and text fidelity. P0-T02 is not closed by this PR.
