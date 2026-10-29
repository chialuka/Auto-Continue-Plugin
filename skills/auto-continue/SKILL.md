---
name: auto-continue
description: Enable, inspect or cancel automatic continuation after a usage reset in this local Codex chat.
---

# Enable immediately

For a request to enable continuation, perform scheduling only. Treat any task
description accompanying the request as the message to save for the due time,
not as work to start in the current turn.

1. Use the current Codex chat UUID and absolute working directory from host context. Do not search other chats.
2. Call `schedule_auto_continue` immediately with `sessionRef` and `workDir`. Omit `message` for the standard interrupted-task instruction; pass only a custom instruction the user explicitly asks to send.
3. After the tool reports success, stop this turn without a separate confirmation message. Do not inspect the project, prepare the task, or start its implementation during activation. The local scheduler reads Codex usage, saves the timer for one minute after the applicable reset, and delivers the saved instruction in a later turn.

No preparatory status, usage fetch, file inspection, panel opening, tool discovery, or native scheduling calls are needed. Do not calculate the reset time in the model, start unrelated work, or add progress checkpoints.

If `schedule_auto_continue` is unavailable, say that the local `agent-scheduler` connector must be installed and configured. If setup fails, report the concrete error; do not claim success or loop blindly.

# Status and cancellation

For status, call `list_scheduled_messages` with the current Codex provider and exact `sessionRef`. For cancellation, call `cancel_scheduled_message` with the selected job ID. Do not fetch usage to inspect or cancel a saved schedule.

Cancel only when explicitly requested. Later messages, interruptions, and apparent completion never cancel or rewrite a saved instruction. An uncertain delivery needs inspection and explicit rearming, never automatic replay.

# Optional usage display

Usage display is separate from scheduling and is not needed to enable continuation.

# Background behavior

The local scheduler reads usage and queues the saved message after reset. Setup through chat uses this model turn; waiting, usage checks, and message delivery use local code. The resumed task uses the chat's model and ordinary permissions. Do not change models, purchase credits, consume reset credits, bypass permissions, or message other chats. Delivery requires the device and agent CLI to be available when the message is due; the server retries a disconnected device and the connector delivers after reconnecting, within the job expiry.

Only for an explicitly identified older native registration, read [legacy native management](references/legacy-native.md). Do not arm native and direct timers together.
