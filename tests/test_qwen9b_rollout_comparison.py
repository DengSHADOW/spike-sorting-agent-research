"""Offline regression tests: synthetic data and fake HTTP only."""
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from scripts.run import run_qwen9b_rollout_comparison as run
from src.agent.curation_observation_v1 import metrics
from src.cluster.manager import ClusterManager


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*a, **kw): raise AssertionError('Network/key/client access')
    monkeypatch.setattr('socket.socket.connect', forbidden)
    monkeypatch.setattr(run.transport, 'make_session', forbidden)


def manager():
    return ClusterManager(np.array([1,1,1,1,3,3,3]), np.array([1,1,2,2,3,3,3]),
        np.array([[1.],[2.],[.9],[0.]]), np.arange(7)*.01,
        np.random.default_rng(12).normal(size=(7,12)))


def observation(m, phase, cid, target=None, **kwargs):
    names = run.minimal.VIEWS[phase]
    return metrics(m, phase, cid, target), ['data:image/png;base64,YQ==']*len(names), [b'a']*len(names), {
        'images':[dict(name=n, sha256=hashlib.sha256(b'a').hexdigest()) for n in names]}


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(run.minimal, 'observe', observation)


@pytest.fixture
def payload():
    obs, urls, _, _ = observation(manager(), 'phase1', 1)
    return run.build_request('minimal', 'phase1', obs, urls)


def raw(request, action='KEEP', finish='stop'):
    return dict(model=run.MODEL, provider='DeepInfra', choices=[dict(finish_reason=finish,
        message=dict(reasoning='OFFLINE', content=json.dumps(dict(action=action, rationale='OFFLINE'))))],
        usage=dict(cost=.001, prompt_tokens=100, completion_tokens=run.MAX_OUTPUT if finish=='length' else 20))


class Response:
    headers = {}
    def __init__(self, data, status=200): self.data, self.status_code = data, status
    def json(self): return self.data


class Session:
    def __init__(self, modes=()): self.modes, self.posts = list(modes), 0
    def post(self, url, *, json, **kw):
        self.posts += 1
        assert json['model'] == run.MODEL and json['max_tokens'] == 8000
        assert json['reasoning'] == dict(enabled=True, exclude=False, max_tokens=6000)
        assert kw['timeout']==(15,600) and kw['allow_redirects'] is False
        mode = self.modes.pop(0) if self.modes else 'ok'
        if mode == 'timeout': raise TimeoutError('fixture')
        if mode == 'interrupt': raise KeyboardInterrupt()
        if isinstance(mode, int): return Response({'error':{'code':mode}}, mode)
        value = raw(json, finish='length' if mode=='length' else 'stop')
        if mode=='parse': value['choices'][0]['message']['content']='invalid'
        return Response(value)
    def close(self): pass


def provider(tmp_path, session=None, prior=.1):
    return run.JournalProvider(tmp_path/'calls', budget=25, prior=prior, session=session, sleeper=lambda _:None)


def test_minimal_is_exact_astra_request_except_envelope_and_sampling():
    for phase, target in [('phase1',None), ('phase2',3)]:
        obs, urls, _, _ = observation(manager(), phase, 1, target)
        astra = run.minimal.build_request(phase, obs, urls)
        qwen = run.build_request('minimal', phase, obs, urls)
        assert astra['max_output_tokens'] == 4000
        expected = run.old.convert(astra, run.MODEL, 8000)
        expected['reasoning'] = dict(enabled=True, exclude=False, max_tokens=6000)
        expected.update(run.SAMPLING)
        assert qwen == expected
        assert qwen['messages'][0]['content'] == astra['instructions']
        assert qwen['messages'][1]['content'][0]['text'] == astra['input'][0]['content'][0]['text']
        assert 'DOMAIN SKILL' not in qwen['messages'][1]['content'][0]['text']
        harness = run.build_request(run.ARMS[1], phase, obs, urls)
        assert harness['messages'][1]['content'][1:] == qwen['messages'][1]['content'][1:]
        assert harness['response_format']['json_schema']['schema'] == qwen['response_format']['json_schema']['schema']
        assert 'DOMAIN SKILL' in harness['messages'][1]['content'][0]['text']


