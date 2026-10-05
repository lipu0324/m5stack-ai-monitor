#!/usr/bin/env python3
"""Read authenticated snapshots without printing credentials or conversation text."""
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[1]
config=json.loads((Path.home()/'.config/ai-monitor/config.json').read_text())
def request(source='all',token=None):
    r=urllib.request.Request(f"http://127.0.0.1:{config['port']}/api/v1/snapshot?source={source}",
       headers={'Authorization':'Bearer '+(token or config['token'])})
    with urllib.request.urlopen(r,timeout=5) as response:
        data=response.read();assert len(data)<=16384
        return json.loads(data)
a=request()
assert a['version']==1
assert time.time()-a['observed_at']<5
detected=[key for key,value in a['sources'].items() if value.get('detected')]
print('Authenticated snapshot: OK; discovered:', ', '.join(detected))
for s in ('codex','hermes','opencode'):
    p=request(s)
    assert all(t['source']==s for t in p['tasks'])
    print(s, 'tasks:',p['total'], 'online:',p['sources'][s]['online'], 'live:',p['sources'][s]['live'], 'metrics:',p['metrics'].get(s,{}).get('available',False))
try:
    request(token='invalid')
    raise AssertionError('bad token accepted')
except urllib.error.HTTPError as e:
    assert e.code==401
print('Invalid-token rejection: OK')
