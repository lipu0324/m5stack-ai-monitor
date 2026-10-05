#!/usr/bin/env python3
"""Measure real LCD page transitions without resetting or approving AI tasks."""
import argparse
import json
import time
from device import connect,info


def command(uart,value,prefix):
    uart.write((value+'\n').encode());deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        line=uart.readline().decode(errors='replace').strip()
        if line.startswith(prefix):return line[len(prefix):]
    raise TimeoutError(value)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cycles',type=int,default=2)
    parser.add_argument('--verify',action='store_true',help='verify optimized animation and DMA buffers')
    args=parser.parse_args()
    if not 1<=args.cycles<=5:parser.error('cycles must be 1..5')
    rows=[]
    with connect() as uart:
        before=info(uart);original=before['page']
        try:
            command(uart,'TAB 4','UI ')
            deadline=time.monotonic()+40
            while True:
                state=info(uart)
                if state['message']=='已连接' and state['host_available'] and state['host_age_ms']<5000:break
                if time.monotonic()>deadline:raise TimeoutError('live host graph not ready: '+str(state))
                time.sleep(.5)
            for cycle in range(args.cycles):
                for page in range(5):
                    started=time.monotonic();command(uart,'TAB '+str(page),'UI ')
                    first=json.loads(command(uart,'PERF','PERF '))
                    time.sleep(.4)
                    settled=json.loads(command(uart,'PERF','PERF '))
                    state=info(uart)
                    assert state['uptime']>=before['uptime'],'device rebooted'
                    assert state['heap']>16384 and state['min_heap']>8192,state
                    if args.verify:
                        assert settled['double_buffer'],settled
                        assert settled['animation_frames']>=6,settled
                        assert settled['animation_worst_us']<60000,settled
                    row=dict(cycle=cycle,page=page,first=first,settled=settled,
                             wall_ms=round((time.monotonic()-started)*1000,1))
                    rows.append(row);print(json.dumps(row),flush=True)
            print('SUMMARY',json.dumps(dict(first_us=[r['first']['frame_us'] for r in rows],
                  settled_us=[r['settled']['frame_us'] for r in rows],state=info(uart)),ensure_ascii=False),flush=True)
        finally:
            if original==5:command(uart,'SETTINGS','UI ')
            else:command(uart,'TAB '+str(original),'UI ')


if __name__=='__main__':main()
