import copy
import hashlib
import json
import socket

import numpy as np
import pytest

from scripts.run import run_openrouter_baseline as run
from scripts.run import run_openrouter_smoke as smoke
from src.agent.curation_observation_v1 import metrics
from src.cluster.manager import ClusterManager


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a,**kw): raise AssertionError('Unexpected network/key/client access')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(smoke,'make_session',forbidden)


@pytest.fixture
def payload():
    j = smoke.read(smoke.SOURCE/'manifest.json')['stepwise']['jobs'][0]
    return smoke.build(smoke.SOURCE,j,run.MODELS[0])


def raw(request, action='KEEP'):
    return dict(model=request['model'],provider='DeepInfra',
        choices=[dict(finish_reason='stop',message=dict(reasoning='offline fixture',
            content=json.dumps(dict(action=action,rationale='SCRIPTED OFFLINE'))))],
        usage=dict(cost=.001,prompt_tokens=100,completion_tokens=20))


class Response:
    status_code=200
    def __init__(self,data): self.data=data
    def json(self): return self.data
    def raise_for_status(self): pass


class Session:
    def __init__(self, action='KEEP', failure=None): self.posts=0;self.action=action;self.failure=failure
    def post(self,url,*,json,**kw):
        assert url==smoke.BASE+'/chat/completions'
        self.posts+=1
        if self.failure=='transport': raise TimeoutError('fixture timeout')
        response=raw(json,self.action)
        if self.failure=='length': response['choices'][0]['finish_reason']='length'
        if self.failure=='missing_cost': response['usage'].pop('cost')
        return Response(response)
    def close(self): pass


def test_convert_equals_frozen_stepwise():
    spec=smoke.read(smoke.SOURCE/'manifest.json')['stepwise']
    for phase in ('phase1','phase2'):
        job=next(j for j in spec['jobs'] if j['phase']==phase)
        source=run.minimal_stepwise.rebuild(smoke.SOURCE,job,run.minimal)
        for model in run.MODELS:
            assert run.convert(source,model)==smoke.build(smoke.SOURCE,job,model)


def test_shared_budget_includes_prior(payload,tmp_path):
    session=Session();p=run.Provider(tmp_path/'calls',budget=25,prior=24.99,session=session)
    with pytest.raises(ValueError,match='Insufficient'):p.ask(payload,'test')
    assert session.posts==0 and p.charged==24.99


def test_replay_no_resend_or_charge(payload,tmp_path):
    folder=tmp_path/'calls'; s=Session()
    p=run.Provider(folder,budget=25,prior=.1,session=s)
    expected=p.ask(payload,'same')
    assert s.posts==1 and p.charged==pytest.approx(.101)
    # No client supplied: if replay touches the network the autouse guard fails.
    q=run.Provider(folder,budget=25,prior=.1)
    assert q.ask(payload,'same')==expected
    assert q.sent==0 and q.charged==pytest.approx(.101)


def test_replay_divergence_stops(payload,tmp_path):
    p=run.Provider(tmp_path/'calls',budget=25,prior=0,session=Session())
    p.ask(payload,'same')
    q=run.Provider(tmp_path/'calls',budget=25,prior=0)
    changed=copy.deepcopy(payload); changed['messages'][0]['content']+='changed'
    with pytest.raises(ValueError,match='divergence'):q.ask(changed,'same')


@pytest.mark.parametrize('failure',['transport','length','missing_cost'])
def test_failure_never_retried_or_resumed(payload,tmp_path,failure):
    s=Session(failure=failure); folder=tmp_path/'calls'
    p=run.Provider(folder,budget=25,prior=0,session=s)
    with pytest.raises((ValueError,TimeoutError)):p.ask(payload,'one')
    assert s.posts==1
    assert p.charged==pytest.approx(.001 if failure=='length' else smoke.reservation(payload['model']))
    with pytest.raises(ValueError,match='Failed/uncertain'):run.Provider(folder,budget=25,prior=0)


