---
name: agent-scheduler
description: Schedule a message for later delivery to the current Codex or Claude Code session.
---

# Schedule only

When the user invokes agent-scheduler, call its MCP tools. Do not substitute
native scheduled tasks, automations, or confirmation cards. Respect host
approvals; these instructions do not bypass permission requirements.

For the current Codex chat, read `CODEX_THREAD_ID` from the calling agent’s shell
(`CODEX_SESSION_ID` is a fallback) and pass it as `sessionRef`. Read the same
shell’s working directory for `workDir`. Resolve these yourself; users should
not need to find IDs or supply tool parameters. For POSIX shells, use
`printenv CODEX_THREAD_ID` and `pwd`; for PowerShell, use `$env:CODEX_THREAD_ID`
and `(Get-Location).Path`. Never infer the current chat from recency, an MCP
transport ID, or the MCP server process environment: the server may be shared.
For Claude Code, use explicit host-provided session context, never a Codex ID.
If reliable context is absent, report the limitation rather than guessing.


When the user asks to schedule a message for a specific time:

1. Identify the exact message, due time, provider, active session reference, and local working directory from the current conversation and host context. Ask only for details that are missing. If the user did not give an expiry, use 24 hours after the due time.
2. Call `schedule_message` once with those values and a suitable expiry after the due time.
3. After the tool reports success, stop this turn without a separate confirmation message.

The setup turn only saves and schedules the message. Do not start the requested
work, inspect the project, prepare a plan, or make progress updates. The local
connector delivers the saved message when it is due, and the agent does the
requested work in that later turn. Keep the message faithful to the user's
request; do not add work that was not requested.

When the user asks to continue a Codex chat after its next usage reset, call
`schedule_auto_continue` with the current chat UUID and working directory. The
connector reads usage locally and chooses the due time; do not calculate or ask
the model to estimate it. Omit a message to use the standard continuation
instruction. After success, stop the turn.

Use `list_scheduled_messages` or `cancel_scheduled_message` only when the user
asks to inspect or cancel schedules. If the scheduler tools are unavailable,
tell the user to install and configure the local connector with
`agent-scheduler setup`. A Worker URL is optional.
