import importlib.util
import json
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from codex_ipc import apply_patches, project, runtime_status
from collectors import HermesCollector, CodexCollector
from model import EventStore, process_identity, text
from server import Monitor, Server, encode_cursor, decode_cursor


def task(id='task', status='running', turn='turn1', updated=None):
    return {'id': id, 'source': 'codex', 'title': '中文任务', 'status': status,
            'turn_id': turn, 'updated_at': updated or int(time.time())}


class StateTests(unittest.TestCase):
    def test_ipc_wait_flags_and_requests(self):
        self.assertEqual(runtime_status({'threadRuntimeStatus': {'type':'active', 'activeFlags':['waitingOnApproval']}}), 'waiting_approval')
        self.assertEqual(runtime_status({'requests':[{'method':'item/tool/requestUserInput'}]}), 'waiting_input')
        self.assertEqual(runtime_status({'threadRuntimeStatus': {'type':'notLoaded'}}), 'unknown')

    def test_project_and_patches_do_not_retain_transcripts(self):
        state = project({'threadRuntimeStatus': {'type':'active', 'activeFlags':[]}, 'requests':[], 'turns':[{'secret':'never store'}]})
        apply_patches(state, [{'op':'replace','path':['threadRuntimeStatus','activeFlags'],'value':['waitingOnApproval']},
                              {'op':'add','path':['turns',0],'value':'private'}])
        self.assertEqual(runtime_status(state), 'waiting_approval')
        self.assertNotIn('turns', state)
        apply_patches(state, [{'op':'add','path':['requests',0],'value':{'method':'item/tool/requestUserInput'}}])
        self.assertEqual(runtime_status(state), 'waiting_input')
        apply_patches(state, [{'op':'remove','path':['requests',0]}])
        self.assertEqual(runtime_status(state), 'waiting_approval')

    def test_baseline_dedup_and_new_round(self):
        store=EventStore()
        store.update([task(status='completed')])
        self.assertEqual(store.events, [])
        store.update([task(turn='turn2')])
        store.update([task(turn='turn2',status='waiting_input')])
        store.update([task(turn='turn2',status='waiting_input')])
        self.assertEqual(len(store.events), 1)
        store.update([task(turn='turn2',status='completed')])
        self.assertEqual(len(store.events), 2)

    def test_restart_does_not_repeat_events(self):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'state.json'
            store=EventStore(file);store.update([task()]);store.update([task(status='failed')]);store.save()
            restored=EventStore(file);restored.update([task(status='failed')])
            self.assertEqual(restored.sequence,1)
            self.assertEqual(file.stat().st_mode & 0o777,0o600)

    def test_old_discovered_completion_is_not_new_alert(self):
        store=EventStore();store.update([task()]);store.update([task(),task(id='old',status='completed',updated=1)])
        self.assertEqual(store.events,[])

    def test_wait_alert_not_replayed_after_source_disconnect(self):
        store=EventStore();store.update([task()])
        waiting=dict(task(status='waiting_approval'),alert_key='request1')
        store.update([waiting]);store.update([task(status='unknown')]);store.update([waiting])
        self.assertEqual(store.sequence,1)
        store.update([dict(waiting,alert_key='request2')])
        self.assertEqual(store.sequence,2)

    def test_redaction(self):
        self.assertNotIn('secret123', text('token=secret123'))
        self.assertNotIn('sk-abcdefghijk', text('key sk-abcdefghijk'))


