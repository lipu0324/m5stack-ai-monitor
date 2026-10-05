"""Observe Desktop IPC; forward a single explicit command-approval decision.

Never acquires ownership, resumes threads, starts turns or changes permissions.
"""
import copy
import json
import socket
import struct
import threading
import time
import uuid
from pathlib import Path

MAX_FRAME = 8 * 1024 * 1024
FIELDS = {'threadRuntimeStatus', 'requests', 'title', 'updatedAt', 'resumeState', 'latestTokenUsageInfo'}


def project(state):
    return {k: copy.deepcopy(state[k]) for k in FIELDS if k in state}


def apply_patches(state, patches):
    for patch in patches:
        path = patch.get('path', [])
        if isinstance(path, str):
            path = [x.replace('~1', '/').replace('~0', '~') for x in path.strip('/').split('/')]
        if not path:
            if patch.get('op') in ('replace', 'add'):
                state.clear()
                state.update(project(patch.get('value', {})))
            continue
        if path[0] not in FIELDS:
            continue
        node = state
        for key in path[:-1]:
            node = node[int(key)] if isinstance(node, list) else node[key]
        key = path[-1]
        if isinstance(node, list):
            key = len(node) if key == '-' else int(key)
            if patch['op'] == 'remove':
                node.pop(key)
            elif patch['op'] == 'add':
                node.insert(key, copy.deepcopy(patch['value']))
            else:
                node[key] = copy.deepcopy(patch['value'])
        elif patch['op'] == 'remove':
            node.pop(key, None)
        else:
            node[key] = copy.deepcopy(patch['value'])


def runtime_status(state):
    runtime = state.get('threadRuntimeStatus') or {}
    flags = runtime.get('activeFlags', []) if isinstance(runtime, dict) else []
    requests = state.get('requests') or []
    for request in requests:
        if not isinstance(request, dict):
            continue
        method = request.get('method', '')
        if 'requestUserInput' in method or 'elicitation' in method or 'requestImplementation' in method:
            return 'waiting_input'
        if 'Approval' in method or 'approval' in method or 'requestPermissions' in method:
            return 'waiting_approval'
    if 'waitingOnApproval' in flags:
        return 'waiting_approval'
    if 'waitingOnUserInput' in flags:
        return 'waiting_input'
    kind = runtime.get('type') if isinstance(runtime, dict) else runtime
    return {'active': 'running', 'idle': 'idle', 'systemError': 'unknown', 'notLoaded': 'unknown'}.get(kind)


