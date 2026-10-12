import copy
import json
import shutil

import pytest

from scripts.run import run_openrouter_smoke as smoke


@pytest.fixture
def calls(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Network/credential access forbidden in offline test')
    import requests
    import dotenv
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    monkeypatch.setattr(dotenv, 'dotenv_values', forbidden)
    monkeypatch.setattr(smoke, 'make_session', forbidden)
    return smoke.prepare(tmp_path / 'dry')


def test_dry_no_network_or_key(calls, tmp_path):
    assert len(calls) == 4
    assert not (tmp_path / 'dry/summary.json').exists()
    for i, (_, r) in enumerate(calls):
        assert 'base64' not in (tmp_path / f'dry/request_{i}.json').read_text()
        assert smoke.read(tmp_path / 'dry/manifest.json')['requests'][i]['request_sha256'] == smoke.digest(r)


def test_same_frozen_prompt_images_and_schema(calls):
    for i in range(2):
        a, b = calls[i][1], calls[i+2][1]
        assert a['messages'] == b['messages']
        assert a['response_format'] == b['response_format']
        assert a['reasoning'] == b['reasoning'] == {'enabled': True, 'exclude': False}
        assert len(a['messages'][1]['content']) == (5 if i == 0 else 4)
        assert a['provider']['allow_fallbacks'] is False
        assert a['provider']['require_parameters'] is True


def test_image_hash_failure(calls, tmp_path):
    j = calls[0][0]
    folder = tmp_path / j['folder']
    shutil.copytree(smoke.SOURCE / j['folder'], folder)
    with (folder / 'waveform.png').open('ab') as f:
        f.write(b'corrupted')
    with pytest.raises(ValueError, match='Image hash'):
        smoke.build(tmp_path, j, next(iter(smoke.MODELS)))


def test_budget():
    model = next(iter(smoke.MODELS))
    with pytest.raises(ValueError, match='Insufficient'):
        smoke.reserve(0.49, .5, model)
    assert smoke.reserve(0, .5, model) > 0


def raw_for(request):
    return dict(model=request['model'], provider=smoke.MODELS[request['model']][1],
        choices=[dict(finish_reason='stop', message=dict(content=json.dumps(
            dict(action='KEEP', rationale='Fixture, not model output.')), reasoning='fixture'))],
        usage=dict(cost=.001, prompt_tokens=100, completion_tokens=50))


def test_parse(calls):
    r = calls[0][1]
    assert smoke.parse(raw_for(r), r)['action'] == 'KEEP'


@pytest.mark.parametrize('failure', ['length', 'model', 'provider', 'json', 'extra', 'wrong_phase', 'thinking'])
def test_invalid_stops(calls, failure):
    r = calls[0][1]; raw = raw_for(r)
    message = raw['choices'][0]['message']
    if failure == 'length': raw['choices'][0]['finish_reason'] = 'length'
    elif failure in {'model', 'provider'}: raw[failure] = 'unexpected'
    elif failure == 'json': message['content'] = '```json\n{}\n```'
    elif failure == 'extra': message['content'] = '{"action":"KEEP","rationale":"x","extra":1}'
    elif failure == 'wrong_phase': message['content'] = '{"action":"MERGE","rationale":"x"}'
    else: message.pop('reasoning')
    with pytest.raises(ValueError): smoke.parse(raw, r)


class FakeResponse:
    status_code = 200
    def __init__(self, value): self.value = value
    def json(self): return self.value
    def raise_for_status(self): pass


class FakeSession:
    def __init__(self, raw): self.raw, self.posts = raw, 0
    def get(self, url, **kwargs):
        model = url.split('/models/')[1].removesuffix('/endpoints')
        tag, _, ip, op = smoke.MODELS[model]
        return FakeResponse(dict(data=dict(endpoints=[dict(tag=tag, quantization=tag.split('/')[-1],
            supported_parameters=['structured_outputs','response_format','reasoning','max_tokens'],
            context_length=smoke.CONTEXT, pricing=dict(prompt=ip, completion=op))])))
    def post(self, *args, **kwargs):
        self.posts += 1
        return FakeResponse(self.raw)
    def close(self): pass


def test_failure_settled_and_no_retry(calls, tmp_path):
    out = tmp_path / 'dry'; raw = raw_for(calls[0][1])
    raw['choices'][0]['finish_reason'] = 'length'
    session = FakeSession(raw)
    result = smoke.execute(out, calls, .5, factory=lambda: session)
    assert result['status'] == 'stopped' and session.posts == 1
    assert result['known_cost_usd'] == .001
    assert result['charged_or_reserved_usd'] == pytest.approx(.001)


def test_missing_cost_keeps_reservation(calls, tmp_path):
    raw = raw_for(calls[0][1]); raw['usage'].pop('cost')
    session = FakeSession(raw)
    result = smoke.execute(tmp_path/'dry', calls, .5, factory=lambda: session)
    assert result['status'] == 'stopped'
    assert result['charged_or_reserved_usd'] == smoke.reservation(calls[0][1]['model'])


def test_insufficient_budget_no_post(calls, tmp_path):
    session = FakeSession({})
    result = smoke.execute(tmp_path/'dry', calls, .00001, factory=lambda: session)
    assert result['status'] == 'stopped' and session.posts == 0


def test_27b_only_does_not_repeat_9b(tmp_path):
    calls = smoke.prepare(tmp_path/'selected', models=['qwen/qwen3.5-27b'])
    assert len(calls) == 2
    for _, r in calls:
        assert r['model'] == 'qwen/qwen3.5-27b'
        assert r['provider']['only'] == ['deepinfra/fp8']
        assert r['provider']['quantizations'] == ['fp8']
        assert r['response_format']['json_schema']['strict'] is True


def test_27b_matches_previous_input_except_route(tmp_path):
    calls = smoke.prepare(tmp_path/'comparison', models=['qwen/qwen3.5-27b'])
    prior = smoke.ROOT/'zero-shot baseline/runs/openrouter_smoke_20261012_v1'
    for i, (_, r) in enumerate(calls):
        current = smoke.redact_images(r)
        old = smoke.read(prior/f'request_{i+2}.json')
        current.pop('provider'); old.pop('provider')
        assert current == old
