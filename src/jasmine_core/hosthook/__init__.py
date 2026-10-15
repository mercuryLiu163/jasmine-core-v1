"""Host Hook Protocol (ADR 0017): agent-neutral capture and guard events.

Everything in this package except `dialects/` is agent-neutral. Importing the
package must stay cheap: the hook runtime is a short-lived process.
"""
