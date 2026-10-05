#!/usr/bin/env python3
"""Read-only display review on real hardware; never sends an approval response."""
import json
import time
from pathlib import Path
from PIL import Image
from device import connect,info

out=Path('.private');out.mkdir(exist_ok=True);records=[]

def command(s,value):
    s.write((value+'\n').encode());end=time.monotonic()+8
    while time.monotonic()<end:
        line=s.readline().decode(errors='replace').strip()
        if line.startswith(('UI ','PERF ')):
            return line
    raise TimeoutError(value+' did not respond')

def screen(s,name):
    s.write(b'SCREEN\n');end=time.monotonic()+8
    while time.monotonic()<end:
        line=s.readline().decode(errors='replace').strip()
        if line=='RGB 320 240':break
    else:raise TimeoutError('missing LCD header')
    pixels=bytearray();s.timeout=5
    while len(pixels)<230400:
        block=s.read(230400-len(pixels))
        if not block:raise TimeoutError('incomplete LCD pixels')
        pixels.extend(block)
    Image.frombytes('RGB',(320,240),bytes(pixels)).save(out/(name+'.png'))
    print('Captured',name,flush=True)

with connect() as s:
    deadline=time.monotonic()+35
    while True:
        state=info(s)
        if state['wifi']==3 and not state['ap'] and state['age_ms']<10000 and state['uptime']>8:break
        if time.monotonic()>deadline:raise RuntimeError('Wi-Fi not recovered: '+json.dumps(state,ensure_ascii=False))
        time.sleep(1)
    print('Connected',json.dumps(state,ensure_ascii=False),flush=True)
    for tab in (0,1,2,3,4,5,-1,0):
        command(s,f'TAB {tab}');actual=info(s)['page'];assert actual==tab%5,(tab,actual)
    for origin in range(5):
        command(s,f'TAB {origin}');command(s,'SETTINGS');assert info(s)['page']==5
        for _ in range(5):command(s,'NEXT_SETTING')
        command(s,'BACK');assert info(s)['page']==origin
        command(s,'SETTINGS');command(s,'SELECT_BACK');assert info(s)['page']==origin
    print('Hardware navigation: 5-tab wrap, settings return and explicit back PASS',flush=True)
    for tab,name in [(0,'v3-running'),(1,'v3-approvals'),(2,'v3-codex-tokens'),(3,'v3-sources')]:
        command(s,f'TAB {tab}');time.sleep(.4);screen(s,name);records.append(info(s))
        print(command(s,'PERF'),flush=True)
    command(s,'TAB 2');command(s,'MODE');time.sleep(.4);screen(s,'v3-codex-quota')
    command(s,'MODE');command(s,'METRIC');time.sleep(.4);screen(s,'v3-hermes-tokens')
    command(s,'SETTINGS');time.sleep(.4);screen(s,'v3-settings')
    command(s,'BACK');command(s,'TAB 0');time.sleep(.4)
    records.append(info(s));print('Final',json.dumps(records[-1],ensure_ascii=False),flush=True)
    print(command(s,'PERF'),flush=True)
(out/'v3-ui-review.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
