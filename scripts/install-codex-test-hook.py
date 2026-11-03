#!/usr/bin/env python3
"""Install the test hook through Codex's user hook config; preserve other hooks."""
import json
import os
from pathlib import Path

source = Path(__file__).resolve().parents[1] / 'hooks' / 'hooks.json'
target = Path.home() / '.codex' / 'hooks.json'
existing = json.loads(target.read_text()) if target.exists() else {}
groups = existing.setdefault('hooks', {}).setdefault('PostModelRequest', [])
ours = json.loads(source.read_text())['hooks']['PostModelRequest'][0]
if ours not in groups:
    if target.exists():
        backup = target.with_name('hooks.json.before-auto-continue')
        if not backup.exists():
            backup.write_text(target.read_text())
            backup.chmod(0o600)
    groups.append(ours)
    temporary = target.with_suffix('.json.new')
    temporary.write_text(json.dumps(existing, indent=2) + '\n')
    temporary.chmod(0o600)
    os.replace(temporary, target)
print(f'Hook configured at {target}. Review and trust it in Codex Settings > Hooks.')
