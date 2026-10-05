#!/usr/bin/env python3
"""Exercise the observer lifecycle without calling a model or sending messages."""
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'host'))
from collectors import HermesCollector

class Context:
    def __init__(self):self.hooks={};self.cleanup=[]
    def register_hook(self,name,callback):self.hooks[name]=callback
    def on_unload(self,callback):self.cleanup.append(callback)

with tempfile.TemporaryDirectory() as directory:
    os.environ['AI_MONITOR_EVENTS_DIR']=directory
    spec=importlib.util.spec_from_file_location('hermes_observer_probe',ROOT/'hermes_plugin/__init__.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    ctx=Context();module.register(ctx)
    collector=HermesCollector(Path(directory)/'home',directory)
    def hook(name,**kwargs):
        assert ctx.hooks[name](**kwargs) is None
    def wait(status):
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            tasks,health=collector.collect()
            found=[t for t in tasks if t['id']=='hermes:test-a']
            if found and found[0]['status']==status:return found[0]
            time.sleep(.05)
        raise AssertionError('expected '+status)
    hook('pre_llm_call',session_id='test-a',task_id='alias-a',turn_id='r1',user_message='测试任务',platform='cli')
    wait('running')
    hook('pre_llm_call',session_id='test-b',task_id='alias-b',turn_id='r2',user_message='并发任务',platform='telegram')
    hook('pre_tool_call',task_id='alias-a',tool_name='terminal',args={'token':'must not be recorded'})
    hook('pre_approval_request',session_key='alias-a',command='not recorded')
    wait('waiting_approval')
    hook('post_approval_response',session_key='alias-a',choice='once')
    wait('running')
    hook('post_llm_call',session_id='test-a',assistant_response='短摘要')
    hook('on_session_end',session_id='test-a',turn_id='r1',completed=True,failed=False,interrupted=False)
    result=wait('completed');assert result['summary']=='短摘要'
    hook('on_session_end',session_id='test-a',completed=False,interrupted=False,reason='cli_exit')
    assert wait('completed')['status']=='completed'
    hook('on_session_end',session_id='test-b',turn_id='r2',failed=True)
    time.sleep(.2);tasks,health=collector.collect()
    assert len(tasks)==2 and any(t['status']=='failed' for t in tasks)
    for f in Path(directory).glob('*.jsonl'):
        assert 'must not be recorded' not in f.read_text()
    for cb in ctx.cleanup:cb()
    module._worker.join(timeout=2)
    assert not module._worker.is_alive()
    print('Hermes asynchronous hooks -> collector: lifecycle, concurrent identity, summary, cleanup OK')
    fixture=ROOT/'tests/fixtures/hermes-lifecycle.json'
    fixture.parent.mkdir(exist_ok=True)
    fixture.write_text(json.dumps({'states':['running','waiting_approval','running','completed','failed'],
                                   'concurrent_sessions':2,'cleanup':True,'no_tool_arguments':True},indent=2))
