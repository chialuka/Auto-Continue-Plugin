"""Read only quota events for explicitly connected local chats; never infer allowance."""
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time

MAX_TAIL_BYTES = 2 * 1024 * 1024


def codex_home():
    return Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex').expanduser().resolve()


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def latest_usage(thread, home=None, now=None):
    now = time.time() if now is None else now
    home = Path(home or codex_home()).resolve()
    if not isinstance(thread,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',thread):
        raise ValueError('Invalid local chat identifier')
    # The path is resolved by exact chat ID; callers cannot supply arbitrary paths.
    databases=sorted(home.glob('state_*.sqlite'),key=lambda p:int(p.stem.split('_')[-1]) if p.stem.split('_')[-1].isdigit() else -1,reverse=True)
    path=None
    for database in databases:
        try:
            con=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=1)
            try:
                row=con.execute('SELECT rollout_path FROM threads WHERE id=?',(thread,)).fetchone()
                if row: path=Path(row[0]);break
            finally:con.close()
        except sqlite3.Error:continue
    if path is None:return None
    path=path.resolve()
    try:path.relative_to((home/'sessions').resolve())
    except ValueError:raise ValueError('Chat transcript is outside the supported local sessions directory')
    if path.suffix!='.jsonl':raise ValueError('Unsupported local transcript format')
    with path.open('rb') as f:
        header=f.readline(65536)
        try:meta=json.loads(header)
        except ValueError:return None
        if meta.get('type')!='session_meta' or (meta.get('payload') or {}).get('id')!=thread:
            raise ValueError('Transcript identity does not match connected chat')
        size=path.stat().st_size
        offset=max(0,size-MAX_TAIL_BYTES)
        f.seek(offset)
        if offset:f.readline()  # Skip a possibly partial first line.
        data=f.read(MAX_TAIL_BYTES)
    latest=None
    for line in data.splitlines(keepends=True):
        if not line.endswith(b'\n'):continue  # The app may still be writing this event.
        try:event=json.loads(line)
        except ValueError:continue
        payload=event.get('payload') or {}
        if event.get('type')!='event_msg' or payload.get('type')!='token_count':continue
        limits=payload.get('rate_limits')
        if not isinstance(limits,dict):continue
        bucket=limits.get('limit_id')
        if not isinstance(bucket,str) or not bucket:continue
        try:observed=dt.datetime.fromisoformat(event['timestamp'].replace('Z','+00:00')).timestamp()
        except (KeyError,ValueError,TypeError):continue
        if observed>now+60:continue
        windows={}
        for key in ('primary','secondary'):
            raw=limits.get(key)
            if not isinstance(raw,dict):windows[key]=None;continue
            used=raw.get('used_percent')
            reset=raw.get('resets_at')
            duration=raw.get('window_minutes')
            windows[key]={'usedPercent':used if finite(used) and 0<=used<=100 else None,
                          'resetsAt':reset if finite(reset) and reset>0 else None,
                          'windowDurationMins':duration if finite(duration) and duration>0 else None}
        if all(w is None or w['usedPercent'] is None for w in windows.values()):continue
        snapshot={'rateLimitsByLimitId':{bucket:windows}}
        if latest is None or observed>=latest['observedAt']:
            latest={'bucket':bucket,'snapshot':snapshot,'observedAt':observed}
    return latest
