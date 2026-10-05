#!/usr/bin/env python3
"""Local status bridge with bounded, explicit once/deny approval responses."""
import argparse
import base64
import hmac
import hashlib
import json
import logging
import os
import signal
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from codex_ipc import CodexIPC
from collectors import CodexCollector, HermesCollector
from model import EventStore, STATES, sort_tasks
from usage import AccountUsage
from host_stats import HostStats
from agents import AgentDiscovery, IDS
from opencode import OpenCodeCollector

LOG = logging.getLogger('ai-monitor')


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('192.0.2.1', 80))
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


def encode_cursor(tid):
    return base64.urlsafe_b64encode(tid.encode()).decode().rstrip('=')


def decode_cursor(cursor):
    if len(cursor) > 256:
        raise ValueError('cursor too long')
    try:
        return base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True).decode()
    except (ValueError, UnicodeError) as exc:
        raise ValueError('invalid cursor') from exc


class Monitor:
    def __init__(self, config):
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.ipc = CodexIPC(config['codex_home'])
        self.collectors = {'codex': CodexCollector(config['codex_home'], self.ipc),
                           'hermes': HermesCollector(config['hermes_home'], config['events_dir'])}
        self.discovery = AgentDiscovery()
        self.discovery.scan()
        self.collectors['opencode'] = OpenCodeCollector(self.discovery, config)
        self.store = EventStore(Path(config['state_dir']) / 'monitor-state.json')
        self.tasks = []
        self.sources = {}
        self.observed_at = 0
        self.revision = 0
        self.last_saved = 0
        self.device = None
        self.account_usage = AccountUsage(config['codex_home'])
        self.host = HostStats(interface=config.get('host_interface'))
        self.responded = []
        self.inflight = set()

    @staticmethod
    def approval_id(task):
        a=task.get('_approval')
        return hashlib.sha256(json.dumps([task['id'],a['request_id']],sort_keys=True).encode()).hexdigest()[:24] if a else None

    def approvals(self):
        return [{'id':self.approval_id(t),'task_id':t['id'],'source':t['source'],'title':t['title'],
                 'command':t['_approval']['command'],'can_approve':'once' in t['_approval']['choices'] and bool(t['_approval']['command']) and not t['_approval'].get('truncated'),
                 'can_deny':'deny' in t['_approval']['choices']}
                for t in self.tasks if t.get('_approval') and self.approval_id(t) not in self.responded][:10]

    def respond(self,approval_id,choice):
        if choice not in ('once','deny'):raise ValueError('invalid_decision')
        with self.lock:
            if approval_id in self.responded or approval_id in self.inflight:raise ValueError('request_expired')
            task=next((t for t in self.tasks if self.approval_id(t)==approval_id and t.get('_approval')),None)
            if not task:raise ValueError('request_expired')
            target=dict(task['_approval'])
            if target['source'] not in ('codex','hermes'):raise ValueError('unsupported_approval_source')
            if choice not in target['choices']:raise ValueError('invalid_decision')
            if choice=='once' and (not target['command'] or target.get('truncated')):raise ValueError('command_requires_desktop_review')
            self.inflight.add(approval_id)
        try:
            if target['source']=='codex':self.ipc.respond(target['session_id'],target['request_id'],choice)
            else:self.collectors['hermes'].live.respond(target['session_id'],target['request_id'],choice)
            with self.lock:
                self.responded.append(approval_id);self.responded=self.responded[-512:]
            return {'accepted':True}
        finally:
            with self.lock:self.inflight.discard(approval_id)

    def metrics(self):
        connected,_,states=self.ipc.snapshot()
        inputs=output=cached=0
        for entry in states.values():
            total=entry.get('state',{}).get('latestTokenUsageInfo',{}).get('total',{})
            inputs+=int(total.get('inputTokens') or 0);output+=int(total.get('outputTokens') or 0);cached+=int(total.get('cachedInputTokens') or 0)
        codex={'available':connected and inputs>0,'input':inputs,'output':output,'cached':cached,'total':inputs+output,
               'hit_percent':100*cached/inputs if inputs else None,'scope':'已加载桌面会话累计；每日为账户统计'}
        codex.update(self.account_usage.snapshot())
        return {'codex':codex,'hermes':self.collectors['hermes'].metrics,
                'opencode':self.collectors['opencode'].metric_snapshot()}

    def poll(self):
        self.host.sample()
        tasks, sources = [], {key:dict(value) for key,value in self.discovery.scan().items()}
        for name, collector in self.collectors.items():
            try:
                found, health = collector.collect()
                tasks.extend(found)
                sources[name].update(health)
                if found or health.get("online"):
                    sources[name]["detected"] = True
            except Exception:
                LOG.exception('collector failed: %s', name)
                sources[name].update(online=False, healthy=False, live=False, wait_supported=False, detail='采集失败')
        with self.lock:
            self.tasks = sort_tasks(tasks)
            self.sources = sources
            self.observed_at = int(time.time())
            self.revision += 1
            self.store.update(self.tasks)
            if time.monotonic() - self.last_saved > 5:
                self.store.save()
                self.last_saved = time.monotonic()

    def start(self):
        self.ipc.start()
        self.account_usage.start()
        self.collectors['opencode'].thread.start()
        self.poll()
        def work():
            while not self.stop.wait(2):
                self.poll()
        threading.Thread(target=work, name='status-collector', daemon=True).start()

    def snapshot(self, source='all', cursor='', view='all', host=False, agents=None):
        if source not in ('all', *IDS):
            raise ValueError('invalid source')
        if view not in ('all','active'):raise ValueError('invalid view')
        allowed = set(IDS) if agents is None else set(agents)
        if not allowed.issubset(IDS):raise ValueError('invalid agents')
        if source != 'all':allowed.intersection_update([source])
        anchor = decode_cursor(cursor) if cursor else None
        with self.lock:
            tasks = [t for t in self.tasks if t['source'] in allowed]
            if view=='active':tasks=[t for t in tasks if t['status'] in ('running','waiting_approval','waiting_input')]
            index = 0
            if anchor:
                for i, task in enumerate(tasks):
                    if task['id'] == anchor:
                        index = i + 1
                        break
            page = [{k:v for k,v in t.items() if not k.startswith('_') and k not in ('pid','process_start')} for t in tasks[index:index + 10]]
            counts = {s: sum(t['status'] == s for t in tasks) for s in STATES}
            result = {'version': 1, 'revision': self.revision, 'generated_at': int(time.time()),
                    'observed_at': self.observed_at, 'sources': self.sources, 'device': self.device,
                    'counts': counts, 'total': len(tasks), 'offset': index, 'tasks': page,
                    'next_cursor': encode_cursor(page[-1]['id']) if page and index + len(page) < len(tasks) else '',
                    'approvals':[a for a in self.approvals() if a['source'] in allowed],'metrics':{k:v for k,v in self.metrics().items() if k in allowed},
                    'event_sequence': self.store.sequence,
                    'events': [e for e in reversed(self.store.events) if e['source'] in allowed]}
            if host:
                result['host'] = self.host.snapshot()
            return result


