"""All fixtures, authorization files and fake responses live in pytest temp dirs."""
import base64
import copy
import json
from pathlib import Path
import shutil
import socket
from types import SimpleNamespace

import pytest

from scripts.run import run_skill_replay as r
from scripts.analysis import score_skill_replay as s


def forbidden(*args, **kwargs):
    raise AssertionError('No client / network / credential access allowed')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(r, 'client_factory', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    config = root / 'configs/curation'
    config.mkdir(parents=True)
    for name in (*r.FROZEN, 'replay_scoring_v1.json'):
        shutil.copyfile(r.contract.CONFIG_DIR / name, config / name)
    for name in r.CODE:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(r.ROOT / name, target)
    monkeypatch.setattr(r, 'ROOT', root)
    monkeypatch.setattr(r, 'SCORING', config / 'replay_scoring_v1.json')
    monkeypatch.setattr(r.contract, 'CONFIG_DIR', config)
    case_dir = root / 'output/fixtures'
    (case_dir / 'actor').mkdir(parents=True)
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7XcAAAAASUVORK5CYII=')
    image = case_dir / 'test.png'
    image.write_bytes(png)
    hashes = {n: r.file_hash(config / n) for n in r.FROZEN}
    constraints = list(r.read_json(r.SCORING)['constraints'])
    comps = [{'1': 10}, {'0': 10}, {'0': 5, '1': 5}]
    cases = []
    for i in range(6):
        phase = 'phase2' if i == 2 else 'phase1'
        layout = 'local_four' if i < 3 else 'legacy_three'
        images = [{'path': str(image.relative_to(root)), 'sha256': r.file_hash(image)}] * (4 if i < 2 else 3)
        c = {'case_id': f'case{i}', 'dataset_id': 'cM2-e004_004-006_CH3', 'phase': phase,
             'target_type': 'recorded_expert_edit' if i < 3 else 'derived_terminal_constraint',
             'expert_action': ['DISCARD', 'SPLIT', 'MERGE'][i] if i < 3 else None,
             'image_layout': layout, 'images': images,
             'request_previews': [f'actor/case{i}_{v}.json' for v in ('v0', 'v1')]}
        if i >= 3:
            c.update(constraint=constraints[i - 3], composition=comps[i - 3], n_spikes=10,
                     expert_positive_spikes=10 - comps[i - 3].get('0', 0))
        obs = ({'cluster_id': i + 1, 'n_spikes': 10, 'n_overclusters': 2} if phase == 'phase1' else
               {'small_cluster_id': 1, 'large_cluster_id': 2, 'n_small': 10, 'n_large': 20})
        for v in ('v0', 'v1'):
            request = r.contract.build_request(phase, obs, ['data:image/png;base64,' + base64.b64encode(png).decode()] * len(images),
                                               skill_version=v, layout=layout)
            for part, item in zip(request['input'][0]['content'][1:], images):
                part['image_url'] = 'LOCAL_PREVIEW_ONLY:' + item['path']
            r.write_new(case_dir / f'actor/case{i}_{v}.json',
                        {'sendable': False, 'api_calls': 0, 'images': images, 'config_hashes': hashes,
                         'request_preview': request})
        cases.append(c)
    (case_dir / 'audit_cases.jsonl').write_text(''.join(json.dumps(c) + '\n' for c in cases))
    r.write_new(case_dir / 'summary.json', {'config_hashes': hashes})
    return case_dir, root / 'output/run', cases


def prepare_run(prepared, repeats=1, batch=False):
    case_dir, out, _ = prepared
    spec, payloads, cases = r.build_plan(case_dir, repeats=repeats, batch=batch)
    r.prepare(out, spec, payloads)
    return out, spec, payloads, cases


def authorize_fixture(out, spec, budget):
    # Synthetic authorization is confined to pytest tmp_path, never real output/.
    assert 'pytest-' in str(out)
    r.write_new(out / 'estimate.json', {
        'manifest_sha256': r.file_hash(out / 'manifest.json'), 'scoring_sha256': spec['scoring_sha256'],
        'status': 'counted-not-inferred', 'pricing': r.PRICES,
        'per_call': [{'job_id': j['job_id'], 'request_sha256': j['request_sha256'],
                      'input_tokens': 100, 'conservative_reserved_usd': r.reservation(100)} for j in spec['jobs']]})
    r.write_new(out / 'authorize_execute.json', {'budget_usd': budget, 'estimate_sha256': r.file_hash(out / 'estimate.json')})


def raw_response(action='KEEP'):
    return {'status': 'completed', 'model': 'gpt-6-astra', 'reasoning': {'effort': 'high'},
            'service_tier': 'default', 'usage': {'input_tokens': 100, 'output_tokens': 10,
                                               'input_tokens_details': {'cached_tokens': 20}},
            'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': json.dumps({'action': action, 'rationale': 'verbatim reason'})}]}]}