def manager():
    return ClusterManager(np.array([1,1,1,1,3,3,3]),np.array([1,1,2,2,3,3,3]),
        np.array([[1.],[2.],[.9],[0.]]),np.arange(7)*.01,np.random.default_rng(12).normal(size=(7,12)))


@pytest.fixture
def fast(monkeypatch):
    def observe(m,phase,cid,target=None,**kw):
        names=run.minimal.VIEWS[phase]
        return metrics(m,phase,cid,target),['data:image/png;base64,YQ==']*len(names),[b'a']*len(names),{
            'images':[dict(name=n,sha256=hashlib.sha256(b'a').hexdigest()) for n in names]}
    monkeypatch.setattr(run.minimal,'observe',observe)


def test_controller_replay_same_terminal(tmp_path,fast):
    p=run.Provider(tmp_path/'calls',budget=25,prior=0,session=Session())
    def controller(provider,name):
        return run.Controller(manager(),run.minimal.DATASETS[0],tmp_path/name,provider=provider,
            budget_usd=25,sampling_rate=30000,model=run.MODELS[0])
    c=controller(p,'first'); c.run()
    q=run.Provider(tmp_path/'calls',budget=25,prior=0)
    d=controller(q,'resumed'); d.run()
    assert c.state_hash()==d.state_hash() and q.sent==0
    assert c.status==d.status=='completed'


def test_controller_failure_preserves_state(tmp_path,fast):
    p=run.Provider(tmp_path/'calls',budget=25,prior=0,session=Session(failure='length'))
    c=run.Controller(manager(),run.minimal.DATASETS[0],tmp_path/'controller',provider=p,
        budget_usd=25,sampling_rate=30000,model=run.MODELS[0])
    before=c.state_hash()
    with pytest.raises(run.minimal.ControllerStop):c.run()
    assert c.state_hash()==before and c.status=='stopped'
    assert not smoke.read(c.out/'summary.json')['terminal_evaluation_allowed']


def test_partial_stepwise_not_full_accuracy():
    targets=[dict(job_id='a',expert_action='SPLIT'),dict(job_id='b',expert_action='DISCARD')]
    score=run.minimal_stepwise.score(targets,[dict(job_id='a',action='SPLIT')])
    assert score['accuracy'] is None and score['accuracy_on_valid']==1


def test_score_terminal_only_completed():
    from types import SimpleNamespace
    with pytest.raises(ValueError,match='partial'):run.score_terminal(SimpleNamespace(status='stopped'),{},None)


def retry_parent(tmp_path, monkeypatch, payload, failure='length'):
    parent=tmp_path/'parent';parent.mkdir()
    session=Session()
    provider=run.Provider(parent/'calls',budget=25,prior=.1,session=session)
    tag=f'{payload["model"]}/stepwise/job'
    provider.ask(payload,tag)
    session.failure=failure
    with pytest.raises((ValueError,TimeoutError)):provider.ask(payload,tag)
    manifest=dict(prior_charged_or_reserved_usd=.1,requests=[
        dict(model=payload['model'],job_id='job',sha256=run.digest(payload))]*2)
    run.write(parent/'manifest.json',manifest)
    monkeypatch.setattr(run,'ROOT',tmp_path)
    monkeypatch.setattr(run,'verify',lambda out,archived=False:manifest)
    return parent,tag


def test_explicit_length_retry_preserves_cost_and_reuses_success(tmp_path,monkeypatch,payload):
    parent,tag=retry_parent(tmp_path,monkeypatch,payload)
    lineage,reusable=run.retry_lineage(parent)
    assert lineage['failed_cost_usd']==pytest.approx(.001)
    assert lineage['successful_calls_reused']==1 and len(reusable)==1
    calls=tmp_path/'child_calls';calls.mkdir()
    run.shutil.copytree(reusable[0],calls/'000001')
    session=Session()
    p=run.Provider(calls,budget=25,prior=lineage['inherited_prior_usd']+lineage['failed_cost_usd'],session=session)
    assert p.charged==pytest.approx(.102)
    p.ask(payload,tag)
    assert session.posts==0
    p.ask(payload,tag)
    assert session.posts==1 and p.charged==pytest.approx(.103)
    assert (parent/'calls/000002/error.json').exists()
    assert not (parent/'calls/000002/decision.json').exists()


