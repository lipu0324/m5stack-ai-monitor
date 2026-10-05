"""Observe the existing local Hermes Desktop backend without attaching/resuming tasks."""
import json
import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from websockets.sync.client import connect


def discover(home):
    """Resolve a locally owned `hermes serve` socket and its desktop bridge credential.

    Only the specific desktop-session token is read; AI provider credentials are
    neither accessed nor forwarded. The listener must belong to this same PID.
    """
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            args=proc.joinpath('cmdline').read_bytes().split(b'\0')
            if b'serve' not in args or not any(str(home / 'hermes-agent').encode() in a for a in args):
                continue
            env=dict(x.split(b'=',1) for x in proc.joinpath('environ').read_bytes().split(b'\0') if b'=' in x)
            token=env.get(b'HERMES_DASHBOARD_SESSION_TOKEN',b'').decode()
            if not token:
                continue
            inodes={os.readlink(p)[8:-1] for p in proc.joinpath('fd').iterdir() if os.readlink(p).startswith('socket:[')}
            for line in proc.joinpath('net/tcp').read_text().splitlines()[1:]:
                fields=line.split()
                if fields[3]=='0A' and fields[9] in inodes and fields[1].split(':')[0]=='0100007F':
                    return int(fields[1].split(':')[1],16),token,int(proc.name)
        except (OSError,ValueError,KeyError):
            continue
    return None


class HermesLive:
    def __init__(self,home):
        self.home=Path(home)
        self.lock=threading.Lock()
        self.sock=None
        self.endpoint=None
        self.next_discovery=0
        self.counter=0
        self.online=False

    def _call(self,method,params):
        if method not in ('session.active_list','session.events.since','approval.respond'):
            raise ValueError('unsupported Hermes operation')
        if self.sock is None:
            if time.monotonic() >= self.next_discovery:
                self.endpoint=discover(self.home)
                self.next_discovery=time.monotonic()+5
            if not self.endpoint:
                raise ConnectionError('desktop backend unavailable')
            port,token,_=self.endpoint
            self.sock=connect(f'ws://127.0.0.1:{port}/api/ws?'+urlencode({'token':token}),open_timeout=2,max_size=1024*1024)
        self.counter+=1
        rid=f'ai-monitor-{self.counter}'
        self.sock.send(json.dumps({'jsonrpc':'2.0','id':rid,'method':method,'params':params}))
        until=time.monotonic()+3
        while time.monotonic()<until:
            frame=json.loads(self.sock.recv(timeout=max(.1,until-time.monotonic())))
            if frame.get('id')==rid:
                if 'error' in frame:
                    raise ConnectionError('Hermes rejected operation')
                return frame.get('result',{})
        raise TimeoutError('Hermes reply unavailable')

    def _close(self):
        if self.sock:
            try:self.sock.close()
            except Exception:pass
        self.sock=None
        self.online=False
        self.next_discovery=0

    def collect(self):
        with self.lock:
            try:
                rows=self._call('session.active_list',{}).get('sessions',[])[:32]
                result=[]
                for row in rows:
                    # A watermark above every event returns only CURRENT unanswered
                    # requests; no transcript replay or renderer ownership change.
                    requests=self._call('session.events.since',{'session_id':row['id'],'last_seen':2**63-1}).get('open_requests',[])
                    result.append({k:row.get(k) for k in ('id','session_key','title','status','last_active','started_at')} | {'requests':requests})
                self.online=True
                return result
            except Exception:
                self._close()
                return []

    def respond(self,session_id,request_id,choice):
        if choice not in ('once','deny'):
            raise ValueError('invalid decision')
        with self.lock:
            try:
                pending=self._call('session.events.since',{'session_id':session_id,'last_seen':2**63-1}).get('open_requests',[])
                request=next((r for r in pending if r.get('method')=='approval' and str(r.get('params',{}).get('request_id'))==str(request_id)),None)
                if not request or choice not in request.get('params',{}).get('choices',['once','deny']):
                    raise ValueError('request_expired')
                result=self._call('approval.respond',{'session_id':session_id,'request_id':request_id,'choice':choice,'all':False})
                if result.get('resolved',0)<1:
                    raise ValueError('request_expired')
                return True
            except ValueError:
                raise
            except Exception:
                self._close()
                raise ConnectionError('Hermes approval channel unavailable')
