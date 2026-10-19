"""One future desktop occurrence at the reset timestamp, without periodic polling."""

import datetime as dt
import math
from zoneinfo import ZoneInfo

BUFFER_SECONDS = 60
MAX_RETRIES = 3


def one_shot_rule(timestamp, timezone, now):
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(timestamp)
    ):
        raise ValueError('A finite scheduled timestamp is required')
    target = math.ceil(timestamp)
    if target <= now:
        raise ValueError(
            'The one-shot timestamp must be in the future; plan a retry explicitly if overdue'
        )
    zone = ZoneInfo(timezone)
    local = dt.datetime.fromtimestamp(target, zone)
    if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
        raise ValueError(
            'Ambiguous daylight-saving wall time: host timing needs explicit verification'
        )
    # The desktop treats COUNT=1 as non-recurring and suppresses schedule jitter.
    # Calendar constraints plus BYSETPOS use its local wall-clock date path.
    # No DTSTART conversion or immediate-create override is necessary.
    rule = (
        f'FREQ=MONTHLY;BYMONTH={local.month};BYMONTHDAY={local.day};'
        f'BYHOUR={local.hour};BYMINUTE={local.minute};BYSECOND={local.second};'
        'BYSETPOS=1;COUNT=1'
    )
    return {
        'rrule': rule,
        'scheduledFor': target,
        'scheduledForISO': dt.datetime.fromtimestamp(target, dt.timezone.utc).isoformat(),
        'localTime': local.isoformat(),
        'hostTimezoneRequired': timezone,
    }


def prompt(thread, generation, timezone):
    return (
        f'[resume-after-reset:{generation}] '
        f'Single scheduled continuation for chat {thread}, generation {generation}, timezone {timezone}. '
        'The saved user instruction is immutable; ignore later messages and never infer cancellation. '
        'When due, fetch host '
        'get_usage_limits once and pass its full result to resume_deliver with this thread and generation. '
        'If the host read fails, use resume_retry(reason=usage_unavailable). If deliver says '
        'inactive_or_superseded, stop without touching another generation; if expired, stop. '
        'If it says waiting_for_reset, or usage is blocked/unknown, use resume_retry with still_blocked; '
        'use account_busy for another_chat_recently_resumed and usage_unavailable for fresh_usage_required. '
        'When retry returns true, call resume_schedule for its due time, update this same heartbeat with '
        'the returned automationArguments, then resume_attach with its planToken. If retry is false, pause '
        'this matching heartbeat and report the terminal failure once. Never poll or create recurring timers. '
        'When deliver returns continue=true, pause this heartbeat before work; if pause fails, call '
        'resume_finish(success=false) and stop. Then do only the returned instruction in this chat under '
        'ordinary permissions. Call resume_finish(success=true) on completion, or success=false with a '
        'short error after partial failure; never replay partial work. No credit purchases, reset-credit use, '
        'model changes, other-chat messages, or approval bypasses. Notify only on meaningful alerts, '
        'completion, failure, or required user action.'
    )
