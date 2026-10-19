"""Dependency-free local MCP stdio server; JSON-RPC only on stdout."""
import json
import math
import datetime as dt
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
from engine import Store

URI = 'ui://resume-after-reset/meter-v2.html'


def schema(properties=None, required=()):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': False}


STRING = {'type': 'string', 'minLength': 1, 'maxLength': 20000}
THREAD = {'thread': STRING}
TOOLS = [
    ('resume_direct_enable', 'Enable direct continuation in this chat: code reads the next reset, adds 60 seconds, and arms a persistent local worker. Starts its per-user macOS background service if needed. No host usage or automation tool calls are needed.',
     schema(dict(THREAD, instruction=STRING, timezone=STRING), ('thread',))),
    ('resume_direct_status', 'Read the direct timer, saved instruction, queue receipt and local worker status without fetching usage. Supply the client timezone for a code-formatted scheduledLocal value.', schema(dict(THREAD, timezone=STRING), ('thread',))),
    ('resume_direct_cancel', 'Cancel a direct timer when the user explicitly asks. Already queued messages cannot be retracted.', schema(THREAD, ('thread',))),
    ('resume_enable', 'Register the saved continuation and build its one-shot calendar plan from the supplied next reset timestamp; usage percentages are optional and needed only for requested warnings. The host must attach the returned plan.',
     schema(dict(THREAD, account=STRING, instruction=STRING, bucket=STRING, resetAt={'type':'number','minimum':0}, timezone=STRING, snapshot={'type':'object'}), ('thread','account','instruction','resetAt','timezone'))),
    ('resume_observe', 'Store a fresh host usage snapshot and return account-wide meter and new warnings.',
     schema(dict(THREAD, snapshot={'type':'object'}), ('thread','snapshot'))),
    ('resume_schedule', 'Build an exact single-occurrence desktop automation at the saved reset time plus buffer; never a polling interval.',
     schema(dict(THREAD, generation=STRING, timezone=STRING), ('thread','generation','timezone'))),
    ('resume_retry', 'Plan one bounded retry only after a blocked scheduled delivery, never periodic polling.',
     schema(dict(THREAD,generation=STRING,reason={'type':'string','enum':['still_blocked','usage_unavailable','account_busy']}), ('thread','generation','reason'))),
    ('resume_attach', 'Save the desktop automation ID after the host successfully creates the heartbeat.',
     schema(dict(THREAD, generation=STRING, automation=STRING, planToken=STRING), ('thread','generation','automation','planToken'))),
    ('resume_status', 'Read persisted registration and cached account meter; does not fetch current usage.',
     schema(THREAD, ('thread',))),
    ('resume_meter', 'Display the cached account meter in an interactive refreshable view.', schema(THREAD)),
    ('resume_meter_state', 'Automatically refresh quota readings from explicitly connected local chat usage events. Does not authorize continuation.', schema(THREAD)),
    ('resume_cancel', 'Disable a chat registration only after the user explicitly asks to stop its saved continuation; pause its host automation separately.',
     schema(dict(THREAD, reason={'type':'string','enum':['disabled','expired']}), ('thread',))),
    ('resume_deliver', 'Ingest one fresh host usage snapshot and atomically authorize one due continuation in a single call.',
     schema(dict(THREAD,generation=STRING,snapshot={'type':'object'}), ('thread','generation','snapshot'))),
    ('resume_finish', 'Record success or ambiguous failure of a claimed continuation. Never automatically replay partial work.',
     schema(dict(THREAD,generation=STRING,success={'type':'boolean'},error=STRING), ('thread','generation'))),
]


def validate(args, spec):
    if not isinstance(args, dict): raise ValueError('arguments must be an object')
    props = spec['properties']
    if set(args)-set(props): raise ValueError('Unknown arguments')
    if set(spec['required'])-set(args): raise ValueError('Missing required arguments')
    for key, value in args.items():
        typ = props[key]['type']
        if typ == 'string' and (not isinstance(value,str) or not value.strip() or len(value)>20000): raise ValueError('Invalid '+key)
        if typ == 'boolean' and not isinstance(value,bool): raise ValueError('Invalid '+key)
        if typ == 'number' and (not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value < props[key].get('minimum',-math.inf)): raise ValueError('Invalid '+key)
        if typ == 'object' and not isinstance(value,dict): raise ValueError('Invalid '+key)
        if 'enum' in props[key] and value not in props[key]['enum']: raise ValueError('Invalid '+key)


