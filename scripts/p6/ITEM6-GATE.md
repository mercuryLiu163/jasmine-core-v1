# Item 6 isolated confirmation Gate

Run from a **clean committed** `p6-item6-web-confirmation` candidate with Python
3.11+ (the repository `.venv`), Node 20+ with native WebSocket, installed Chrome,
and the locked Dashboard dependencies in `web/node_modules`:

```sh
.venv/bin/python scripts/p6/item6_realcore_gate.py \
  --out validation-runs/p6/<new-UTC-run-id>-item6
```

The runner builds the Dashboard from that candidate, creates its own temporary
Core database and registered keys, starts only its own loopback Core/Dashboard
processes and a fresh Chrome profile, and removes that private runtime afterward.
Inherited `JASMINE_*` deployment settings are excluded. It does not use operator
services, hooks, archived V0, native Codex capture or a Memory engine.

Invoice tests execute as real subprocesses. Their captured exit code/output and
artifact bytes are recorded through the existing configured `TEST` producer
contract. Positive confirmations use actual browser controls and Core's dedicated
prepare/submit API. Every Step/Task State operation is a separate browser action.
Full Core material and secret-pattern fixture notes are compared **before**
deriving redacted report/screenshot copies. Hashes remain those of the original
product bytes; redacted copies cannot be used to recompute those original hashes.

The committed-response loss case intercepts the real Core HTTP 201 at Chrome's
response stage, substitutes a labelled harness HTTP 502, and requires the UI to
hold `UNKNOWN_OUTCOME` until same-ID status recovery reads the committed chain.
The runner restarts its own Core and uses a fresh browser profile for history and
source deep-link readback. A separate admin key reads the real audit rows.

Negative cases check exact error codes/reasons and the unchanged Task/Step,
criteria, full State/Event history, Evidence and Rule versions. Concurrency uses
simultaneous real HTTP sockets; forced early-miss barriers and crash fault
injection belong to the separately labelled component suite. Expiry waits for
the actual server wall-clock 600-second boundary. This normally takes at least
ten minutes. No product clock, expiry policy or database row is changed.

Each browser phase enforces distinct required check names/counts and object/target
chains. The outer runner also enforces its required scenarios. Failure exits
nonzero and preserves the report. A successful Gate is scoped to item 6; physical
human presence, aggregate P6/V1, P4 Memory and P7 are not established by it.

`--development` explicitly permits an uncommitted smoke and skips the ten-minute
expiry wait. Its only successful status is `DEVELOPMENT_PASS`, never a frozen
Gate PASS. Keep such runs and failures alongside later independent validation.
