import concurrent.futures
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from engine import Store
from server import handle

ROOT=Path(__file__).resolve().parents[1]

def snap(primary=90,secondary=20,reset=200,weekly=500,allowed=True,account='a'):
    return {'accountId':account,'ordinaryUsageAllowed':allowed,'rateLimitsByLimitId':{'codex':{
        'primary':{'usedPercent':primary,'windowDurationMins':300,'resetsAt':reset},
        'secondary':{'usedPercent':secondary,'windowDurationMins':10080,'resetsAt':weekly}}}}

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name)
        self.job=self.s.enable('chat','a','Continue the authorized unfinished task',now=100)
    def tearDown(self):self.tmp.cleanup()
    def prepare(self):
        self.s.observe('chat',snap(),now=100)
        self.s.observe('chat',snap(2,reset=600),now=261)
    def test_persist_and_activation_idempotency(self):
        self.assertEqual(Store(self.tmp.name).get('chat')['generation'],self.job['generation'])
        self.assertEqual(self.s.enable('chat','a',self.job['instruction'])['generation'],self.job['generation'])
        with self.assertRaises(ValueError):self.s.enable('chat','a','Different task')
    def test_enable_uses_reset_timestamp_without_usage_snapshot(self):
        job=self.s.enable('scheduled','a','Continue this task',reset_at=200,now=100)
        self.assertEqual(job['due'],260)
        plan=self.s.schedule('scheduled',job['generation'],'Africa/Lagos',now=100)
        self.assertEqual(plan['scheduledFor'],260)

    def test_enable_plan_can_include_snapshot_for_requested_warnings(self):
        result=self.s.enable_plan('warned','a','Continue', 'Africa/Lagos', 200, snapshot=snap(96,reset=200), now=100)
        self.assertEqual(result['job']['due'],260)
        self.assertEqual(result['plan']['scheduledFor'],260)
        self.assertEqual(result['observation']['alerts'][0]['threshold'],95)

    def test_enable_rejects_invalid_or_past_reset(self):
        for reset_at in [True,float('nan'),99]:
            with self.assertRaisesRegex(ValueError,'reset timestamp'):
                self.s.enable('scheduled','a','Continue this task',reset_at=reset_at,now=100)

    def test_resume_enable_tool_accepts_timestamp_without_observation(self):
        reset_at=time.time()+300
        expected=math.ceil(reset_at)+60
        result=handle({'method':'tools/call','params':{'name':'resume_enable','arguments':{
            'thread':'scheduled','account':'a','bucket':'codex','instruction':'Continue', 'resetAt':reset_at,'timezone':'Africa/Lagos'}}},self.s)
        self.assertEqual(result['structuredContent']['job']['due'],expected)
        self.assertEqual(result['structuredContent']['plan']['scheduledFor'],expected)

    def test_both_windows_block(self):
        self.assertEqual(self.s.observe('chat',snap(100,100,allowed=False),now=100)['due'],560)
        self.assertFalse(self.s.claim('chat',self.job['generation'],now=261)['continue'])
    def test_unknown_reset_fails_closed(self):
        self.assertIsNone(self.s.observe('chat',snap(100,100,weekly=None),now=100)['due'])
    def test_no_early_or_stale_claim(self):
        self.s.observe('chat',snap(),now=100)
        self.assertFalse(self.s.claim('chat',self.job['generation'],now=200)['continue'])
        self.assertEqual(self.s.claim('chat',self.job['generation'],now=800)['reason'],'fresh_usage_required')
    def test_delivery_combines_fresh_observation_and_claim(self):
        self.prepare()
        result=self.s.deliver('chat',self.job['generation'],snap(2,reset=600),now=261)
        self.assertTrue(result['continue'])
        self.assertEqual(result['continue'],True)
        self.assertEqual(result['automation'],None)
    def test_delivery_rejects_wrong_generation_before_ingesting_usage(self):
        self.prepare()
        result=self.s.deliver('chat','old-generation',snap(2,reset=600),now=261)
        self.assertEqual(result['reason'],'inactive_or_superseded')
        self.assertNotIn('automation',result)
        self.assertEqual(self.s.status('chat',now=261)['observedAt'],261)
    def test_fresh_reset_preserves_due(self):
        self.prepare()
        self.assertEqual(self.s.get('chat')['due'],260)
        self.assertTrue(self.s.claim('chat',self.job['generation'],now=261)['continue'])
        self.s.finish('chat',self.job['generation'])
        self.assertFalse(self.s.claim('chat',self.job['generation'],now=240)['continue'])
    def test_concurrent_claim_is_single(self):
        self.prepare()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as e:
            results=list(e.map(lambda _:Store(self.tmp.name).claim('chat',self.job['generation'],now=261),range(8)))
        self.assertEqual(sum(r['continue'] for r in results),1)
    def test_account_mismatch_rejected(self):
        with self.assertRaises(ValueError):self.s.observe('chat',snap(account='b'),now=100)
    def test_multiple_chats_staggered(self):
        self.prepare();other=self.s.enable('other','a','Continue task B',now=100)
        self.s.observe('other',snap(),now=100)
        self.s.observe('other',snap(2,reset=600),now=261)
        self.assertTrue(self.s.claim('chat',self.job['generation'],now=261)['continue'])
        self.assertEqual(self.s.claim('other',other['generation'],now=261)['reason'],'another_chat_recently_resumed')
    def test_false_allowed_does_not_deliver(self):
        self.prepare();self.s.observe('chat',snap(2,allowed=False),now=262)
        self.assertFalse(self.s.claim('chat',self.job['generation'],now=263)['continue'])
    def test_explicit_cancel_and_new_generation(self):
        self.prepare();self.s.cancel('chat')
        self.assertFalse(self.s.claim('chat',self.job['generation'],now=261)['continue'])
        new=self.s.enable('chat','a','New task',now=232)
        self.assertNotEqual(new['generation'],self.job['generation'])
        self.assertFalse(self.s.claim('chat',self.job['generation'],now=240)['continue'])
    def test_expiry(self):
        self.assertEqual(self.s.claim('chat',self.job['generation'],now=100+9*86400)['reason'],'expired')
    def test_partial_failure_not_replayed(self):
        self.prepare();self.s.claim('chat',self.job['generation'],now=261)
        self.s.finish('chat',self.job['generation'],success=False,error='Connection lost')
        self.assertEqual(Store(self.tmp.name).get('chat')['status'],'uncertain')
        self.assertFalse(self.s.claim('chat',self.job['generation'],now=240)['continue'])
    def test_manual_activity_and_interrupt_preserve_frozen_instruction(self):
        original = self.job['instruction']
        self.s.hook({'session_id':'chat','hook_event_name':'UserPromptSubmit','prompt':'A later unrelated task'})
        self.assertEqual(self.s.get('chat')['status'],'armed')
        self.s.hook({'session_id':'chat','hook_event_name':'Interrupt'})
        self.assertEqual(self.s.get('chat')['status'],'armed')
        self.assertEqual(self.s.get('chat')['instruction'],original)
    def test_heartbeat_and_unregistered_chat_untouched(self):
        self.s.hook({'session_id':'chat','hook_event_name':'UserPromptSubmit','prompt':'[resume-after-reset:'+self.job['generation']+'] Check usage'})
        self.assertEqual(self.s.get('chat')['status'],'armed')
        self.assertEqual(self.s.hook({'session_id':'unknown','hook_event_name':'Interrupt'}),{})
    def test_meter_staleness(self):
        self.s.observe('chat',snap(),now=100)
        self.assertFalse(self.s.status('chat',now=200)['stale'])
        self.assertTrue(self.s.status('chat',now=800)['stale'])
    def test_secret_fields_not_stored(self):
        data=snap();data['rateLimitsByLimitId']['codex']['credits']={'secret':'sentinel'}
        self.s.observe('chat',data,now=100)
        with self.s.connect() as c:
            saved=c.execute('SELECT snapshot FROM readings').fetchone()[0]
        self.assertNotIn('sentinel',saved)
    def test_protocol_roundtrip_subprocess(self):
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05'}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'resume_status','arguments':{'thread':'chat'}}},
                  {'jsonrpc':'2.0','id':4,'method':'resources/read','params':{'uri':'ui://resume-after-reset/meter-v2.html'}},
                  {'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'resume_cancel','arguments':{'thread':'chat','reason':'bad'}}}]
        p=subprocess.run([sys.executable,str(ROOT/'scripts/server.py')],input='\n'.join(map(json.dumps,requests))+'\n',text=True,capture_output=True,
                         env=dict(os.environ,RESUME_AFTER_RESET_DATA=self.tmp.name),timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)
        replies=[json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual(len(replies),5)
        self.assertIn('resume_direct_enable',{t['name'] for t in replies[1]['result']['tools']})
        self.assertEqual(replies[2]['result']['structuredContent']['job']['thread'],'chat')
        self.assertIn('Unit-o-meter',replies[3]['result']['contents'][0]['text'])
        self.assertIn('error',replies[4])
    def test_snapshot_alerts_survive_restart_and_other_chat(self):
        first=self.s.observe('chat',snap(92),now=100)
        self.assertEqual(first['alerts'][0]['threshold'],90)
        self.assertEqual(Store(self.tmp.name).observe('chat',snap(93),now=101)['alerts'],[])
        other=self.s.enable('other','a','Other task',now=100)
        self.assertEqual(self.s.observe('other',snap(93),now=101)['alerts'],[])
        self.assertEqual(self.s.observe('other',snap(96),now=102)['alerts'][0]['threshold'],95)
    def test_negative_usage_cannot_authorize_work(self):
        self.prepare();self.s.observe('chat',snap(-1,reset=600),now=231)
        self.assertFalse(self.s.claim('chat',self.job['generation'],now=263)['continue'])
    def test_later_messages_do_not_replace_saved_instruction(self):
        original = self.job['instruction']
        self.s.hook({'session_id':'chat','hook_event_name':'UserPromptSubmit',
                     'prompt':'<heartbeat><instructions>[resume-after-reset:'+self.job['generation']+'] Check usage</instructions></heartbeat>'})
        self.s.hook({'session_id':'chat','hook_event_name':'UserPromptSubmit','prompt':'Please do something else'})
        self.assertEqual(self.s.get('chat')['status'],'armed')
        self.assertEqual(self.s.get('chat')['instruction'],original)
    def test_hook_process_context(self):
        p=subprocess.run([sys.executable,str(ROOT/'hooks/lifecycle.py')],input=json.dumps({'session_id':'chat','hook_event_name':'SessionStart'}),
                         text=True,capture_output=True,env=dict(os.environ,RESUME_AFTER_RESET_DATA=self.tmp.name),timeout=10)
        self.assertIn('hookSpecificOutput',json.loads(p.stdout))

if __name__=='__main__':unittest.main()