def test_dry_run_never_creates_client_or_reads_credentials(prepared, monkeypatch):
    import os
    original_getitem = type(os.environ).__getitem__
    def no_keys(self, key):
        if 'KEY' in str(key).upper() or 'TOKEN' in str(key).upper():
            forbidden()
        return original_getitem(self, key)
    monkeypatch.setattr(type(os.environ), '__getitem__', no_keys)
    case_dir, out, _ = prepared
    assert r.main(['--case-dir', str(case_dir), '--output-root', str(out)]) == 0
    result = r.read_json(out / 'dry_run.json')
    assert result['planned_calls'] == 12 and result['network_calls'] == 0
    assert not result['client_created']
    assert not (out / 'authorize_execute.json').exists()
    assert 'base64' not in (out / 'requests.jsonl').read_text()
    assert r.main(['--case-dir', str(case_dir), '--output-root', str(out)]) == 2  # no overwrite


def test_pair_invariance_schedule_and_manifest(prepared):
    out, spec, payloads, cases = prepare_run(prepared, repeats=2)
    for c in cases:
        a, b = (payloads[(c['case_id'], v)] for v in ('v0', 'v1'))
        assert r.canonical(r.without_skill(a)) == r.canonical(r.without_skill(b))
        assert r.digest(a) != r.digest(b)
        assert a['instructions'] == b['instructions']
    assert spec == r.build_plan(prepared[0], repeats=2)[0]
    assert spec['jobs'] != sorted(spec['jobs'], key=lambda j: j['version'])
    assert r.verify_run(out)[0] == spec


def test_image_hash_mismatch_stops(prepared):
    (prepared[0] / 'test.png').write_bytes(b'changed test fixture')
    with pytest.raises(r.ReplayStop, match='Image hash'):
        r.build_plan(prepared[0])
    assert not prepared[1].exists()


def test_reserved_case_rejected_before_opening_references(prepared, monkeypatch):
    c = copy.deepcopy(prepared[2][0])
    c['dataset_id'] = 'cM2-e007_012-017_CH3'
    (prepared[0] / 'audit_cases.jsonl').write_text(json.dumps(c) + '\n')
    monkeypatch.setattr(r, 'case_requests', forbidden)
    with pytest.raises(ValueError, match='development dataset'):
        r.build_plan(prepared[0])


@pytest.mark.parametrize('mode', ['missing', 'budget', 'hash', 'insufficient'])
def test_execution_authorization_and_budget_gates(prepared, mode):
    out, spec, payloads, _ = prepare_run(prepared)
    if mode == 'missing':
        expected, budget = 'authorization', 5
    else:
        budget = 0.01 if mode == 'insufficient' else 5
        authorize_fixture(out, spec, budget)
        expected = {'budget': 'budget mismatch', 'hash': 'hash mismatch', 'insufficient': 'Remaining budget'}[mode]
        if mode == 'budget':
            budget = 6
        elif mode == 'hash':
            with (out / 'estimate.json').open('a') as f:
                f.write(' ')
    with pytest.raises(r.ReplayStop, match=expected):
        r.execute(out, spec, payloads, budget)
    assert not (out / 'execution_started.json').exists()


