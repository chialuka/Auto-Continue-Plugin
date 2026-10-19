"""Durable timers. No model turn is used to register, wait, check usage, or enqueue."""
import contextlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid
from direct_host import Host, thread_id

DEFAULT_INSTRUCTION = ('Resume the task that was stopped when the usage limit was reached in this chat. '
                       'Continue from where it left off, preserving the user\'s instructions and ordinary '
                       'permissions. If no work remains, report that briefly.')
ACTIVE = ('armed', 'checking', 'sending', 'uncertain')


def root_path():
    return Path(os.environ.get('RESUME_AFTER_RESET_DATA') or
                Path.home()/'.local/share/resume-after-reset').expanduser().resolve()


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def bucket_reading(snapshot, bucket):
    buckets = snapshot.get('rateLimitsByLimitId')
    reading = buckets.get(bucket) if isinstance(buckets, dict) else None
    return reading if isinstance(reading, dict) else {}


def reset_time(snapshot, bucket, now):
    reading = bucket_reading(snapshot, bucket)
    windows = [reading.get(k) for k in ('primary', 'secondary') if isinstance(reading.get(k), dict)]
    exhausted = [w for w in windows if finite(w.get('usedPercent')) and w['usedPercent'] >= 100]
    selected = exhausted or windows
    values = [w.get('resetsAt') for w in selected]
    if not values or not all(finite(v) and v > now for v in values):
        raise ValueError('No reliable future reset time is available')
    return math.ceil(max(values) if exhausted else min(values)) + 60


