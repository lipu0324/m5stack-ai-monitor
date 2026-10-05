"""Read Desktop asynchronous question metadata and exact reply IDs from SQLite.

These UI questions are agentMessage.questions, not app-server requests. Never
retain answer text, other user messages, transcripts or reasoning.
"""
import json
import re
from model import text


def pending_question(db,thread_id,turn_id):
    rows=db.execute('''SELECT json_extract(item_json,'$.id') AS call_id,
        json_extract(item_json,'$.questions') AS questions,created_at_ms,rollout_ordinal
        FROM thread_items WHERE thread_id=? AND turn_id=? AND item_type='agentMessage'
        AND json_extract(item_json,'$.delivery')='async'
        AND json_array_length(item_json,'$.questions')>0
        ORDER BY rollout_ordinal DESC LIMIT 16''',(thread_id,turn_id)).fetchall()
    pending=[]
    for row in rows:
        try:
            questions=json.loads(row['questions'])
            if row['call_id'] and isinstance(questions,list) and questions:
                pending.append((row,questions))
        except (ValueError,TypeError):continue
    if not pending:return None
    answered=set();oldest=min(row['rollout_ordinal'] for row,_ in pending)
    for reply in db.execute('''SELECT json_extract(item_json,'$.content') AS content FROM thread_items
        WHERE thread_id=? AND turn_id=? AND item_type='userMessage' AND rollout_ordinal>?
        AND item_json LIKE '%<send_user_message_question_reply>%'
        ORDER BY rollout_ordinal DESC LIMIT 64''',(thread_id,turn_id,oldest)):
        try:content=json.loads(reply['content'])
        except (TypeError,ValueError):continue
        for part in content if isinstance(content,list) else []:
            if not isinstance(part,dict):continue
            value=part.get('text','')
            if not isinstance(value,str) or len(value)>65536:continue
            match=re.search(r'<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>',value,re.S)
            if not match:continue
            try:
                payload=json.loads(match.group(1))
                for item in payload if isinstance(payload,list) else []:
                    key=json.loads(item.get('questionItemId',''))
                    if isinstance(key,list) and len(key)==3 and key[0] in ('request_user_input_async','request_user_input'):
                        answered.add((str(key[1]),int(key[2])))
            except (ValueError,TypeError,AttributeError):continue
    for row,questions in pending:
        for i,q in enumerate(questions):
            if (row['call_id'],i) not in answered and isinstance(q,dict):
                return {'id':'question:'+row['call_id'],'title':text(q.get('title','有问题需要回答'),96),
                        'at':int(row['created_at_ms'] or 0)//1000}
    return None
