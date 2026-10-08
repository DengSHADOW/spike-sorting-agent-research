"""Offline checks: SDK, credentials and network forbidden throughout."""
import copy
import json
import socket
from pathlib import Path
import pytest
from scripts.run import run_skill_ablation as a
from scripts.run import run_skill_replay as r


def forbidden(*args, **kwargs):
    raise AssertionError('Network/credentials forbidden')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(r, 'client_factory', forbidden)
    monkeypatch.setattr(a, 'enable_credentials', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


def test_real_35_dry_plan_invariance():
    spec, payloads, cases, skills = a.build_plan()
    assert len(cases) == 35 and len(spec['jobs']) == 210
    assert spec['repeats'] == 1 and spec['budget_cap_usd'] == 55
    assert spec['versions'] == ['v0', 'S1', 'S2', 'S3', 'S4', 'S5']
    for case in cases:
        common = r.canonical(r.without_skill(payloads[(case['case_id'], 'v0')]))
        for v in spec['versions']:
            req = payloads[(case['case_id'], v)]
            assert r.canonical(r.without_skill(req)) == common
            assert req['reasoning'] == {'effort': 'high'}
            assert req['max_output_tokens'] == 4000
    for name in ('S1', 'S3', 'S5'):
        assert skills[name]['phase1'].startswith(skills['v0']['phase1'])
    assert skills['S4']['phase1'] == skills['v0']['phase1']
    assert skills['S1']['phase2'] == skills['v0']['phase2']
    assert spec['jobs'] != sorted(spec['jobs'], key=lambda x: x['version'])
    assert 'base64' not in json.dumps(spec)


def test_ablation_budget_checks_all_calls_before_client(tmp_path, monkeypatch):
    # This synthetic budget test must not inspect the real running campaign.
    monkeypatch.setattr(a, 'ROOT', tmp_path)
    manifest = tmp_path/'manifest.json'; manifest.write_text('{}')
    job = dict(job_id='x', request_sha256='abc')
    spec = dict(budget_cap_usd=.20, scoring_sha256='rules', jobs=[job])
    row = dict(job, input_tokens=100, conservative_reserved_usd=r.reservation(100))
    r.write_new(tmp_path/'estimate.json', dict(manifest_sha256=r.file_hash(manifest),
                                            scoring_sha256='rules', per_call=[row]))
    with pytest.raises(r.BudgetStop, match='Whole-round'):
        a.whole_round_gate(tmp_path, spec, .20)
    with pytest.raises(r.ReplayStop, match='Budget differs'):
        a.whole_round_gate(tmp_path, spec, 50)


def test_candidate_source_hash_change_rejected(tmp_path, monkeypatch):
    plan = r.read_json(a.ABLATION); plan['base_sha256'] = 'wrong'
    path = tmp_path/'plan.json'; path.write_text(json.dumps(plan))
    monkeypatch.setattr(a, 'ABLATION', path)
    with pytest.raises(r.ReplayStop, match='source changed'):
        a.candidate_skills()


def test_missing_execution_authorization_still_rejected(tmp_path):
    with pytest.raises(r.ReplayStop, match='authorization'):
        r.authorization(tmp_path, 50, {'mode': 'synchronous'})
