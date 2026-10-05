import glob
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from codex_ipc import runtime_status
from hermes_live import HermesLive
from questions import pending_question
from model import process_matches, sort_tasks, text


def read_db(path):
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=0.2)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def source_alive(fragment):
    for path in glob.glob('/proc/[0-9]*/cmdline'):
        try:
            if os.stat(path).st_uid != os.getuid():
                continue
            command = Path(path).read_bytes().replace(b'\0', b' ')
            if fragment in command:
                return True
        except OSError:
            pass
    return False


class CodexCollector:
    def __init__(self, home, ipc):
        self.home = Path(home)
        self.ipc = ipc
        self.last_tasks = []

    def collect(self):
        online = source_alive(b'/usr/lib/chatgpt/ChatGPT')
        connected, ipc_error, states = self.ipc.snapshot()
        try:
            with read_db(self.home / 'state_5.sqlite') as c:
                threads = [dict(r) for r in c.execute("SELECT id,title,name,rollout_path,updated_at,created_at,source,cwd FROM threads WHERE archived=0 AND originator IN ('Codex Desktop','codex_work_desktop') AND source='vscode' ORDER BY updated_at DESC LIMIT 200")]
            with read_db(self.home / 'thread_history_1.sqlite') as c:
                turns = {r['thread_id']: dict(r) for r in c.execute('''SELECT t.* FROM thread_turns t
                    JOIN (SELECT thread_id, MAX(rollout_ordinal) AS ordinal FROM thread_turns GROUP BY thread_id) l
                    ON t.thread_id=l.thread_id AND t.rollout_ordinal=l.ordinal''')}
                tasks = []
                for thread in threads:
                    tid = thread['id']
                    turn = turns.get(tid)
                    if not turn:
                        continue
                    # Local desktop histories only; remote histories are not present in this database.
                    status = {'inProgress': 'running', 'completed': 'completed', 'failed': 'failed', 'interrupted': 'interrupted'}.get(turn['status'], 'unknown')
                    state = states.get(tid, {}).get('state', {}) if connected else {}
                    live_status = runtime_status(state)
                    if live_status in ('running', 'waiting_input', 'waiting_approval'):
                        status = live_status
                    elif turn['status']=='inProgress' and state and live_status in ('idle','unknown'):
                        status='unknown'
                    if not online and status in ('running', 'waiting_input', 'waiting_approval'):
                        status = 'unknown'
                    item = c.execute('''SELECT item_type,item_json,created_at_ms FROM thread_items
                        WHERE thread_id=? AND turn_id=? AND item_type IN
                        ('commandExecution','mcpToolCall','webSearch','fileChange','agentMessage','reasoning','subAgentActivity')
                        ORDER BY updated_at_ordinal DESC,rollout_ordinal DESC LIMIT 1''', (tid, turn['turn_id'])).fetchone()
                    action, summary, item_at = '', '', 0
                    if item:
                        item_at = (item['created_at_ms'] or 0) // 1000
                        try:
                            payload = json.loads(item['item_json'])
                            action = {'commandExecution': '执行命令', 'mcpToolCall': '调用工具', 'webSearch': '搜索资料', 'fileChange': '修改文件', 'reasoning': '推理中', 'subAgentActivity': '子任务', 'agentMessage': '回复'}.get(item['item_type'], '')
                            # No command arguments, reasoning text, tool output or full conversation leaves the host.
                            if item['item_type'] == 'agentMessage':
                                summary = text(payload.get('text', ''))
                            elif item['item_type'] == 'mcpToolCall':
                                summary = text(payload.get('tool', ''), 48)
                        except (ValueError, TypeError):
                            pass
                    question=pending_question(c,tid,turn['turn_id']) if online and status in ('running','waiting_input') else None
                    if question:
                        status='waiting_input';action='等待回答';summary=question['title'];item_at=max(item_at,question['at'])
                    started = turn['started_at'] or thread['updated_at']
                    ended = turn['completed_at']
                    tasks.append({'id': 'codex:' + tid, 'source': 'codex', 'kind': 'desktop',
                                  'turn_id': turn['turn_id'], 'title': text(thread['name'] or thread['title'], 64),
                                  'status': status, 'action': action, 'summary': summary,
                                  'started_at': started, 'ended_at': ended,
                                  'updated_at': max(ended or started, item_at, int(states.get(tid,{}).get('seen',0)) if status in ('waiting_input','waiting_approval') else 0),
                                  'alert_key': question['id'] if question else ','.join(str(r.get('id','')) for r in state.get('requests',[]) if isinstance(r,dict)) if status in ('waiting_input','waiting_approval') else '',
                                  'wait_supported': bool(question) or (connected and bool(state))})
                    pending=next((r for r in state.get('requests',[]) if r.get('method')=='item/commandExecution/requestApproval'),None)
                    if pending:
                        params=pending.get('params',{});command=params.get('command') or ''
                        if isinstance(command,list):command=' '.join(map(str,command))
                        tasks[-1]['_approval']={'source':'codex','session_id':tid,'request_id':pending['id'],
                            'command':text(command,256),'truncated':len(text(command,4096))>256,
                            'choices':(['once'] if params.get('availableDecisions') is None or 'accept' in params['availableDecisions'] else [])+['deny']}
            active_ids = [t['id'].split(':', 1)[1] for t in sort_tasks(tasks) if t['status'] in ('running', 'waiting_input', 'waiting_approval')]
            recent_ids = [t['id'].split(':', 1)[1] for t in sort_tasks(tasks)]
            self.ipc.watch(list(dict.fromkeys(active_ids + recent_ids))[:32])
            self.last_tasks = sort_tasks(tasks)
            return self.last_tasks, {'online': online, 'healthy': True, 'live': connected,
                                     'wait_supported': connected, 'detail': ipc_error if online else 'Codex 桌面已关闭'}
        except (OSError, sqlite3.Error, KeyError):
            stale = [dict(t, status='unknown') if t['status'] in ('running', 'waiting_input', 'waiting_approval') else t for t in self.last_tasks]
            return stale, {'online': online, 'healthy': False, 'live': connected,
                           'wait_supported': False, 'detail': '本地任务数据库暂不可读'}