@pytest.mark.parametrize('failure',['transport','missing_cost'])
def test_explicit_retry_refuses_uncertain_calls(tmp_path,monkeypatch,payload,failure):
    parent,_=retry_parent(tmp_path,monkeypatch,payload,failure)
    with pytest.raises((ValueError,FileNotFoundError)):run.retry_lineage(parent)


def test_explicit_retry_validates_request_order(tmp_path,monkeypatch,payload):
    parent,_=retry_parent(tmp_path,monkeypatch,payload)
    manifest=run.read(parent/'manifest.json');manifest['requests'][1]['sha256']='wrong'
    monkeypatch.setattr(run,'verify',lambda out,archived=False:manifest)
    with pytest.raises(ValueError,match='frozen order'):run.retry_lineage(parent)


def test_8000_changes_only_output_limit():
    job=smoke.read(smoke.SOURCE/'manifest.json')['stepwise']['jobs'][0]
    for model in run.MODELS:
        old=smoke.build(smoke.SOURCE,job,model)
        new=run.build_stepwise(job,model,8000)
        assert new['max_tokens']==8000
        new['max_tokens']=4000
        assert new==old


def test_8000_reservation_covers_higher_limit(payload,tmp_path):
    payload['max_tokens']=8000
    model=payload['model'];_,_,ip,op=smoke.MODELS[model]
    p=run.Provider(tmp_path/'calls',budget=25,prior=0,session=Session(),max_output=8000)
    p.ask(payload,'test')
    held=smoke.read(tmp_path/'calls/000001/request.json')['reservation_usd']
    assert held==pytest.approx(1.1*(smoke.CONTEXT*ip+8000*op))
    assert held>smoke.reservation(model)


def test_truncation_is_replayed_as_failure_without_retry(payload,tmp_path):
    payload['max_tokens']=8000
    session=Session(failure='length')
    p=run.Provider(tmp_path/'calls',budget=25,prior=.1,session=session,max_output=8000,record_length_failures=True)
    with pytest.raises(run.TruncatedResponse):p.ask(payload,'failed')
    assert not (p.out/'000001/decision.json').exists()
    session.failure=None
    p.ask(payload,'next-independent-sample')
    assert session.posts==2 and p.charged==pytest.approx(.102)
    q=run.Provider(p.out,budget=25,prior=.1,max_output=8000,record_length_failures=True)
    with pytest.raises(run.TruncatedResponse):q.ask(payload,'failed')
    assert q.ask(payload,'next-independent-sample')['action']=='KEEP'
    assert q.sent==0 and q.charged==pytest.approx(.102)
    with pytest.raises(ValueError,match='Invalid recorded truncation'):
        run.Provider(p.out,budget=25,prior=.1,max_output=4000,record_length_failures=True)


@pytest.mark.parametrize('failure',['transport','missing_cost'])
def test_8000_unknown_failure_still_blocks_resume(payload,tmp_path,failure):
    payload['max_tokens']=8000
    p=run.Provider(tmp_path/'calls',budget=25,prior=0,session=Session(failure=failure),max_output=8000,record_length_failures=True)
    with pytest.raises((ValueError,TimeoutError)):p.ask(payload,'sample')
    assert not (p.out/'000001/failure.json').exists()
    with pytest.raises(ValueError,match='Failed/uncertain'):
        run.Provider(p.out,budget=25,prior=0,max_output=8000,record_length_failures=True)


def test_full_attempt_accuracy_includes_failure_without_imputed_action():
    targets=[dict(job_id='a',expert_action='KEEP'),dict(job_id='b',expert_action='DISCARD')]
    predictions=[dict(job_id='a',action='KEEP')]
    partial=run.score_block(targets,predictions,[])
    assert partial['accuracy'] is None and partial['unattempted']==1
    score=run.score_block(targets,predictions,[dict(job_id='b',failure='length')])
    assert score['accuracy']==.5 and score['accuracy_on_valid']==1 and score['coverage']==.5
    assert score['failed']==1 and score['unattempted']==0
    assert score['per_class']['DISCARD']['failed_support']==1
    with pytest.raises(ValueError):run.score_block(targets,predictions,[dict(job_id='a',failure='length')])


