"""Read-only OpenCode v2 SQLite projection and v1/v2 local HTTP status."""
import base64
import copy
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from model import text, sort_tasks
from usage import seven_days


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def milliseconds(value):
    return int(value or 0) // 1000


def local_url(value):
    p = urlsplit(value)
    if p.scheme != 'http' or p.hostname not in ('127.0.0.1', 'localhost', '::1') or p.username or p.password or p.query or p.fragment or p.path not in ('', '/'):
        raise ValueError('OpenCode URL must be a local HTTP origin')
    return value.rstrip('/')


class OpenCodeCollector:
    def __init__(self, discovery, config):
        self.discovery = discovery
        self.db = Path(config.get('opencode_db', Path.home()/'.local/share/opencode/opencode.db'))
        self.config_dir = Path(config.get('opencode_config', Path.home()/'.config/opencode'))
        self.explicit_url = local_url(config['opencode_url']) if config.get('opencode_url') else None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.tasks = []
        self.health = {'online': False, 'healthy': False, 'live': False, 'wait_supported': False, 'detail': '正在检测 OpenCode'}
        self.metrics = {'available': False, 'quota': {'available': False, 'windows': []}}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.last_urls = []
        self.last_scan = 0
        self.thread = threading.Thread(target=self.run, name='opencode-monitor', daemon=True)

    def auth(self):
        password = username = None
        for pid in self.discovery.processes['opencode']:
            try:
                env = dict(x.split(b'=', 1) for x in (self.discovery.proc/str(pid)/'environ').read_bytes().split(b'\0') if b'=' in x)
                password = env.get(b'OPENCODE_SERVER_PASSWORD', b'').decode()
                username = env.get(b'OPENCODE_SERVER_USERNAME', b'opencode').decode()
                if password:
                    break
            except (OSError, UnicodeError):
                pass
        if not password:
            try:
                password = json.loads((self.config_dir/'service.json').read_text()).get('password')
            except (OSError, ValueError, AttributeError):
                pass
        return {'Authorization': 'Basic ' + base64.b64encode(((username or 'opencode')+':'+password).encode()).decode()} if password else {}

    def get(self, url, path):
        # Only literal read-only routes reach here. Never start/resume/respond.
        request = urllib.request.Request(url+path, headers=self.auth(), method='GET')
        with self.opener.open(request, timeout=.45) as response:
            raw = response.read(2*1024*1024+1)
            if len(raw) > 2*1024*1024:
                raise ValueError('OpenCode response too large')
            return json.loads(raw)

    def history(self):
        conn = sqlite3.connect(f'file:{self.db}?mode=ro', uri=True, timeout=.15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA query_only=ON')
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'session_v2' not in tables:
                return [], None  # v1 uses live session/status; unknown persisted outcomes.
            rows = [dict(r) for r in conn.execute('''SELECT id,title,parent_id,directory,time_created,time_updated,time_idle,idle_outcome,time_archived
                FROM session_v2 WHERE time_archived IS NULL AND parent_id IS NULL ORDER BY time_updated DESC LIMIT 200''')]
            totals = conn.execute('''SELECT SUM(tokens_input) i,SUM(tokens_output) o,SUM(tokens_cache_read) r,SUM(tokens_cache_write) w
                FROM session_v2''').fetchone()
            inputs, output, cached, written = [int(totals[k] or 0) for k in ('i','o','r','w')]
            prompt = inputs+cached+written
            daily = [dict(r) for r in conn.execute('''SELECT date(time_created/1000,'unixepoch','localtime') day,
                SUM(tokens_input+tokens_output+tokens_cache_read+tokens_cache_write) tokens FROM session_v2 GROUP BY day ORDER BY day DESC LIMIT 7''')]
            metrics = {'available': True, 'input': prompt, 'output': output, 'cached': cached, 'total': prompt+output,
                       'hit_percent': 100*cached/prompt if prompt else None, 'daily': seven_days(daily),
                       'quota': {'available': False, 'windows': []}, 'updated_at': int(time.time()),
                       'scope': '本机全部会话累计；按开始日；推理字段不额外相加'}
            return rows, metrics
        finally:
            conn.close()

    def poll(self):
        self.discovery.scan()
        now = int(time.time())
        deadline = time.monotonic()+3
        rows, metrics, detail = [], None, '本地数据库不可读'
        try:
            rows, metrics = self.history()
            detail = '历史数据；实时接口不可达'
        except (OSError, sqlite3.Error, ValueError, TypeError):
            pass
        if time.monotonic()-self.last_scan > 5:
            self.last_urls = [self.explicit_url] if self.explicit_url else self.discovery.opencode_urls()
            self.last_scan = time.monotonic()
        active, pending, live, waits, v2 = {}, [], False, False, True
        for url in self.last_urls:
            if time.monotonic()>deadline:break
            try:
                try:
                    payload = self.get(url, '/api/session/active')
                    if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict):
                        raise ValueError('incompatible active sessions')
                    statuses = payload['data']
                    v2 = True
                except urllib.error.HTTPError as exc:
                    if exc.code != 404:
                        raise
                    statuses = self.get(url, '/session/status')
                    if not isinstance(statuses, dict):
                        raise ValueError('incompatible v1 statuses')
                    v2 = False
                active.update({k: v for k, v in statuses.items() if v2 or isinstance(v,dict) and v.get('type') in ('busy','retry')})
                # Include new sessions absent from DB / v1 installs; no transcripts.
                sessions = self.get(url, '/api/session?limit=200' if v2 else '/session')
                sessions = sessions.get('data') if v2 and isinstance(sessions, dict) else sessions
                if not isinstance(sessions,list):
                    raise ValueError('incompatible sessions')
                known = {r['id'] for r in rows}
                for session in sessions[:200]:
                    if not isinstance(session,dict) or not isinstance(session.get('id'),str) or session.get('parentID') or session.get('time',{}).get('archived'):
                        continue
                    if session['id'] not in known:
                        t = session.get('time',{})
                        rows.append({'id': session['id'], 'title': session.get('title'), 'directory': session.get('location',{}).get('directory') or session.get('directory'),
                                     'time_created': t.get('created'), 'time_updated': t.get('updated'), 'time_idle': None, 'idle_outcome': None})
                        known.add(session['id'])
                live = True
                if v2:
                    # Request APIs are location scoped; inspect active root locations only.
                    directories = list(dict.fromkeys(r['directory'] for r in rows if r['id'] in active and r.get('directory')))[:8]
                    for directory in directories:
                        query = '?'+urlencode({'location[directory]':directory})
                        for route, kind in (('/api/permission/request','approval'),('/api/question/request','question')):
                            if time.monotonic()>deadline:raise TimeoutError()
                            try:
                                requests = self.get(url,route+query)
                                if isinstance(requests,dict) and isinstance(requests.get('data'),list):
                                    pending.extend(dict(r,_kind=kind) for r in requests['data'] if isinstance(r,dict))
                                    waits = True
                            except urllib.error.HTTPError as e:
                                if e.code != 404:
                                    raise
                else:
                    for route, kind in (('/permission','approval'),('/question','question')):
                        try:
                            requests = self.get(url,route)
                            if isinstance(requests,list):
                                pending.extend(dict(r,_kind=kind) for r in requests if isinstance(r,dict));waits=True
                        except urllib.error.HTTPError as e:
                            if e.code != 404:
                                raise
                detail = '本机 HTTP + SQLite' if metrics else '本机 HTTP；用量暂不可用'
            except urllib.error.HTTPError as exc:
                detail = 'OpenCode 本地认证失败' if exc.code in (401,403) else 'OpenCode 接口不兼容'
            except (OSError, ValueError, TypeError, KeyError):
                detail = 'OpenCode 实时接口暂不可读'
        with self.lock:
            previous = {t['id'].split(':',1)[1]:dict(t) for t in self.tasks}
            previous_active = {key:t for key,t in previous.items() if t['status'] in ('running','waiting_input','waiting_approval','unknown')}
        tasks = []
        for row in rows[:400]:
            tid = row['id']
            status = {'succeeded':'completed', 'failed':'failed', 'interrupted':'interrupted'}.get(row.get('idle_outcome'),'idle' if live else 'unknown')
            if not live and (row.get('time_idle') is None or int(row.get('time_updated') or 0)>int(row.get('time_idle') or 0)):
                status='unknown'
            if tid in previous_active and int(row.get('time_idle') or 0)<=previous_active[tid].get('_idle_at',0):
                status='unknown'
            if live and tid in active:
                status='running'
            request = next((p for p in pending if p.get('sessionID')==tid),None)
            if request:
                status='waiting_approval' if request['_kind']=='approval' else 'waiting_input'
            tasks.append({'id':'opencode:'+tid,'source':'opencode','kind':'local','title':text(row.get('title') or 'OpenCode 会话',64),
                          'status':status,'action':'等待电脑审批' if status=='waiting_approval' else '等待电脑回答' if status=='waiting_input' else '执行中' if status=='running' else '',
                          'summary':text(request.get('action') or request.get('permission') or '',96) if request else '',
                          'started_at':milliseconds(row.get('time_created')), 'ended_at':None if status in ('running','waiting_input','waiting_approval','unknown') else milliseconds(row.get('time_idle')) or None,
                          'updated_at':now if request else milliseconds(row.get('time_idle') or row.get('time_updated')),
                          'turn_id':str(row.get('time_idle') or row.get('time_created') or ''),
                          'alert_key':str(request.get('id','')) if request else '', 'wait_supported':waits, '_idle_at':int(row.get('time_idle') or 0)})
        if not rows and not live:
            tasks=[dict(t,status='unknown') if t['status'] in ('running','waiting_input','waiting_approval') else t for t in previous.values()]
        with self.lock:
            self.tasks = sort_tasks(tasks)[:200]
            self.health = {'online':live or bool(self.discovery.processes['opencode']), 'healthy':live or metrics is not None,
                           'live':live, 'wait_supported':waits, 'detail':detail, 'observed_at':now}
            self.metrics = metrics or {'available':False,'quota':{'available':False,'windows':[]}}

    def collect(self):
        with self.lock:
            tasks, health = copy.deepcopy(self.tasks), dict(self.health)
            if time.time()-health.get('observed_at',0)>10:
                tasks=[dict(t,status='unknown') if t['status'] in ('running','waiting_input','waiting_approval') else t for t in tasks]
                health.update(live=False,healthy=False,detail='OpenCode 数据已过期')
            return tasks, health

    def metric_snapshot(self):
        with self.lock:
            return copy.deepcopy(self.metrics)

    def run(self):
        while not self.stop.is_set():
            try:
                self.poll()
            except Exception:
                # Isolate schema/API failures and never infer completion from silence.
                with self.lock:
                    self.tasks = [dict(t,status='unknown') if t['status'] in ('running','waiting_input','waiting_approval') else t for t in self.tasks]
                    self.health.update(healthy=False,live=False,detail='OpenCode 采集暂不可用')
            self.stop.wait(2)
