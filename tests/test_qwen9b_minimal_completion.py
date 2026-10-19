"""Offline checks for the explicitly authorized minimal-only continuation."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.run import complete_qwen9b_minimal as run
from tests.test_qwen9b_rollout_comparison import Session, observation, manager, raw

base = run.base


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Unexpected network/key/client access')
    monkeypatch.setattr('socket.socket.connect', forbidden)
    monkeypatch.setattr(base.transport, 'make_session', forbidden)


@pytest.fixture
def payload():
    obs, urls, _, _ = observation(manager(), 'phase1', 1)
    return base.build_request('minimal', 'phase1', obs, urls)


@pytest.fixture
def source(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(run, 'ROOT', tmp_path)
    out = tmp_path/'run'; out.mkdir()
    (out/'execution.lock').touch()
    ids = list(base.minimal.DATASETS)
    rows = [dict(dataset_id=d) for d in ids]
    m = dict(datasets=rows, prior_charged_or_reserved_usd=.4)
    base.write(out/'manifest.json', m)
    monkeypatch.setattr(base, 'verify', lambda _: m)
    provider = base.JournalProvider(out/'calls', budget=25, prior=.4, session=Session())
    provider.ask(payload, f'{base.MODEL}/rollout/minimal/{ids[0]}/1', 'minimal')
    # Last call is a valid JSON SPLIT; no transport/length failure.
    provider.cursor = 1
    class SplitSession(Session):
        def post(self, url, *, json, **kwargs):
            from tests.test_qwen9b_rollout_comparison import Response
            self.posts += 1
            return Response(raw(json, action='SPLIT'))
    provider.session = SplitSession()
    provider.ask(payload, f'{base.MODEL}/rollout/minimal/{ids[1]}/1', 'minimal')
    folders = [out/'attempt_001/minimal'/d for d in ids[:2]]
    for folder, name in zip(folders, ('terminal', 'stopped')):
        folder.mkdir(parents=True)
        assigns, tree = np.array([1, 1, 2]), np.zeros((4, 0))
        path = folder/(name+'.npz')
        np.savez(path, assigns=assigns, tree=tree)
        state = base.arrays_hash(assigns, tree)
        base.write(folder/(name+'.json'), dict(sha256=base.sha(path), state_sha256=state))
    result = dict(dataset_id=ids[0], arm='minimal', status='completed', calls=1,
        final_sha256=state, controller_output=str(folders[0].relative_to(tmp_path)))
    base.write(folders[0]/'evaluation.json', result)
    base.write(out/'results.json', [result])
    base.write(folders[1]/'summary.json', dict(status='stopped', calls=1, terminal_evaluation_allowed=False))
    step = folders[1]/'step_00001'; step.mkdir()
    base.write(step/'request.json', dict(phase='phase1', context=None,
        observation=dict(n_overclusters=1), state_sha256=state, request_sha256=base.digest(payload)))
    base.write(out/'execution_status.json', dict(status='stopped', error=dict(type='ControllerStop',
        cause='ValueError', message=run.INVALID_SPLIT), costs=provider.ledger()))
    return out


def authorize(out):
    record = run.plan(out)
    folder = out/run.AMENDMENT; folder.mkdir()
    base.write(folder/'authorization.json', record)
    (folder/'runner.py').write_bytes(Path(run.__file__).read_bytes())
    base.write(folder/'seal.json', dict(authorization_sha256=base.sha(folder/'authorization.json')))
    return record


def test_plan_offline_preserves_all_files(source):
    before = {str(p):base.sha(p) for p in source.rglob('*') if p.is_file()}
    a = run.plan(source)
    assert a['arms'] == ['minimal'] and a['harness_cancelled']
    assert len(a['remaining_datasets']) == 2 and a['journal_cursor'] == 2
    assert [r['status'] for r in a['preserved_results']] == ['completed', 'stopped_invalid_action']
    assert 'one_to_one' not in a['preserved_results'][1]
    assert before == {str(p):base.sha(p) for p in source.rglob('*') if p.is_file()}


def test_saved_prefix_skipped_without_new_charge(source, payload):
    a = run.plan(source)
    s = Session()
    p = run.MinimalProvider(source/'calls', budget=25, prior=.4, session=s)
    p.cursor = a['journal_cursor']
    old_cost = p.charged
    tag = f"{base.MODEL}/rollout/minimal/{a['remaining_datasets'][0]['dataset_id']}/1"
    p.ask(payload, tag, 'minimal')
    assert s.posts == 1 and p.charged == pytest.approx(old_cost+.001)
    q = run.MinimalProvider(source/'calls', budget=25, prior=.4)
    q.cursor = a['journal_cursor']
    assert q.ask(payload, tag, 'minimal')['action'] == 'KEEP'
    assert q.sent == 0 and q.charged == p.charged


def test_harness_rejected_before_network(source, payload):
    p = run.MinimalProvider(source/'calls', budget=25, prior=.4)
    with pytest.raises(ValueError, match='harness forbidden'):
        p.ask(payload, 'not-minimal', 'system_v1_skill_v0')
    assert p.sent == 0


def test_budget_stops_before_new_request(source, payload):
    p = run.MinimalProvider(source/'calls', budget=25, prior=24.99)
    p.cursor = 2
    with pytest.raises(ValueError, match='budget'):
        p.ask(payload, f'{base.MODEL}/rollout/minimal/new/1', 'minimal')
    assert p.sent == 0


@pytest.mark.parametrize('change', ['evidence', 'authorization', 'snapshot'])
def test_amendment_tamper_rejected(source, change):
    authorize(source)
    run.verify(source)
    if change == 'evidence':
        path = source/'calls/000002/decision.json'
    elif change == 'authorization':
        path = source/run.AMENDMENT/'authorization.json'
    else:
        path = source/run.AMENDMENT/'runner.py'
    path.write_text(path.read_text()+'\n')
    with pytest.raises(ValueError):
        run.verify(source)


@pytest.mark.parametrize('field,value', [('state_sha256','changed'), ('observation',dict(n_overclusters=2))])
def test_failed_state_must_be_preserved_leaf(source, field, value):
    step = next((source/'attempt_001/minimal'/base.minimal.DATASETS[1]).glob('step_*/request.json'))
    obj = base.read(step); obj[field] = value; base.atomic(step, obj)
    with pytest.raises(ValueError, match='evidence/state mismatch'):
        run.plan(source)


def test_impossible_split_advances_without_synthetic_action(source, monkeypatch):
    a = run.plan(source)
    row = dict(dataset_id=base.minimal.DATASETS[1])
    c = SimpleNamespace(out=run.ROOT/a['preserved_results'][1]['controller_output'])
    def fail(*args, **kwargs):
        raise base.minimal.ControllerStop('state restored') from ValueError(run.INVALID_SPLIT)
    monkeypatch.setattr(base.old, 'evaluate_rollout', fail)
    result = run.evaluate(c, row, None)
    assert result['status'] == 'stopped_invalid_action'
    assert result['final_metrics'] is None and not result['terminal_evaluation_allowed']


@pytest.mark.parametrize('cause', [ValueError('parse'), ValueError('Other execution failure'), base.TransportStopped('exhausted')])
def test_other_errors_stop_not_silently_skipped(monkeypatch, cause):
    def fail(*args, **kwargs):
        raise base.minimal.ControllerStop('stop') from cause
    monkeypatch.setattr(base.old, 'evaluate_rollout', fail)
    with pytest.raises(base.minimal.ControllerStop):
        run.evaluate(None, {}, None)


def test_execute_only_remaining_minimal_then_stop(source, monkeypatch):
    seen = []
    monkeypatch.setattr(base, 'check_endpoint', lambda *args: None)
    monkeypatch.setattr(base.minimal, 'load_actor', lambda row:(SimpleNamespace(assigns=np.array([1])),30000))
    class Controller:
        def __init__(self, actor, dataset, out, *, provider, arm, sampling_rate):
            assert arm == 'minimal'
            self.out=out; out.mkdir(parents=True)
            self.calls=1; self.operations=0
            seen.append(dataset)
    monkeypatch.setattr(base, 'Controller', Controller)
    monkeypatch.setattr(run, 'evaluate', lambda c,row,initial:dict(dataset_id=row['dataset_id'],status='completed'))
    result = run.execute(source)
    assert seen == list(base.minimal.DATASETS)[2:]
    assert result['completed_rollouts']==3 and result['stopped_rollouts']==1
    assert result['status']=='completed_with_failures' and result['harness_cancelled']
    assert result['new_http_attempts']==0
    with pytest.raises(ValueError, match='already finished'):
        run.execute(source, resume=True)


def test_request_and_executor_are_frozen():
    assert base.Controller.run is base.CurationController.run
    assert base.Controller.apply is base.CurationController.apply
    assert run.MinimalProvider.ask is not base.JournalProvider.ask  # scope guard only
