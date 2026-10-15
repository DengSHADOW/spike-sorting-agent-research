import json
import base64
import hashlib
from types import SimpleNamespace

import pytest

from scripts.run import probe_qwen_thinking_budget as probe


class Counter:
    def encode(self, text, **kwargs):
        return SimpleNamespace(ids=text.split())


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, 'ROOT', tmp_path)
    source = tmp_path / 'source'
    step = source / probe.STEP
    step.mkdir(parents=True)
    (source / 'calls/000002').mkdir(parents=True)
    r = request()
    r.update(max_tokens=4000, reasoning=dict(enabled=True, exclude=False),
             provider=dict(only=['deepinfra/bf16'], allow_fallbacks=False,
                           require_parameters=True, quantizations=['bf16']),
             messages=[{'role':'system','content':'Offline fixture'},
                       {'role':'user','content':[{'type':'text','text':'Offline numbers'}]}])
    images=[]
    for name in ('waveform','isi','amplitude','tree'):
        data = name.encode()
        (step / (name+'.png')).write_bytes(data)
        images.append(dict(name=name,sha256=hashlib.sha256(data).hexdigest()))
        r['messages'][1]['content'].append(dict(type='image_url',image_url=dict(
            url='data:image/png;base64,'+base64.b64encode(data).decode(),detail='high')))
    saved=dict(payload=probe.io.redact_images(r),request_sha256=probe.io.digest(r),display=dict(images=images))
    probe.io.write(step/'request.json',saved)
    probe.io.write(source/'calls/000002/request.json',dict(request=saved['payload'],request_sha256=saved['request_sha256']))
    probe.io.write(source/'cost_breakdown.json',dict(charged_or_reserved_usd=.5))
    probe.io.write(source/'manifest.json',{})
    return source


def request():
    return dict(model=probe.MODEL, reasoning={'max_tokens':128},
                response_format={'json_schema':{'schema':{'properties':{'action':{'enum':['KEEP']}}}}})


def raw(n, finish='stop'):
    return dict(model=probe.MODEL, provider='DeepInfra', choices=[dict(finish_reason=finish,
        message=dict(reasoning='word '*n, content=json.dumps({'action':'KEEP','rationale':'test'})))],
        usage={'completion_tokens':4000, 'completion_tokens_details':{'reasoning_tokens':128}})


def test_http_acceptance_and_reported_count_do_not_prove_cap():
    result=probe.assess(raw(1500),request(),Counter())
    assert result['status']=='budget_not_enforced'
    assert result['reported_reasoning_tokens']==128
    assert result['valid_action_json']


def test_short_output_not_proof():
    assert probe.assess(raw(70),request(),Counter())['status']=='within_budget_observed_not_proven'


def test_truncation_not_valid_action():
    result=probe.assess(raw(4000,'length'),request(),Counter())
    assert result['status']=='budget_not_enforced' and not result['valid_action_json']


def test_api_rejection():
    assert probe.assess({'error':{'code':400}},request(),Counter())['status']=='rejected_or_no_completion'


def test_dry_run_never_accesses_network_keys(tmp_path,monkeypatch,source):
    def forbidden(*a,**kw):raise AssertionError('Network/client/key access')
    monkeypatch.setattr('socket.socket.connect',forbidden)
    monkeypatch.setattr(probe.io,'make_session',forbidden)
    probe.prepare(tmp_path/'dry',128,source=source)
    m=probe.io.read(tmp_path/'dry/manifest.json')
    assert m['planned_calls']==1 and not m['apply_actions']
    with pytest.raises(FileExistsError):probe.prepare(tmp_path/'dry',128,source=source)


def test_only_reasoning_budget_changes(source):
    r,_=probe.build(source,128)
    del r['reasoning']['max_tokens']
    assert probe.io.digest(r)==probe.io.read(source/'calls/000002/request.json')['request_sha256']


def test_invalid_budget_rejected():
    with pytest.raises(ValueError):probe.build(probe.SOURCE,9999)


def test_bad_image_hash_stops(source):
    (source/probe.STEP/'waveform.png').write_bytes(b'changed')
    with pytest.raises(ValueError,match='Image hash'):probe.build(source,128)


def test_previous_probe_cost_carries_forward(tmp_path,source):
    previous=tmp_path/'zero-shot baseline/runs/first'
    previous.mkdir(parents=True)
    probe.io.write(previous/'manifest.json',dict(thinking_budget=128,
        source_cost_sha256=probe.io.sha(source/'cost_breakdown.json')))
    probe.io.write(previous/'result.json',dict(costs=dict(prior_charged_or_reserved_usd=.5,
        cumulative_charged_or_reserved_usd=.5004)))
    probe.prepare(tmp_path/'next',3000,source=source,previous_probe=previous)
    m=probe.io.read(tmp_path/'next/manifest.json')
    assert m['prior_charged_or_reserved_usd']==.5004
    assert m['previous_probe']['result_sha256']==probe.io.sha(previous/'result.json')
