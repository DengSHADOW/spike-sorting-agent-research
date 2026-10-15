"""Offline tests for the Qwen 27B tuned-sampling runner. A fake session stands in for OpenRouter."""
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
import run_qwen27b_tuned as q  # noqa: E402
from test_openrouter_baseline import Session, fast, manager, no_network  # noqa: E402,F401

URLS = ['data:image/png;base64,YQ==']


def test_stepwise_request_only_adds_sampling():
    job = q.read(q.transport.SOURCE / 'manifest.json')['stepwise']['jobs'][0]
    base = q.ob.build_stepwise(job, q.MODEL, 4000)
    tuned = q.stepwise_request(job)
    assert {k: v for k, v in tuned.items() if k not in q.SAMPLING} == base
    assert tuned['temperature'] == 1.0 and tuned['top_k'] == 20 and tuned['presence_penalty'] == 1.5
    assert tuned['max_tokens'] == 4000 and tuned['provider']['only'] == ['deepinfra/fp8']
    with pytest.raises(ValueError):
        q.tune(tuned)  # sampling cannot be applied twice or overridden


def test_variants_differ_only_in_prompt(fast):
    obs, urls, _, _ = q.minimal.observe(manager(), 'phase1', 1)
    a = q.rollout_request('minimal', 'phase1', obs, urls)
    b = q.rollout_request('skill_v0', 'phase1', obs, urls)
    assert a['messages'][0]['content'] == q.minimal.build_request('phase1', obs, urls)['instructions']
    assert b['messages'][0]['content'] == (q.contract.CONFIG_DIR / 'system_v1.txt').read_text()
    assert b['messages'][1]['content'][0]['text'].startswith('DOMAIN SKILL\n')
    drop = lambda r: {k: v for k, v in r.items() if k not in ('messages', 'response_format')}
    schema = lambda r: r['response_format']['json_schema']['schema']
    assert drop(a) == drop(b) and schema(a) == schema(b)  # only the schema name differs
    with pytest.raises(ValueError):
        q.rollout_request('skill_v1', 'phase1', obs, urls)


def controller(cls, tmp_path, provider, name='c'):
    return cls(manager(), q.minimal.DATASETS[0], tmp_path / name, provider=provider, budget_usd=25, sampling_rate=30000.)


@pytest.mark.parametrize('cls', [q.MinimalTuned, q.SkillTuned])
def test_rollout_completes_and_replays_without_resend(tmp_path, fast, cls):
    p = q.ob.Provider(tmp_path / 'calls', budget=25, prior=0, session=Session(), max_output=4000, record_length_failures=True)
    c = controller(cls, tmp_path, p, 'first')
    c.run()
    r = q.ob.Provider(tmp_path / 'calls', budget=25, prior=0, max_output=4000, record_length_failures=True)
    d = controller(cls, tmp_path, r, 'resumed')
    d.run()
    assert c.status == d.status == 'completed' and c.state_hash() == d.state_hash() and r.sent == 0
    assert q.read(c.out / 'harness.json')['variant'] == cls.VARIANT


@pytest.mark.parametrize('cls', [q.MinimalTuned, q.SkillTuned])
def test_truncation_fails_the_channel_without_action(tmp_path, fast, monkeypatch, cls):
    p = q.ob.Provider(tmp_path / 'calls', budget=25, prior=0, session=Session(failure='length'),
                      max_output=4000, record_length_failures=True)
    c = controller(cls, tmp_path, p)
    before = c.state_hash()
    monkeypatch.setattr(q.ob, 'score_terminal', lambda *a: (_ for _ in ()).throw(AssertionError('scored')))
    result = q.ob.evaluate_rollout(c, dict(dataset_id=c.dataset_id), c.manager.assigns.copy(), allow_truncation=True)
    assert result['status'] == 'stopped_truncated' and c.state_hash() == before and p.sent == 1


def test_other_failures_stop_instead_of_skipping(tmp_path, fast):
    p = q.ob.Provider(tmp_path / 'calls', budget=25, prior=0, session=Session(failure='missing_cost'),
                      max_output=4000, record_length_failures=True)
    c = controller(q.SkillTuned, tmp_path, p)
    with pytest.raises(q.minimal.ControllerStop):
        q.ob.evaluate_rollout(c, dict(dataset_id=c.dataset_id), c.manager.assigns.copy(), allow_truncation=True)


def test_prepare_carries_all_prior_costs(tmp_path):
    m = q.prepare(tmp_path / 'run')
    assert m['prior_charged_or_reserved_usd'] > 0.46 and len(m['stepwise_requests']) == 294
    assert m['max_output_tokens'] == 4000 and m['sampling'] == q.SAMPLING
    assert q.verify(tmp_path / 'run') == q.read(tmp_path / 'run' / 'manifest.json')
    with pytest.raises(ValueError):
        q.prepare(tmp_path / 'run')


def test_budget_amendment_chain(tmp_path, monkeypatch):
    out = tmp_path / 'run'
    q.prepare(out)
    with pytest.raises(ValueError):
        q.amend_budget(out, 60.)  # above the authorized maximum
    with pytest.raises(ValueError):
        q.amend_budget(out, 20.)  # never lowers or keeps the cap
    q.amend_budget(out, 50.)
    assert q.verify(out)['budget_usd'] == 50.
    monkeypatch.setattr(q, 'code_hashes', lambda: {'changed': 'x'})
    with pytest.raises(ValueError):
        q.verify(out)  # code changed again after the amendment


def test_provider_cap_above_frozen_25(tmp_path):
    p = q.Provider(tmp_path / 'calls', budget=50., prior=10., session=Session(), max_output=4000, record_length_failures=True)
    assert p.budget == 50. and p.charged == 10.
    with pytest.raises(ValueError):
        q.Provider(tmp_path / 'other', budget=51., prior=0)


@pytest.mark.parametrize('cls', [q.MinimalTuned, q.SkillTuned])
def test_leaf_split_fails_only_the_channel(tmp_path, fast, monkeypatch, cls):
    p = q.ob.Provider(tmp_path / 'calls', budget=25, prior=0, session=Session(action='SPLIT'),
                      max_output=4000, record_length_failures=True)
    c = controller(cls, tmp_path, p)
    monkeypatch.setattr(q.ob, 'score_terminal', lambda *a: (_ for _ in ()).throw(AssertionError('scored')))
    result = q.evaluate(c, dict(dataset_id=c.dataset_id), c.manager.assigns.copy())
    assert result['status'] == 'stopped_invalid_action' and result['final_metrics'] is None
    assert (c.out / 'stopped.npz').exists()


def test_other_execution_failures_still_stop(tmp_path, fast, monkeypatch):
    p = q.ob.Provider(tmp_path / 'calls', budget=25, prior=0, session=Session(action='SPLIT'),
                      max_output=4000, record_length_failures=True)
    c = controller(q.SkillTuned, tmp_path, p)
    monkeypatch.setattr(c.manager, 'split_last_merge', lambda cid: (_ for _ in ()).throw(ValueError('other')))
    with pytest.raises(q.minimal.ControllerStop):
        q.evaluate(c, dict(dataset_id=c.dataset_id), c.manager.assigns.copy())


def test_policy_amendment_keeps_budget_chain(tmp_path):
    out = tmp_path / 'run'
    q.prepare(out)
    q.amend_budget(out, 50.)
    q.amend_policy(out)
    assert q.verify(out)['budget_usd'] == 50.
