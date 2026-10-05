#!/usr/bin/env python3
import json
import sqlite3
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from codex_ipc import CodexIPC, runtime_status
c=sqlite3.connect('file:'+str(Path.home()/'.codex/thread_history_1.sqlite')+'?mode=ro',uri=True)
tid=c.execute("select thread_id from thread_turns where status='inProgress' order by started_at desc limit 1").fetchone()[0]
ipc=CodexIPC(Path.home()/'.codex');ipc.watch([tid]);ipc.start()
def wait(predicate,timeout=12):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        value=ipc.snapshot()
        if predicate(value):return value
        time.sleep(.1)
    raise AssertionError('IPC probe timed out')
try:
    _,_,states=wait(lambda s:tid in s[2])
    first=states[tid]['seen'];revision=states[tid]['revision']
    print('Snapshot received; current status:',runtime_status(states[tid]['state']))
    _,_,states=wait(lambda s:tid in s[2] and s[2][tid]['revision']>revision,timeout=20)
    print('Live patches received; revision advanced')
    last=states[tid]['seen']
    ipc.sock.shutdown(2)
    _,_,states=wait(lambda s:s[0] and tid in s[2] and s[2][tid]['seen']>last,timeout=12)
    print('Forced disconnect recovered with a new snapshot')
    result={'snapshot':True,'patches':True,'reconnect':True,'status':runtime_status(states[tid]['state'])}
    output=Path(__file__).resolve().parents[1]/'tests/fixtures/codex-live-probe.json'
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2))
finally:
    ipc.stop.set()
