"""Hermes hook observer: bounded asynchronous output; every callback returns None."""
import json
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path

_queue = queue.Queue(maxsize=512)
_lock = threading.Lock()
_tasks = {}
_aliases = {}
_local = threading.local()
_worker = None
_stop = threading.Event()
_instance = uuid.uuid4().hex[:8]
_pid = os.getpid()
_start = ''
try:
    _stat = Path('/proc/self/stat').read_text()
    _start = _stat[_stat.rfind(')') + 2:].split()[19]
except OSError:
    pass


def clean(value, limit=96):
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or ''))
    value = re.sub(r'(?i)\b(?:sk-[\w-]{8,}|Bearer\s+\S+)', '[已隐藏]', value)
    value = re.sub(r'(?i)(password|token|api[_-]?key|secret)\s*[:=]\s*\S+', r'\1=[已隐藏]', value)
    return value[:limit]


def emit(event, **kwargs):
    try:
        with _lock:
            ids = [str(kwargs[k]) for k in ('session_id', 'task_id', 'session_key') if kwargs.get(k)]
            if event == 'start':
                tid = ids[0] if ids else 'process-' + str(_pid)
                if tid not in _tasks and len(_tasks) >= 128:
                    expired = next((k for k,v in _tasks.items() if v not in ('running','waiting_approval')), None)
                    if expired is None:
                        return None
                    _tasks.pop(expired)
                    for alias in [a for a,b in _aliases.items() if b == expired]:
                        _aliases.pop(alias,None)
                _local.task_id = tid
                for alias in ids:
                    _aliases[alias] = tid
                _tasks[tid] = 'running'
                _local.turn_id = str(kwargs.get('turn_id') or uuid.uuid4())
            else:
                tid = next((_aliases[x] for x in ids if x in _aliases), None)
                tid = tid or (getattr(_local, 'task_id', None) if not ids else None)
                if tid is None:
                    active = [k for k, v in _tasks.items() if v in ('running', 'waiting_approval')]
                    tid = active[0] if len(active) == 1 else (ids[0] if ids else None)
                if tid is None:
                    return None
            status = _tasks.get(tid,'running') if event == 'summary' else 'running'
            if event == 'approval':
                status = 'waiting_approval'
            elif event == 'end':
                if not any(kwargs.get(k) for k in ('completed','failed','interrupted')) and _tasks.get(tid) in ('completed','failed','interrupted'):
                    return None
                status = 'interrupted' if kwargs.get('interrupted') else 'failed' if kwargs.get('failed') else 'completed' if kwargs.get('completed') else 'unknown'
            elif event == 'approval_response' and kwargs.get('choice') == 'notify_failed':
                status = 'waiting_approval'
            if tid not in _tasks and len(_tasks) >= 128:
                return None
            _tasks[tid] = status
            entry = {'v': 1, 'id': str(uuid.uuid4()), 'at': time.time(), 'event': event,
                     'task_id': tid, 'turn_id': str(kwargs.get('turn_id') or getattr(_local, 'turn_id', '')),
                     'pid': _pid, 'process_start': _start, 'status': status,
                     'platform': clean(kwargs.get('platform') or kwargs.get('surface') or 'cli', 24)}
            if event == 'start':
                entry['title'] = clean(kwargs.get('user_message') or 'Hermes 任务', 64)
            if event in ('end','summary') and kwargs.get('assistant_response'):
                entry['summary'] = clean(kwargs['assistant_response'])
            if kwargs.get('tool_name'):
                entry['tool'] = clean(kwargs['tool_name'], 40)
            if kwargs.get('choice'):
                entry['choice'] = clean(kwargs['choice'], 24)
        try:
            _queue.put_nowait(entry)
        except queue.Full:
            # Prefer recent state over old progress when the writer is slow.
            try:
                _queue.get_nowait()
                _queue.put_nowait(entry)
            except (queue.Empty, queue.Full):
                pass
    except Exception:
        pass
    return None


def writer():
    directory = Path(os.environ.get('AI_MONITOR_EVENTS_DIR', Path.home() / '.local/state/ai-monitor/events'))
    base = directory / f'hermes-{_pid}-{_start}-{_instance}'
    last_heartbeat = 0
    while not _stop.is_set():
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                entry = _queue.get(timeout=1)
            except queue.Empty:
                entry = None
            if entry:
                log = base.with_suffix('.jsonl')
                if log.exists() and log.stat().st_size > 2 * 1024 * 1024:
                    log.replace(base.with_suffix('.jsonl.1'))
                fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.write(fd, (json.dumps(entry, ensure_ascii=False, separators=(',', ':')) + '\n').encode())
                finally:
                    os.close(fd)
            if time.monotonic() - last_heartbeat > 5:
                with _lock:
                    states = dict(_tasks)
                data = {'v': 1, 'pid': _pid, 'process_start': _start, 'at': time.time(), 'tasks': states}
                tmp = base.with_suffix('.heartbeat.tmp')
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'w') as f:
                    json.dump(data, f)
                tmp.replace(base.with_suffix('.heartbeat'))
                last_heartbeat = time.monotonic()
        except Exception:
            # The agent never waits for I/O, and persistent failures cannot spin.
            time.sleep(1)


def register(ctx):
    global _worker
    if _worker is None:
        _worker = threading.Thread(target=writer, name='m5stack-status', daemon=True)
        _worker.start()
    for hook, event in (('pre_llm_call', 'start'), ('post_llm_call', 'summary'), ('pre_tool_call', 'tool'),
                        ('pre_approval_request', 'approval'), ('post_approval_response', 'approval_response'),
                        ('on_session_end', 'end')):
        def callback(*args, _event=event, **kwargs):
            return emit(_event, **kwargs)
        ctx.register_hook(hook, callback)
    ctx.on_unload(_stop.set)