class Handler(BaseHTTPRequestHandler):
    server_version = 'AI-Monitor/1'

    def log_message(self, fmt, *args):
        # Do not log URLs, headers, tokens or task text.
        return

    def response(self, code, payload):
        data = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
        if len(data) > 16384:
            code, data = 503, b'{"error":"snapshot_too_large"}'
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        supplied = self.headers.get('Authorization', '')
        expected = 'Bearer ' + self.server.token
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            self.response(401, {'error': 'unauthorized'})
            return
        url = urlsplit(self.path)
        if url.path == '/api/v1/agents':
            with self.server.monitor.lock:
                agents = [dict(v) for v in self.server.monitor.sources.values() if v.get('detected')]
            self.response(200, {'version':1, 'agents':agents})
            return
        if url.path == '/api/v1/host':
            self.response(200, {'version': 1, 'host': self.server.monitor.host.snapshot()})
            return
        if url.path != '/api/v1/snapshot':
            self.response(404, {'error': 'not_found'})
            return
        query = parse_qs(url.query)
        try:
            include_host = query.get('host', ['0'])[0]
            if include_host not in ('0', '1'):
                raise ValueError('invalid host option')
            device = query.get('device',[''])[0]
            if device and len(device)<=16 and all(c in '0123456789abcdef' for c in device):
                try:
                    stats = {key:int(query[key][0]) for key in ('uptime','heap','min_heap')}
                    if all(0<=n<=0xffffffff for n in stats.values()):
                        with self.server.monitor.lock:
                            self.server.monitor.device = dict(stats,id=device,last_seen=int(time.time()))
                except (ValueError,KeyError,IndexError):
                    pass
            selection = query.get('agents', [None])[0]
            allowed = None if selection is None or selection == 'all' else [] if selection == 'none' else selection.split(',')
            self.response(200, self.server.monitor.snapshot(query.get('source', ['all'])[0], query.get('cursor', [''])[0],query.get('view',['all'])[0],host=include_host=='1',agents=allowed))
        except ValueError:
            self.response(400, {'error': 'invalid_query'})

    def do_POST(self):
        if not hmac.compare_digest(self.headers.get('Authorization','').encode(),('Bearer '+self.server.token).encode()):
            self.response(401,{'error':'unauthorized'});return
        if self.path!='/api/v1/approvals/respond':self.response(404,{'error':'not_found'});return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=1024:raise ValueError('invalid_body')
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict) or set(data)!= {'id','choice'} or not isinstance(data['id'],str) or len(data['id'])!=24:
                raise ValueError('invalid_body')
            self.response(200,self.server.monitor.respond(data['id'],data['choice']))
        except (ValueError,TypeError,KeyError):self.response(409,{'error':'request_expired_or_invalid'})
        except Exception:self.response(503,{'error':'approval_channel_unavailable'})

    def setup(self):
        super().setup()
        self.connection.settimeout(5)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, monitor, token):
        super().__init__(address, Handler)
        self.monitor = monitor
        self.token = token
        self.limit = threading.BoundedSemaphore(16)

    def process_request(self, request, address):
        if not self.limit.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.limit.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.limit.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path.home() / '.config/ai-monitor/config.json')
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = json.loads(args.config.read_text())
    monitor = Monitor(config)
    server = Server((config.get('bind', '0.0.0.0'), config['port']), monitor, config['token'])
    monitor.start()
    zeroconf = None
    try:
        from zeroconf import Zeroconf, ServiceInfo
        zeroconf = Zeroconf()
        info = ServiceInfo('_ai-monitor._tcp.local.', 'M5Stack ' + socket.gethostname() + '._ai-monitor._tcp.local.',
                           addresses=[socket.inet_aton(lan_ip())], port=config['port'],
                           server=config['mdns_host'] + '.local.', properties={'version': '1', 'path': '/api/v1/snapshot'})
        zeroconf.register_service(info, allow_name_change=True)
        LOG.info('mDNS advertised; port %d', config['port'])
    except Exception:
        LOG.warning('mDNS unavailable; configured LAN IP remains usable')
    def stop(_signum, _frame):
        monitor.stop.set()
        monitor.ipc.stop.set()
        monitor.account_usage.stop.set()
        monitor.collectors['opencode'].stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info('status service ready')
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        monitor.store.save()
        monitor.account_usage.stop.set()
        monitor.collectors['opencode'].stop.set()
        if monitor.account_usage.thread.is_alive():monitor.account_usage.thread.join(timeout=3)
        if zeroconf:
            zeroconf.close()


if __name__ == '__main__':
    main()
