"""Read account metrics through Codex's authenticated, read-only account RPCs."""
import copy
import json
import os
import select
import shutil
import subprocess
import threading
import time
from datetime import datetime,timedelta,timezone


def quota_projection(result):
    windows=[]
    buckets=result.get('rateLimitsByLimitId') or {'codex':result.get('rateLimits',{})}
    for key,bucket in buckets.items():
        for name in ('primary','secondary'):
            row=bucket.get(name)
            if not row or row.get('usedPercent') is None:continue
            used=max(0,min(100,float(row['usedPercent'])))
            windows.append({'label':str(bucket.get('limitName') or key)[:32],'used_percent':used,
                            'remaining_percent':100-used,'duration_mins':row.get('windowDurationMins'),
                            'reset_at':row.get('resetsAt')})
    return windows[:4]


def seven_days(buckets):
    today=datetime.now(timezone(timedelta(hours=8))).date()
    lookup={str(r.get('startDate',r.get('day',''))):int(r.get('tokens',0)) for r in buckets}
    return [{'day':str(today-timedelta(days=i)), 'tokens':lookup.get(str(today-timedelta(days=i)),0)} for i in range(6,-1,-1)]


class AccountUsage:
    def __init__(self,home):
        self.home=str(home)
        self.lock=threading.Lock()
        self.stop=threading.Event()
        self.data={'quota':{'available':False,'windows':[]},'daily':None}
        self.process=None
        self.buffer=b''
        self.rid=0
        self.thread=threading.Thread(target=self.run,name='account-usage',daemon=True)

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.data)

    def start(self):self.thread.start()

    def call(self,method,params=None):
        # This child is ONLY an account client, never a source of existing task state.
        if method not in ('initialize','account/rateLimits/read','account/usage/read'):
            raise ValueError('account RPC allowlist')
        self.rid+=1
        request={'id':self.rid,'method':method,'params':params or {}}
        self.process.stdin.write((json.dumps(request)+'\n').encode());self.process.stdin.flush()
        until=time.monotonic()+12
        while time.monotonic()<until and not self.stop.is_set():
            if b'\n' not in self.buffer:
                if not select.select([self.process.stdout],[],[],.5)[0]:continue
                chunk=os.read(self.process.stdout.fileno(),65536)
                if not chunk:raise ConnectionError('account client stopped')
                self.buffer+=chunk
                if len(self.buffer)>1024*1024:raise ValueError('account response oversized')
            while b'\n' in self.buffer:
                line,self.buffer=self.buffer.split(b'\n',1)
                row=json.loads(line)
                if row.get('id')==self.rid:
                    if 'error' in row:raise ValueError('account RPC unavailable')
                    return row.get('result',{})
        raise TimeoutError('account RPC timed out')

    def close(self):
        if self.process:
            self.process.terminate()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
            for pipe in (self.process.stdin,self.process.stdout):pipe.close()
            self.process=None

    def run(self):
        binary=shutil.which('codex') or '/usr/local/bin/codex'
        while not self.stop.is_set():
            try:
                env=os.environ.copy();env['CODEX_HOME']=self.home
                self.process=subprocess.Popen([binary,'app-server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env)
                self.buffer=b''
                self.call('initialize',{'clientInfo':{'name':'ai-monitor-account','version':'2'},'capabilities':{'experimentalApi':True}})
                self.process.stdin.write(b'{"method":"initialized"}\n');self.process.stdin.flush()
                next_daily=0
                while not self.stop.is_set():
                    quota=self.call('account/rateLimits/read')
                    with self.lock:self.data['quota']={'available':True,'windows':quota_projection(quota),'updated_at':int(time.time())}
                    if time.monotonic()>=next_daily:
                        try:
                            usage=self.call('account/usage/read')
                            with self.lock:
                                self.data['daily']=seven_days(usage['dailyUsageBuckets']) if usage.get('dailyUsageBuckets') is not None else None
                                self.data['lifetime_tokens']=usage.get('summary',{}).get('lifetimeTokens')
                                self.data['usage_updated_at']=int(time.time())
                        except (ValueError,KeyError,TimeoutError):pass
                        next_daily=time.monotonic()+300
                    self.stop.wait(60)
            except Exception:
                with self.lock:self.data['quota']['available']=False
            finally:self.close()
            self.stop.wait(10)
