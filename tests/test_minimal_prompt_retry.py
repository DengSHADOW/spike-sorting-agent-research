"""Offline audit of retained failed-request reservation and manual retry gates."""
import pytest
from scripts.run import retry_minimal_prompt_baseline as r


def ledger():
    return [dict(event='carry_forward',prior_conservative_usd=49.,prior_calls=1200),
            dict(event='reserved',call=1201,reserved_usd=.25),
            dict(event='usage',call=1201,conservative_usd=.05),
            dict(event='reserved',call=1202,reserved_usd=.25),
            dict(event='failure',call=1202,status_code=503)]


def test_failed_reservation_is_not_refunded():
    assert r.audit_ledger(ledger(),dict(conservative_charged_or_reserved_usd=49.30,inference_calls=1202))==.25
    with pytest.raises(ValueError,match='remain reserved'):
        r.audit_ledger(ledger(),dict(conservative_charged_or_reserved_usd=49.05,inference_calls=1202))


def test_not_any_failure_can_be_retried():
    rows=ledger();rows[-1]['status_code']=401
    with pytest.raises(ValueError,match='503'):r.audit_ledger(rows,{})


@pytest.mark.parametrize('bad',['missing','budget','hash','started'])
def test_explicit_retry_authorization(tmp_path,bad,monkeypatch):
    monkeypatch.setattr(r.base.infra,'make_client',lambda:pytest.fail('client'))
    r.base.write_json(tmp_path/'manifest.json',{})
    auth=dict(scope=r.SCOPE,cumulative_budget_usd=100,manifest_sha256=r.base.sha256(tmp_path/'manifest.json'))
    if bad=='budget':auth['cumulative_budget_usd']=150
    if bad=='hash':auth['manifest_sha256']='bad'
    if bad!='missing':r.base.write_json(tmp_path/'authorize_execute.json',auth)
    if bad=='started':r.base.write_json(tmp_path/'execution_started.json',{})
    with pytest.raises((ValueError,FileNotFoundError)):r.authorize(tmp_path)
