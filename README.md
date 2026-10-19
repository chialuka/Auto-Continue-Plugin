# Resume After Reset

A local macOS plugin that queues your saved continuation instruction into the same Codex chat at the next reset plus one minute.

## Use it

After installing, invoke the plugin in the chat you want to resume and say:

> Enable continuation after my next usage reset in this chat.

The agent makes one `resume_direct_enable` call. Code reads the reset timestamp, adds one minute, saves the generic interrupted-task instruction, and arms the local worker. The agent returns one short confirmation with the exact local time. It does not resume your task during setup. If you want a custom message delivered later, include it explicitly in the request.

Opening a usage panel is not required. Its controls remain an optional convenience. Chat invocation starts a model turn; skill instructions can minimize the work, but cannot force zero hidden reasoning or select a separate setup model. Waiting, quota checks and queue submission use local code without inference.

The saved instruction stays unchanged after enabling. Later messages and apparent task completion do not cancel it; explicitly ask to cancel if you want to stop delivery. Automatic enrollment of every chat is not part of this release.

## Install and verify

After the owner publishes this repository, other users can add its repo marketplace (replace the placeholders with the actual public GitHub repository):

```sh
codex plugin marketplace add YOUR_ACCOUNT/YOUR_REPOSITORY
codex plugin add resume-after-reset-mvp@resume-after-reset-community
```

This is manual repository distribution, separate from OpenAI directory approval.

The source stays in this folder. Register it using `python3 scripts/install_local.py`, then install/update through the host plugin manager or `codex plugin add resume-after-reset-mvp@local-codex-plugins`. Refresh the desktop plugin view if it still shows an older version. Do not edit version caches.

The service can be installed by the enable button, or explicitly with:

```sh
python3 scripts/direct_service.py install
python3 scripts/direct_service.py status --thread YOUR_CHAT_UUID
```

The daemon runs from a private runtime snapshot outside the plugin cache. Timers live in `~/.local/share/resume-after-reset/direct.sqlite3`. The launch agent is `~/Library/LaunchAgents/local.resume-after-reset.direct.plist`. No API key is required. The bundled Codex executable must support app-server usage reads and `queue`.

## Reliability and states

- `armed`: saved timer waiting for its due time.
- `checking`: worker is checking fresh usage without inference.
- `sending`: worker has committed to one queue attempt.
- `queued`: CLI accepted the message; `queue_id` is the receipt. This does not mean the task completed.
- `disabled`, `expired`, `failed`: will not send automatically.
- `uncertain`: the queue may have accepted the message, but the receipt was lost or the worker crashed during sending. Never replay automatically; inspect before explicitly cancelling/rearming.

An exclusive worker lock and transactional claim prevent duplicate sends. Cancellation during a usage read prevents delivery. Once sending begins, cancellation cannot retract the message. Account identity is checked at delivery, blocking windows postpone the timer, unknown readings get three bounded retries, and registrations expire after eight days. Multiple chats on the same account are staggered by two minutes.

The worker restarts through launchd, and persisted overdue timers are considered after restart or waking. It cannot execute while the Mac is asleep, powered off, or logged out. Real exhausted-quota recovery and physical sleep/wake require natural-condition acceptance testing; timer/queue smoke tests do not prove those cases.

## Removal

To cancel a pending direct timer, use its panel button, `resume_direct_cancel`, or `python3 scripts/direct_service.py cancel --thread YOUR_CHAT_UUID`. To remove the worker without deleting history, run `python3 scripts/direct_service.py uninstall`; retained timers will not fire until it is installed again. Uninstalling the plugin through the host does not automatically remove the independent launch agent, so remove the service too.

## Existing native timers

Legacy native scheduling tools remain available for older registrations. Direct setup refuses to arm a chat that has an active native registration, and native setup refuses an active direct registration. Existing native automation prompts remain snapshots from their creation time. Disable an old native timer explicitly before moving that chat to direct scheduling.

The optional meter reads local quota events for display. Its passive values are never used to authorize delivery. For older native registrations, use the compatibility instructions in the plugin skill.

## Public source and directory submission

Licensed under [MIT](LICENSE). This is an independent project and does not claim OpenAI endorsement. Manual GitHub marketplace distribution is separate from OpenAI directory review. No remote repository is configured by this source package.
