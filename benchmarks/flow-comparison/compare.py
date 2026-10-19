"""Isolated production-handler/UI simulation; no live timers, model calls or usage reads."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch

PLUGIN = Path(os.environ.get('RESUME_FLOW_PLUGIN_ROOT') or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(PLUGIN/'scripts'))
from engine import Store
from server import handle
from direct_scheduler import DirectStore, DEFAULT_INSTRUCTION

THREAD = '12345678-1234-4234-8234-123456789abc'
NOW = 1792367993
SNAPSHOT = {'accountId':'fixture-account','ordinaryUsageAllowed':True,
            'rateLimitsByLimitId':{'codex':{
                'primary':{'usedPercent':66,'windowDurationMins':300,'resetsAt':NOW+900},
                'secondary':{'usedPercent':53,'windowDurationMins':10080,'resetsAt':NOW+86400},
                'rateLimitReachedType':None}}}


def backend():
    no_events = sys.argv[-1] == 'no-events'
    stats = {'freshUsageReads':0,'serviceEnsureCalls':0,'liveTimersCreated':0}
    def usage():
        stats['freshUsageReads'] += 1
        return copy.deepcopy(SNAPSHOT)
    def ensure(*_):
        stats['serviceEnsureCalls'] += 1
        return service
    passive = {'observedAt':NOW,'bucket':'codex',
               'snapshot':{'rateLimitsByLimitId':SNAPSHOT['rateLimitsByLimitId']}}
    service = {'installed':True,'loaded':True,'pid':12345,
               'plist':'/Users/fixture/Library/LaunchAgents/local.resume-after-reset.direct.plist',
               'dataRoot':'/Users/fixture/.local/share/resume-after-reset'}
    with tempfile.TemporaryDirectory(prefix='resume-flow-fixture-') as root, \
         patch('direct_service.ensure_service',side_effect=ensure), \
         patch('direct_service.service_status',return_value=service), \
         patch('direct_host.executable',return_value='/fixture/codex'), \
         patch('direct_host.Host.usage',side_effect=usage), \
         patch('direct_host.Host.queue',side_effect=AssertionError('Queue forbidden in setup simulation')), \
         patch('engine.latest_usage',return_value=None if no_events else passive), \
         patch('direct_scheduler.time.time',return_value=NOW), \
         patch('direct_scheduler.uuid.uuid4',return_value=uuid.UUID('11111111-2222-4333-8444-555555555555')):
        store = Store(root)
        for line in sys.stdin:
            request = json.loads(line)
            try:
                if request['method'] == 'fixture/state':
                    result = {'job':DirectStore(root).status(THREAD),'stats':stats}
                else:
                    result = handle(request, store)
                response = {'id':request['id'],'result':result}
            except Exception as error:
                response = {'id':request['id'],'error':str(error)}
            print(json.dumps(response), flush=True)


def compare():
    encoding = None
    if '--no-tokenizer' not in sys.argv:
        import requests
        original_get = requests.get
        def bounded_get(*args, **kwargs):
            kwargs.setdefault('timeout', 8)
            return original_get(*args, **kwargs)
        requests.get = bounded_get
        try:
            import tiktoken
            encoding = tiktoken.get_encoding('o200k_base')
        except Exception:
            encoding = None
    cases = ('direct','direct-default','cold-panel','warm-panel','cold-panel-no-events','unbound-panel')
    results = []
    for case in cases:
        completed = subprocess.run(['node',str(Path(__file__).with_name('ui_driver.js')),
                                    sys.executable,str(Path(__file__).resolve()),str(PLUGIN),case],
                                   capture_output=True,text=True,timeout=20,check=True)
        result = json.loads(completed.stdout)
        chunks = []
        for call in result.pop('modelCalls'):
            chunks.append(json.dumps(call['arguments'], separators=(',',':')))
            chunks.extend(item['text'] for item in call['result']['content'] if item['type']=='text')
        result['visibleToolPayloadTokens_o200k_base'] = sum(len(encoding.encode(chunk)) for chunk in chunks) if encoding else None
        result['visibleToolPayloadBytes'] = sum(len(chunk.encode()) for chunk in chunks)
        result['payloadScope'] = 'Model-mediated tool arguments and text results only; excludes user prompt, shared context, hidden reasoning, and final response.'
        if case != 'unbound-panel':
            assert result['armed'] and result['savedInstruction']==DEFAULT_INSTRUCTION
            assert result['due']==NOW+960
            assert result['stats']['freshUsageReads']==1
        else:
            assert not result['armed']
        result.pop('savedInstruction')
        results.append(result)
    report = {'measurement':'Deterministic simulation of actual plugin backend and actual meter JavaScript.',
              'notMeasured':'Actual billed model tokens, model latency, hidden reasoning, and desktop rendering reliability.',
              'liveTimersCreated':0,'modelApiCallsMade':0,'encoding':'o200k_base (proxy, not a claimed GPT-6 billing tokenizer)' if encoding else 'unavailable; no token estimate substituted',
              'results':results}
    output = Path(os.environ.get('RESUME_FLOW_RESULTS') or Path(__file__).with_name('results.json'))
    output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    backend() if '--backend' in sys.argv else compare()