def test_scheduler_executor_unchanged():
    assert run.Controller.run is run.CurationController.run
    assert run.Controller.apply is run.CurationController.apply


def test_replay_without_client_or_double_charge(payload,tmp_path):
    s=Session(); p=provider(tmp_path,s)
    expected=p.ask(payload,'minimal/one','minimal')
    q=provider(tmp_path)
    assert q.ask(payload,'minimal/one','minimal')==expected
    assert q.sent==0 and s.posts==1 and q.charged==pytest.approx(.101)


def test_changed_request_refuses_resume(payload,tmp_path):
    provider(tmp_path,Session()).ask(payload,'one','minimal')
    changed=copy.deepcopy(payload); changed['messages'][0]['content']+='bad'
    with pytest.raises(ValueError,match='divergence'): provider(tmp_path).ask(changed,'one','minimal')


def test_27b_is_rejected(payload,tmp_path):
    payload['model']='qwen/qwen3.5-27b'
    with pytest.raises(ValueError,match='settings'): provider(tmp_path).ask(payload,'one','minimal')


@pytest.mark.parametrize('mode',[429,502,503,504,'timeout'])
def test_transport_retries_preserve_unknown_cost(payload,tmp_path,mode):
    s=Session([mode,'ok']); p=provider(tmp_path,s)
    assert p.ask(payload,'one','minimal')['action']=='KEEP'
    assert s.posts==2 and p.charged==pytest.approx(.101+p.held)
    ledger=p.ledger()['by_arm']['minimal']
    assert ledger['attempts']==2 and ledger['known_usd']==.001
    assert ledger['unsettled_reservation_usd']==p.held


def test_exhausted_recovery_does_not_loop_forever(payload,tmp_path):
    s=Session([429]*8); p=provider(tmp_path,s)
    with pytest.raises(run.TransportStopped):p.ask(payload,'one','minimal')
    assert s.posts==4
    q=provider(tmp_path,s)
    with pytest.raises(run.TransportStopped):q.ask(payload,'one','minimal')
    assert s.posts==4 and q.charged==pytest.approx(.1+4*q.held)


def test_interrupted_inflight_resumes_with_new_reservation(payload,tmp_path):
    s=Session(['interrupt']);p=provider(tmp_path,s)
    with pytest.raises(KeyboardInterrupt):p.ask(payload,'one','minimal')
    q=provider(tmp_path,Session()); q.ask(payload,'one','minimal')
    assert q.sent==1 and q.charged==pytest.approx(.101+q.held)


def test_saved_response_reconciled_offline(payload,tmp_path):
    p=provider(tmp_path,Session(['interrupt']))
    with pytest.raises(KeyboardInterrupt):p.ask(payload,'one','minimal')
    a=tmp_path/'calls/000001/attempts/001'
    run.write(a/'http.json',dict(status=200))
    run.write(a/'response.json',raw(payload))
    q=provider(tmp_path)
    assert q.ask(payload,'one','minimal')['action']=='KEEP'
    assert q.sent==0 and q.charged==pytest.approx(.101)


@pytest.mark.parametrize('mode',[401,402,403,'parse'])
def test_nonrecoverable_error_never_retried(payload,tmp_path,mode):
    s=Session([mode]); p=provider(tmp_path,s)
    with pytest.raises(ValueError):p.ask(payload,'one','minimal')
    assert s.posts==1
    with pytest.raises(ValueError):provider(tmp_path).ask(payload,'one','minimal')


