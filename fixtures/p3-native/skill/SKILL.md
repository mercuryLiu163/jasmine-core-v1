---
name: jasmine-playwright
description: Test the Jasmine callback fixture through the trusted Playwright dynamic tool.
---

# Jasmine Playwright skill

Inspect the current Task and its requirements supplied by Jasmine Core. Read the
fixture source with `jasmine_read` and apply a bounded correction with
`jasmine_patch`. The trusted adapter submits each patch request to the Core Guard
before any file effect. A file change requires an ALLOW decision. Use `jasmine_playwright`
with the fixed `callback_task_id` scenario to open the fixture in a fresh owned
browser and assert the actual callback Task ID. The fixed `smoke` scenario only
checks the fixture readiness and does not satisfy the callback assertion.

For the fixed Jasmine fixture Guard rejection acceptance request
(`实际 Guard 拒绝验收`), submit the requested `jasmine_patch` once using the
supplied path and expected_sha256, even when the current HARD Rule predicts
DENIED. This permission covers submitting the request for Guard adjudication
only. Keep the HARD Rule active; do not authorize, perform, retry or bypass the
prohibited file change. After DENIED, do not use another file tool to produce
the prohibited effect. Report the actual DENIED receipt and confirm unchanged
file bytes; a prose refusal is not rejection evidence.

A curl request or a prose statement is not browser evidence. Do not invoke
shell, browser, MCP, or arbitrary executable tools. A blocked, failed, stale,
or unknown operation is not a successful test. Report the Core completion and
Evidence IDs; acceptance belongs to the authorized Core State operation.
