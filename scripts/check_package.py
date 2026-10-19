"""Validate local package references without third-party dependencies."""
import json
from pathlib import Path
import re
root=Path(__file__).resolve().parents[1]
m=json.loads((root/'plugin.json').read_text())
assert m['$schema']=='https://agent-plugins.org/schemas/1.0.0/plugin.schema.json'
assert re.fullmatch(r'\d+\.\d+\.\d+',m['version'])
assert len(m['extensions']['com.openai']['interface']['shortDescription'])<=30
for path in ['scripts/server.py','scripts/engine.py','scripts/usage_meter.py','scripts/scheduling.py','scripts/passive_usage.py','scripts/direct_host.py','scripts/direct_scheduler.py','scripts/direct_service.py','scripts/probe_direct_host.py','hooks/lifecycle.py','web/meter.html']:
    assert (root/path).is_file(),path
hooks=json.loads((root/'hooks/hooks.json').read_text())
assert set(hooks['hooks'])=={'SessionStart'}
for skill in (root/'skills').glob('*/SKILL.md'):
    text=skill.read_text();assert text.startswith('---\n')
    header=text.split('---',2)[1]
    # This package deliberately uses the simple mapping subset of YAML.
    front={}
    for line in header.strip().splitlines():
        key,sep,value=line.partition(': ')
        assert sep and key in ('name','description') and value.strip() and key not in front
        front[key]=value
    assert set(front)=={'name','description'} and front['name']==skill.parent.name
mcp=json.loads((root/'mcp.json').read_text())
assert mcp['mcpServers']['resume-state']['args']==['${PLUGIN_ROOT}/scripts/server.py']
catalog=json.loads((root/'.agents/plugins/marketplace.json').read_text())
assert catalog['plugins'][0]['name']==m['name'] and catalog['plugins'][0]['source']['path']=='./'
print('Package references and metadata passed. This is not a full upstream schema validator.')