def handle(message, store):
    method, params = message.get('method'), message.get('params') or {}
    if not isinstance(params,dict): raise ValueError('params must be an object')
    if method == 'initialize':
        return {'protocolVersion': params.get('protocolVersion', '2024-11-05'),
                'capabilities': {'tools': {}, 'resources': {}},
                'serverInfo': {'name':'resume-after-reset','version':'1.0.2'}}
    if method == 'ping': return {}
    if method == 'tools/list':
        return {'tools': [dict(name=name,description=desc,inputSchema=spec,
                              annotations={'readOnlyHint':name in ('resume_direct_status','resume_status','resume_meter','resume_meter_state'),
                                           'openWorldHint':name=='resume_direct_enable', 'destructiveHint':False},
                              **({'title':'Unit-o-meter','_meta':{'ui':{'resourceUri':URI},'openai/ui':{'entrypoints':[{'type':'thread'}]}}} if name=='resume_meter' else {}))
                          for name,desc,spec in TOOLS]}
    if method == 'resources/list':
        return {'resources':[{'uri':URI,'name':'Account usage meter','mimeType':'text/html;profile=mcp-app'}]}
    if method == 'resources/read':
        if params.get('uri') != URI: raise ValueError('Unknown resource')
        return {'contents':[{'uri':URI,'mimeType':'text/html;profile=mcp-app',
                             'text':(Path(__file__).resolve().parent.parent/'web/meter.html').read_text(),
                             '_meta':{'ui':{'prefersBorder':True}}}]}
    if method == 'tools/call':
        name = params.get('name')
        spec = next((x[2] for x in TOOLS if x[0]==name), None)
        if spec is None: raise ValueError('Unknown tool')
        args = params.get('arguments',{})
        validate(args,spec)
        action = name.removeprefix('resume_')
        if action.startswith('direct_'):
            from direct_scheduler import DirectStore
            from direct_service import ensure_service, service_status
            timezone = args.pop('timezone', 'UTC')
            zone = ZoneInfo(timezone)
            def presented(job):
                if job is None:
                    return None
                return {**job, 'scheduledLocal': dt.datetime.fromtimestamp(job['due'], zone).isoformat(),
                        'scheduledUTC': dt.datetime.fromtimestamp(job['due'], dt.timezone.utc).isoformat(),
                        'timezone': timezone}
            direct = DirectStore(store.root)
            if action == 'direct_enable':
                service = ensure_service(store.root)
                job = presented(direct.enable(**args))
                # Setup needs a confirmation, not internal IDs, account data or the full saved message.
                result = {'job': {key: job[key] for key in ('status', 'due', 'scheduledLocal', 'timezone')},
                          'service': {'loaded': service['loaded']}}
            elif action == 'direct_cancel':
                result = {'job': presented(direct.cancel(args['thread']))}
            else:
                result = {'job': presented(direct.status(args['thread'])), 'service': service_status(store.root)}
            return {'content':[{'type':'text','text':json.dumps(result)}], 'structuredContent':result}
        if action == 'meter': action = 'meter_state'
        if action == 'deliver':
            result = store.deliver(**args)
            result = {'thread':args.get('thread'), **result}
            return {'content':[{'type':'text','text':json.dumps(result)}], 'structuredContent':result}
        if action == 'enable' and 'resetAt' in args:
            args['reset_at'] = args.pop('resetAt')
            action = 'enable_plan'
        if action == 'enable_plan':
            from direct_scheduler import DirectStore, ACTIVE
            try:
                direct = DirectStore(store.root).status(args['thread'])
            except ValueError:
                direct = None  # Legacy registrations historically allowed non-UUID identifiers.
            if direct and direct['status'] in ACTIVE:
                raise ValueError('Cancel the direct registration before enabling a native timer')
        result = getattr(store,action)(**args)
        if action == 'meter_state' and result.get('thread'):
            from direct_scheduler import DirectStore
            try:
                result['directJob'] = DirectStore(store.root).status(result['thread'])
                if result['directJob']:
                    from direct_service import service_status
                    result['directService'] = service_status(store.root)
            except ValueError:
                result['directJob'] = None
        result = {'thread':args.get('thread'), **result} if isinstance(result,dict) else {'result':result}
        return {'content':[{'type':'text','text':json.dumps(result)}], 'structuredContent':result}
    raise ValueError('Unsupported method')


def main():
    store = Store()
    for line in sys.stdin:
        message = None
        try:
            message = json.loads(line)
            if not isinstance(message,dict): raise ValueError('Request must be an object')
            if 'id' not in message: continue
            result = handle(message,store)
            response = {'jsonrpc':'2.0','id':message['id'],'result':result}
        except Exception as exc:
            response = {'jsonrpc':'2.0','id':message.get('id') if isinstance(message,dict) else None,
                        'error':{'code':-32602,'message':str(exc)}}
        print(json.dumps(response),flush=True)


if __name__=='__main__': main()