class HermesTests(unittest.TestCase):
    def event(self,event,**extra):
        return dict(v=1,task_id='s',event=event,at=time.time(),pid=os.getpid(),process_start=process_identity(os.getpid()),**extra)

    def test_all_outcomes_and_no_timeout_inference(self):
        with tempfile.TemporaryDirectory() as d:
            c=HermesCollector(Path(d)/'home',d)
            for status in ('completed','failed','interrupted'):
                c.apply(self.event('start',turn_id='r'))
                c.apply(self.event('approval'))
                self.assertEqual(c.tasks['hermes:s']['status'],'waiting_approval')
                c.apply(self.event('approval_response',choice='once'))
                c.apply(self.event('end',status=status))
                self.assertEqual(c.tasks['hermes:s']['status'],status)

    def test_dead_producer_unknown_not_completed(self):
        with tempfile.TemporaryDirectory() as d:
            c=HermesCollector(Path(d)/'home',d)
            c.apply(self.event('start',turn_id='r'))
            tasks,health=c.collect()
            self.assertEqual(tasks[0]['status'],'unknown')
            self.assertFalse(health['online'])

    def test_partial_log_and_fresh_heartbeat(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'hermes-test.jsonl'
            event=self.event('start',turn_id='r',title='并发任务')
            raw=json.dumps(event).encode()
            path.write_bytes(raw[:12])
            c=HermesCollector(Path(d)/'home',d)
            self.assertEqual(c.collect()[0],[])
            with path.open('ab') as f:f.write(raw[12:]+b'\n')
            (Path(d)/'hermes-test.heartbeat').write_text(json.dumps({'pid':os.getpid(),'process_start':process_identity(os.getpid()),'at':time.time(),'tasks':{'s':'running'}}))
            tasks,health=c.collect()
            self.assertEqual(tasks[0]['status'],'running');self.assertTrue(health['live'])
            self.assertEqual(len(c.collect()[0]),1)

    def test_plugin_never_controls_and_saturates_safely(self):
        spec=importlib.util.spec_from_file_location('observer',ROOT/'hermes_plugin/__init__.py')
        plugin=importlib.util.module_from_spec(spec);spec.loader.exec_module(plugin)
        self.assertIsNone(plugin.emit('start',session_id='s',user_message='任务',turn_id='r'))
        self.assertIsNone(plugin.emit('approval',session_key='s'))
        self.assertEqual(plugin._tasks['s'],'waiting_approval')
        self.assertIsNone(plugin.emit('end',session_id='s',failed=True))
        self.assertEqual(plugin._tasks['s'],'failed')
        start=time.monotonic()
        for _ in range(2000):self.assertIsNone(plugin.emit('tool',session_id='s',tool_name='terminal'))
        self.assertLess(time.monotonic()-start,1)
        self.assertLessEqual(plugin._queue.qsize(),512)
        self.assertFalse(any('args' in e for e in list(plugin._queue.queue)))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        d=self.directory.name
        self.monitor=Monitor(dict(codex_home=d,hermes_home=d,state_dir=d,events_dir=d))
        self.monitor.tasks=[task(id=f't{i:02}', status='running') for i in range(23)]
        self.monitor.sources={'codex':{'online':True},'hermes':{'online':False}}
        self.monitor.observed_at=int(time.time())
        self.server=Server(('127.0.0.1',0),self.monitor,'test-secret')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.directory.cleanup()

    def get(self,path,token='test-secret'):
        return urllib.request.urlopen(urllib.request.Request(self.base+path,headers={'Authorization':'Bearer '+token}),timeout=2)

    def test_auth_and_no_control_endpoint(self):
        for path,token,code in [('/api/v1/snapshot','bad',401),('/control','test-secret',404)]:
            with self.assertRaises(urllib.error.HTTPError) as e:self.get(path,token)
            self.assertEqual(e.exception.code,code)

    def test_pagination_and_utf8_bounded(self):
        cursor='';found=[]
        while True:
            with self.get('/api/v1/snapshot?cursor='+cursor) as r:
                raw=r.read();self.assertLessEqual(len(raw),16384);payload=json.loads(raw)
            self.assertLessEqual(len(payload['tasks']),10)
            found.extend(t['id'] for t in payload['tasks']);cursor=payload['next_cursor']
            if not cursor:break
        self.assertEqual(found,[f't{i:02}' for i in range(23)])
        with self.get('/api/v1/snapshot?source=hermes') as r:self.assertEqual(json.load(r)['tasks'],[])

    def test_bad_query_rejected(self):
        for suffix in ('source=invalid','cursor=***'):
            with self.assertRaises(urllib.error.HTTPError) as e:self.get('/api/v1/snapshot?'+suffix)
            self.assertEqual(e.exception.code,400)

    def test_device_telemetry_authenticated_and_bounded(self):
        with self.get('/api/v1/snapshot?device=abc123&uptime=120&heap=120000&min_heap=90000') as r:
            result=json.load(r)
        self.assertEqual(result['device']['uptime'],120)
        with self.get('/api/v1/snapshot?device=abc123&uptime=-1&heap=2&min_heap=3') as r:
            self.assertEqual(json.load(r)['device']['uptime'],120)

    def test_response_large_fails_closed(self):
        self.monitor.tasks=[dict(task(id=str(i)),title='中'*2000) for i in range(10)]
        with self.assertRaises(urllib.error.HTTPError) as e:self.get('/api/v1/snapshot')
        self.assertEqual(e.exception.code,503)


if __name__=='__main__':unittest.main()
