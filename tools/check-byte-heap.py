#!/usr/bin/env python3
"""Focused hardware regression for speaker + large HTTP replies + graph rendering."""
import argparse
import json
import time
from device import connect,info


def command(uart,value,prefix):
    uart.write((value+'\n').encode())
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        line=uart.readline().decode(errors='replace').strip()
        if line.startswith(prefix):return line[len(prefix):]
    raise TimeoutError(value)


def diagnostic(uart,value,prefix):return json.loads(command(uart,value,prefix))


def healthy(uart):
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        state=info(uart)
        if state['message']=='已连接' and state['age_ms']<5000:return state
        time.sleep(.5)
    raise TimeoutError(state)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,default=180)
    args=parser.parse_args()
    if not 30<=args.seconds<=600:parser.error('seconds must be 30..600')
    uart=connect();original=None
    try:
        state=healthy(uart);original=state['page'];last_uptime=state['uptime']
        # Opening diagnostics again must preserve the failure/uptime state.
        uart.close();uart=connect();state=info(uart)
        assert state['uptime']>=last_uptime,'diagnostic connection reset ESP32'
        command(uart,'TAB 4','UI ')
        deadline=time.monotonic()+15
        while True:
            state=healthy(uart)
            if state['host_available'] and state['host_age_ms']<5000:break
            assert time.monotonic()<deadline,state
            time.sleep(.5)
        before=diagnostic(uart,'HTTP_DIAG','HTTP ')
        uart.write(b'TEST_SOUND\n')  # Honors mute; allocates real speaker DMA if enabled.
        began=time.monotonic();cycles=0;next_ui=began+30
        while time.monotonic()-began<args.seconds:
            time.sleep(2);state=info(uart)
            network=diagnostic(uart,'NET_INFO','NET ')
            body=diagnostic(uart,'HTTP_DIAG','HTTP ')
            memory=diagnostic(uart,'MEM_INFO','MEM ')
            assert state['page']==4,'test interrupted by page change; leave device on host page'
            assert state['uptime']>=last_uptime,'unexpected ESP32 reboot'
            last_uptime=state['uptime']
            assert state['message']=='已连接' and state['age_ms']<5000,state
            assert state['host_available'] and state['host_age_ms']<5000,state
            assert network['snapshot_http']==network['host_http']==200,network
            assert network['transport_failures']==0 and network['wifi_rejoins']==0,network
            assert body['snapshot_errors']==before['snapshot_errors'],body
            assert body['host_errors']==before['host_errors'],body
            assert body['received']==body['expected']>0,body
            assert memory['byte_heap']>16384 and memory['min_byte_heap']>8192,memory
            assert memory['canvas_bytes']==25600,memory
            if time.monotonic()>=next_ui:
                command(uart,'HOST_MODE','UI ');command(uart,'SETTINGS','UI ');command(uart,'BACK','UI ')
                assert info(uart)['page']==4,'modal navigation lost host page'
                next_ui=time.monotonic()+30;cycles+=1
            print(json.dumps(dict(elapsed=round(time.monotonic()-began,1),state=state,
                                  network=network,http=body,memory=memory),ensure_ascii=False),flush=True)
        after=diagnostic(uart,'HTTP_DIAG','HTTP ')
        minimum=args.seconds//4
        assert after['snapshot_reads']-before['snapshot_reads']>=minimum,after
        assert after['host_reads']-before['host_reads']>=minimum,after
        perf=diagnostic(uart,'PERF','PERF ')
        assert perf['strip_buffer'] and perf['frame_us']<150000,perf
        print('PASS',json.dumps(dict(seconds=args.seconds,ui_cycles=cycles,perf=perf,
              snapshot_reads=after['snapshot_reads']-before['snapshot_reads'],
              host_reads=after['host_reads']-before['host_reads']),ensure_ascii=False),flush=True)
    finally:
        try:
            if original is not None:command(uart,'TAB '+str(original),'UI ')
        finally:uart.close()


if __name__=='__main__':main()
