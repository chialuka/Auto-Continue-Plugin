"""Local durable state. Desktop host owns inference and heartbeat scheduling."""
import contextlib
import datetime as dt
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid
from usage_meter import meter
from passive_usage import latest_usage
from scheduling import BUFFER_SECONDS, MAX_RETRIES, one_shot_rule, prompt

MAX_AGE = 600


def data_root():
    return Path(os.environ.get('RESUME_AFTER_RESET_DATA')
                or Path.home() / '.local/share/resume-after-reset')


def valid_text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 20000:
        raise ValueError(field + ' must be nonempty text of at most 20000 characters')
    return value


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Store:
    def __init__(self, path=None):
        self.root = Path(path or data_root())
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = self.root / 'state.sqlite3'
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
              thread TEXT PRIMARY KEY, account TEXT NOT NULL, bucket TEXT NOT NULL,
              instruction TEXT NOT NULL, generation TEXT NOT NULL,
              status TEXT NOT NULL, due REAL, expires REAL NOT NULL,
              automation TEXT, attempts INTEGER DEFAULT 0, created REAL NOT NULL,
              updated REAL NOT NULL, last_error TEXT);
            CREATE TABLE IF NOT EXISTS readings (
              account TEXT NOT NULL, bucket TEXT NOT NULL, snapshot TEXT NOT NULL,
              state TEXT NOT NULL, observed REAL NOT NULL, PRIMARY KEY(account,bucket));
            CREATE TABLE IF NOT EXISTS meter_sources (
              thread TEXT PRIMARY KEY, connected REAL NOT NULL, observed REAL,
              snapshot TEXT, bucket TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')
            existing = {r[1] for r in c.execute('PRAGMA table_info(jobs)')}
            for name, sql_type in [('scheduled_due', 'REAL'), ('plan_token', 'TEXT'),
                                   ('plan_due', 'REAL'), ('retry_count', 'INTEGER DEFAULT 0')]:
                if name not in existing:
                    c.execute('ALTER TABLE jobs ADD COLUMN '+name+' '+sql_type)
        self.db.chmod(0o600)

    @contextlib.contextmanager
    def connect(self):
        c = sqlite3.connect(str(self.db), timeout=10, isolation_level=None)
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

    def get(self, thread):
        with self.connect() as c:
            row = c.execute('SELECT * FROM jobs WHERE thread=?', (thread,)).fetchone()
            return dict(row) if row else None

    def enable(self, thread, account, instruction, bucket='codex', reset_at=None, now=None):
        now = time.time() if now is None else now
        for v, k in [(thread, 'thread'), (account, 'account'), (instruction, 'instruction'), (bucket, 'bucket')]:
            valid_text(v, k)
        due = None
        if reset_at is not None:
            if not number(reset_at) or reset_at <= now:
                raise ValueError('A finite future reset timestamp is required')
            due = math.ceil(reset_at) + BUFFER_SECONDS
        with self.connect() as c:
            old = c.execute('SELECT * FROM jobs WHERE thread=?', (thread,)).fetchone()
            if old and old['status'] in ('armed', 'delivering', 'uncertain'):
                if (old['account'], old['bucket'], old['instruction']) == (account, bucket, instruction):
                    return dict(old)  # Idempotent activation; never overwrite an in-flight registration.
                raise ValueError('Disable the existing registration before changing it')
            gen = uuid.uuid4().hex
            c.execute('INSERT OR REPLACE INTO jobs (thread,account,bucket,instruction,generation,status,due,expires,automation,attempts,created,updated,last_error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (thread, account, bucket, instruction, gen, 'armed', due, now+8*86400,
                       old['automation'] if old else None, 0, now, now, None))
        return self.get(thread)

    def enable_plan(self, thread, account, instruction, timezone, reset_at, bucket='codex', snapshot=None, now=None):
        job = self.enable(thread, account, instruction, bucket=bucket, reset_at=reset_at, now=now)
        observation = self.observe(thread, snapshot, now=now) if snapshot is not None else None
        if observation is not None:
            job = self.get(thread)
        plan = self.schedule(thread, job['generation'], timezone, now=now)
        return {'job': job, 'plan': plan, 'observation': observation}

    def schedule(self, thread, generation, timezone, now=None):
        now = time.time() if now is None else now
        with self.connect() as c:
            job = c.execute('SELECT * FROM jobs WHERE thread=?', (thread,)).fetchone()
            if not job or job['generation'] != generation or job['status'] != 'armed':
                raise ValueError('No matching armed registration')
            if job['expires'] <= now:
                raise ValueError('Registration expired')
            if job['due'] is None:
                raise ValueError('A known reset timestamp or explicit retry is required')
            plan = one_shot_rule(job['due'], timezone, now)
            if plan['scheduledFor'] >= job['expires']:
                raise ValueError('Scheduled timestamp exceeds registration expiry')
            token = uuid.uuid4().hex
            c.execute('UPDATE jobs SET plan_token=?,plan_due=? WHERE thread=?',
                      (token, plan['scheduledFor'], thread))
            args = {'mode': 'update' if job['automation'] else 'create',
                    'kind': 'heartbeat', 'destination': 'thread',
                    'name': 'Resume After Reset — '+thread[:12],
                    'prompt': prompt(thread, generation, timezone),
                    'rrule': plan['rrule'], 'status': 'ACTIVE'}
            if job['automation']: args['id'] = job['automation']
            return dict(plan, planToken=token, automationArguments=args)

    def attach(self, thread, generation, automation, planToken):
        valid_text(automation, 'automation')
        with self.connect() as c:
            job = c.execute('SELECT * FROM jobs WHERE thread=?', (thread,)).fetchone()
            if (not job or job['generation'] != generation or job['status'] != 'armed'
                    or job['plan_token'] != planToken or not planToken
                    or job['due'] is None or job['plan_due'] != math.ceil(job['due'])):
                raise ValueError('Stale or mismatched one-shot schedule plan; re-plan before attaching')
            c.execute('UPDATE jobs SET automation=?,scheduled_due=plan_due,plan_token=NULL WHERE thread=?',
                      (automation, thread))
        return self.get(thread)

    def retry(self, thread, generation, reason, now=None):
        now = time.time() if now is None else now
        if reason not in ('still_blocked', 'usage_unavailable', 'account_busy'):
            raise ValueError('Invalid retry reason')
        with self.connect() as c:
            job = c.execute('SELECT * FROM jobs WHERE thread=?', (thread,)).fetchone()
            if not job or job['generation'] != generation or job['status'] != 'armed':
                return {'retry': False, 'reason': 'inactive_or_superseded'}
            if job['expires'] <= now or job['retry_count'] >= MAX_RETRIES:
                c.execute('UPDATE jobs SET status=?,last_error=? WHERE thread=?',
                          ('expired' if job['expires'] <= now else 'failed', reason, thread))
                return {'retry': False, 'reason': 'expired_or_retry_limit'}
            if job['retry_count'] > 0 and job['due'] is not None and job['due'] > now:
                return {'retry': True, 'due': job['due'], 'retryCount': job['retry_count']}
            # A newly reported future reset is the earliest possible retry. Otherwise bounded backoff.
            due = max(now+30*(2**job['retry_count']), job['due'] or 0)
            if reason == 'account_busy':
                row = c.execute('SELECT value FROM meta WHERE key=?', ('account-lease:'+job['account'],)).fetchone()
                if row: due = max(due, float(row['value'])+1)
            if due >= job['expires']:
                c.execute('UPDATE jobs SET status="expired" WHERE thread=?', (thread,))
                return {'retry': False, 'reason': 'retry_exceeds_expiry'}
            c.execute('UPDATE jobs SET due=?,retry_count=retry_count+1,updated=?,last_error=? WHERE thread=?',
                      (due, now, reason, thread))
            return {'retry': True, 'due': due, 'retryCount': job['retry_count']+1}

    def observe(self, thread, snapshot, now=None):
        now = time.time() if now is None else now
        if not isinstance(snapshot, dict): raise ValueError('snapshot must be an object')
        account = snapshot.get('accountId')
        valid_text(account, 'snapshot.accountId')
        with self.connect() as c:
            row = c.execute('SELECT * FROM jobs WHERE thread=?', (thread,)).fetchone()
            if not row: raise ValueError('Enable this chat first')
            if row['account'] != account: raise ValueError('Account changed; disable and register again')
            prev = c.execute('SELECT state FROM readings WHERE account=? AND bucket=?',
                             (account, row['bucket'])).fetchone()
            reading = meter(snapshot, row['bucket'], json.loads(prev['state']) if prev else None)
            # Store only quota fields, never account emails, credentials, reset-credit IDs or balances.
            raw_bucket = self.bucket(snapshot, row['bucket'])
            sanitized = {k: raw_bucket.get(k) for k in ('primary', 'secondary', 'rateLimitReachedType')}
            for key in ('primary', 'secondary'):
                if isinstance(sanitized[key], dict):
                    sanitized[key] = {k: sanitized[key].get(k) for k in ('usedPercent', 'windowDurationMins', 'resetsAt')}
            clean = {'accountId': account, 'ordinaryUsageAllowed': snapshot.get('ordinaryUsageAllowed'),
                     'rateLimitsByLimitId': {row['bucket']: sanitized}}
            c.execute('INSERT OR REPLACE INTO readings VALUES (?,?,?,?,?)',
                      (account, row['bucket'], json.dumps(clean), json.dumps(reading['state']), now))
            windows = reading['windows']
            exhausted = [w for w in windows if w.get('usedPercent') is not None and w['usedPercent'] >= 100]
            reset_values = [w.get('resetsAt') for w in exhausted]
            due = None
            if exhausted:
                if all(number(x) for x in reset_values) and max(reset_values)+BUFFER_SECONDS > now:
                    due = max(reset_values)+BUFFER_SECONDS
            else:
                # Pre-register the next short-window reset before inference is blocked.
                primary = windows[0]
                reset = primary.get('resetsAt')
                if primary.get('usedPercent') is not None and number(reset) and reset > now: due = reset+BUFFER_SECONDS
            old_due = row['due']
            # Never postpone an already due job just because a new window has started.
            if old_due is not None and old_due <= now and not exhausted: due = old_due
            c.execute('UPDATE jobs SET due=?,updated=? WHERE thread=? AND status="armed"', (due, now, thread))
            reading.update(observedAt=now, due=due, scheduleNeeded=(row['status']=='armed' and due is not None and row['scheduled_due'] != math.ceil(due)))
            return reading

    @staticmethod
    def bucket(snapshot, bucket):
        buckets = snapshot.get('rateLimitsByLimitId')
        if isinstance(buckets, dict): return buckets.get(bucket) or {}
        legacy = snapshot.get('rateLimits') or {}
        return legacy if legacy.get('limitId') == bucket else {}

    def status(self, thread, now=None):
        now = time.time() if now is None else now
        job = self.get(thread)
        if not job: return {'job': None, 'meter': None, 'stale': True}
        with self.connect() as c:
            row = c.execute('SELECT * FROM readings WHERE account=? AND bucket=?',
                            (job['account'], job['bucket'])).fetchone()
        result = meter(json.loads(row['snapshot']), job['bucket'], json.loads(row['state'])) if row else None
        return {'job': job, 'meter': result, 'observedAt': row['observed'] if row else None,
                'stale': not row or now-row['observed'] > MAX_AGE}

    def meter_state(self, thread=None, now=None):
        now = time.time() if now is None else now
        if thread is not None:
            valid_text(thread, 'thread')
            with self.connect() as c:
                c.execute('INSERT OR IGNORE INTO meter_sources(thread,connected) VALUES (?,?)', (thread,now))
        with self.connect() as c:
            rows=(c.execute('SELECT * FROM meter_sources WHERE thread=?',(thread,)).fetchall() if thread
                  else c.execute('SELECT * FROM meter_sources ORDER BY connected DESC LIMIT 20').fetchall())
        for source in rows:
            try:
                reading=latest_usage(source['thread'], now=now)
                if reading and (source['observed'] is None or reading['observedAt']>=source['observed']):
                    with self.connect() as c:
                        c.execute('UPDATE meter_sources SET observed=?,snapshot=?,bucket=?,error=NULL WHERE thread=?',
                                  (reading['observedAt'],json.dumps(reading['snapshot']),reading['bucket'],source['thread']))
            except (OSError,ValueError,sqlite3.Error) as exc:
                with self.connect() as c:
                    c.execute('UPDATE meter_sources SET error=? WHERE thread=?',(str(exc),source['thread']))
        with self.connect() as c:
            if thread:
                source=c.execute('SELECT * FROM meter_sources WHERE thread=?',(thread,)).fetchone()
            else:
                source=c.execute('SELECT * FROM meter_sources ORDER BY observed DESC LIMIT 1').fetchone()
        if source and source['snapshot']:
            reading=meter(json.loads(source['snapshot']),source['bucket'])
            return {'thread':source['thread'],'meter':reading,'observedAt':source['observed'],
                    'stale':now-source['observed']>MAX_AGE,'source':'desktop_usage_event',
                    'automatic':True,'sourceError':source['error']}
        if thread:
            cached=self.status(thread, now)
            if cached['meter']:
                return dict(cached,thread=thread,automatic=False,source='host_snapshot')
        return {'thread':thread,'meter':None,'observedAt':None,'stale':True,'automatic':False,
                'sourceError':source['error'] if source else None,'source':'unavailable'}

    def cancel(self, thread, reason='disabled'):
        if reason not in ('disabled', 'expired'):
            raise ValueError('Invalid cancellation reason')
        with self.connect() as c:
            c.execute('UPDATE jobs SET status=?,updated=? WHERE thread=?', (reason, time.time(), thread))
        return self.get(thread)

    def claim(self, thread, generation, now=None):
        now = time.time() if now is None else now
        with self.connect() as c:
            job = c.execute('SELECT * FROM jobs WHERE thread=?', (thread,)).fetchone()
            if not job or job['generation'] != generation or job['status'] != 'armed':
                return {'continue': False, 'reason': 'inactive_or_superseded'}
            if job['expires'] <= now:
                c.execute('UPDATE jobs SET status="expired" WHERE thread=?', (thread,))
                return {'continue': False, 'reason': 'expired'}
            if job['due'] is None or job['due'] > now:
                return {'continue': False, 'reason': 'waiting_for_reset', 'due': job['due']}
            row = c.execute('SELECT * FROM readings WHERE account=? AND bucket=?',
                            (job['account'], job['bucket'])).fetchone()
            if not row or now-row['observed'] > MAX_AGE:
                return {'continue': False, 'reason': 'fresh_usage_required'}
            snapshot = json.loads(row['snapshot'])
            b = self.bucket(snapshot, job['bucket'])
            primary = b.get('primary') or {}
            secondary = b.get('secondary')
            if snapshot.get('ordinaryUsageAllowed') is not True or b.get('rateLimitReachedType'):
                return {'continue': False, 'reason': 'usage_blocked_or_unknown'}
            for w in [primary] + ([secondary] if secondary is not None else []):
                used = w.get('usedPercent')
                if not number(used) or used < 0 or used >= 100:
                    return {'continue': False, 'reason': 'usage_blocked_or_unknown'}
            if job['attempts'] >= 3:
                c.execute('UPDATE jobs SET status="failed" WHERE thread=?', (thread,))
                return {'continue': False, 'reason': 'retry_limit'}
            lease_key = 'account-lease:' + job['account']
            lease = c.execute('SELECT value FROM meta WHERE key=?', (lease_key,)).fetchone()
            if lease and float(lease['value']) > now:
                return {'continue': False, 'reason': 'another_chat_recently_resumed'}
            c.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (lease_key, str(now+120)))
            c.execute('UPDATE jobs SET status="delivering",attempts=attempts+1,updated=? WHERE thread=?', (now, thread))
            return {'continue': True, 'instruction': job['instruction'], 'generation': generation}

    def deliver(self, thread, generation, snapshot, now=None):
        """Ingest one fresh host reading and atomically claim, in one plugin round-trip."""
        now = time.time() if now is None else now
        job = self.get(thread)
        if not job or job['generation'] != generation or job['status'] != 'armed':
            return {'continue': False, 'reason': 'inactive_or_superseded'}
        if job['expires'] <= now:
            self.cancel(thread, 'expired')
            return {'continue': False, 'reason': 'expired'}
        if job['due'] is None or job['due'] > now:
            return {'continue': False, 'reason': 'waiting_for_reset', 'due': job['due'],
                    'automation': job['automation']}
        observation = self.observe(thread, snapshot, now=now)
        result = self.claim(thread, generation, now=now)
        return {**result, 'due': observation['due'],
                'scheduleNeeded': observation['scheduleNeeded'],
                'alerts': observation['alerts'], 'automation': job['automation']}

    def finish(self, thread, generation, success=True, error=None, now=None):
        now = time.time() if now is None else now
        with self.connect() as c:
            job = c.execute('SELECT * FROM jobs WHERE thread=? AND generation=?', (thread, generation)).fetchone()
            if not job or job['status'] != 'delivering': raise ValueError('No matching delivery claim')
            # Do not automatically replay a partially performed task after an ambiguous crash.
            c.execute('UPDATE jobs SET status=?,last_error=?,updated=? WHERE thread=?',
                      ('delivered' if success else 'uncertain', error, now, thread))
        return self.get(thread)

    def hook(self, event):
        thread = event.get('session_id')
        job = self.get(thread) if thread else None
        if not job or job['status'] != 'armed': return {}
        name = event.get('hook_event_name')
        context = ('Resume After Reset is armed for this chat. Its saved continuation instruction is frozen; '
                   'later user messages and interruptions do not change or cancel it. Do not read usage '
                   'just for this registration; the scheduled delivery performs its one fresh authorization check. '
                   'Only an explicit user request to disable/cancel may cancel this registration.')
        if name == 'SessionStart':
            return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': context}}
        return {}
