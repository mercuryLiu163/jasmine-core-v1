"""Real isolated Core stores; synthetic provider/native payload/API transport/tokenizer.

No actual model, native Gate, or external operator evidence is claimed.
"""
import json
import os
from unittest.mock import patch
from test_p2_admission import AdmissionBusinessGraph
from jasmine_core import SCHEMA_VERSION
from jasmine_core.capture import p2_codex_hook, p3_codex_hook
from jasmine_core.capture.p2_runtime import Deadline, Lease, atomic_json
from jasmine_core.context.builder import ContextBuilder
from jasmine_core.models import NewObject

class ReviewWaitStores(AdmissionBusinessGraph):
 def test_real_pending_approval_same_source_then_real_current_context(self):
    self._review_wait_flow()
 def test_real_normal_child_race_before_context_blocks(self):
    self._review_wait_flow(child_race=True)
 def test_real_normal_child_race_on_readmission_interpret_blocks(self):
    self._review_wait_flow(child_race='interpret')
 def test_real_normal_child_race_on_final_step_read_blocks(self):
    self._review_wait_flow(child_race='step',prompt='请读取 index.html 的当前内容，不要修改文件。')
 def test_real_noncontinuation_pending_approval_same_native_source(self):
    self._review_wait_flow(prompt='请读取 index.html 的当前内容，不要修改文件。')
 def test_real_noncontinuation_rejected_review_has_no_context(self):
    self._review_wait_flow(prompt='请读取 index.html 的当前内容，不要修改文件。',refusal='reject')
 def test_real_noncontinuation_changed_source_has_no_context(self):
    self._review_wait_flow(prompt='请读取 index.html 的当前内容，不要修改文件。',refusal='source')
 def test_real_noncontinuation_wrong_nonce_never_waits(self):
    self._review_wait_flow(prompt='请读取 index.html 的当前内容，不要修改文件。',refusal='nonce')
 def _review_wait_flow(self,child_race=False,prompt='继续',refusal=None):
    initial=p2_codex_hook.handle(self.payload(),self.binding)
    self.assertIn('additionalContext',initial.get('hookSpecificOutput',{}),initial)
    old=Lease(self.binding,Deadline()).read();fixture=self.fixture
    adapter=self.root/'adapter.json'
    atomic_json(adapter,{'actor_id':fixture.agent,'receipt_root':str(self.root),'hook_definition_sha256':'a'*64})
    token=self.root/'operator';token.write_text('component');token.chmod(0o600)
    self.config.update(p3_adapter_config_file=str(adapter),p3_operator_token_file=str(token),p3_core_session_id=None,p3_review_wait=True)
    atomic_json(self.binding,self.config)
    original=fixture.fake.action
    def tentative(source):
      value=json.loads(original(source))
      for candidate in value['candidates']:candidate['certainty']='TENTATIVE'
      return json.dumps(value)
    fixture.fake.action=tentative
    tokenizer=type('SyntheticTokenizer',(),{'identity':{'qualification':'SYNTHETIC_COMPONENT'},'count':lambda self,text:len(text)//4})()
    def scan(budget,*,conn):
      return {'snapshot':{'complete':False,'partial_reasons':['synthetic-component']},'sample_window':{'started_at_unix':1,'finished_at_unix':2}}
    builder=ContextBuilder(fixture.conn,schema_version=SCHEMA_VERSION,tokenizer=tokenizer,scanner=scan)
    Base=p2_codex_hook.DeadlineClient;calls=[];review_reads=[];review_id_for_step=[]
    def correct(parent,source):
      fixture.resolver.maintenance.change(parent,'correct',{
        'idempotency_key':'component-normal-child-race','host_id':fixture.host,
        'reason':'explicit simulated operator correction after approval read','expected_revision':1,
        'result':json.loads(original(fixture.interpretations._source(source)))},actor_id=fixture.system)
      head=fixture.resolver.maintenance.get(parent)['maintenance_head']
      self.assertEqual(head['status'],'NORMAL');self.assertNotEqual(head['current_interpretation_id'],parent)
    class Transport(Base):
      def post(self,path,body,**kwargs):return self.request('POST',path,body,**kwargs)
      def request(self,method,path,body=None,**kwargs):
        calls.append((method,path,body))
        if path=='/v1/sessions':return fixture.objects.create(NewObject.session(body),actor_id=fixture.agent)
        if path=='/v1/context/build':return builder.build(body,actor_id=fixture.agent)
        if path.endswith('/check-current'):return builder.check_current(path.split('/')[3],body,actor_id=fixture.agent)
        if method=='GET' and path.startswith('/v1/reviews/'):
          review=fixture.reviews.get(path.split('/')[-1]);review_reads.append(review['status']);review_id_for_step[:]=[review['review_id']]
          if refusal=='source' and len(review_reads)==2:
            # Negative transport corruption; actual persisted Event remains untouched.
            return {'review':{**review,'resolution':{**review['resolution'],'source_event_id':'wrong-source'}}}
          if refusal=='reject' and len(review_reads)==2:
            fixture.reviews.change(review['review_id'],'reject',{'idempotency_key':'component-reject','host_id':fixture.host,'reason':'explicit refusal','expected_revision':review['revision']},actor_id=fixture.human,scopes=fixture.scopes)
            return {'review':fixture.reviews.get(review['review_id'])}
          if len(review_reads)==2:
            preview=review['preview']
            # Explicit simulated external operator action via the real Review CAS.
            fixture.reviews.change(review['review_id'],'approve',{
              'idempotency_key':'explicit-component-operator','host_id':fixture.host,'reason':'component operator confirmation',
              'expected_revision':review['revision'],'expected_revisions':preview['expected_revisions'],
              'expected_context_digest':preview['expected_context_digest'],
              'actions':[{'candidate_index':0,'action':'ATTACH_TASK','payload':{}},{'candidate_index':1,'action':'NO_ACTION','payload':{}}]},
              actor_id=fixture.human,scopes=fixture.scopes)
            review=fixture.reviews.get(review['review_id'])
          elif child_race is True and len(review_reads)==3:
            parent=review['resolution']['interpretation_id']
            correct(parent,review['resolution']['source_event_id'])
          return {'review':review}
        response=super().request(method,path,body,**kwargs)
        if child_race=='step' and path.startswith('/v1/steps/') and method=='GET' and len(review_reads)>=3:
          current=fixture.reviews.get(review_id_for_step[0])
          correct(current['resolution']['interpretation_id'],current['resolution']['source_event_id'])
        if child_race=='interpret' and path=='/v1/interpret' and len(review_reads)==2:
          correct(response['interpretation']['interpretation_id'],body['event_id'])
        return response
    with patch.dict(os.environ,{'JASMINE_CORE_GATE_NONCE':'wrong'} if refusal=='nonce' else {}),patch.object(p3_codex_hook,'DeadlineClient',Transport),patch.object(p2_codex_hook,'DeadlineClient',Transport):
      result=p3_codex_hook.handle({**self.payload('real-component-next'),'prompt':prompt},self.binding,client_factory=Transport,tokenizer=tokenizer)
    if refusal=='nonce':
      self.assertNotIn('additionalContext',result.get('hookSpecificOutput',{}))
      self.assertEqual(calls,[])
      return
    if child_race or refusal:
      self.assertEqual(result['decision'],'block',result)
      self.assertNotEqual(Lease(self.binding,Deadline()).read()['phase'],'READY')
      self.assertEqual(len([x for x in calls if x[1]=='/v1/context/build']),0)
      self.assertEqual(len([x for x in calls if x[1]=='/v1/events']),1)
      return
    self.assertIn('additionalContext',result.get('hookSpecificOutput',{}),result)
    current=Lease(self.binding,Deadline()).read();source=fixture.resolver.events.get(current['event_id'])
    self.assertEqual(current['phase'],'READY');self.assertEqual(current['generation'],'2')
    self.assertEqual(source['task_id'],old['task_id']);self.assertEqual(source['payload']['text'],prompt)
    self.assertEqual(source['payload']['turn_id'],'real-component-next');self.assertNotEqual(source['event_id'],old['event_id'])
    self.assertEqual(fixture.reviews.get(current['review_id'])['status'],'APPROVED')
    self.assertEqual(len([x for x in calls if x[1]=='/v1/events']),1)
    self.assertEqual(len([x for x in calls if x[1]=='/v1/context/build']),1)
    self.assertEqual(len([x for x in calls if x[1].endswith('/check-current')]),1)
    self.assertEqual(builder.get(current['context_pack_id'])['rendered_content'],result['hookSpecificOutput']['additionalContext'])