def test_length_not_retried_not_action(payload,tmp_path):
    s=Session(['length']);p=provider(tmp_path,s)
    with pytest.raises(run.old.TruncatedResponse):p.ask(payload,'one','minimal')
    with pytest.raises(run.old.TruncatedResponse):provider(tmp_path).ask(payload,'one','minimal')
    assert s.posts==1
    assert not (tmp_path/'calls/000001/decision.json').exists()
    assert p.ledger()['by_arm']['minimal']['truncated']==1


def test_budget_stops_before_network(payload,tmp_path):
    s=Session(); p=provider(tmp_path,s,prior=24.999)
    with pytest.raises(ValueError,match='budget'):p.ask(payload,'one','minimal')
    assert s.posts==0


@pytest.mark.parametrize('change', ['output', 'thinking'])
def test_changed_token_limits_rejected_before_request(payload,tmp_path,change):
    if change=='output': payload['max_tokens']=4000
    else: payload['reasoning']['max_tokens']=3000
    s=Session()
    with pytest.raises(ValueError,match='settings'):provider(tmp_path,s).ask(payload,'one','minimal')
    assert s.posts==0


def test_reservation_covers_8000_not_legacy_4000(payload,tmp_path):
    assert run.reservation()==pytest.approx(.03015584)
    assert run.reservation()>run.transport.reservation(run.MODEL)
    s=Session(); p=provider(tmp_path,s,prior=24.97)
    with pytest.raises(ValueError,match='budget'):p.ask(payload,'one','minimal')
    assert s.posts==0


def test_old_4000_manifest_cannot_be_resumed(tmp_path):
    run.write(tmp_path/'manifest.json',dict(model=run.MODEL,arms=list(run.ARMS),max_output_tokens=4000))
    run.write(tmp_path/'preflight.json',dict(manifest_sha256=run.sha(tmp_path/'manifest.json')))
    with pytest.raises(ValueError,match='Frozen experiment changed'):run.verify(tmp_path)


def test_two_rollouts_continue_after_length(tmp_path,fast):
    p=provider(tmp_path,Session(['length']))
    for idx,arm in enumerate(run.ARMS):
        c=run.Controller(manager(),run.minimal.DATASETS[0],tmp_path/f'controller{idx}',
            provider=p,arm=arm,sampling_rate=30000)
        initial=c.manager.assigns.copy()
        if idx==0:
            result=run.old.evaluate_rollout(c,dict(dataset_id=c.dataset_id),initial,allow_truncation=True)
            assert result['status']=='stopped_truncated' and result['final_metrics'] is None
            np.testing.assert_array_equal(c.manager.assigns,initial)
            assert (c.out/'stopped.npz').exists()
        else:
            c.run()
            assert c.status=='completed'
    assert p.ledger()['by_arm']['minimal']['truncated']==1
    assert p.ledger()['by_arm'][run.ARMS[1]]['valid']==2


def test_prepare_offline_and_no_old_output_edits(tmp_path,fast,monkeypatch):
    rows=[dict(dataset_id=d,mat_path='synthetic',mat_sha256='synthetic') for d in run.minimal.DATASETS]
    obs,urls,_,_=observation(manager(),'phase1',1)
    h=run.digest(run.minimal.build_request('phase1',obs,urls))
    monkeypatch.setattr(run,'astra_evidence',lambda:dict(datasets=[dict(r,first_request_sha256=h) for r in rows]))
    monkeypatch.setattr(run,'historical_costs',lambda _:dict(path='fixture',total_charged_or_reserved_usd=.38))
    monkeypatch.setattr(run.minimal,'selected_rows',lambda _:rows)
    monkeypatch.setattr(run.minimal,'load_actor',lambda _:(manager(),30000))
    out=tmp_path/'prepared'
    m=run.prepare(out,tmp_path/'prior')
    assert m['model']==run.MODEL and m['arms']==list(run.ARMS) and m['budget_usd']==25
    assert len(m['previews'])==4 and run.read(out/'preflight.json')['api_calls']==0
    run.atomic(out/'manifest.json',dict(m,model='changed'))
    with pytest.raises(ValueError,match='Manifest changed'):run.verify(out)


