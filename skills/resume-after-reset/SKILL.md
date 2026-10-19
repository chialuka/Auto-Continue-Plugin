---
name: resume-after-reset
description: Enable, inspect or cancel automatic continuation after a usage reset in this local Codex chat.
---

# Enable immediately

For a request to enable continuation, perform setup only:

1. Use the current chat UUID and client IANA timezone from context. Only if the UUID is absent, read CODEX_THREAD_ID. Do not search other chats.
2. Call `resume_direct_enable` immediately with `thread` and `timezone`. Omit `instruction` for generic enablement: the backend already has the interrupted-task default. Supply the exact user-authored instruction only when the user explicitly supplies a custom message to send.
3. Confirm the returned `job.status` and code-formatted `job.scheduledLocal` with `job.timezone` in one sentence. Stop. Do not resume the underlying task during activation.

No preparatory status, usage fetch, file inspection, panel opening, tool discovery, or native scheduling calls are needed when the direct tool is available. Do not start unrelated work, add progress checkpoints, or convert timestamps mentally. A successful response already confirms the saved timer and running worker.

If the direct tool is missing, report that the plugin needs refresh/reinstall. If setup fails, report the concrete error; do not claim success or loop blindly. The direct implementation currently requires macOS, Python, and a bundled Codex executable supporting app-server usage reads and `queue`.

# Status and cancellation

For status, call `resume_direct_status` with this exact `thread` and client `timezone`; report the returned state and scheduledLocal without fetching usage. `queued` confirms accepted input, not completed work.

Cancel only when explicitly requested using `resume_direct_cancel`. The saved instruction is immutable. Later messages, interruptions, and apparent completion never cancel or rewrite it. An uncertain delivery needs inspection and explicit rearming, never automatic replay.

# Optional usage display

For an explicit visual usage request, call `resume_meter` with the current chat UUID. The panel is optional and is never a prerequisite for scheduling. Only exact-chat-bound controls may enable or cancel a timer. Do not promise the UI rendered unless verified.

# Background behavior

Code reads usage, adds one minute, persists a timer, and queues the saved message into this chat when allowance is available. Setup through chat still uses this model turn; waiting, usage checks, and queue submission use code. The resumed task uses the chat's model and ordinary permissions. Do not change models, purchase credits, consume reset credits, bypass permissions, or message other chats. Delivery cannot execute while the Mac is asleep, off, or logged out; overdue timers are considered after waking/login, within their eight-day lifetime.

Only for an explicitly identified older native registration, read [legacy native management](references/legacy-native.md). Do not arm native and direct timers together.
