#!/usr/bin/env python3
"""Short USB health trace, keeping one connection open across a notification."""
import argparse
import json
import time
from device import connect,info
p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,default=60);a=p.parse_args()
with connect() as s:
    start=time.monotonic();ready=False
    while time.monotonic()-start<a.seconds:
        v=info(s)
        if not ready and v['uptime']>8 and v['wifi']==3 and v['age_ms']<5000 and v['message']=='已连接':
            ready=True;print('READY',json.dumps(v,ensure_ascii=False),flush=True)
        else:print(json.dumps(v,ensure_ascii=False),flush=True)
        time.sleep(2)
    assert ready,'Wi-Fi did not recover'