def test_truncated_rollout_has_checkpoint_but_no_terminal_score(tmp_path,fast,monkeypatch):
    p=run.Provider(tmp_path/'calls',budget=25,prior=0,session=Session(failure='length'),max_output=8000,record_length_failures=True)
    c=run.Controller(manager(),run.minimal.DATASETS[0],tmp_path/'controller',provider=p,
        budget_usd=25,sampling_rate=30000,model=run.MODELS[0])
    before=c.state_hash()
    def forbidden(*a,**kw):raise AssertionError('Partial trajectory must not be scored')
    monkeypatch.setattr(run,'score_terminal',forbidden)
    result=run.evaluate_rollout(c,dict(dataset_id=c.dataset_id),c.manager.assigns.copy(),allow_truncation=True)
    assert result['status']=='stopped_truncated' and result['final_metrics'] is None
    assert c.state_hash()==before and (c.out/'stopped.npz').exists()
    assert not (c.out/'terminal.npz').exists()


def test_cost_history_carries_all_expenses_without_predictions(tmp_path,monkeypatch,payload):
    parent,_=retry_parent(tmp_path,monkeypatch,payload)
    attempt=parent/'attempt_001';attempt.mkdir()
    run.write(attempt/'summary.json',dict(status='stopped',charged_or_reserved_including_smoke=.102))
    history=run.historical_costs(parent)
    assert history['total_charged_or_reserved_usd']==pytest.approx(.102)
    assert history['successful_predictions_reused']==0


def test_8000_requires_history_and_cannot_reuse_4000(tmp_path):
    with pytest.raises(ValueError,match='carry prior'):run.prepare(tmp_path/'out',max_output=8000)
    with pytest.raises(ValueError,match='Legacy retry'):run.prepare(tmp_path/'out',retry_from=tmp_path/'old',max_output=8000)


def test_campaign_continues_after_stepwise_and_channel_truncation(tmp_path,monkeypatch,payload):
    jobs=[dict(job_id='a',dataset_id='CH31',phase='phase1'),dict(job_id='b',dataset_id='CH31',phase='phase1')]
    source=tmp_path/'source';source.mkdir();run.write(source/'manifest.json',dict(stepwise=dict(jobs=jobs)))
    monkeypatch.setattr(run,'SOURCE',source)
    def build(job,model,max_output):
        r=copy.deepcopy(payload);r['model']=model;r['max_tokens']=max_output
        return r
    manifest=dict(budget_usd=25,prior_charged_or_reserved_usd=.1,max_output_tokens=8000,
        record_length_failures=True,datasets=[dict(dataset_id='CH31'),dict(dataset_id='CH3')],
        requests=[dict(model=model,job_id=j['job_id'],sha256=run.digest(build(j,model,8000))) for model in run.MODELS for j in jobs])
    monkeypatch.setattr(run,'verify',lambda out:manifest)
    monkeypatch.setattr(run,'build_stepwise',build)
    monkeypatch.setattr(run,'check_endpoints',lambda *a:None)
    class FirstLength(Session):
        def post(self,*a,**kw):
            self.failure='length' if self.posts==0 else None
            return super().post(*a,**kw)
    session=FirstLength();real_provider=run.Provider
    monkeypatch.setattr(run,'Provider',lambda *a,**kw:real_provider(*a,session=session,**kw))
    monkeypatch.setattr(run.minimal,'load_actor',lambda row:(manager(),30000))
    class FakeController:
        def __init__(self,actor,dataset_id,out,**kw):
            self.out=out;out.mkdir(parents=True);self.status='running';self.dataset_id=dataset_id
        def run(self):
            if self.dataset_id=='CH31':
                self.status='stopped'
                raise run.minimal.ControllerStop('fixture') from run.TruncatedResponse('fixture')
            self.status='completed'
        def state_hash(self):return 'fixture'
    monkeypatch.setattr(run,'Controller',FakeController)
    monkeypatch.setattr(run,'score_terminal',lambda c,row,initial:dict(dataset_id=row['dataset_id'],status='completed'))
    targets=[dict(j,expert_action='KEEP') for j in jobs]
    monkeypatch.setattr(run,'score_stepwise',lambda p,f=():dict(overall=run.score_block(targets,p,f)))
    out=tmp_path/'run';out.mkdir()
    summary=run.execute(out)
    assert summary['status']=='completed_with_failures' and session.posts==4
    assert summary['predictions_completed']=={run.MODELS[0]:1,run.MODELS[1]:2}
    assert summary['stepwise_failures']=={run.MODELS[0]:1,run.MODELS[1]:0}
    assert summary['completed_rollouts']=={m:1 for m in run.MODELS}
    assert summary['stopped_rollouts']=={m:1 for m in run.MODELS}
    assert summary['charged_or_reserved_including_smoke']==pytest.approx(.104)
    # Resumption replays both valid and failed outcomes without additional API calls.
    resumed=run.execute(out,resume=True)
    assert resumed['status']=='completed_with_failures' and session.posts==4
    assert resumed['new_http_calls_this_attempt']==0


