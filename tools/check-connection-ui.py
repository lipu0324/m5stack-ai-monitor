#!/usr/bin/env python3
"""Test only the monitor bridge outage; never stops or controls Codex/Hermes."""
import json
import subprocess
import time
from pathlib import Path
from PIL import Image
from device import connect,info

out=Path('.private');records=[]

def capture(s,name):
    time.sleep(.2);s.write(b'SCREEN\n');deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        if s.readline().strip()==b'RGB 320 240':break
    else:raise TimeoutError('LCD header missing')
    s.timeout=5;pixels=bytearray()
    while len(pixels)<230400:
        block=s.read(230400-len(pixels))
        if not block:
            (out/(name+'-partial.rgb')).write_bytes(pixels)
            print('Partial pixels',len(pixels),'boot_marker',b'Guru Meditation' in pixels or b'AI Monitor boot' in pixels,flush=True)
            try:print('After capture failure',json.dumps(info(s),ensure_ascii=False),flush=True)
            except Exception as error:print('Capture diagnostic',type(error).__name__,flush=True)
            raise TimeoutError('LCD pixels incomplete')
        pixels.extend(block)
    Image.frombytes('RGB',(320,240),bytes(pixels)).save(out/(name+'.png'))
    print('Captured',name,flush=True)

def wait_for(s,predicate,seconds):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        state=info(s)
        if predicate(state):return state
        time.sleep(.5)
    raise TimeoutError('state not reached: '+json.dumps(state,ensure_ascii=False))

with connect() as s:
    baseline=wait_for(s,lambda v:v['uptime']>8 and v['wifi']==3 and v['message']=='已连接' and v['age_ms']<5000,35)
    print('Baseline',json.dumps(baseline,ensure_ascii=False),flush=True)
    try:
        subprocess.run(['systemctl','--user','stop','ai-monitor.service'],check=True)
        immediate=wait_for(s,lambda v:'服务不可达' in v['message'],12)
        assert immediate['age_ms']<10000,immediate
        records.append(immediate);print('Immediate outage',json.dumps(immediate,ensure_ascii=False),flush=True)
        capture(s,'v3-service-unreachable')
        stale=info(s);assert stale['age_ms']>10000 and stale['wifi']==3,stale
        records.append(stale);capture(s,'v3-data-stale')
    finally:
        subprocess.run(['systemctl','--user','start','ai-monitor.service'],check=True)
    recovered=wait_for(s,lambda v:v['message']=='已连接' and v['wifi']==3 and v['age_ms']<5000,45)
    assert recovered['uptime']>baseline['uptime'],recovered
    records.append(recovered);print('Recovered without reboot',json.dumps(recovered,ensure_ascii=False),flush=True)
    s.write(b'TAB 0\n');time.sleep(.4);capture(s,'v3-running')
    records.append(info(s));print('Final',json.dumps(records[-1],ensure_ascii=False),flush=True)
(out/'v3-connection-review.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