class DirectStore:
    def __init__(self, root=None):
        self.root = Path(root or root_path()).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = self.root/'direct.sqlite3'
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS direct_jobs (
              thread TEXT PRIMARY KEY, generation TEXT NOT NULL, instruction TEXT NOT NULL,
              account TEXT NOT NULL, bucket TEXT NOT NULL, due REAL NOT NULL, expires REAL NOT NULL,
              status TEXT NOT NULL, retries INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL,
              queue_id TEXT, accepted_at REAL, last_error TEXT, is_test INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS direct_leases (account TEXT PRIMARY KEY, until REAL NOT NULL);''')
        self.db.chmod(0o600)

    @contextlib.contextmanager
    def connect(self):
        c = sqlite3.connect(str(self.db), isolation_level=None, timeout=35)
        c.row_factory = sqlite3.Row
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def status(self, thread):
        thread_id(thread)
        with self.connect() as c:
            row = c.execute('SELECT * FROM direct_jobs WHERE thread=?', (thread,)).fetchone()
        return dict(row) if row else None

    def enable(self, thread, instruction=DEFAULT_INSTRUCTION, host=None, now=None, test_delay=None):
        thread_id(thread)
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 20000:
            raise ValueError('A nonempty instruction of at most 20000 characters is required')
        existing = self.status(thread)
        if existing and existing['status'] in ACTIVE:
            if existing['instruction'] == instruction and existing['status'] != 'uncertain':
                return existing  # No usage read for identical repeated enablement.
            raise ValueError('Cancel the existing registration or inspect uncertain delivery first')
        # Prevent accidental parallel registration through the legacy native scheduler.
        legacy = self.root/'state.sqlite3'
        if legacy.exists():
            with contextlib.closing(sqlite3.connect(legacy.as_uri()+'?mode=ro', uri=True)) as c:
                row = c.execute('SELECT status FROM jobs WHERE thread=?', (thread,)).fetchone()
            if row and row[0] in ('armed', 'delivering', 'uncertain'):
                raise ValueError('Disable the existing native registration before direct setup')
        host = host or Host()
        snapshot = host.usage()
        now = time.time() if now is None else now
        account = snapshot.get('accountId')
        buckets = snapshot.get('rateLimitsByLimitId') or {}
        bucket = 'codex' if 'codex' in buckets else next(iter(buckets)) if len(buckets) == 1 else None
        if not isinstance(account, str) or not account or not bucket:
            raise ValueError('Account or applicable usage bucket is unavailable')
        if test_delay is not None:
            if not finite(test_delay) or not 10 <= test_delay <= 300:
                raise ValueError('Smoke-test delay must be between 10 and 300 seconds')
            due = now + test_delay
        else:
            due = reset_time(snapshot, bucket, now)
        if due >= now+8*86400:
            raise ValueError('Reset exceeds the eight-day registration lifetime')
        with self.connect() as c:
            old = c.execute('SELECT * FROM direct_jobs WHERE thread=?', (thread,)).fetchone()
            if old and old['status'] in ACTIVE:
                if old['instruction'] == instruction and old['status'] != 'uncertain':
                    return dict(old)
                raise ValueError('Registration changed while obtaining usage')
            c.execute('INSERT OR REPLACE INTO direct_jobs '
                      '(thread,generation,instruction,account,bucket,due,expires,status,updated,is_test) '
                      'VALUES (?,?,?,?,?,?,?,?,?,?)',
                      (thread, uuid.uuid4().hex, instruction, account, bucket, due, now+8*86400,
                       'armed', now, int(test_delay is not None)))
        return self.status(thread)

    def cancel(self, thread):
        thread_id(thread)
        with self.connect() as c:
            job = c.execute('SELECT * FROM direct_jobs WHERE thread=?', (thread,)).fetchone()
            if job and job['status'] in ('sending', 'queued'):
                raise ValueError('Message is being queued or already queued; it cannot be retracted here')
            c.execute('UPDATE direct_jobs SET status="disabled",updated=? WHERE thread=?', (time.time(), thread))
        return self.status(thread)

    def recover(self, now):
        # Called only after obtaining the worker's exclusive OS lock.
        with self.connect() as c:
            c.execute('UPDATE direct_jobs SET status="uncertain",last_error="worker_interrupted_during_queue",'
                      'updated=? WHERE status="sending"', (now,))
            c.execute('UPDATE direct_jobs SET status="armed",updated=? WHERE status="checking"', (now,))

    def claim(self, now):
        with self.connect() as c:
            c.execute('UPDATE direct_jobs SET status="expired",updated=? WHERE status="armed" AND expires<=?', (now, now))
            job = c.execute('SELECT * FROM direct_jobs WHERE status="armed" AND due<=? ORDER BY due LIMIT 1', (now,)).fetchone()
            if not job:
                return None
            lease = c.execute('SELECT until FROM direct_leases WHERE account=?', (job['account'],)).fetchone()
            if lease and lease[0] > now:
                c.execute('UPDATE direct_jobs SET due=? WHERE thread=?', (lease[0], job['thread']))
                return None
            c.execute('UPDATE direct_jobs SET status="checking",updated=? WHERE thread=?', (now, job['thread']))
            return dict(job)

    def update(self, job, status, now, error=None, due=None, retry=False, queue_id=None):
        with self.connect() as c:
            c.execute('UPDATE direct_jobs SET status=?,updated=?,last_error=?,due=COALESCE(?,due),'
                      'retries=retries+?,queue_id=COALESCE(?,queue_id),accepted_at=CASE WHEN ? IS NOT NULL '
                      'THEN ? ELSE accepted_at END WHERE thread=? AND generation=? AND status IN ("checking","sending")',
                      (status, now, error, due, int(retry), queue_id, queue_id, now, job['thread'], job['generation']))

    def begin_send(self, job, now):
        with self.connect() as c:
            cursor = c.execute('UPDATE direct_jobs SET status="sending",updated=? WHERE thread=? AND generation=? '
                               'AND status="checking" AND expires>?', (now, job['thread'], job['generation'], now))
            if cursor.rowcount:
                c.execute('INSERT OR REPLACE INTO direct_leases VALUES (?,?)', (job['account'], now+120))
            return bool(cursor.rowcount)


class Worker:
    def __init__(self, store, host=None):
        self.store, self.host = store, host or Host()

    def defer(self, job, now, reason, due=None):
        known_reset = due is not None and due > now
        if not known_reset and job['retries'] >= 3:
            self.store.update(job, 'failed', now, reason)
            return
        due = due if known_reset else now+60*(2**job['retries'])
        self.store.update(job, 'expired' if due >= job['expires'] else 'armed', now,
                          reason, due, retry=not known_reset)

    def tick(self, now=None):
        now = time.time() if now is None else now
        job = self.store.claim(now)
        if not job:
            return None
        try:
            snapshot = self.host.usage()
            if not isinstance(snapshot, dict):
                raise ValueError('Usage response must be an object')
        except Exception:
            self.defer(job, now, 'usage_unavailable')
            return 'deferred'
        if snapshot.get('accountId') != job['account']:
            self.store.update(job, 'failed', now, 'account_changed')
            return 'failed'
        reading = bucket_reading(snapshot, job['bucket'])
        windows = [reading['primary'] if isinstance(reading.get('primary'), dict) else {}]
        if reading.get('secondary') is not None:
            windows.append(reading['secondary'] if isinstance(reading['secondary'], dict) else {})
        allowed = (snapshot.get('ordinaryUsageAllowed') is True and not reading.get('rateLimitReachedType')
                   and all(finite(w.get('usedPercent')) and 0 <= w['usedPercent'] < 100 for w in windows))
        if not allowed:
            try:
                exhausted = any(finite(w.get('usedPercent')) and w['usedPercent'] >= 100 for w in windows)
                due = reset_time(snapshot, job['bucket'], now) if exhausted else None
            except ValueError:
                due = None
            self.defer(job, now, 'usage_blocked_or_unknown', due)
            return 'deferred'
        if not self.store.begin_send(job, now):
            return 'cancelled'
        try:
            receipt = self.host.queue(job['thread'], job['instruction'])
        except Exception:
            self.store.update(job, 'uncertain', now, 'queue_result_uncertain')
            return 'uncertain'
        self.store.update(job, 'queued', now, queue_id=receipt)
        return 'queued'
