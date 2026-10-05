import json
import sqlite3
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from questions import pending_question
from model import EventStore

class QuestionTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE thread_items(thread_id TEXT,turn_id TEXT,item_type TEXT,item_json TEXT,created_at_ms INTEGER,rollout_ordinal INTEGER)')
    def tearDown(self):self.db.close()
    def add(self,kind,payload,ordinal,turn='active'):
        self.db.execute('INSERT INTO thread_items VALUES(?,?,?,?,?,?)',('chat',turn,kind,json.dumps(payload),100000,ordinal))
    def question(self,call='call_a',count=1,ordinal=1,turn='active'):
        self.add('agentMessage',{'id':call,'delivery':'async','questions':[{'title':'通知测试 '+str(i)} for i in range(count)]},ordinal,turn)
    def reply(self,call='call_a',index=0,ordinal=2):
        body=[{'questionItemId':json.dumps(['request_user_input_async',call,index]),'answer':'private-answer-not-retained'}]
        self.add('userMessage',{'content':[{'type':'text','text':'<send_user_message_question_reply>\n'+json.dumps(body)+'\n</send_user_message_question_reply>'}]},ordinal)
    def test_pending_then_exact_reply_clears(self):
        self.question();q=pending_question(self.db,'chat','active');self.assertEqual(q['id'],'question:call_a')
        self.assertNotIn('answer',q);self.reply();self.assertIsNone(pending_question(self.db,'chat','active'))
    def test_partial_reply_and_other_call_do_not_clear(self):
        self.question(count=2);self.reply(call='wrong');self.assertIsNotNone(pending_question(self.db,'chat','active'))
        self.reply(ordinal=3);self.assertEqual(pending_question(self.db,'chat','active')['title'],'通知测试 1')
        self.reply(index=1,ordinal=4);self.assertIsNone(pending_question(self.db,'chat','active'))
    def test_old_turn_not_pending_and_latest_pending_preserved(self):
        self.question(turn='old');self.assertIsNone(pending_question(self.db,'chat','active'))
        self.question(call='new',ordinal=5);self.assertEqual(pending_question(self.db,'chat','active')['id'],'question:new')
    def test_native_question_stays_under_native_ipc_control(self):
        self.add('agentMessage',{'id':'native','questions':[{'title':'native'}]},1)
        self.assertIsNone(pending_question(self.db,'chat','active'))
    def test_same_question_alert_only_once_and_next_question_alerts(self):
        store=EventStore();t={'id':'chat','source':'codex','title':'任务','turn_id':'active','updated_at':100,'status':'running'}
        store.update([t]);self.question();q=pending_question(self.db,'chat','active');t.update(status='waiting_input',alert_key=q['id'])
        store.update([t]);store.update([t]);self.assertEqual(store.sequence,1)
        t.update(status='running');store.update([t]);self.question(call='new',ordinal=3);q=pending_question(self.db,'chat','active')
        t.update(status='waiting_input',alert_key=q['id']);store.update([t]);self.assertEqual(store.sequence,2)
