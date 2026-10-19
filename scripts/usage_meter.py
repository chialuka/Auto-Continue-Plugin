"""Pure usage-meter logic. Host supplies snapshots; this module never infers usage."""

import math

THRESHOLDS = (80, 90, 95, 100)


def meter(snapshot, bucket_id='codex', previous=None):
    buckets = snapshot.get('rateLimitsByLimitId')
    bucket = buckets.get(bucket_id) if isinstance(buckets, dict) else None
    if bucket is None and not isinstance(buckets, dict):
        legacy = snapshot.get('rateLimits') or {}
        if legacy.get('limitId') == bucket_id:
            bucket = legacy
    result = {
        'scope': 'account',
        'bucketId': bucket_id,
        'ordinaryUsageAllowed': snapshot.get('ordinaryUsageAllowed'),
        'windows': [],
        'alerts': [],
        'state': {},
    }
    for key in ('primary', 'secondary'):
        window = (bucket or {}).get(key) or {}
        used = window.get('usedPercent')
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used):
            result['windows'].append({'window': key, 'usedPercent': None})
            continue
        used = max(0, min(100, used))
        reset = window.get('resetsAt')
        old = (previous or {}).get(key) or {}
        # Re-arm alerts for a new reset timestamp. Persist returned state globally.
        seen = old.get('threshold', 0) if old.get('resetsAt') == reset else 0
        reached = max((t for t in THRESHOLDS if used >= t), default=0)
        if reached > seen:
            result['alerts'].append({'window': key, 'threshold': reached})
        result['state'][key] = {'resetsAt': reset, 'threshold': max(seen, reached)}
        result['windows'].append(
            {
                'window': key,
                'usedPercent': used,
                'remainingPercent': 100 - used,
                'windowDurationMins': window.get('windowDurationMins'),
                'resetsAt': reset,
            }
        )
    return result
