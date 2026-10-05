"""Discovery identity, OpenCode projections, selective API and device masks."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from agents import AgentDiscovery, agent_command, IDS
from opencode import OpenCodeCollector, local_url
from server import Monitor
from model import EventStore


class AgentTests(unittest.TestCase):
    def test_exact_process_identity_not_prompts_or_substrings(self):
        self.assertEqual(agent_command(['/opt/opencode','serve']), 'opencode')
        self.assertEqual(agent_command(['node','/npm/@anthropic-ai/claude-code/cli.js']), 'claude')
        self.assertEqual(agent_command(['python3','-m','aider']), 'aider')
        self.assertEqual(agent_command(['node','/npm/@google/gemini-cli/dist/index.js']), 'gemini')
        for args in (['sh','-c','opencode serve'], ['cat','opencode'], ['my-opencode-wrapper'], ['node','./gemini-test.js']):
            self.assertIsNone(agent_command(args))

    def test_discovery_absence_installation_and_presence_are_separate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);proc=root/'proc';proc.mkdir();home=root/'home';home.mkdir()
            discovery=AgentDiscovery(home,proc,path=str(root/'empty'))
            self.assertFalse(any(v['detected'] for v in discovery.scan(True).values()))
            (home/'.claude').mkdir()
            p=proc/'123';p.mkdir();(p/'cmdline').write_bytes(b'/opt/opencode\0serve\0--password\0private\0')
            found=discovery.scan(True)
            self.assertTrue(found['claude']['installed']);self.assertFalse(found['claude']['online'])
            self.assertTrue(found['opencode']['online']);self.assertEqual(found['opencode']['process_count'],1)
            self.assertNotIn('private',json.dumps(found));self.assertEqual(found['claude']['capability'],'presence')
            (p/'cmdline').write_bytes(b'/opt/other\0opencode\0')
            self.assertFalse(discovery.scan(True)['opencode']['detected'])

    def test_local_endpoint_validation(self):
        self.assertEqual(local_url('http://127.0.0.1:1234/'),'http://127.0.0.1:1234')
        for value in ('http://remote:1234','http://a:b@localhost','http://localhost/secret','https://localhost','http://localhost?token=a'):
            with self.assertRaises(ValueError):local_url(value)

    def test_selective_snapshot_filters_counts_approvals_metrics_events_and_pages(self):
        with tempfile.TemporaryDirectory() as d:
            m=Monitor(dict(codex_home=d,hermes_home=d,state_dir=d,events_dir=d))
            m.tasks=[dict(id=f'{s}:{i}',source=s,status='running',title='任务',updated_at=1) for s in IDS[:3] for i in range(12)]
            m.store.events=[dict(source=s,status='completed',id=str(i)) for i,s in enumerate(IDS[:3])]
            one=m.snapshot(agents=['opencode']);self.assertEqual(one['total'],12);self.assertEqual(one['counts']['running'],12)
            self.assertEqual(set(t['source'] for t in one['tasks']),{'opencode'})
            self.assertEqual(list(one['metrics']),['opencode']);self.assertEqual(len(one['events']),1)
            second=m.snapshot(agents=['opencode'],cursor=one['next_cursor']);self.assertEqual(len(second['tasks']),2)
            empty=m.snapshot(agents=[]);self.assertEqual(empty['total'],0);self.assertEqual(empty['metrics'],{});self.assertEqual(empty['events'],[])
            with self.assertRaises(ValueError):m.snapshot(agents=['not-an-agent'])
            self.assertEqual(m.snapshot(source='claude')['tasks'],[])

    def test_device_stable_masks_discovery_changes_and_all_hidden(self):
        root=Path(__file__).resolve().parents[1]
        program=r'''
#include <cassert>
#include "agents.h"
int main() {
 AgentSelection a;bool detected[9]={true,true,true};
 assert(a.next(-1,detected,true)==0);assert(a.next(1,detected,true)==2);
 a.toggle(2);assert(a.next(1,detected,true)==0);assert(a.next(1,detected,false)==2);
 detected[0]=false;assert(a.next(-1,detected,true)==1);
 a.toggle(1);assert(a.next(-1,detected,true)==-1);
 detected[3]=true;assert(a.next(-1,detected,true)==3);
 assert(AgentSelection::index("opencode")==2);assert(AgentSelection::index("unknown")==-1);
 AgentSelection restored;restored.hidden=a.hidden;assert(!restored.enabled(2));assert(restored.enabled(3));
 a.toggle(-1);assert(!a.enabled(-1));
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'test.cpp').write_text(program)
            subprocess.run(['c++','-std=c++11','-I',str(root/'src'),str(p/'test.cpp'),'-o',str(p/'test')],check=True,capture_output=True)
            subprocess.run([str(p/'test')],check=True)


class OpenCodeTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.root=Path(self.dir.name)
        db=sqlite3.connect(self.root/'db')
        db.execute('''CREATE TABLE session_v2(id TEXT,title TEXT,parent_id TEXT,directory TEXT,time_created INTEGER,time_updated INTEGER,
                       time_idle INTEGER,idle_outcome TEXT,time_archived INTEGER,tokens_input INTEGER,tokens_output INTEGER,
                       tokens_cache_read INTEGER,tokens_cache_write INTEGER)''')
        for tid,outcome in [('done','succeeded'),('bad','failed'),('stop','interrupted'),('fresh',None)]:
            db.execute('INSERT INTO session_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(tid,'中文 token=secret123',None,'/work',1000,2000,2000 if outcome else None,outcome,None,10,5,20,2))
        db.commit();db.close()
        self.discovery=AgentDiscovery(self.root,self.root/'proc',path=str(self.root/'empty'))
        self.c=OpenCodeCollector(self.discovery,dict(opencode_db=str(self.root/'db'),opencode_config=str(self.root/'config'),opencode_url='http://127.0.0.1:4321'))

    def tearDown(self):self.dir.cleanup()

    def get(self,url,path):
        if path=='/api/session/active':return {'data':{'fresh':{}}}
        if path.startswith('/api/session?'):return {'data':[]}
        if path.startswith('/api/permission/request?'):
            self.assertIn('location%5Bdirectory%5D',path)
            return {'data':[{'id':'p1','sessionID':'fresh','action':'shell','resources':['private arguments']}]}
        if path.startswith('/api/question/request?'):raise urllib.error.HTTPError(url,404,'absent',{},None)
        raise AssertionError(path)

    def test_v2_explicit_outcomes_waits_tokens_and_no_commands_leak(self):
        self.c.get=Mock(side_effect=self.get);self.c.poll();tasks,health=self.c.collect();byid={t['id']:t for t in tasks}
        self.assertEqual(byid['opencode:fresh']['status'],'waiting_approval');self.assertIsNone(byid['opencode:fresh']['ended_at'])
        self.assertEqual(byid['opencode:done']['status'],'completed');self.assertEqual(byid['opencode:bad']['status'],'failed');self.assertEqual(byid['opencode:stop']['status'],'interrupted')
        self.assertNotIn('private arguments',json.dumps(tasks));self.assertNotIn('secret123',json.dumps(tasks))
        self.assertTrue(health['live']);self.assertTrue(health['wait_supported'])
        metric=self.c.metric_snapshot();self.assertEqual(metric['input'],128);self.assertEqual(metric['total'],148);self.assertEqual(metric['cached'],80)
        self.assertAlmostEqual(metric['hit_percent'],62.5);self.assertFalse(metric['quota']['available'])
        self.assertFalse(any('_approval' in t for t in tasks))

    def test_disconnect_never_turns_running_into_old_completion_and_reconnect(self):
        self.c.get=Mock(side_effect=lambda u,p:{'data':{'done':{}}} if p=='/api/session/active' else {'data':[]})
        self.c.poll();self.assertEqual(next(t for t in self.c.tasks if t['id']=='opencode:done')['status'],'running')
        self.c.get=Mock(side_effect=OSError('offline'));self.c.poll()
        self.assertEqual(next(t for t in self.c.tasks if t['id']=='opencode:done')['status'],'unknown')
        self.c.get=Mock(side_effect=lambda u,p:{'data':{}} if p=='/api/session/active' else {'data':[]});self.c.poll()
        self.assertEqual(next(t for t in self.c.tasks if t['id']=='opencode:done')['status'],'unknown')
        db=sqlite3.connect(self.root/'db');db.execute("UPDATE session_v2 SET time_idle=3000 WHERE id='done'");db.commit();db.close()
        self.c.poll();self.assertEqual(next(t for t in self.c.tasks if t['id']=='opencode:done')['status'],'completed')

    def test_question_wait_and_exact_event_dedup(self):
        def get(u,p):
            if p.startswith('/api/permission/request'):return {'data':[]}
            if p.startswith('/api/question/request'):return {'data':[{'id':'q1','sessionID':'fresh','questions':['private']}]}
            return self.get(u,p)
        self.c.get=get;store=EventStore();store.update([]);self.c.poll();tasks,_=self.c.collect();store.update(tasks);count=store.sequence
        self.assertEqual(tasks[0]['status'],'waiting_input');self.c.poll();store.update(self.c.collect()[0]);self.assertEqual(store.sequence,count)
        self.assertNotIn('private',json.dumps(tasks))

    def test_incompatible_schema_and_stale_live_projection(self):
        db=sqlite3.connect(self.root/'db');db.execute("UPDATE session_v2 SET time_updated=9000 WHERE id='done'");db.commit();db.close()
        self.c.get=Mock(side_effect=ValueError('incompatible'));self.c.poll();self.assertFalse(self.c.health['live'])
        self.assertEqual(next(t for t in self.c.tasks if t['id']=='opencode:done')['status'],'unknown')
        self.assertEqual(next(t for t in self.c.tasks if t['id']=='opencode:fresh')['status'],'unknown')
        self.c.tasks=[dict(id='opencode:s',status='running')];self.c.health.update(observed_at=time.time()-11,live=True)
        tasks,h=self.c.collect();self.assertEqual(tasks[0]['status'],'unknown');self.assertFalse(h['live'])

    def test_v1_status_fallback_and_no_false_terminal_inference(self):
        self.c.db=self.root/'no-db'
        def get(u,p):
            if p=='/api/session/active':raise urllib.error.HTTPError(u,404,'v1',{},None)
            if p=='/session/status':return {'s':{'type':'busy'}}
            if p=='/session':return [dict(id='s',title='v1',time=dict(created=1000,updated=2000))]
            if p in ('/permission','/question'):return []
            raise AssertionError(p)
        self.c.get=get;self.c.poll();self.assertEqual(self.c.tasks[0]['status'],'running');self.assertFalse(self.c.metrics['available'])
        self.c.get=Mock(side_effect=OSError('closed'));self.c.poll();self.assertEqual(self.c.tasks[0]['status'],'unknown')

class DiscoveryHTTPTests(unittest.TestCase):
    def test_owned_socket_ports_only_no_port_scan(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'net').mkdir();(root/'42/fd').mkdir(parents=True)
            (root/'42/fd/1').symlink_to('socket:[123]')
            (root/'net/tcp').write_text('header\n 0: 0100007F:C0DE 00000000:0000 0A 0 0 0 0 0 123\n 1: 0100007F:1111 00000000:0000 0A 0 0 0 0 0 999\n')
            c=AgentDiscovery(root,root);c.processes['opencode']=[42]
            self.assertEqual(c.opencode_urls(),['http://127.0.0.1:49374'])

    def test_agents_endpoint_auth_and_selection_validation(self):
        import threading
        import urllib.request
        from server import Server
        with tempfile.TemporaryDirectory() as d:
            m=Monitor(dict(codex_home=d,hermes_home=d,state_dir=d,events_dir=d))
            m.sources={'opencode':dict(id='opencode',detected=True,online=True,capability='tasks'), 'claude':dict(id='claude',detected=False)}
            s=Server(('127.0.0.1',0),m,'secret');threading.Thread(target=s.serve_forever,daemon=True).start()
            base=f'http://127.0.0.1:{s.server_port}'
            try:
                req=urllib.request.Request(base+'/api/v1/agents',headers={'Authorization':'Bearer secret'})
                with urllib.request.urlopen(req) as r:
                    data=json.load(r);self.assertEqual([a['id'] for a in data['agents']],['opencode'])
                for path,token,code in [('/api/v1/agents','bad',401),('/api/v1/snapshot?agents=unknown','secret',400)]:
                    req=urllib.request.Request(base+path,headers={'Authorization':'Bearer '+token})
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(req)
                    self.assertEqual(error.exception.code,code)
            finally:s.shutdown();s.server_close()
