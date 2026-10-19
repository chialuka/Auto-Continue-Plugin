import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from passive_usage import latest_usage
from engine import Store
from server import handle

class PassiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)/'codex';self.home.mkdir()
        (self.home/'sessions').mkdir();self.path=self.home/'sessions/current.jsonl'
        c=sqlite3.connect(self.home/'state_5.sqlite');c.execute('CREATE TABLE threads (id TEXT,rollout_path TEXT)')
        c.execute('INSERT INTO threads VALUES (?,?)',('current',str(self.path)));c.commit();c.close()
        self.path.write_text(json.dumps({'type':'session_meta','payload':{'id':'current'}})+'\n')
    def tearDown(self):self.tmp.cleanup()
    def event(self,used=20,at=100):
        return {'timestamp':dt.datetime.fromtimestamp(at,dt.timezone.utc).isoformat(),'type':'event_msg','payload':{'type':'token_count','rate_limits':{'limit_id':'codex','primary':{'used_percent':used,'window_minutes':300,'resets_at':300},'secondary':{'used_percent':30,'window_minutes':10080,'resets_at':600},'credits':{'secret':'never-store'}}}}
    def append(self,event):
        with self.path.open('a') as f:f.write(json.dumps(event)+'\n')
    def test_native_shape_and_latest_reading(self):
        self.append(self.event(20,100));self.append(self.event(25,101))
        reading=latest_usage('current',self.home,now=102)
        self.assertEqual(reading['snapshot']['rateLimitsByLimitId']['codex']['primary']['usedPercent'],25)
        self.assertEqual(reading['observedAt'],101)
        self.assertNotIn('never-store',json.dumps(reading))
    def test_partial_line_future_and_unknown_skipped(self):
        self.append(self.event(20,100));self.append(self.event(90,1000))
        with self.path.open('a') as f:f.write(json.dumps(self.event(99,102)))
        self.assertEqual(latest_usage('current',self.home,now=102)['observedAt'],100)
    def test_no_cross_chat_or_arbitrary_paths(self):
        self.assertIsNone(latest_usage('other',self.home,now=102))
        with self.assertRaises(ValueError):latest_usage('../../secret',self.home)
        self.path.write_text('{"type":"session_meta","payload":{"id":"other"}}\n')
        with self.assertRaises(ValueError):latest_usage('current',self.home)
    def test_outside_sessions_rejected(self):
        c=sqlite3.connect(self.home/'state_5.sqlite');c.execute('UPDATE threads SET rollout_path=?',(str(self.home/'secret.jsonl'),));c.commit();c.close()
        with self.assertRaises(ValueError):latest_usage('current',self.home)
    def test_meter_does_not_arm_continuation_and_auto_updates(self):
        self.append(self.event(20,100))
        with patch.dict(os.environ,{'CODEX_HOME':str(self.home)}):
            s=Store(Path(self.tmp.name)/'plugin')
            first=s.meter_state('current',now=101)
            self.assertEqual(first['meter']['windows'][0]['remainingPercent'],80)
            self.assertIsNone(s.get('current'))
            self.append(self.event(33,102))
            newer=s.meter_state('current',now=103)
            self.assertEqual(newer['meter']['windows'][0]['usedPercent'],33)
            self.assertTrue(newer['automatic'])
            self.assertFalse(newer['stale'])
            self.assertTrue(s.meter_state('current',now=1000)['stale'])
            self.assertEqual(s.meter_state(now=103)['thread'],'current')
    def test_passive_display_cannot_authorize_claim(self):
        self.append(self.event(0,100))
        with patch.dict(os.environ,{'CODEX_HOME':str(self.home)}):
            s=Store(Path(self.tmp.name)/'plugin');job=s.enable('current','account','Continue',now=100)
            s.meter_state('current',now=101)
            self.assertFalse(s.claim('current',job['generation'],now=101)['continue'])
    def test_thread_entrypoint_accepts_no_arguments(self):
        with patch.dict(os.environ,{'CODEX_HOME':str(self.home)}):
            s=Store(Path(self.tmp.name)/'plugin')
            response=handle({'method':'tools/call','params':{'name':'resume_meter','arguments':{}}},s)
            self.assertIsNone(response['structuredContent']['meter'])
            tools=handle({'method':'tools/list'},s)['tools']
            entry=next(x for x in tools if x['name']=='resume_meter')
            self.assertEqual(entry['_meta']['openai/ui']['entrypoints'],[{'type':'thread'}])
            self.assertEqual(entry['inputSchema']['required'],[])
    def test_missing_native_data_is_explicit(self):
        with patch.dict(os.environ,{'CODEX_HOME':str(self.home)}):
            s=Store(Path(self.tmp.name)/'plugin');data=s.meter_state('missing',now=100)
            self.assertIsNone(data['meter']);self.assertFalse(data['automatic'])

if __name__=='__main__':unittest.main()
