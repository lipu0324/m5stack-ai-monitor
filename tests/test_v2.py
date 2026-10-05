import json
import sys
import tempfile
import sqlite3
import time
import threading
import urllib.request
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from codex_ipc import CodexIPC,project
from server import Monitor,Server
from usage import AccountUsage,quota_projection,seven_days
from hermes_live import HermesLive
from collectors import HermesCollector

class V2Tests(unittest.TestCase):
    def test_usage_projection_keeps_tokens_not_transcripts(self):
        self.assertEqual(project({'latestTokenUsageInfo':{'total':{'inputTokens':50}},'turns':['private']}),{'latestTokenUsageInfo':{'total':{'inputTokens':50}}})
    def test_nullable_token_states_keep_snapshot_usable(self):
        with tempfile.TemporaryDirectory() as d:
            m=Monitor(dict(codex_home=d,hermes_home=d,state_dir=d,events_dir=d))
            states={'a':{'state':{'latestTokenUsageInfo':None}},'b':{'state':None},'c':None,
                    'd':{'state':{'latestTokenUsageInfo':{'total':None}}},
                    'e':{'state':{'latestTokenUsageInfo':{'total':{'inputTokens':'invalid'}}}},
                    'valid':{'state':{'latestTokenUsageInfo':{'total':{'inputTokens':100,'outputTokens':20,'cachedInputTokens':75}}}}}
            m.ipc.snapshot=Mock(return_value=(True,'',states))
            result=m.snapshot()['metrics']['codex']
            self.assertEqual(result['input'],100);self.assertEqual(result['output'],20);self.assertEqual(result['hit_percent'],75)
            m.ipc.snapshot=Mock(return_value=(True,'',{'a':states['a']}))
            self.assertFalse(m.snapshot()['metrics']['codex']['available'])
    def test_quota_remaining_not_used_and_unknown_window(self):
        self.assertEqual(quota_projection({'rateLimits':{'primary':{'usedPercent':20,'windowDurationMins':10080,'resetsAt':123}}})[0]['remaining_percent'],80)
        self.assertEqual(quota_projection({'rateLimits':{'primary':None}}),[])
        self.assertEqual(len(seven_days([])),7)
    def test_daily_timeout_does_not_invalidate_successful_quota(self):
        c=AccountUsage('/tmp/not-a-codex-home')
        c.call=Mock(side_effect=[{}, {'rateLimits':{'primary':{'usedPercent':20}}},TimeoutError('daily response delayed')])
        c.stop.wait=Mock(side_effect=lambda *_:c.stop.set() or True)
        with patch('usage.subprocess.Popen',return_value=Mock()):c.run()
        self.assertTrue(c.snapshot()['quota']['available'])
        self.assertEqual(c.snapshot()['quota']['windows'][0]['remaining_percent'],80)
        self.assertIsNone(c.snapshot()['daily'])
    def test_approval_is_exact_request_once_only_and_not_replayed(self):
        with tempfile.TemporaryDirectory() as d:
            m=Monitor(dict(codex_home=d,hermes_home=d,state_dir=d,events_dir=d))
            m.tasks=[{'id':'codex:chat','source':'codex','title':'任务','status':'waiting_approval',
                     '_approval':{'source':'codex','session_id':'chat','request_id':17,'command':'ls','choices':['once','deny']}}]
            m.ipc.respond=Mock(return_value=True);key=m.approvals()[0]['id']
            with self.assertRaises(ValueError):m.respond(key,'always')
            self.assertTrue(m.respond(key,'once')['accepted']);m.ipc.respond.assert_called_once_with('chat',17,'once')
            with self.assertRaises(ValueError):m.respond(key,'once')
            self.assertEqual(m.approvals(),[])
            with self.assertRaises(ValueError):m.respond('0'*24,'deny')
    def test_codex_typed_delivery_receipt(self):
        c=CodexIPC('/tmp/not-a-codex-home');c.connected=True;c.client='monitor'
        c.states={'chat':{'owner':'desktop','state':{'requests':[{'id':17,'method':'item/commandExecution/requestApproval'}]}}}
        def deliver(message):
            event,reply=c.replies[message['requestId']]
            reply.update(resultType='success',result={'method':'thread-follower-command-approval-decision','result':{'ok':True}})
            event.set()
        c.send=Mock(side_effect=deliver)
        self.assertTrue(c.respond('chat',17,'once'))
        self.assertEqual(c.send.call_args.args[0]['params']['decision'],'accept')
        self.assertEqual(c.replies,{})
    def test_stale_codex_approval_never_sends(self):
        c=CodexIPC('/tmp/not-a-codex-home');c.send=Mock()
        with self.assertRaises(ValueError):c.respond('chat',17,'once')
        c.send.assert_not_called()
    def test_hermes_approval_revalidates_current_open_request(self):
        c=HermesLive('/tmp/none');c._call=Mock(return_value={'open_requests':[]})
        with self.assertRaises(ValueError):c.respond('runtime','old-request','once')
        self.assertEqual(c._call.call_count,1)
        c._call=Mock(side_effect=[{'open_requests':[{'method':'approval','params':{'request_id':'r','choices':['once','deny']}}]},{'resolved':1}])
        self.assertTrue(c.respond('runtime','r','once'))
        self.assertEqual(c._call.call_args.args,('approval.respond',{'session_id':'runtime','request_id':'r','choice':'once','all':False}))
    def test_active_view_excludes_history_but_retains_waits(self):
        with tempfile.TemporaryDirectory() as d:
            m=Monitor(dict(codex_home=d,hermes_home=d,state_dir=d,events_dir=d))
            m.tasks=[{'id':s,'source':'codex','status':s,'title':s,'updated_at':1} for s in ('completed','idle','running','waiting_input','waiting_approval','unknown')]
            self.assertEqual([t['status'] for t in m.snapshot(view='active')['tasks']],['running','waiting_input','waiting_approval'])
    def test_hermes_existing_database_and_live_identity_merge(self):
        with tempfile.TemporaryDirectory() as d:
            db=sqlite3.connect(Path(d)/'state.db')
            db.execute('''CREATE TABLE sessions(id TEXT,title TEXT,display_name TEXT,source TEXT,started_at REAL,ended_at REAL,end_reason TEXT,
                last_activity_at REAL,last_activity_description TEXT,input_tokens INTEGER,output_tokens INTEGER,cache_read_tokens INTEGER,
                cache_write_tokens INTEGER,archived INTEGER,hidden INTEGER,parent_session_id TEXT)''')
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('stored','旧会话',None,'desktop',time.time(),None,None,time.time(),'terminal',5,3,10,2,0,0,None));db.commit();db.close()
            c=HermesCollector(d,Path(d)/'events');c.live.collect=Mock(return_value=[{'id':'runtime','session_key':'stored','title':'运行任务','status':'working','started_at':time.time(),'last_active':time.time(),'requests':[]}]);c.live.online=True
            tasks,_=c.collect();self.assertEqual(len(tasks),1);self.assertEqual(tasks[0]['id'],'hermes:stored');self.assertEqual(tasks[0]['status'],'running')
            self.assertEqual(c.metrics['input'],17);self.assertEqual(c.metrics['total'],20);self.assertAlmostEqual(c.metrics['hit_percent'],1000/17)
    def test_truncated_command_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as d:
            m=Monitor(dict(codex_home=d,hermes_home=d,state_dir=d,events_dir=d));m.tasks=[{'id':'codex:c','title':'长命令','source':'codex','_approval':{'source':'codex','session_id':'c','request_id':'r','command':'truncated','truncated':True,'choices':['once','deny']}}]
            self.assertFalse(m.approvals()[0]['can_approve'])
            with self.assertRaises(ValueError):m.respond(m.approvals()[0]['id'],'once')
    def test_approval_http_requires_token_and_rejects_stale_requests(self):
        with tempfile.TemporaryDirectory() as d:
            m=Monitor(dict(codex_home=d,hermes_home=d,state_dir=d,events_dir=d));s=Server(('127.0.0.1',0),m,'local-secret')
            thread=threading.Thread(target=s.serve_forever,daemon=True);thread.start()
            try:
                for token,body,code in [('wrong',{'id':'a'*24,'choice':'once'},401),('local-secret',{'id':'a'*24,'choice':'always'},409),('local-secret',{'id':'a'*24,'choice':'once'},409)]:
                    r=urllib.request.Request(f'http://127.0.0.1:{s.server_port}/api/v1/approvals/respond',method='POST',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(r,timeout=2)
                    self.assertEqual(error.exception.code,code)
            finally:s.shutdown();s.server_close()
if __name__=='__main__':unittest.main()
