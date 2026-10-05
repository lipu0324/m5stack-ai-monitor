import hashlib
import json
import re
import time
from pathlib import Path

STATES = ('idle', 'running', 'waiting_approval', 'waiting_input', 'completed', 'failed', 'interrupted', 'unknown')
ORDER = {'waiting_approval': 0, 'waiting_input': 0, 'running': 1, 'failed': 2, 'interrupted': 3, 'completed': 4, 'idle': 5, 'unknown': 6}
ALERTS = {'completed', 'failed', 'waiting_approval', 'waiting_input', 'interrupted'}


def text(value, limit=96):
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or ''))
    value = re.sub(r'(?i)\b(?:sk-[\w-]{8,}|Bearer\s+\S+)', '[已隐藏]', value)
    value = re.sub(r'(?i)(password|token|api[_-]?key|secret)\s*[:=]\s*\S+', r'\1=[已隐藏]', value)
    return value[:limit]


def process_identity(pid):
    try:
        data = Path(f'/proc/{int(pid)}/stat').read_text()
        return data[data.rfind(')') + 2:].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def process_matches(pid, start):
    return start is not None and process_identity(pid) == str(start)


def sort_tasks(tasks):
    return sorted(tasks, key=lambda t: (ORDER.get(t['status'], 6), -t.get('updated_at', 0), t['id']))


class EventStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.previous = {}
        self.events = []
        self.sequence = 0
        self.bootstrapped = False
        self.seen = []
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.previous = data['previous']
                self.events = data['events'][-20:]
                self.sequence = data['sequence']
                self.seen = data.get('seen', [])[-512:]
                self.bootstrapped = True
            except (OSError, ValueError, KeyError):
                pass

    def update(self, tasks):
        now = int(time.time())
        for task in tasks:
            signature = (task.get('turn_id', ''), task['status'], task.get('alert_key',''))
            previous = self.previous.get(task['id'])
            key = hashlib.sha256(json.dumps([task['id'], *signature, task.get('alert_key','')],ensure_ascii=False).encode()).hexdigest()
            if self.bootstrapped and previous != list(signature) and task['status'] in ALERTS and key not in self.seen:
                # Do not announce old terminal history first discovered after a restart.
                if previous is not None or task.get('updated_at', 0) >= now - 10:
                    self.sequence += 1
                    self.events.append({'id': str(self.sequence), 'task_id': task['id'],
                                        'source': task['source'], 'title': task['title'],
                                        'status': task['status'], 'at': task.get('updated_at', now)})
            if task['status'] in ALERTS and key not in self.seen:
                self.seen.append(key)
            self.previous[task['id']] = list(signature)
        live_ids = {t['id'] for t in tasks}
        self.previous = {k: v for k, v in self.previous.items() if k in live_ids}
        self.events = self.events[-20:]
        self.seen = self.seen[-512:]
        self.bootstrapped = True

    def save(self):
        if self.path:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps({'previous': self.previous, 'events': self.events,
                                       'sequence': self.sequence, 'seen': self.seen}, ensure_ascii=False))
            tmp.chmod(0o600)
            tmp.replace(self.path)