class CodexIPC:
    def __init__(self, home):
        self.path = Path(home) / 'ipc/ipc.sock'
        self.lock = threading.Lock()
        self.desired = set()
        self.states = {}
        self.connected = False
        self.error = '未连接桌面 IPC'
        self.stop = threading.Event()
        self.sock = None
        self.client = None
        self.send_lock = threading.Lock()
        self.replies = {}
        self.thread = threading.Thread(target=self.run, name='codex-ipc', daemon=True)

    def start(self):
        self.thread.start()

    def watch(self, ids):
        with self.lock:
            self.desired = set(list(ids)[:32])

    def snapshot(self):
        with self.lock:
            return self.connected, self.error, copy.deepcopy(self.states)

    def send(self, message):
        if message.get('method') not in ('initialize', 'thread-stream-following-changed', 'thread-follower-command-approval-decision'):
            raise ValueError('IPC method allowlist')
        data = json.dumps(message, separators=(',', ':')).encode()
        with self.send_lock:
            if not self.sock:raise ConnectionError('IPC disconnected')
            self.sock.sendall(struct.pack('<I', len(data)) + data)

    def respond(self, tid, request_id, choice):
        if choice not in ('once','deny'):raise ValueError('invalid decision')
        with self.lock:
            current=self.states.get(tid,{})
            request=next((r for r in current.get('state',{}).get('requests',[]) if str(r.get('id'))==str(request_id) and r.get('method')=='item/commandExecution/requestApproval'),None)
            if not request or not self.connected or not current.get('owner'):raise ValueError('request_expired')
            owner=current['owner'];client=self.client
            rid=str(uuid.uuid4());ready=threading.Event();reply={}
            self.replies[rid]=(ready,reply)
        try:
            self.send({'type':'request','requestId':rid,'sourceClientId':client,'targetClientId':owner,'version':1,
                       'method':'thread-follower-command-approval-decision',
                       'params':{'conversationId':tid,'requestId':request['id'],'decision':'accept' if choice=='once' else 'decline'}})
            if not ready.wait(5):raise ConnectionError('approval response timed out')
            result=reply.get('result',{})
            if isinstance(result,dict) and result.get('method')=='thread-follower-command-approval-decision':result=result.get('result',{})
            if reply.get('resultType')!='success' or not isinstance(result,dict) or not result.get('ok'):raise ConnectionError('desktop declined approval delivery')
            return True
        finally:
            with self.lock:self.replies.pop(rid,None)

    def read_exact(self, length):
        result = bytearray()
        while len(result) < length:
            block = self.sock.recv(length - len(result))
            if not block:
                raise ConnectionError('IPC disconnected')
            result.extend(block)
        return result

    def read(self):
        length = struct.unpack('<I', self.read_exact(4))[0]
        if not 0 < length <= MAX_FRAME:
            raise ValueError('IPC frame exceeds bound')
        return json.loads(self.read_exact(length))

    def follow(self, client, tid, enabled):
        self.send({'type': 'broadcast', 'method': 'thread-stream-following-changed',
                   'sourceClientId': client, 'version': 1,
                   'params': {'hostId': 'local', 'conversationId': tid, 'following': enabled}})

    def run(self):
        while not self.stop.is_set():
            followed = set()
            client = None
            try:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(3)
                self.sock.connect(str(self.path))
                request_id = str(uuid.uuid4())
                self.send({'type': 'request', 'requestId': request_id,
                           'sourceClientId': 'initializing-client', 'version': 0,
                           'method': 'initialize', 'params': {'clientType': 'm5stack-monitor'}})
                while client is None:
                    response = self.read()
                    if response.get('requestId') == request_id:
                        client = response.get('result', {}).get('clientId')
                        if not client:
                            raise ValueError('IPC initialize rejected')
                with self.lock:
                    self.connected = True
                    self.client = client
                    self.error = ''
                self.sock.settimeout(1)
                while not self.stop.is_set():
                    with self.lock:
                        desired = self.desired.copy()
                    for tid in followed - desired:
                        self.follow(client, tid, False)
                        with self.lock:
                            self.states.pop(tid, None)
                    for tid in desired - followed:
                        self.follow(client, tid, True)
                    followed = desired
                    # Read header with timeout; once a frame begins, allow time for its body.
                    try:
                        header = self.sock.recv(4, socket.MSG_PEEK)
                    except socket.timeout:
                        continue
                    if not header:
                        raise ConnectionError('IPC disconnected')
                    self.sock.settimeout(10)
                    message = self.read()
                    self.sock.settimeout(1)
                    with self.lock:
                        pending=self.replies.get(message.get('requestId'))
                        if pending and message.get('type')=='response':
                            pending[1].update(message);pending[0].set();continue
                    if message.get('type') == 'client-discovery-request':
                        # Decline every ownership/control request; do not act as an owner.
                        payload = {'type': 'client-discovery-response', 'requestId': message['requestId'],
                                   'response': {'canHandle': False}}
                        data = json.dumps(payload).encode()
                        with self.send_lock:self.sock.sendall(struct.pack('<I', len(data)) + data)
                        continue
                    if message.get('method') != 'thread-stream-state-changed' or message.get('version') != 11:
                        continue
                    params = message.get('params', {})
                    tid = params.get('conversationId')
                    if params.get('hostId') != 'local' or tid not in desired:
                        continue
                    change = params.get('change', {})
                    with self.lock:
                        if change.get('type') == 'snapshot':
                            self.states[tid] = {'state': project(change['conversationState']),
                                                'revision': change['revision'], 'seen': time.time(),
                                                'owner': message.get('sourceClientId')}
                        elif change.get('type') == 'patches':
                            current = self.states.get(tid)
                            if not current or current['revision'] != change.get('baseRevision'):
                                # Request another snapshot by re-announcing our read-only subscription.
                                self.states.pop(tid, None)
                                followed.discard(tid)
                                continue
                            apply_patches(current['state'], change.get('patches', []))
                            current.update(revision=change['revision'], seen=time.time())
            except (OSError, ValueError, KeyError, TypeError, IndexError, struct.error):
                with self.lock:
                    self.connected = False
                    self.error = '桌面 IPC 不可用；等待状态未知'
                    self.states.clear()
            finally:
                if self.sock:
                    if client:
                        for tid in followed:
                            try:
                                self.follow(client, tid, False)
                            except OSError:
                                break
                    self.sock.close()
                self.sock = None
                with self.lock:
                    self.connected = False
            self.stop.wait(3)