def rate_limited_parent(tmp_path,monkeypatch,payload,code=429):
    payload['max_tokens']=8000
    parent=tmp_path/'rate_parent';parent.mkdir()
    class LimitedSession(Session):
        limited=False
        def post(self,*a,**kw):
            if not self.limited:return super().post(*a,**kw)
            self.posts+=1
            response=Response(dict(error=dict(code=code,message='Provider returned error',metadata=dict(
                provider_name='DeepInfra',provider_error_code='engine_overloaded',limit_source='upstream_provider_shared_pool'))))
            response.status_code=code
            return response
    session=LimitedSession()
    p=run.Provider(parent/'calls',budget=25,prior=.1,session=session,max_output=8000,record_length_failures=True)
    tag=f'{payload["model"]}/stepwise/job'
    p.ask(payload,tag)
    session.failure='length'
    with pytest.raises(run.TruncatedResponse):p.ask(payload,tag)
    session.limited=True
    with pytest.raises(ValueError,match=f'HTTP {code}'):p.ask(payload,tag)
    manifest=dict(prior_charged_or_reserved_usd=.1,max_output_tokens=8000,record_length_failures=True,
                  requests=[dict(model=payload['model'],job_id='job',sha256=run.digest(payload))]*3)
    run.write(parent/'manifest.json',manifest)
    (parent/'attempt_001').mkdir()
    run.write(parent/'attempt_001/summary.json',dict(status='stopped',charged_or_reserved_including_smoke=p.charged))
    monkeypatch.setattr(run,'ROOT',tmp_path)
    monkeypatch.setattr(run,'verify',lambda out,archived=False:manifest)
    return parent,tag,p.charged


def test_explicit_429_retry_preserves_truncations_and_reservations(tmp_path,monkeypatch,payload):
    parent,tag,total=rate_limited_parent(tmp_path,monkeypatch,payload)
    lineage,folders=run.rate_limit_lineage(parent)
    assert lineage['valid_outcomes_reused']==1 and lineage['truncations_reused']==1
    assert len(folders)==2 and lineage['cumulative_before_retry_usd']==pytest.approx(total)
    calls=tmp_path/'child_calls';calls.mkdir()
    for f in folders:run.shutil.copytree(f,calls/f.name)
    session=Session()
    q=run.Provider(calls,budget=25,prior=lineage['inherited_prior_usd']+lineage['retained_429_reservation_usd'],
                   session=session,max_output=8000,record_length_failures=True)
    assert q.charged==pytest.approx(total)
    q.ask(payload,tag)
    with pytest.raises(run.TruncatedResponse):q.ask(payload,tag)
    assert session.posts==0
    q.ask(payload,tag)
    assert session.posts==1 and q.charged==pytest.approx(total+.001)
    assert not (parent/'calls/000003/settled.json').exists()


