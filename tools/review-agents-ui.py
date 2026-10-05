#!/usr/bin/env python3
"""Short real-device discovery/filter check; restore original visibility on exit."""
import json
import time
from pathlib import Path
from PIL import Image
from device import connect, info


def command(serial, value, prefix='UI '):
    serial.write((value+'\n').encode())
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        line=serial.readline().decode(errors='replace').strip()
        if line.startswith(prefix):return line
    raise TimeoutError(value)


def agents(serial):return json.loads(command(serial,'AGENTS','AGENTS ')[7:])


def healthy(serial):
    deadline=time.monotonic()+35
    while time.monotonic()<deadline:
        state=info(serial)
        if not state['ap'] and state['wifi']==3 and state['age_ms']<5000 and state['message']=='已连接' and state['uptime']>8:
            return state
        time.sleep(.5)
    raise TimeoutError(state)


def select(serial,index):
    for _ in range(9):
        if agents(serial)['selected']==index:return
        command(serial,'SOURCE_NEXT')
    raise AssertionError('source not detected')


def capture(serial,name):
    serial.write(b'SCREEN\n');deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        if serial.readline().startswith(b'RGB 320 240'):break
    else:raise TimeoutError('screen header')
    raw=bytearray();serial.timeout=5
    while len(raw)<320*240*3:
        data=serial.read(320*240*3-len(raw))
        if not data:raise TimeoutError('screen data')
        raw.extend(data)
    path=Path('.private')/name;path.parent.mkdir(exist_ok=True)
    Image.frombytes('RGB',(320,240),bytes(raw)).save(path)
    print('LCD',path,flush=True)


serial=connect();original=None
try:
    print('Initial',healthy(serial),flush=True)
    diag=json.loads(command(serial,'HTTP_DIAG','HTTP ')[5:]);assert diag['json_capacity']==32768 and diag['snapshot_errors']==0,diag
    print('HTTP arena',diag,flush=True)
    original=agents(serial)['hidden'];assert 'opencode' in agents(serial)['detected']
    command(serial,'TAB 3');time.sleep(.5);capture(serial,'v5-sources.png')
    select(serial,2)
    # Ensure OpenCode hidden; passive USB diagnostics must preserve device uptime.
    if not agents(serial)['hidden'] & 4:command(serial,'SOURCE_TOGGLE')
    hidden=agents(serial)['hidden'];before_reconnect=info(serial)['uptime'];serial.close();serial=connect();after_reconnect=healthy(serial)['uptime']
    print('NVS reconnect uptime',before_reconnect,'->',after_reconnect,flush=True)
    assert after_reconnect>=before_reconnect,'diagnostic connection reset device'
    assert agents(serial)['hidden']==hidden,'NVS choice lost'
    command(serial,'TAB 2')
    for _ in range(4):
        command(serial,'METRIC');assert agents(serial)['metric']!=2,'hidden OpenCode appears in metrics'
    # Show only OpenCode: no fabricated active session is created for the test.
    command(serial,'TAB 3')
    for i in range(3):
        select(serial,i);should_hide=i!=2
        if bool(agents(serial)['hidden'] & (1<<i))!=should_hide:command(serial,'SOURCE_TOGGLE')
    time.sleep(4);print('OpenCode-only',healthy(serial),agents(serial),flush=True)
    command(serial,'TAB 2');time.sleep(.5);assert agents(serial)['metric']==2
    capture(serial,'v5-opencode.png')
    command(serial,'TAB 3');select(serial,2);command(serial,'SOURCE_TOGGLE')
    time.sleep(4);command(serial,'TAB 2');assert agents(serial)['metric']==-1
    assert healthy(serial)['tasks']==0
    print('All hidden: empty tasks and no metrics OK',flush=True)
finally:
    if original is not None and serial.is_open:
        command(serial,'TAB 3')
        for i in range(9):
            if bool(agents(serial)['hidden'] & (1<<i))!=bool(original & (1<<i)):
                select(serial,i);command(serial,'SOURCE_TOGGLE')
        assert agents(serial)['hidden']==original
        time.sleep(4);command(serial,'TAB 3');print('Restored',healthy(serial),agents(serial),flush=True)
    serial.close()
print('PASS: discovery, NVS persistence, visibility and OpenCode metrics; no AI task started.',flush=True)