@pytest.mark.parametrize('failure', ['interface', 'truncated', 'parse', 'action'])
def test_first_error_stops_without_retry(prepared, monkeypatch, failure):
    out, spec, payloads, _ = prepare_run(prepared)
    authorize_fixture(out, spec, 5)
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if failure == 'interface':
            raise RuntimeError('synthetic API failure')
        raw = raw_response('ABSTAIN' if failure == 'action' else 'DISCARD')
        if failure == 'truncated':
            raw['status'] = 'incomplete'
            raw['incomplete_details'] = {'reason': 'max_output_tokens'}
        if failure == 'parse':
            raw['output'][0]['content'][0]['text'] = 'not JSON'
        return SimpleNamespace(model_dump=lambda **_: raw)
    monkeypatch.setattr(r, 'client_factory', lambda: SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: None))
    with pytest.raises(r.ReplayStop, match='Execution stopped'):
        r.execute(out, spec, payloads, 5)
    assert len(calls) == 1
    assert r.read_json(out / 'execution_status.json')['valid_responses'] == 0
    assert len(r.read_jsonl(out / 'errors.jsonl')) == 1
    with pytest.raises(r.ReplayStop, match='already started'):
        r.execute(out, spec, payloads, 5)


def test_batch_plan_offline_but_execution_refused(prepared):
    out, spec, payloads, _ = prepare_run(prepared, batch=True)
    assert (out / 'batch_plan.jsonl').is_file()
    assert all(not j['sendable'] for j in r.read_jsonl(out / 'batch_plan.jsonl'))
    with pytest.raises(r.ReplayStop, match='Batch execution disabled'):
        r.execute(out, spec, payloads, 5)


def test_scoring_harms_recall_repeats_and_acceptance(prepared):
    _, spec, _, cases = prepare_run(prepared, repeats=2)
    rules = r.read_json(r.SCORING)
    answers = {}
    for j in spec['jobs']:
        i = int(j['case_id'][-1])
        action = (['DISCARD', 'SPLIT', 'MERGE', 'DISCARD', 'KEEP', 'KEEP'][i] if j['version'] == 'v0' else
                  ['DISCARD', 'SPLIT', 'MERGE', 'KEEP', 'DISCARD', 'SPLIT'][i])
        if i == 0 and j['version'] == 'v1' and j['repeat'] == 1:
            action = 'KEEP'  # exactly one expert regression across BOTH repeats
        answers[j['job_id']] = {'action': action, 'rationale': 'reason exactly as returned'}
    result = s.calculate_scores(cases, spec['jobs'], answers, rules, 2)
    assert result['by_version']['v0']['derived']['harmful_rate'] == 1
    assert result['by_version']['v1']['derived']['harmful_rate'] == 0
    assert result['by_version']['v1']['expert']['recall_by_action']['DISCARD']['action_match_rate'] == .5
    assert result['preregistered_conditions']['candidate_eligible_for_review'] is True
    assert len(result['expert_regressions']) == 1
    assert result['expert_regressions'][0]['v1']['rationale'] == 'reason exactly as returned'
    assert result['repeat_consistency']['v1']['pairwise_consistency'] == 5 / 6
    assert len(result['by_stratum']['v0']) == 3
    changed = copy.deepcopy(answers)
    job = next(j for j in spec['jobs'] if j['case_id'] == 'case0' and j['version'] == 'v1' and j['repeat'] == 2)
    changed[job['job_id']]['action'] = 'KEEP'
    assert s.calculate_scores(cases, spec['jobs'], changed, rules, 2)['preregistered_conditions']['candidate_eligible_for_review'] is False
    changed.pop(job['job_id'])
    assert s.calculate_scores(cases, spec['jobs'], changed, rules, 2)['preregistered_conditions']['candidate_eligible_for_review'] is None