@pytest.mark.parametrize('code',[401,500,503])
def test_429_recovery_does_not_authorize_other_api_errors(tmp_path,monkeypatch,payload,code):
    parent,_,_=rate_limited_parent(tmp_path,monkeypatch,payload,code)
    with pytest.raises(ValueError,match='Not a known'):run.rate_limit_lineage(parent)


def test_429_recovery_cannot_change_protocol_or_combine_histories(tmp_path):
    with pytest.raises(ValueError,match='cannot change'):
        run.prepare(tmp_path/'out',rate_limit_from=tmp_path/'parent',max_output=4000)
    with pytest.raises(ValueError,match='cannot change'):
        run.prepare(tmp_path/'out',rate_limit_from=tmp_path/'parent',cost_history_from=tmp_path/'other',max_output=8000)


class Overloaded(Session):
    def __init__(self, count=1, code=429, failure=None, kind='engine_overloaded', header=None):
        super().__init__(failure=failure)
        self.count,self.code,self.kind,self.header=count,code,kind,header
        self.hashes=[]
    def post(self,*a,**kw):
        self.hashes.append(run.digest(kw['json']))
        if self.posts >= self.count: return super().post(*a,**kw)
        self.posts += 1
        r=Response(dict(error=dict(code=self.code,metadata=dict(provider_name='DeepInfra',
            provider_error_code=self.kind,limit_source='upstream_provider_shared_pool'))))
        r.status_code=self.code
        r.headers={} if self.header is None else {'Retry-After':self.header}
        return r


def test_bounded_overload_retry_same_request_and_replay(tmp_path,monkeypatch,payload):
    payload['max_tokens']=8000
    sleeps=[];monkeypatch.setattr(run.time,'sleep',sleeps.append)
    session=Overloaded(count=2)
    p=run.Provider(tmp_path/'calls',budget=25,prior=.1,session=session,max_output=8000,
                   record_length_failures=True,retry_overload=True)
    decision=p.ask(payload,'qwen/qwen3.5-9b/stepwise/job')
    held=run.read(p.out/'000001/request.json')['reservation_usd']
    assert sleeps==[30,60] and session.posts==3 and len(set(session.hashes))==1
    assert p.charged==pytest.approx(.101+2*held) and p.known==pytest.approx(.001)
    assert run.retry_reservations(p.out/'000001')==pytest.approx(2*held)
    q=run.Provider(p.out,budget=25,prior=.1,max_output=8000,record_length_failures=True,retry_overload=True)
    assert q.ask(payload,'qwen/qwen3.5-9b/stepwise/job')==decision
    assert q.sent==0 and q.charged==pytest.approx(p.charged)
    b=run.cost_breakdown(tmp_path,.1)['by_model_and_stage'][payload['model']]['stepwise']
    assert b['calls']==3 and b['overload_retries']==2
    assert b['unsettled_reservation_usd']==pytest.approx(2*held)


def test_bounded_overload_exhaustion(tmp_path,monkeypatch,payload):
    sleeps=[];monkeypatch.setattr(run.time,'sleep',sleeps.append)
    s=Overloaded(count=10)
    p=run.Provider(tmp_path/'calls',budget=25,prior=0,session=s,retry_overload=True)
    with pytest.raises(ValueError,match='HTTP 429 upstream overload; stopped after 3 retries'):
        p.ask(payload,'job')
    assert s.posts==4 and sleeps==[30,60,120]
    assert p.charged==pytest.approx(4*smoke.reservation(payload['model']))
    with pytest.raises(ValueError,match='Failed/uncertain'):
        run.Provider(p.out,budget=25,prior=0,retry_overload=True)


@pytest.mark.parametrize('code,kind',[(401,'engine_overloaded'),(500,'engine_overloaded'),
                                   (503,'engine_overloaded'),(429,'unknown')])
def test_retry_does_not_include_other_http_errors(tmp_path,monkeypatch,payload,code,kind):
    monkeypatch.setattr(run.time,'sleep',lambda *a:pytest.fail('Must not retry'))
    s=Overloaded(code=code,kind=kind)
    p=run.Provider(tmp_path/'calls',budget=25,prior=0,session=s,retry_overload=True)
    with pytest.raises(ValueError,match=f'HTTP {code}'):p.ask(payload,'job')
    assert s.posts==1


