import datetime as dt
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from engine import Store
from scheduling import one_shot_rule
from test_engine import snap

class SchedulingTests(unittest.TestCase):
    def test_lagos_date_and_second_precision(self):
        when=dt.datetime(2026,10,18,17,43,40,tzinfo=dt.timezone.utc).timestamp()
        plan=one_shot_rule(when,'Africa/Lagos',when-180)
        self.assertEqual(plan['rrule'],'FREQ=MONTHLY;BYMONTH=10;BYMONTHDAY=18;BYHOUR=18;BYMINUTE=43;BYSECOND=40;BYSETPOS=1;COUNT=1')
        self.assertEqual(plan['scheduledFor'],when)
        self.assertNotIn('INTERVAL',plan['rrule'])
        self.assertNotIn('DTSTART',plan['rrule'])
    def test_month_year_and_weekly_horizons(self):
        for iso in ['2026-11-01T00:00:05+00:00','2027-01-01T00:00:05+00:00','2026-10-25T17:43:40+00:00']:
            when=dt.datetime.fromisoformat(iso).timestamp()
            plan=one_shot_rule(when,'Africa/Lagos',when-7*86400-30)
            self.assertEqual(plan['scheduledFor'],when)
            self.assertTrue(plan['rrule'].endswith('COUNT=1'))
    def test_no_rounding_to_thirty_minute_grid(self):
        for second in [1,17,31,59]:
            plan=one_shot_rule(1792345380+second,'Africa/Lagos',1792345000)
            self.assertEqual(plan['scheduledFor'],1792345380+second)
            self.assertIn('BYSECOND='+str(second),plan['rrule'])
    def test_past_unknown_and_ambiguous_times_rejected(self):
        for timestamp in [None,True,float('nan'),99]:
            with self.assertRaises(ValueError):one_shot_rule(timestamp,'Africa/Lagos',100)
        when=dt.datetime.fromisoformat('2026-11-01T06:30:00+00:00').timestamp()
        with self.assertRaisesRegex(ValueError,'Ambiguous'):one_shot_rule(when,'America/New_York',when-86400)
    def test_reset_buffer_and_attach_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp);j=s.enable('chat','a','Continue test',now=100)
            observation=s.observe('chat',snap(reset=200),now=100)
            self.assertEqual(observation['due'],260)
            self.assertTrue(observation['scheduleNeeded'])
            plan=s.schedule('chat',j['generation'],'Africa/Lagos',now=100)
            self.assertEqual(plan['scheduledFor'],260)
            self.assertEqual(plan['automationArguments']['mode'],'create')
            s.attach('chat',j['generation'],'host-automation',plan['planToken'])
            self.assertEqual(Store(tmp).get('chat')['scheduled_due'],260)
            self.assertFalse(s.observe('chat',snap(reset=200),now=101)['scheduleNeeded'])
            changed=s.observe('chat',snap(100,100,weekly=500),now=102)
            self.assertEqual(changed['due'],560)
            self.assertTrue(changed['scheduleNeeded'])
            update=s.schedule('chat',j['generation'],'Africa/Lagos',now=103)
            self.assertEqual(update['automationArguments']['id'],'host-automation')
            self.assertEqual(update['automationArguments']['mode'],'update')
    def test_stale_plan_and_old_generation_cannot_attach(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp);j=s.enable('chat','a','Continue',now=100)
            s.observe('chat',snap(),now=100)
            plan=s.schedule('chat',j['generation'],'Africa/Lagos',now=100)
            s.observe('chat',snap(100,100),now=101)
            with self.assertRaisesRegex(ValueError,'Stale'):s.attach('chat',j['generation'],'timer',plan['planToken'])
            s.cancel('chat')
            with self.assertRaises(ValueError):s.schedule('chat',j['generation'],'Africa/Lagos',now=102)
    def test_retry_only_schedules_single_bounded_occurrences(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp);j=s.enable('chat','a','Continue',now=100)
            s.observe('chat',snap(),now=100)
            s.observe('chat',snap(2,reset=600,allowed=False),now=261)
            self.assertFalse(s.claim('chat',j['generation'],now=261)['continue'])
            for count,now in enumerate([261,292,353],1):
                retry=s.retry('chat',j['generation'],'still_blocked',now=now)
                self.assertEqual(retry['retryCount'],count)
                self.assertEqual(retry['due'],now+30*2**(count-1))
                plan=s.schedule('chat',j['generation'],'Africa/Lagos',now=now)
                self.assertIn('COUNT=1',plan['rrule'])
            self.assertFalse(s.retry('chat',j['generation'],'still_blocked',now=474)['retry'])
            self.assertEqual(s.get('chat')['status'],'failed')
    def test_duplicate_retry_does_not_consume_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp);j=s.enable('chat','a','Continue',now=100)
            s.observe('chat',snap(),now=100)
            first=s.retry('chat',j['generation'],'still_blocked',now=231)
            again=s.retry('chat',j['generation'],'still_blocked',now=232)
            self.assertEqual(first,again)
    def test_retry_waits_for_new_reported_weekly_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(tmp);j=s.enable('chat','a','Continue',now=100)
            s.observe('chat',snap(100,100,weekly=500),now=231)
            self.assertEqual(s.retry('chat',j['generation'],'still_blocked',now=231)['due'],560)
    def test_legacy_database_migrates_without_losing_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=sqlite3.connect(str(Path(tmp)/'state.sqlite3'))
            c.execute('CREATE TABLE jobs(thread TEXT PRIMARY KEY,account TEXT NOT NULL,bucket TEXT NOT NULL,instruction TEXT NOT NULL,generation TEXT NOT NULL,status TEXT NOT NULL,due REAL,expires REAL NOT NULL,automation TEXT,attempts INTEGER DEFAULT 0,created REAL NOT NULL,updated REAL NOT NULL,last_error TEXT)')
            c.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',('chat','a','codex','Continue','oldgen','armed',200,99999,'oldtimer',0,100,100,None))
            c.commit();c.close()
            job=Store(tmp).get('chat')
            self.assertEqual(job['generation'],'oldgen');self.assertEqual(job['retry_count'],0)
            self.assertIsNone(job['scheduled_due'])

if __name__=='__main__':unittest.main()
