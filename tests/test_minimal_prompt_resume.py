"""Offline continuation invariants; synthetic responses are not experiments."""
import json
from types import SimpleNamespace
import pytest
from scripts.run import resume_minimal_prompt_baseline as r


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def fail(*a,**kw): raise AssertionError('Unexpected network/client')
    monkeypatch.setattr('socket.socket.connect',fail)
    monkeypatch.setattr(r.base.infra,'make_client',fail)


def records():
    return [dict(request=dict(request_sha256=r.base.digest({'n':1})), raw={'usage':{'input_tokens':100}}),
            dict(request=dict(request_sha256=r.base.digest({'n':2})), raw=None)]


def test_cached_prefix_never_calls_live():
    class Live:
        def count(self,*a): pytest.fail('Cached count reached network')
        def respond(self,*a): pytest.fail('Cached response reached network')
    p=r.PrefixProvider(records(),Live())
    assert p.count({'n':1})==100
    assert p.respond({'n':1})=={'usage':{'input_tokens':100}}
    assert p.index==1


def test_offline_boundary_and_mismatch():
    p=r.PrefixProvider(records())
    with pytest.raises(ValueError):p.count({'n':999})
    p.count({'n':1})
    with pytest.raises(ValueError):p.respond({'n':999})
    p.respond({'n':1})
    with pytest.raises(r.ReplayBoundary):p.count({'n':2})
    assert p.boundary_verified


def test_only_unsent_suffix_reaches_live():
    calls=[]
    live=SimpleNamespace(count=lambda x:calls.append(('count',x)) or 110,
        respond=lambda x:calls.append(('respond',x)) or {'new':True})
    p=r.PrefixProvider(records(),live)
    p.count({'n':1});p.respond({'n':1})
    assert not calls
    assert p.count({'n':2})==110
    assert p.respond({'n':2})=={'new':True}
    assert calls==[('count',{'n':2}),('respond',{'n':2})]


@pytest.mark.parametrize('bad',['missing','budget','hash','scope','started'])
def test_authorization_refuses_mismatch(tmp_path,bad):
    r.base.write_json(tmp_path/'manifest.json',{})
    a=dict(scope=r.SCOPE,cumulative_budget_usd=100,manifest_sha256=r.base.sha256(tmp_path/'manifest.json'))
    if bad=='budget':a['cumulative_budget_usd']=50
    if bad=='hash':a['manifest_sha256']='wrong'
    if bad=='scope':a['scope']='old'
    if bad!='missing':r.base.write_json(tmp_path/'authorize_execute.json',a)
    if bad=='started':r.base.write_json(tmp_path/'execution_started.json',{})
    with pytest.raises((ValueError,FileNotFoundError)):r.authorize(tmp_path,100.)


def test_old_cost_not_reset_on_budget_extension():
    with pytest.raises(r.base.accounting.BudgetStop):
        r.base.accounting.reserve(99.9,100,2288,4000)
    assert r.base.accounting.reserve(49.7791335,100,2288,4000)>0
