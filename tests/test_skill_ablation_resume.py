import copy
import socket
import pytest
from scripts.run import resume_skill_ablation as resume
from scripts.run import run_skill_replay as r


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Offline: no client/credentials/network')
    monkeypatch.setattr(r, 'client_factory', forbidden)
    monkeypatch.setattr(resume.a, 'enable_credentials', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def test_actual_remaining_prefix_and_budget():
    source = r.ROOT/'output/curation_skill_round2_budget55_20261006'
    spec, _, _, estimate = resume.build(source)
    assert spec['planned_calls'] == 42
    assert spec['jobs'][0]['job_id'] == 'astra_CH30_00038--S3--r1'
    prior = {x['job_id'] for x in r.read_jsonl(source/'responses.jsonl')}
    assert not prior & {x['job_id'] for x in spec['jobs']}
    c = spec['continuation']
    assert c['retained_responses'] == 168
    assert c['unresolved_prior_reservation_usd'] == .2489375
    assert c['prior_charged_or_reserved_usd'] + c['whole_remaining_reserve_usd'] < 55
    assert c['available_budget_usd'] < 55
    assert estimate['token_count_calls'] == 0


def test_no_retry_after_saved_invalid_response_or_wrong_failure():
    source = r.ROOT/'output/curation_skill_round2_budget55_20261006'
    spec = r.read_json(source/'manifest.json')['spec']
    rows = r.read_jsonl(source/'responses.jsonl')
    status = r.read_json(source/'execution_status.json')
    bad = copy.deepcopy(rows); bad[-1]['response']['status'] = 'incomplete'
    with pytest.raises(r.ReplayStop): resume.remaining_jobs(spec, bad, status)
    bad = copy.deepcopy(status); bad['failure']['status_code'] = 429
    with pytest.raises(r.ReplayStop): resume.remaining_jobs(spec, rows, bad)
    with pytest.raises(r.ReplayStop): resume.remaining_jobs(spec, rows[:-1], status)