@pytest.mark.parametrize('failure',['length','missing_cost','transport'])
def test_retry_does_not_include_truncation_cost_or_timeout(tmp_path,monkeypatch,payload,failure):
    monkeypatch.setattr(run.time,'sleep',lambda *a:pytest.fail('Must not retry'))
    s=Session(failure=failure)
    p=run.Provider(tmp_path/'calls',budget=25,prior=0,session=s,retry_overload=True,record_length_failures=True)
    with pytest.raises((ValueError,TimeoutError)):p.ask(payload,'job')
    assert s.posts==1


def test_overload_retry_checks_remaining_budget(tmp_path,monkeypatch,payload):
    monkeypatch.setattr(run.time,'sleep',lambda *a:pytest.fail('Must not retry'))
    held=smoke.reservation(payload['model']);s=Overloaded()
    p=run.Provider(tmp_path/'calls',budget=25,prior=25-held-.0001,session=s,retry_overload=True)
    with pytest.raises(ValueError,match='insufficient budget for retry'):p.ask(payload,'job')
    assert s.posts==1 and run.retry_reservations(p.out/'000001')==0
    assert p.charged==pytest.approx(24.9999)


def test_retry_after_respected_and_bounded(tmp_path,monkeypatch,payload):
    sleeps=[];monkeypatch.setattr(run.time,'sleep',sleeps.append)
    p=run.Provider(tmp_path/'calls',budget=25,prior=0,session=Overloaded(header='80'),retry_overload=True)
    p.ask(payload,'job');assert sleeps==[80]
    s=Overloaded(header='301')
    q=run.Provider(tmp_path/'other',budget=25,prior=0,session=s,retry_overload=True)
    with pytest.raises(ValueError,match='bounded wait'):q.ask(payload,'job')
    assert s.posts==1


def test_retry_history_tamper_rejected(tmp_path,monkeypatch,payload):
    monkeypatch.setattr(run.time,'sleep',lambda *a:None)
    p=run.Provider(tmp_path/'calls',budget=25,prior=0,session=Overloaded(),retry_overload=True)
    p.ask(payload,'job')
    f=p.out/'000001/overload_attempts/001/response.json'
    f.write_text(json.dumps(dict(error='tampered')))
    with pytest.raises(ValueError,match='Overload attempt changed'):
        run.Provider(p.out,budget=25,prior=0,retry_overload=True)


def test_nested_retry_cost_history_matches_ledger(tmp_path,monkeypatch,payload):
    monkeypatch.setattr(run.time,'sleep',lambda *a:None)
    parent=tmp_path/'parent';parent.mkdir()
    p=run.Provider(parent/'calls',budget=25,prior=.1,session=Overloaded(count=2),retry_overload=True)
    p.ask(payload,'job')
    manifest=dict(prior_charged_or_reserved_usd=.1)
    run.write(parent/'manifest.json',manifest)
    (parent/'attempt_001').mkdir()
    run.write(parent/'attempt_001/summary.json',dict(status='completed',charged_or_reserved_including_smoke=p.charged))
    monkeypatch.setattr(run,'ROOT',tmp_path)
    monkeypatch.setattr(run,'verify',lambda out,archived=False:manifest)
    history=run.historical_costs(parent)
    assert history['total_charged_or_reserved_usd']==pytest.approx(p.charged)
    assert 'calls/000001/overload_attempts/002/response.json' in history['file_hashes']


def test_parse_failure_not_retried_with_overload_enabled(tmp_path,monkeypatch,payload):
    monkeypatch.setattr(run.time,'sleep',lambda *a:pytest.fail('Must not retry'))
    s=Session(action='INVALID')
    p=run.Provider(tmp_path/'calls',budget=25,prior=0,session=s,retry_overload=True)
    with pytest.raises(ValueError):p.ask(payload,'job')
    assert s.posts==1