def test_score_raw_response_integration_and_rule_hash(prepared):
    out, spec, _, _ = prepare_run(prepared)
    rows = [{'job_id': j['job_id'], 'request_sha256': j['request_sha256'],
             'response': raw_response('MERGE' if j['phase'] == 'phase2' else 'DISCARD')} for j in spec['jobs']]
    (out / 'responses.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in rows))
    r.write_new(out / 'execution_status.json', {'state': 'completed', 'valid_responses': 12,
                'attempted_calls': 12, 'charged_or_reserved_usd': 0.02})
    result = s.score_run(out)
    assert result['valid_responses'] == 12
    assert result['usage']['cached_input_tokens'] == 240
    assert result['preregistered_conditions']['v1_harmful_rate_lower'] is False
    assert 'diagnostic only' in (out / 'REPORT.md').read_text()
    with pytest.raises(r.ReplayStop, match='overwrite'):
        s.score_run(out)
    with r.SCORING.open('a') as f:
        f.write(' ')  # only modifies the test COPY
    with pytest.raises(r.ReplayStop, match='Scoring hash changed'):
        s.score_run(out)


def test_reservation_and_cached_cost():
    assert r.money(1000, 100, cached=500) == .0105
    assert r.money(1000, 100, batch=True) == .0075
    assert r.reservation(1000) > r.money(1000, 4000, conservative=True)
    with pytest.raises(r.BudgetStop):
        r.reserve(.99, 1, 1000)


def test_estimate_count_only_fake_client(prepared, monkeypatch):
    out, spec, payloads, _ = prepare_run(prepared, repeats=2)
    calls = []
    def count(**kwargs):
        calls.append(kwargs)
        assert 'max_output_tokens' not in kwargs
        return SimpleNamespace(input_tokens=100)
    client = SimpleNamespace(responses=SimpleNamespace(input_tokens=SimpleNamespace(count=count), create=forbidden), close=lambda: None)
    monkeypatch.setattr(r, 'client_factory', lambda: client)
    est = r.estimate(out, spec, payloads)
    assert len(calls) == 12 and est['inference_calls'] == 0
    assert len(est['per_call']) == 24
    assert est['totals']['batch_max_output_usd'] == est['totals']['standard_max_output_usd'] / 2
    with pytest.raises(FileExistsError):
        r.estimate(out, spec, payloads)


@pytest.mark.parametrize('budget,expected_calls', [(5, 12), (.215, 1)])
def test_execution_success_and_midrun_budget_stop(prepared, monkeypatch, budget, expected_calls):
    out, spec, payloads, _ = prepare_run(prepared)
    authorize_fixture(out, spec, budget)
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(model_dump=lambda **_: raw_response('DISCARD'))
    monkeypatch.setattr(r, 'client_factory', lambda: SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: None))
    if expected_calls < 12:
        with pytest.raises(r.ReplayStop, match='Execution stopped'):
            r.execute(out, spec, payloads, budget)
    else:
        r.execute(out, spec, payloads, budget)
    assert len(calls) == expected_calls
    result = s.score_run(out)
    assert result['valid_responses'] == expected_calls
    assert result['usage']['attempted_calls'] == expected_calls
    if expected_calls == 1:
        assert result['preregistered_conditions']['candidate_eligible_for_review'] is None
        assert r.read_json(out / 'execution_status.json')['failure']['error_type'] == 'BudgetStop'


def test_tolerable_split_and_expert_improvement(prepared):
    _, spec, _, cases = prepare_run(prepared)
    answers = {}
    for job in spec['jobs']:
        i = int(job['case_id'][-1])
        action = 'MERGE' if i == 2 else 'SPLIT'
        if i == 0 and job['version'] == 'v1':
            action = 'DISCARD'
        answers[job['job_id']] = {'action': action, 'rationale': 'unchanged original text'}
    result = s.calculate_scores(cases, spec['jobs'], answers, r.read_json(r.SCORING), 1)
    assert result['by_version']['v1']['derived']['categories'] == {'tolerable_not_ideal': 2, 'acceptable': 1}
    assert len(result['expert_improvements']) == 1
    assert result['expert_improvements'][0]['v0']['rationale'] == 'unchanged original text'
    assert result['preregistered_conditions']['v1_harmful_rate_lower'] is False
