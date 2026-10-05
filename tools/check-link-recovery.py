#!/usr/bin/env python3
"""Briefly stop only ai-monitor.service; verify device rejoin and automatic sync."""
import json
import subprocess
import time
from device import connect, info


def command(serial,value,prefix):
    serial.write((value+'\n').encode());until=time.monotonic()+8
    while time.monotonic()<until:
        line=serial.readline().decode(errors='replace').strip()
        if line.startswith(prefix):return line[len(prefix):]
    raise TimeoutError(value)


def net(serial):return json.loads(command(serial,'NET_INFO','NET '))


def healthy(serial):
    end=time.monotonic()+40
    while time.monotonic()<end:
        state=info(serial)
        if state['uptime']>8 and state['message']=='已连接' and state['age_ms']<5000:return state
        time.sleep(.5)
    raise TimeoutError(state)


if __name__=='__main__':
    subprocess.run(['systemctl','--user','is-active','--quiet','ai-monitor.service'],check=True)
    with connect() as serial:
        first=healthy(serial);before=net(serial);print('Before',first,before,flush=True)
        command(serial,'TAB 4','UI ');time.sleep(3)
        last_uptime=info(serial)['uptime'];offline=False
        try:
            offline=True
            subprocess.run(['systemctl','--user','stop','ai-monitor.service'],check=True)
            end=time.monotonic()+25;saw_transport=False
            while time.monotonic()<end:
                time.sleep(1);state=info(serial);diagnostic=net(serial)
                assert state['uptime']>=last_uptime,'unexpected reboot';last_uptime=state['uptime']
                saw_transport=saw_transport or diagnostic['snapshot_http']<0
                print('Offline',state,diagnostic,flush=True)
                # Exercise modal navigation while the network task is retrying.
                command(serial,'SETTINGS','UI ');command(serial,'BACK','UI ')
                if diagnostic['wifi_rejoins']>before['wifi_rejoins']:break
            else:raise AssertionError('automatic STA recovery was not triggered')
            assert saw_transport,'test did not hit a transport failure'
        finally:
            if offline:subprocess.run(['systemctl','--user','start','ai-monitor.service'],check=True)
        state=healthy(serial);assert state['uptime']>=last_uptime
        end=time.monotonic()+10
        while not state['host_available'] or state['host_age_ms']>=5000:
            assert time.monotonic()<end,state
            time.sleep(.5);state=info(serial)
        diagnostic=net(serial);assert diagnostic['snapshot_http']==200 and diagnostic['transport_failures']==0
        print('Recovered',state,diagnostic,flush=True)
        command(serial,'TAB 0','UI ')
        print('PASS: transport-only STA rejoin, responsive navigation, no reboot, automatic AI/host recovery.',flush=True)
