---
name: jasmine-playwright
description: Test the Jasmine callback fixture through the trusted Playwright dynamic tool.
---

# Jasmine Playwright skill

Inspect the current Task and its requirements supplied by Jasmine Core. Read the
fixture source with `jasmine_read` and apply a bounded correction with
`jasmine_patch` only when the Core Guard permits it. Use `jasmine_playwright`
with the fixed `callback_task_id` scenario to open the fixture in a fresh owned
browser and assert the actual callback Task ID. The fixed `smoke` scenario only
checks the fixture readiness and does not satisfy the callback assertion.

A curl request or a prose statement is not browser evidence. Do not invoke
shell, browser, MCP, or arbitrary executable tools. A blocked, failed, stale,
or unknown operation is not a successful test. Report the Core completion and
Evidence IDs; acceptance belongs to the authorized Core State operation.
