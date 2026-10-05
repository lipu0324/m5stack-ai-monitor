#!/usr/bin/env python3
"""Eight-hour Wi-Fi observation; no USB connection or AI task control."""
import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--hours',type=float,default=8);p.add_argument('--wait-hours',type=float,default=24);a=p.parse_args()
config=json.loads((Path.home()/'.config/ai-monitor/config.json').read_text())
state=Path(config['state_dir']);log=state/'soak-samples.jsonl';summary=state/'soak-result.json'
start=time.time();started=None;last_uptime=None;reboots=0;missed=0;samples=0;hosts=[];heaps=[];last_device_id=None
result={'status':'waiting_for_wifi_device','hours':a.hours,'requested_at':int(start)}
summary.write_text(json.dumps(result,indent=2))
while True:
    now=time.time();sample={'at':int(now)}
    try:
        req=urllib.request.Request(f"http://127.0.0.1:{config['port']}/api/v1/snapshot",headers={'Authorization':'Bearer '+config['token']})
        with urllib.request.urlopen(req,timeout=5) as r:data=json.load(r)
        assert data['version']==1 and now-data['observed_at']<10
        device=data.get('device')
        pid=subprocess.check_output(['systemctl','--user','show','ai-monitor.service','-p','MainPID','--value'],text=True).strip()
        stat=Path('/proc/'+pid+'/status').read_text()
        rss=int(next(x for x in stat.splitlines() if x.startswith('VmRSS:')).split()[1])
        sample['host_rss_kb']=rss;hosts.append(rss)
        if device and now-device['last_seen']<15:
            if started is None:
                started=now;result['started_at']=int(now);result['status']='running'
            if last_device_id==device['id'] and last_uptime is not None and device['uptime']<last_uptime:
                reboots+=1
            last_device_id=device['id'];last_uptime=device['uptime']
            sample['device']=device;heaps.append(device['heap']);samples+=1
        elif started is not None:
            missed+=1;sample['error']='device_offline'
    except Exception as e:
        if started is not None:missed+=1
        sample['error']=type(e).__name__
    with log.open('a') as f:f.write(json.dumps(sample,separators=(',',':'))+'\n')
    result.update(samples=samples,missed_samples=missed,reboots=reboots,last_sample=sample)
    if hosts:result['host_rss_range_kb']=[min(hosts),max(hosts)]
    if heaps:result['device_heap_range']=[min(heaps),max(heaps)]
    if started and now-started>=a.hours*3600:
        # Trend compares the last hour to first hour; flag growth instead of silently passing.
        n=min(60,len(hosts)//2)
        host_growth=(sum(hosts[-n:])/n-sum(hosts[:n])/n) if n else 0
        n=min(60,len(heaps)//2)
        heap_loss=(sum(heaps[:n])/n-sum(heaps[-n:])/n) if n else 0
        result.update(status='passed' if not reboots and not missed and host_growth<16384 and heap_loss<16384 else 'needs_review',
                      ended_at=int(now),host_growth_kb=int(host_growth),device_heap_loss=int(heap_loss))
        summary.write_text(json.dumps(result,indent=2));break
    if started is None and now-start>a.wait_hours*3600:
        result.update(status='not_started_device_not_paired',ended_at=int(now));summary.write_text(json.dumps(result,indent=2));break
    summary.write_text(json.dumps(result,indent=2));time.sleep(60)
