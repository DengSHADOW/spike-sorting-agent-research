import copy
import socket
import pytest
from scripts.run import run_image_ab as a
from scripts.run import run_skill_replay as r
from scripts.analysis import score_image_ab as scoring


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args,**kw): raise AssertionError('No network or SDK')
    monkeypatch.setattr(r,'client_factory',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden)


def test_requests_only_waveform_differs():
    c=dict(phase='phase1',image_layout='local_four')
    m=dict(cluster_id=1,n_spikes=10,n_overclusters=2)
    images={'raw':[b'a',b'b',b'c',b'd'],'summary':[b'new',b'b',b'c',b'd']}
    requests=a.paired_requests(c,m,images)
    assert requests['raw']['instructions']==requests['summary']['instructions']
    assert requests['raw']['input'][0]['content'][0]==requests['summary']['input'][0]['content'][0]
    images['summary'][1]=b'changed non-waveform'
    with pytest.raises(r.ReplayStop): a.paired_requests(c,m,images)


def test_score_expert_and_constraint_separate():
    rules=r.read_json(r.SCORING); key='all_members_terminal_noise_next_action_not_unique'
    cases=[dict(case_id='e',phase='phase1',target_type='recorded_expert_edit',expert_action='DISCARD'),
           dict(case_id='n',phase='phase1',target_type='derived_terminal_constraint',constraint=key)]
    jobs=[]; answers={}
    for arm in ('raw','summary'):
        for c in cases:
            job=dict(job_id=c['case_id']+arm,case_id=c['case_id'],version=arm,repeat=1,phase=c['phase'],
                     target_type=c['target_type'],image_layout='local_four',image_count=4,dataset_id='fixture')
            jobs.append(job)
            answers[job['job_id']]=dict(action='KEEP' if arm=='raw' else 'DISCARD',rationale='fixture')
    result=scoring.score(cases,jobs,answers,rules)
    assert result['totals']['raw']['expert']['correct']==0
    assert result['totals']['summary']['expert']['correct']==1
    assert result['totals']['raw']['derived']['harmful']==1
    assert result['totals']['summary']['derived']['harmful']==0
    assert result['decision_changes']==2 and result['repeat_consistency'] is None
    answers.pop(jobs[0]['job_id'])
    with pytest.raises(r.ReplayStop): scoring.score(cases,jobs,answers,rules)


def test_real_prepared_pairs_and_hash_guard(monkeypatch):
    from pathlib import Path
    out=a.ROOT/'output/curation_image_ab_v0_20261006'
    if not (out/'manifest.json').exists(): pytest.skip('Real offline image fixture not prepared')
    spec=r.read_json(out/'manifest.json')['spec']
    cases=r.load_cases(Path(spec['case_dir']),r.read_json(r.SCORING))
    observations=r.read_json(out/'observations.json')
    rebuilt,requests=a.assemble(out,cases,observations,spec['source_hashes'])
    assert rebuilt==spec and len(spec['jobs'])==70
    assert all(j['repeat']==1 for j in spec['jobs'])
    assert 'base64' not in __import__('json').dumps(spec)
    for c in cases:
        raw=requests[(c['case_id'],'raw')]; summary=requests[(c['case_id'],'summary')]
        assert a.without_waveforms(raw,c['phase'])==a.without_waveforms(summary,c['phase'])
        assert r.digest(raw)!=r.digest(summary)
        o=observations[c['case_id']]
        if c['phase']=='phase2':
            assert o['images']['summary'][0]['display']['ylim']==o['images']['summary'][1]['display']['ylim']
    # Corrupt reads in memory only: original/generated PNGs remain untouched.
    target=a.ROOT/observations[cases[0]['case_id']]['images']['raw'][0]['path']
    read_bytes=Path.read_bytes
    def tampered(path): return b'changed' if path==target else read_bytes(path)
    monkeypatch.setattr(Path,'read_bytes',tampered)
    with pytest.raises(r.ReplayStop,match='hash/type'): a.assemble(out,cases,observations,spec['source_hashes'])
