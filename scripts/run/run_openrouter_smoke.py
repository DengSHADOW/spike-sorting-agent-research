"""Four real-image integration calls, not a performance benchmark.

Default is entirely offline. Frozen Astra inputs are reused without labels.
HTTP is direct (no SDK retries); no fallback, repair, or automatic resume.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'zero-shot baseline/runs/minimal_astra_high_dual_20261007_v3'
MODELS = {
    'qwen/qwen3.5-9b': ('deepinfra/bf16', 'DeepInfra', 0.0000001, 0.00000015),
    # User-approved 2026-10-12: explicit replacement, NOT an automatic fallback.
    'qwen/qwen3.5-27b': ('deepinfra/fp8', 'DeepInfra', 0.00000026, 0.0000026),
}
BASE = 'https://openrouter.ai/api/v1'
MAX_OUTPUT = 4000
CONTEXT = 262144


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, obj):
    with Path(path).open('x') as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write('\n')


def log(path, obj):
    with Path(path).open('a') as f:
        f.write(json.dumps(obj, ensure_ascii=False, allow_nan=False) + '\n')


def build(source, job, model):
    folder = source / job['folder']
    if not folder.resolve().is_relative_to(source.resolve()):
        raise ValueError('Input outside frozen source')
    if sha(folder / 'request_payload.json') != job['payload_sha256']:
        raise ValueError('Payload hash mismatch')
    if sha(folder / 'observation.json') != job['observation_sha256']:
        raise ValueError('Observation hash mismatch')
    old = read(folder / 'request_payload.json')
    phase = job['phase']
    prompt = (ROOT / 'zero-shot baseline/prompts' / (phase + '.txt')).read_text()
    if old['instructions'] != prompt:
        raise ValueError('Frozen prompt mismatch')
    parts = old['input'][0]['content']
    if parts[0]['type'] != 'input_text' or len(parts) != len(job['images']) + 1:
        raise ValueError('Unexpected input layout')
    content = [{'type': 'text', 'text': parts[0]['text']}]
    for part, recorded in zip(parts[1:], job['images']):
        ref = part['image_url']
        path = folder / ref['local_file']
        if path.resolve().parent != folder.resolve():
            raise ValueError('Image outside sample')
        if path.name != recorded['name'] + '.png' or sha(path) != ref['sha256'] or ref['sha256'] != recorded['sha256']:
            raise ValueError('Image hash mismatch')
        content.append({'type': 'image_url', 'image_url': {
            'url': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode(),
            'detail': part['detail']}})
    fmt = copy.deepcopy(old['text']['format'])
    fmt.pop('type')
    return dict(model=model, messages=[{'role': 'system', 'content': prompt},
        {'role': 'user', 'content': content}], max_tokens=MAX_OUTPUT,
        reasoning={'enabled': True, 'exclude': False}, stream=False,
        response_format={'type': 'json_schema', 'json_schema': fmt},
        provider={'only': [MODELS[model][0]], 'allow_fallbacks': False,
                  'require_parameters': True, 'quantizations': [MODELS[model][0].split('/')[-1]]})


def redact_images(request):
    result = copy.deepcopy(request)
    for part in result['messages'][1]['content'][1:]:
        url = part['image_url']['url']
        raw = base64.b64decode(url.split(',', 1)[1], validate=True)
        part['image_url']['url'] = {'png_sha256': hashlib.sha256(raw).hexdigest()}
    return result


def prepare(out, source=SOURCE, models=None):
    models = tuple(MODELS) if models is None else tuple(models)
    if not models or len(set(models)) != len(models) or any(m not in MODELS for m in models):
        raise ValueError('Unknown/duplicate/empty model selection')
    m = read(source / 'manifest.json')
    for name, h in m['prompt_hashes'].items():
        if sha(ROOT / 'zero-shot baseline/prompts' / name) != h:
            raise ValueError('Prompt manifest mismatch')
    jobs = [next(j for j in m['stepwise']['jobs'] if j['phase'] == phase and j['dataset_id'].endswith('_' + ch))
            for phase, ch in [('phase1', 'CH31'), ('phase2', 'CH20')]]
    calls = [(job, build(source, job, model)) for model in models for job in jobs]
    # Model/provider fields are the only differences between the two models.
    for i in range(2 if len(models) == 2 else 0):
        a, b = (copy.deepcopy(calls[k][1]) for k in (i, i + 2))
        for x in (a, b):
            x.pop('model'); x.pop('provider')
        if a != b:
            raise ValueError('Cross-model inputs differ')
    out.mkdir(parents=True, exist_ok=False)
    manifest = dict(kind='openrouter-real-image-smoke-v2', inference_calls_planned=len(calls),
        source=str(source.relative_to(ROOT)), source_manifest_sha256=sha(source / 'manifest.json'),
        script_sha256=sha(__file__), campaign_budget_usd=25, smoke_budget_ceiling_usd=0.5,
        sampler='provider defaults (not claimed equivalent to Astra)', thinking=True,
        max_output_tokens=MAX_OUTPUT, retries=0, fallback=False, models={m: MODELS[m] for m in models},
        note='Connectivity only. Approved 9B BF16 / 27B FP8, pinned DeepInfra; no fallback. Historical Novita results unchanged.',
        requests=[dict(index=i, job_id=j['job_id'], phase=j['phase'], model=r['model'],
                       request_sha256=digest(r), images=j['images']) for i, (j, r) in enumerate(calls)])
    write(out / 'manifest.json', manifest)
    for i, (_, r) in enumerate(calls):
        write(out / f'request_{i}.json', redact_images(r))
    return calls


def make_session():
    # Only invoked by explicit execute, never preparation/import.
    import requests
    from dotenv import dotenv_values
    key = dotenv_values(ROOT / '.env', interpolate=False).get('OPENROUTER_API_KEY')
    if not key or not key.startswith('sk-or-'):
        raise ValueError('Missing canonical OpenRouter key')
    session = requests.Session()
    session.trust_env = False
    session.headers['Authorization'] = 'Bearer ' + key
    return session


def reservation(model):
    _, _, ip, op = MODELS[model]
    # Reserve the ENTIRE advertised context, not an unverified image tokenizer estimate.
    return 1.1 * (CONTEXT * ip + MAX_OUTPUT * op)


def reserve(charged, budget, model):
    held = reservation(model)
    if not math.isfinite(budget) or budget <= 0 or charged + held > budget:
        raise ValueError('Insufficient smoke budget')
    return held


def parse(raw, request):
    if raw.get('error'):
        raise ValueError('API error')
    if raw.get('provider') != MODELS[request['model']][1] or raw.get('model') != request['model']:
        raise ValueError('Provider/model mismatch')
    choices = raw.get('choices', [])
    if len(choices) != 1 or choices[0].get('finish_reason') != 'stop':
        raise ValueError('Incomplete/non-stop response')
    message = choices[0]['message']
    if message.get('refusal') or message.get('tool_calls'):
        raise ValueError('Refusal/unexpected tools')
    decision = json.loads(message['content'])
    actions = request['response_format']['json_schema']['schema']['properties']['action']['enum']
    if not isinstance(decision, dict) or set(decision) != {'action', 'rationale'}:
        raise ValueError('Schema mismatch')
    if decision['action'] not in actions or not isinstance(decision['rationale'], str):
        raise ValueError('Invalid action/rationale')
    usage = raw.get('usage', {})
    rt = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens')
    evidence = bool(message.get('reasoning') or message.get('reasoning_details') or (rt and rt > 0))
    if not evidence:
        raise ValueError('Thinking requested but no verifiable evidence returned')
    return decision


def execute(out, calls, budget, factory=make_session):
    if not math.isfinite(budget) or not 0 < budget <= 0.5:
        raise ValueError('Smoke budget must be <= $0.50 within the $25 campaign')
    write(out / 'execution_started.json', dict(budget_usd=budget, manifest_sha256=sha(out / 'manifest.json')))
    charged, known, sent, valid = 0., 0., 0, 0
    status, error = 'stopped', None
    session = None
    try:
        session = factory()
        for i, (job, request) in enumerate(calls):
            model = request['model']
            response = session.get(BASE + '/models/' + model + '/endpoints', timeout=(15, 30), allow_redirects=False)
            response.raise_for_status()
            catalog = response.json()
            write(out / f'catalog_{i}.json', catalog)
            endpoint = next(e for e in catalog['data']['endpoints'] if e['tag'] == MODELS[model][0])
            if endpoint['quantization'] != request['provider']['quantizations'][0] or endpoint['context_length'] != CONTEXT:
                raise ValueError('Endpoint precision/context changed')
            if not {'structured_outputs', 'response_format', 'reasoning', 'max_tokens'}.issubset(endpoint.get('supported_parameters', [])):
                raise ValueError('Required parameter support no longer advertised')
            for field, expected in zip(('prompt', 'completion'), MODELS[model][2:]):
                if float(endpoint['pricing'][field]) != expected:
                    raise ValueError('Endpoint price changed')
            if any(float(v) for k, v in endpoint['pricing'].items() if k not in {'prompt', 'completion', 'discount'} and v is not None):
                raise ValueError('Unexpected additional pricing')
            held = reserve(charged, budget, model)
            charged += held
            log(out / 'ledger.jsonl', dict(event='reserved', index=i, usd=held, model=model, phase=job['phase']))
            sent += 1
            start = time.monotonic()
            response = session.post(BASE + '/chat/completions', json=request, timeout=(15, 240), allow_redirects=False)
            write(out / f'http_{i}.json', dict(status_code=response.status_code, seconds=time.monotonic()-start))
            try:
                raw = response.json()
            except ValueError:
                write(out / f'response_{i}.json', dict(status=response.status_code, body=response.text))
                raise ValueError('Non-JSON HTTP response') from None
            write(out / f'response_{i}.json', raw)
            usage = raw.get('usage') or {}
            cost = usage.get('cost')
            if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
                known += cost
                charged += cost - held
                log(out / 'ledger.jsonl', dict(event='settled', index=i, usd=cost, usage=usage,
                    seconds=time.monotonic()-start, provider=raw.get('provider'), model=model, phase=job['phase']))
                if cost > held or charged > budget:
                    raise ValueError('Reported cost exceeded reservation')
            response.raise_for_status()
            if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
                raise ValueError('Missing actual cost; reservation retained')
            decision = parse(raw, request)
            write(out / f'decision_{i}.json', decision)
            valid += 1
            print(f'{model} {job["phase"]}: valid {decision["action"]}; cost=${cost:.6f}', flush=True)
        status = 'completed'
    except Exception as exc:
        # HTTP exception messages can contain data; report only safe local error types.
        error = dict(type=type(exc).__name__, index=sent-1,
                     message=str(exc) if isinstance(exc, ValueError) else 'See saved response; no retry')
    finally:
        if session is not None:
            session.close()
        summary = dict(status=status, error=error, inference_calls=sent, valid_responses=valid,
            known_cost_usd=known, charged_or_reserved_usd=charged, budget_usd=budget,
            scope='smoke only; not a performance result')
        write(out / 'summary.json', summary)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root', required=True, type=Path)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--budget-usd', type=float)
    p.add_argument('--models', nargs='+', choices=list(MODELS), default=list(MODELS))
    args = p.parse_args()
    out = args.output_root.absolute()
    if out.resolve() != out or out.parent != ROOT / 'zero-shot baseline/runs':
        p.error('Use a new direct child of zero-shot baseline/runs')
    if args.execute and args.budget_usd is None:
        p.error('--execute requires --budget-usd')
    calls = prepare(out, models=args.models)
    if args.execute:
        result = execute(out, calls, args.budget_usd)
        print(json.dumps(result))
        return 0 if result['status'] == 'completed' else 1
    print(json.dumps(dict(mode='dry-run', planned_calls=len(calls), inference_calls=0,
                         manifest_sha256=sha(out / 'manifest.json'))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
