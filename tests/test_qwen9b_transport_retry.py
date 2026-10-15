"""Offline recovery checks; all HTTP and credentials are forbidden/faked."""
import copy
import json
import pytest

from scripts.run import retry_qwen9b_transport as retry

base = retry.base


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Unexpected network/key access')
    monkeypatch.setattr('socket.socket.connect', forbidden)
    monkeypatch.setattr(base.transport, 'make_session', forbidden)


@pytest.fixture
def payload():
    return dict(model=base.MODEL,max_tokens=8000,reasoning=base.reasoning_config(),**base.SAMPLING,
        messages=[dict(role='system',content='OFFLINE'),dict(role='user',content=[dict(type='text',text='OFFLINE')])],
        response_format=dict(json_schema=dict(schema=dict(properties=dict(action=dict(enum=['KEEP','SPLIT','DISCARD']))))))


class Response:
    headers = {}
    def __init__(self,mode): self.mode=mode;self.status_code=429 if mode=='429' else 200
    def json(self):
        if self.mode=='429':
            return dict(error=dict(code=429,metadata=dict(provider_name='DeepInfra',
                provider_error_code='engine_overloaded',limit_source='upstream_provider_shared_pool')))
        return dict(model=base.MODEL,provider='DeepInfra',usage=dict(cost=.001,completion_tokens=8000 if self.mode=='length' else 50),
            choices=[dict(finish_reason='length' if self.mode=='length' else 'stop',message=dict(reasoning='OFFLINE',
                content=json.dumps(dict(action='KEEP',rationale='OFFLINE'))))])


class Session:
    def __init__(self,modes):self.modes=list(modes);self.posts=0
    def post(self,*args,**kwargs):
        self.posts+=1
        return Response(self.modes.pop(0))
    def close(self):pass


@pytest.fixture
def exhausted(tmp_path,payload,monkeypatch):
    s=Session(['ok']+['429']*4)
    p=base.JournalProvider(tmp_path/'calls',budget=25,prior=.4,session=s,sleeper=lambda _:None)
    p.ask(payload,'first','minimal')
    with pytest.raises(base.TransportStopped):p.ask(payload,'second','minimal')
    base.write(tmp_path/'manifest.json',dict(prior_charged_or_reserved_usd=.4))
    base.write(tmp_path/'execution_status.json',dict(status='stopped',error=dict(cause='TransportStopped'),costs=p.ledger()))
    monkeypatch.setattr(base,'verify',lambda out:base.read(out/'manifest.json'))
    return tmp_path


def provider(out,modes=(),prior=.4,allowances=None):
    s=Session(modes)
    p=retry.RetryProvider(out/'calls',budget=25,prior=prior,session=s,sleeper=lambda _:None,
        allowances={'000002':8} if allowances is None else allowances)
    return p,s


def test_offline_plan_preserves_files(exhausted):
    before={str(f):base.sha(f) for f in exhausted.rglob('*') if f.is_file()}
    result=retry.plan(exhausted)
    assert result['call']=='000002' and result['max_attempts']==8
    assert result['cumulative_charged_or_reserved_usd']==pytest.approx(.401+4*base.reservation())
    assert before=={str(f):base.sha(f) for f in exhausted.rglob('*') if f.is_file()}


def test_replay_then_only_one_new_paid_call(exhausted,payload):
    p,s=provider(exhausted,['ok'])
    p.ask(payload,'first','minimal');assert s.posts==0
    p.ask(payload,'second','minimal');assert s.posts==1
    assert (exhausted/'calls/000002/attempts/005/decision.json').exists() is False
    assert (exhausted/'calls/000002/decision.json').exists()
    assert p.charged==pytest.approx(.402+4*p.held)
    q,qs=provider(exhausted)
    q.ask(payload,'first','minimal');q.ask(payload,'second','minimal')
    assert qs.posts==0 and q.charged==p.charged


def test_retry_requires_allowance(exhausted,payload):
    p,s=provider(exhausted,allowances={})
    p.ask(payload,'first','minimal')
    with pytest.raises(base.TransportStopped):p.ask(payload,'second','minimal')
    assert s.posts==0


def test_four_more_attempts_not_infinite(exhausted,payload):
    p,s=provider(exhausted,['429']*8)
    p.ask(payload,'first','minimal')
    with pytest.raises(base.TransportStopped):p.ask(payload,'second','minimal')
    assert s.posts==4 and p.charged==pytest.approx(.401+8*p.held)
    q,qs=provider(exhausted)
    q.ask(payload,'first','minimal')
    with pytest.raises(base.TransportStopped):q.ask(payload,'second','minimal')
    assert qs.posts==0


def test_truncation_still_never_retried(exhausted,payload):
    p,s=provider(exhausted,['length','ok'])
    p.ask(payload,'first','minimal')
    with pytest.raises(base.old.TruncatedResponse):p.ask(payload,'second','minimal')
    assert s.posts==1 and not (exhausted/'calls/000002/decision.json').exists()


def test_changed_request_stops_before_http(exhausted,payload):
    p,s=provider(exhausted,['ok']);p.ask(payload,'first','minimal')
    payload['messages'][0]['content']='CHANGED'
    with pytest.raises(ValueError,match='divergence'):p.ask(payload,'second','minimal')
    assert s.posts==0


def test_budget_includes_old_unknown_costs(exhausted,payload):
    p,s=provider(exhausted,['ok'],prior=24.87);p.ask(payload,'first','minimal')
    with pytest.raises(ValueError,match='budget'):p.ask(payload,'second','minimal')
    assert s.posts==0


def test_refuse_nontransport_stop(exhausted):
    obj=base.read(exhausted/'execution_status.json');obj['error']['cause']='ValueError'
    base.atomic(exhausted/'execution_status.json',obj)
    with pytest.raises(ValueError,match='transport stop'):retry.plan(exhausted)


def test_saved_round_tamper_detected(exhausted):
    record=retry.plan(exhausted)
    folder=exhausted/'transport_recovery/round_001';folder.mkdir(parents=True)
    from pathlib import Path
    (folder/'runner.py').write_bytes(Path(retry.__file__).read_bytes())
    base.write(folder/'authorization.json',record)
    assert retry.saved_rounds(exhausted)=={'000002':8}
    target=exhausted/'calls/000002/attempts/004/http.json'
    obj=base.read(target);obj['status']=200;base.atomic(target,obj)
    with pytest.raises(ValueError,match='evidence changed'):retry.saved_rounds(exhausted)
