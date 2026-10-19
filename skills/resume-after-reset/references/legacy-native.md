# Legacy native scheduler (existing registrations only)

The following flow remains for compatibility with existing host-owned timers. Do not arm direct and native timers together in the same chat. Preserve existing native scheduling and approval requirements when managing an older registration.

## Enable in this chat

1. Read `get_usage_limits` once. From the applicable account bucket, select the earliest finite future `resetsAt` across its primary and secondary windows. Call `resume_enable` with the current thread, returned `accountId`, applicable bucket, selected `resetAt`, the user's IANA timezone, and the continuation instruction. The tool saves the registration and returns the one-shot calendar plan in the same call. Do not call `resume_observe` during setup. Omit `snapshot` for reset-only continuation; if the user also requests usage warnings, include the already fetched full snapshot in this same `resume_enable` call. Repeated identical activation is idempotent. A different instruction requires canceling the existing registration first.
2. Pass `plan.automationArguments` from the result to the host `automation_update` tool unchanged, apart from preserving unrelated existing settings on update. This schedules the selected reset plus 60 seconds with one occurrence; do not substitute an interval, poll, add jitter, change to suggested creation, or edit host scheduling files. The generated prompt contains the full delivery/retry procedure. Existing automations are updated in place. The thread-heartbeat tool has no model override; do not pass an unsupported `model` field or change the chat model to force one.
3. On host success, call `resume_attach` with the generation from `job`, returned automation ID and `plan.planToken`. If scheduling or attaching fails, pause any matching timer actually created or updated and cancel the local registration; report the concrete failure.
4. Only then report enablement and the scheduled local time. This is one timer at the next reset plus 60 seconds, not recurring checks. The host may defer a wakeup when the app is unavailable or the chat is busy; do not promise execution precision beyond its saved schedule.

## Scheduled delivery and retries

`resume_schedule` builds the self-contained heartbeat prompt in `scripts/scheduling.py`; use that prompt rather than reconstructing it. On delivery, make one fresh host usage read and pass it to `resume_deliver`. That single plugin call validates the generation and due time, ingests the snapshot, and claims once. Do not call `resume_status`, `resume_observe`, or a separate claim as part of normal delivery. Pause the consumed timer before authorized work.

If a fresh reading reports a later blocking reset, create a replacement one-shot for that timestamp plus 60 seconds. If the scheduled delivery is still blocked without a newer known reset, call `resume_retry` and schedule its returned due time using `resume_schedule`. Retries use 30, 60 and 120 seconds of backoff, at most three. Account contention waits for the saved account lease. Duplicate retry calls before the already planned retry do not consume more attempts. Terminal or ambiguous failures are not automatically replayed.

There are no scheduled checks before the saved reset time and no repeating polling interval after it. Do not refresh host usage at progress checkpoints for continuation; the saved timer is based on the single reset timestamp captured at activation, and delivery makes one fresh authorization read. If no reset timestamp is available at activation, report that scheduling is unavailable; never guess.

Native registration at both a near-future timestamp and a timestamp seven days ahead was verified against the desktop scheduler's stored next-run time. Real exhausted-quota turn startup and blocked native invocations still need natural-quota acceptance testing.

## During work and meter requests

For continuation-only activation, do not fetch usage again during work. If the user separately asks for usage warnings, read host usage once and pass that snapshot to `resume_observe`; show only new alerts at 80%, 90%, 95% and exhaustion. The server deduplicates threshold alerts per account/bucket/window/reset timestamp across registered chats.

For a usage-only request, call `resume_meter` with the current thread ID immediately so the host renders the battery-style view. This connects the meter to that chat's local quota events and does not enable continuation or create a schedule. Do not substitute a text table when the visual tool is available. If the host cannot render MCP Apps, report that concrete UI limitation and use a short text fallback.

The Unit-o-meter has a conversation-panel entrypoint, so the user can keep it open beside the chat. The panel calls `resume_meter_state` every five seconds while visible; the server reads only usage events from explicitly connected chats. No repeated user prompt, model turn, Codex CLI or background heartbeat is needed for meter refresh. Readings arrive when the desktop records a new quota event during work, not on every keystroke. If native event data is unavailable or stale, show that honestly; do not fabricate a live value. A panel opened with no arguments can show a previously connected account meter; it must never guess a different current chat's ID.

Use the user's battery analogy: a full green battery means more remaining allowance, amber means at most 20% remaining, and red means at most 5%. Show used percentage secondarily. The two batteries represent account-wide windows, not tokens consumed by one conversation. The plugin cannot force a permanent meter into the host's chat header/composer or keep a closed panel visible.

Passive display readings are isolated from continuation authorization. `resume_deliver` requires a fresh native `get_usage_limits` snapshot. Never feed passive quota events into delivery authorization or reschedule continuation from a display-only refresh.

Do not cancel because an answer, setup step, or later task in the same chat appears complete. The queued user-authored instruction is an immutable scheduled message. Trusted lifecycle hooks may remind the model about active registrations, but never cancel or rewrite them based on interruption or new user activity. Only an explicit user cancellation, expiry, or delivery completion ends the registration. Hooks never authorize other actions or bypass permissions.

## Status, disable and edit

Status: call `resume_status`; report registration status, reset gate, automation ID and meter freshness. Inactive local state means no continuation is authorized even if a heartbeat still exists.

Disable: cancel locally first using `resume_cancel`, then pause the saved native heartbeat through automation_update, preserving its other fields. Confirm both results; if pausing fails, explain that local cancellation still prevents the plugin from claiming work but the schedule needs cleanup.

Edit: disable and pause the old registration, then enable a new generation with the revised instruction. Never reactivate an old prompt for a new generation. The saved instruction is immutable after scheduling. A newer manual message does not cancel or replace it; use the explicit disable action to stop the queued delivery.

## Delivery smoke test

When explicitly requested, test the same calendar one-shot format for a harmless message a few minutes ahead, using `scripts/scheduling.py` to generate the calendar fields for a test timestamp. Do not alter real quota or production due times. Use the native host scheduling tool and verify its single-occurrence schedule, then let the chat go idle so it can fire. Do not use a repeating interval for this test. Pause the test after delivery if cleanup is needed. Label it as timer delivery, not actual quota recovery.

## Failures and limits

Persisted registration survives server process restart. Delivery claims are atomic, duplicate calls cannot both continue, and different chats on one account are staggered by two minutes. A crash after claiming leaves delivering or uncertain state; require user inspection and re-activation rather than retry potentially completed side effects. Registrations expire after eight days. Native scheduler behavior during exhausted quota, sleep, offline periods, app restart and archived chats still requires host acceptance tests. Do not claim background delivery when the app is closed or claim activation after exhaustion without a successful test.
