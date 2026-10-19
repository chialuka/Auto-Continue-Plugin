import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from engine import Store
try:
    event=json.load(sys.stdin)
    result=Store().hook(event)
    print(json.dumps(result))
except Exception as exc:
    # Advisory only: never block normal work because the plugin failed.
    print(json.dumps({'systemMessage':'Resume After Reset hook unavailable: '+str(exc)}))