@pytest.fixture
def history_chain(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(run, 'ROOT', tmp_path)
    base = tmp_path/'zero-shot baseline/runs'
    base.mkdir(parents=True)
    legacy = base/'legacy'; legacy.mkdir()
    run.write(legacy/'manifest.json', {})
    inherited = dict(path=str(legacy.relative_to(tmp_path)), total_charged_or_reserved_usd=.2)
    monkeypatch.setattr(run.old, 'historical_costs', lambda _: inherited)
    journal = base/'journal'; journal.mkdir()
    # Freeze a real synthetic 4000-token journal, then restore today's settings.
    with monkeypatch.context() as patch:
        patch.setattr(run, 'MAX_OUTPUT', 4000)
        p = run.JournalProvider(journal/'calls', budget=25, prior=.2)
        folder = p.out/'000001'; (folder/'attempts/001').mkdir(parents=True)
        run.write(folder/'request.json', dict(arm='minimal', request_sha256='fixture'))
        a = folder/'attempts/001'
        run.write(a/'reserved.json', dict(usd=p.held, request_sha256='fixture'))
        # An unknown historical reservation must remain charged, even after probes.
        ledger = p.ledger()
    m = dict(version='qwen9b-rollout-comparison-4000-v1', model=run.MODEL,
             max_output_tokens=4000, prior_charged_or_reserved_usd=.2, cost_history=inherited)
    run.write(journal/'manifest.json', m)
    run.write(journal/'preflight.json', dict(manifest_sha256=run.sha(journal/'manifest.json')))
    status = dict(status='completed_with_failures', costs=ledger)
    for name, obj in [('cost_breakdown',ledger), ('execution_status',status), ('progress',status)]:
        run.write(journal/(name+'.json'),obj)
    previous = None; total = ledger['charged_or_reserved_usd']
    for name, cost in [('probe128',.003), ('probe3000',.004)]:
        probe = base/name; probe.mkdir()
        pm = dict(kind='qwen9b-independent-thinking-budget-capability-probe',
            source=str(journal.relative_to(tmp_path)), source_manifest_sha256=run.sha(journal/'manifest.json'),
            source_cost_sha256=run.sha(journal/'cost_breakdown.json'),
            prior_charged_or_reserved_usd=total, previous_probe=previous)
        costs = dict(known_usd=cost,unsettled_reservation_usd=0,
            prior_charged_or_reserved_usd=total,cumulative_charged_or_reserved_usd=total+cost)
        for file, obj in [('manifest',pm), ('cost',costs), ('result',dict(costs=costs,calls_sent=1)),
                          ('response',dict(usage=dict(cost=cost))), ('http',{}), ('reserved',{}), ('execution_started',{})]:
            run.write(probe/(file+'.json'),obj)
        previous = dict(path=str(probe.relative_to(tmp_path)),result_sha256=run.sha(probe/'result.json'))
        total += cost
    return base, total


def test_history_carries_old_reservation_and_both_probes_once(history_chain):
    base, total = history_chain
    history = run.historical_costs(base/'probe3000')
    assert history['total_charged_or_reserved_usd'] == pytest.approx(.2+.02949584+.003+.004)
    assert history['total_charged_or_reserved_usd'] == total
    assert history['successful_predictions_reused'] == 0


@pytest.mark.parametrize('damage', ['probe_cost', 'source_cost', 'previous_result', 'in_flight'])
def test_history_refuses_changed_or_active_ledgers(history_chain,damage):
    base, _ = history_chain
    file, obj = {
        'probe_cost':(base/'probe3000/response.json',dict(usage=dict(cost=0))),
        'source_cost':(base/'journal/cost_breakdown.json',{}),
        'previous_result':(base/'probe128/result.json',{}),
        'in_flight':(base/'journal/progress.json',dict(status='request_in_flight')),
    }[damage]
    run.atomic(file,obj)
    with pytest.raises(ValueError):run.historical_costs(base/'probe3000')