class HermesCollector:
    def __init__(self, home, state_dir):
        self.home = Path(home)
        self.state_dir = Path(state_dir)
        self.tasks = {}
        self.offsets = {}
        self.gateway_pid = None
        self.gateway_start = None
        self.live = HermesLive(self.home)
        self.metrics = {'available':False,'quota':{'available':False,'windows':[]}}

    def apply(self, e):
        if e.get('v') != 1 or not e.get('task_id'):
            return
        tid = 'hermes:' + str(e['task_id'])
        previous = self.tasks.get(tid)
        # Ignore late events from an older run, even across multiple producers.
        if previous and e.get('at', 0) <= previous.get('_event_at', 0):
            return
        task = previous or {'id': tid, 'source': 'hermes', 'title': 'Hermes 任务', 'summary': '',
                            'action': '', 'started_at': int(e['at']), 'ended_at': None,
                            'kind': text(e.get('platform', 'cli'), 24), 'wait_supported': True}
        event = e.get('event')
        if event == 'start':
            task.update(status='running', turn_id=e.get('turn_id', ''), started_at=int(e['at']), ended_at=None)
        elif event == 'approval':
            task.update(status='waiting_approval', action='等待审批', alert_key=e.get('id',''))
        elif event == 'approval_response':
            if e.get('choice') != 'notify_failed':
                task.update(status='running', action='审批已响应')
        elif event == 'tool':
            task.update(status='running', action='工具: ' + text(e.get('tool', ''), 40))
        elif event == 'summary':
            pass
        elif event == 'end':
            task['alert_key']=''
            task.update(status=e.get('status', 'unknown'), ended_at=int(e['at']))
        else:
            return
        for field in ('title', 'summary'):
            if e.get(field):
                task[field] = text(e[field], 64 if field == 'title' else 96)
        task.update(pid=e.get('pid'), process_start=e.get('process_start'), updated_at=int(e['at']), _event_at=e['at'])
        self.tasks[tid] = task

    def collect(self):
        now = time.time()
        heartbeats = {}
        try:
            files = sorted(self.state_dir.glob('hermes-*.jsonl*'), key=lambda p: p.stat().st_mtime)[-64:]
            for path in files:
                stat = path.stat()
                key = str(path)
                previous = self.offsets.get(key)
                offset = previous[1] if previous and previous[0] == stat.st_ino and stat.st_size >= previous[1] else 0
                with path.open('rb') as f:
                    f.seek(offset)
                    data = f.read(4 * 1024 * 1024)
                end = data.rfind(b'\n') + 1
                for line in data[:end].splitlines():
                    if len(line) > 4096:
                        continue
                    try:
                        self.apply(json.loads(line))
                    except (ValueError, KeyError, TypeError):
                        continue
                self.offsets[key] = (stat.st_ino, offset + end)
            current_files = {str(p) for p in files}
            self.offsets = {k: v for k, v in self.offsets.items() if k in current_files}
            for path in list(self.state_dir.glob('hermes-*.heartbeat'))[-64:]:
                if re.fullmatch(r'hermes-\d+-\d+\.heartbeat',path.name):
                    continue  # Ignore the prototype observer that did not have unload cleanup.
                try:
                    hb = json.loads(path.read_text())
                    if now - hb['at'] < 20 and process_matches(hb['pid'], hb['process_start']):
                        key=(hb['pid'],str(hb['process_start']))
                        if hb['at']>=heartbeats.get(key,{}).get('at',0):
                            heartbeats[key]=hb
                except (ValueError, OSError, KeyError):
                    pass
        except OSError:
            pass
        gateway = {}
        try:
            gateway = json.loads((self.home / 'gateway_state.json').read_text())
        except (OSError, ValueError):
            pass
        pid, start = gateway.get('pid'), gateway.get('start_time')
        gateway_online = process_matches(pid, start) and gateway.get('gateway_state') not in ('stopped', 'failed')
        # Heartbeat timestamp is separately checked; a live but stuck process is not healthy.
        from datetime import datetime
        try:
            gateway_fresh = now - datetime.fromisoformat(gateway['updated_at']).timestamp() < 30
        except (ValueError, KeyError, TypeError):
            gateway_fresh = False
        for task in self.tasks.values():
            live = (task.get('pid'), str(task.get('process_start'))) in heartbeats
            if task.get('status') in ('running', 'waiting_approval', 'waiting_input') and not live:
                task['status'] = 'unknown'
            if live and task.get('status') == 'unknown':
                # Heartbeat includes the last explicit state for recovery after collector restarts.
                hb = heartbeats[(task['pid'], str(task['process_start']))]
                recovered = hb.get('tasks', {}).get(task['id'].split(':', 1)[1])
                if recovered:
                    task['status'] = recovered
        # Persisted history is available even when the plugin was installed
        # after these sessions. Live Desktop state takes precedence over history.
        combined=dict(self.tasks)
        try:
            with read_db(self.home/'state.db') as db:
                rows=[dict(r) for r in db.execute('''SELECT id,title,display_name,source,started_at,ended_at,end_reason,last_activity_at,
                    last_activity_description,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens
                    FROM sessions WHERE archived=0 AND hidden=0 AND parent_session_id IS NULL ORDER BY COALESCE(last_activity_at,started_at) DESC LIMIT 200''')]
                for row in rows:
                    tid='hermes:'+row['id']
                    if tid not in combined:
                        combined[tid]={'id':tid,'source':'hermes','kind':row['source'],'title':text(row['title'] or row['display_name'] or 'Hermes 会话',64),
                            'summary':'','action':text(row['last_activity_description'] or '',40),'status':'idle',
                            'turn_id':'history','started_at':int(row['started_at']),'ended_at':int(row['ended_at']) if row['ended_at'] else None,
                            'updated_at':int(row['last_activity_at'] or row['started_at']),'wait_supported':False}
                totals=db.execute('''SELECT SUM(input_tokens) input,SUM(output_tokens) output,SUM(cache_read_tokens) cached,
                    SUM(cache_write_tokens) written FROM sessions''').fetchone()
                inputs=int(totals['input'] or 0);cached=int(totals['cached'] or 0);written=int(totals['written'] or 0);output=int(totals['output'] or 0)
                prompt=inputs+cached+written
                from usage import seven_days
                days=[{'day':r['day'],'tokens':r['tokens']} for r in db.execute('''SELECT date(started_at,'unixepoch','+8 hours') day,
                    SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens) tokens FROM sessions GROUP BY day ORDER BY day DESC LIMIT 7''')]
                self.metrics={'available':True,'input':prompt,'output':output,'cached':cached,'total':prompt+output,
                    'hit_percent':100*cached/prompt if prompt else None,'daily':seven_days(days),'scope':'本机会话累计；按会话开始日',
                    'quota':{'available':False,'windows':[]},'updated_at':int(now)}
        except (OSError,sqlite3.Error,KeyError,TypeError):
            self.metrics['available']=False
        live_rows=self.live.collect()
        for row in live_rows:
            key=str(row.get('session_key') or row['id']);tid='hermes:'+key
            t=combined.get(tid) or {'id':tid,'source':'hermes','kind':'desktop','summary':'','action':'','turn_id':row['id'],
                                      'started_at':int(row.get('started_at') or now),'ended_at':None}
            status={'working':'running','starting':'running','streaming':'running','resuming':'running','waiting':'waiting_input','idle':'idle'}.get(row.get('status'),'unknown')
            pending=next((r for r in row['requests'] if r.get('method')=='approval'),None)
            t.update(status='waiting_approval' if pending else status,title=text(row.get('title') or t.get('title') or 'Hermes 会话',64),updated_at=int(row.get('last_active') or now),wait_supported=True)
            t.pop('_approval',None)
            if pending:
                params=pending.get('params',{})
                t.update(action='等待审批',alert_key=str(params.get('request_id') or pending.get('id')),
                         _approval={'source':'hermes','session_id':row['id'],'request_id':params.get('request_id'),
                                    'command':text(params.get('command') or '',256),'truncated':len(text(params.get('command') or '',4096))>256,
                                    'choices':params.get('choices',['once','deny'])})
            combined[tid]=t
        ordered = sort_tasks(list(combined.values()))
        # Retain all active tasks and at most 100 historical sessions.
        returned=ordered[:200]
        online = bool(heartbeats) or gateway_online
        online=online or self.live.online
        detail = '桌面实时 / 会话数据库' if self.live.online else '插件 / 会话数据库' if heartbeats else '会话数据库 / 实时未连接' if returned else 'Hermes 未运行'
        return returned, {'online': online, 'healthy': self.live.online or bool(heartbeats) or (gateway_online and gateway_fresh),
                                         'live': self.live.online or bool(heartbeats), 'wait_supported': self.live.online or bool(heartbeats), 'detail': detail,
                                         'gateway_online': gateway_online, 'gateway_active': gateway.get('active_agents', 0)}
