# Verification results

Last checked: 19 October 2026, version 1.0.2.

## Automated checks

- 69 Python tests pass: persisted timers, idempotent setup, immutable instructions, due-time selection, concurrent claims, cancellation during usage reads, usage/account gating, bounded retries, crash/ambiguous-receipt handling, native compatibility and code-formatted local timestamps.
- UI checks pass for the battery display, unknown/stale readings, exact-chat binding, cancellation controls, and preserving the message field when enable returns a compact confirmation.
- Local package-reference/metadata checks pass. This validator is not an OpenAI submission approval check.
- Isolated flow simulations exercise the production handler and panel JavaScript without a real account, daemon, timer, or model request. See `benchmarks/flow-comparison`.

## Live checks performed during development

- Fresh usage was read directly through the installed Codex app-server without starting a model turn.
- The queue command submitted a marker into the requesting desktop chat, which received and processed it.
- A 45-second timer survived an explicit worker restart with its generation/due unchanged; the worker recorded its queue receipt, and that same chat processed the scheduled marker.
- A generic scheduled continuation later reached the same chat. That does not by itself prove the chat had previously been stopped by a usage limit.
- Plugin installation through the supported manager and installed MCP tool/resource discovery were checked.

## Still requiring acceptance testing

- A task demonstrably stopped by real quota exhaustion and resumed after allowance returns.
- Physical sleep/wake, logout/login and powered-off intervals.
- Consistent rendering and opening of the panel inside the desktop host; simulated JavaScript success is not live UI evidence.
- A clean-machine setup and independent reviewer workflow.

Local development history containing actual chat/queue identifiers is retained only in `.private/`, which is excluded from Git.
