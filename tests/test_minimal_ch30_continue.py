"""Offline recovery gates and no double billing of cached decisions."""
import copy
from types import SimpleNamespace
import pytest
from scripts.run import continue_minimal_ch30 as r


def rows():
    return [dict(event='carry_forward',prior_conservative_usd=50.25,prior_calls=10,unsettled_reservation_usd=.25),
            dict(event='reserved',call=11,reserved_usd=.3),dict(event='usage',call=11,conservative_usd=.05)]


def status():
    return dict(conservative_charged_or_reserved_usd=50.30,inference_calls=11,inherited_unsettled_reservation_usd=.25)


def test_ledger_keeps_failed_reserve():
    r.audit_ledger(rows(),status())
    old=status();old['conservative_charged_or_reserved_usd']-=.25
    with pytest.raises(ValueError):r.audit_ledger(rows(),old)


@pytest.mark.parametrize('kind',['pending','duplicate','failure','missing_usage','lost_reserve','wrong_calls'])
def test_reject_bad_ledger(kind):
    data=rows();old=status()
    if kind=='pending':data.pop()
    if kind=='duplicate':data.append(data[1])
    if kind=='failure':data.append(dict(event='failure'))
    if kind=='missing_usage':data.pop(1)
    if kind=='lost_reserve':old['inherited_unsettled_reservation_usd']=0
    if kind=='wrong_calls':old['inference_calls']=12
    with pytest.raises(ValueError):r.audit_ledger(data,old)


@pytest.mark.parametrize('bad',['missing','budget','hash','started','nan','bool','ok'])
def test_authorization_before_client(tmp_path,monkeypatch,bad):
    monkeypatch.setattr(r.base.infra,'make_client',lambda:pytest.fail('client'))
    r.base.write_json(tmp_path/'manifest.json',dict(cumulative_budget_usd=150))
    auth=dict(scope=r.SCOPE,cumulative_budget_usd=150,manifest_sha256=r.base.sha256(tmp_path/'manifest.json'))
    if bad=='budget':auth['cumulative_budget_usd']=100
    if bad=='hash':auth['manifest_sha256']='bad'
    if bad!='missing':r.base.write_json(tmp_path/'authorize_execute.json',auth)
    if bad=='started':r.base.write_json(tmp_path/'execution_started.json',{})
    budget=float('nan') if bad=='nan' else True if bad=='bool' else 150
    if bad=='ok':r.authorize(tmp_path,budget)
    else:
        with pytest.raises((ValueError,FileNotFoundError)):r.authorize(tmp_path,budget)


def test_cached_decision_never_calls_live(tmp_path,monkeypatch):
    request={'a':1}; raw={'usage':{'input_tokens':2}}
    saved=dict(phase='phase1',observation={'cluster_id':3},context=None,state_sha256='state',request_sha256=r.base.digest(request))
    records=[dict(request=saved,raw=raw,folder=tmp_path)]
    live=SimpleNamespace(count=lambda _:pytest.fail('live count'),respond=lambda _:pytest.fail('live inference'))
    c=object.__new__(r.CachedController)
    c.provider=r.resume.PrefixProvider(records,live);c.status='ready';c.seen=set();c.calls=0;c.charged=0
    c.max_calls=10;c.frozen={};c.state_hash=lambda:'state';c.log=lambda *a,**k:None
    monkeypatch.setattr(r.base,'prompt_hashes',lambda:{})
    monkeypatch.setattr(r,'cached_request',lambda _:request)
    monkeypatch.setattr(r.base.accounting,'usage_cost',lambda _:dict(conservative_usd=.05))
    monkeypatch.setattr(r.base.accounting,'decision_from_response',lambda *a:dict(action='KEEP',rationale='saved'))
    assert c.ask('phase1',3)['action']=='KEEP'
    assert c.calls==1 and c.provider.index==1 and c.charged==.05
    assert raw==records[0]['raw']


def test_wrong_scheduler_stops_before_replay(monkeypatch):
    c=object.__new__(r.CachedController);c.status='ready';c.state_hash=lambda:'state'
    c.provider=SimpleNamespace(index=0,records=[dict(raw={},request=dict(phase='phase1',observation={'cluster_id':3},state_sha256='state'))])
    monkeypatch.setattr(r,'cached_request',lambda _:pytest.fail('wrong state must stop first'))
    with pytest.raises(ValueError,match='scheduler'):c.ask('phase1',4)


def test_collector_preserves_latest_completed_results(tmp_path,monkeypatch):
    source=tmp_path/'source';root=tmp_path/'root';out=tmp_path/'out'
    for d in (source,root,out):d.mkdir()
    latest={'datasets':[{'dataset_id':'CH20','status':'completed','final_metrics':{'F1':.36}}]}
    r.base.write_json(source/'score.json',latest);r.base.write_json(root/'manifest.json',{'datasets':[]})
    r.base.write_json(out/'manifest.json',{'cumulative_budget_usd':150})
    monkeypatch.setattr(r.base,'BASE',tmp_path)
    def collect(view,dest,st):
        assert r.read(view/'score.json')==latest
        report=tmp_path/'results'/out.name;report.mkdir(parents=True)
        (report/'REPORT.md').write_text('cumulative cap $100; Local replay of 44 saved CH20 decisions')
    monkeypatch.setattr(r.resume,'collect',collect)
    r.collect(source,root,out,{'inherited_unsettled_reservation_usd':.25})
    text=(tmp_path/'results'/out.name/'REPORT.md').read_text()
    assert '$150' in text and '1060 saved CH30' in text and '$0.2500000' in text
