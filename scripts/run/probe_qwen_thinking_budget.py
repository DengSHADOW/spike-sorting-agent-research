"""Single-request capability probe, never a rollout or a scored prediction.

Default: offline preparation, no credentials/client. --execute: one request,
no retries/fallbacks/actions, <= $0.03 reservation inside the existing $25 cap.
Reuse one previously authorized CH31 observation; only reasoning.max_tokens changes.
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
sys.path.insert(0, str(ROOT))
from scripts.run import run_openrouter_smoke as io

SOURCE = ROOT / 'zero-shot baseline/runs/qwen9b_rollout_comparison_20261013_4000_v1'
STEP = Path('attempt_001/minimal/cM2-e004_004-006_CH31/step_00002')
MODEL = 'qwen/qwen3.5-9b'
REVISION = 'c202236235762e1c871ad0ccb60c8ee5ba337b9a'


def build(source, budget):
    if budget not in (128, 3000):
        raise ValueError('Only predeclared diagnostic budgets 128/3000')
    meta = io.read(source / 'calls/000002/request.json')
    saved = io.read(source / STEP / 'request.json')
    if saved['payload'] != meta['request'] or saved['request_sha256'] != meta['request_sha256']:
        raise ValueError('Source request mismatch')
    request = copy.deepcopy(meta['request'])
    hashes = {}
    for part, image in zip(request['messages'][1]['content'][1:], saved['display']['images'], strict=True):
        name = image['name'] + '.png'
        if Path(name).name != name:
            raise ValueError('Invalid image path')
        path = source / STEP / name
        actual = io.sha(path)
        if actual != image['sha256'] or actual != part['image_url']['url']['png_sha256']:
            raise ValueError('Image hash mismatch')
        hashes[name] = actual
        part['image_url']['url'] = 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()
    if io.digest(request) != meta['request_sha256']:
        raise ValueError('Original request hash mismatch')
    if request['model'] != MODEL or request['max_tokens'] != 4000:
        raise ValueError('Unexpected original settings')
    if request['provider'] != dict(only=['deepinfra/bf16'], allow_fallbacks=False,
                                  require_parameters=True, quantizations=['bf16']):
        raise ValueError('Unexpected provider settings')
    if request['reasoning'] != dict(enabled=True, exclude=False):
        raise ValueError('Unexpected reasoning settings')
    request['reasoning']['max_tokens'] = budget
    restored = copy.deepcopy(request)
    del restored['reasoning']['max_tokens']
    assert io.digest(restored) == meta['request_sha256']
    return request, hashes


def assess(raw, request, tokenizer):
    choices = raw.get('choices') or []
    if len(choices) != 1:
        return dict(status='rejected_or_no_completion', error=raw.get('error'))
    choice, usage = choices[0], raw.get('usage') or {}
    message = choice.get('message') or {}
    reasoning = message.get('reasoning')
    if not isinstance(reasoning, str):
        reasoning = ''.join(d.get('text', '') for d in message.get('reasoning_details', [])
                            if d.get('type') == 'reasoning.text')
    content = message.get('content') or ''
    # Independent official tokenizer count of returned text (no chat/special tokens).
    count = len(tokenizer.encode(reasoning, add_special_tokens=False).ids)
    requested = request['reasoning']['max_tokens']
    try:
        io.parse(raw, request)
        valid = True
    except (ValueError, TypeError, KeyError):
        valid = False
    # Eight tokens allow boundary/retokenization effects, not a general tolerance claim.
    # Passing one probe never proves a hard cap; a gross excess falsifies its use here.
    exceeded = count > requested + 8
    status = ('budget_not_enforced' if exceeded else
              'truncated_not_usable' if choice.get('finish_reason') == 'length' else
              'within_budget_observed_not_proven' if valid and reasoning else 'inconclusive')
    return dict(status=status, finish_reason=choice.get('finish_reason'), valid_action_json=valid,
                requested_thinking_tokens=requested, independent_reasoning_text_tokens=count,
                reported_reasoning_tokens=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens'),
                completion_tokens=usage.get('completion_tokens'), content_characters=len(content),
                reasoning_characters=len(reasoning), budget_exceeded_beyond_boundary_allowance=exceeded,
                model=raw.get('model'), provider=raw.get('provider'),
                limitation='Returned-text retokenization is not server generation accounting; no accuracy/causal claim.')


def prepare(out, budget, source=SOURCE, previous_probe=None):
    request, images = build(source, budget)
    parent = io.read(source / 'cost_breakdown.json')
    prior = parent['charged_or_reserved_usd']
    previous = None
    if previous_probe is not None:
        previous_probe = previous_probe.resolve()
        if not previous_probe.is_relative_to((ROOT / 'zero-shot baseline/runs').resolve()):
            raise ValueError('Previous probe outside run directory')
        previous_result = io.read(previous_probe / 'result.json')
        previous_manifest = io.read(previous_probe / 'manifest.json')
        if (budget != 3000 or previous_manifest['thinking_budget'] != 128
                or previous_manifest['source_cost_sha256'] != io.sha(source / 'cost_breakdown.json')
                or previous_result['costs']['prior_charged_or_reserved_usd'] != prior):
            raise ValueError('Invalid probe cost lineage')
        prior = previous_result['costs']['cumulative_charged_or_reserved_usd']
        previous = dict(path=str(previous_probe.relative_to(ROOT)),
                        result_sha256=io.sha(previous_probe / 'result.json'))
    if not math.isfinite(prior) or not 0 <= prior <= 25:
        raise ValueError('Invalid inherited budget')
    out.mkdir(parents=True, exist_ok=False)
    io.write(out / 'request.json', io.redact_images(request))
    io.write(out / 'manifest.json', dict(kind='qwen9b-independent-thinking-budget-capability-probe',
        source=str(source.relative_to(ROOT)), source_request_sha256=io.sha(source / STEP / 'request.json'),
        source_cost_sha256=io.sha(source / 'cost_breakdown.json'), request_sha256=io.digest(request),
        source_manifest_sha256=io.sha(source / 'manifest.json'), images=images,
        script_sha256=io.sha(__file__), thinking_budget=budget, max_tokens=4000,
        prior_charged_or_reserved_usd=prior, campaign_cap_usd=25, probe_cap_usd=.03,
        previous_probe=previous,
        reservation_usd=io.reservation(MODEL), planned_calls=1, retries=0, apply_actions=False,
        tokenizer_model='Qwen/Qwen3.5-9B', tokenizer_revision=REVISION,
        authorization='User requested capability verification; prior CH31/OpenRouter/DeepInfra authorization and cumulative $25 apply.',
        conclusion_rule='HTTP acceptance is not enforcement; independently count reasoning; > budget+8 is a violation; short output alone does not prove enforcement.'))
    print(json.dumps(dict(mode='dry_run', calls_sent=0, request_sha256=io.digest(request))))


def execute(out, tokenizer_json, tokenizer_lib, factory=io.make_session):
    m = io.read(out / 'manifest.json')
    source = ROOT / m['source']
    previous = m.get('previous_probe')
    if previous and io.sha(ROOT / previous['path'] / 'result.json') != previous['result_sha256']:
        raise ValueError('Previous probe ledger changed')
    request, _ = build(source, m['thinking_budget'])
    if (m['script_sha256'] != io.sha(__file__) or m['request_sha256'] != io.digest(request)
            or m['source_cost_sha256'] != io.sha(source / 'cost_breakdown.json')
            or m['source_request_sha256'] != io.sha(source / STEP / 'request.json')
            or m['source_manifest_sha256'] != io.sha(source / 'manifest.json')
            or io.read(out / 'request.json') != io.redact_images(request)):
        raise ValueError('Frozen artifacts changed')
    held = io.reservation(MODEL)
    prior = m['prior_charged_or_reserved_usd']
    if held > .03 or prior + held > 25:
        raise ValueError('Insufficient budget')
    sys.path.insert(0, str(tokenizer_lib))
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(tokenizer_json))
    # Exclusive creation prevents repeated sends and preserves unknown costs on interruption.
    io.write(out / 'execution_started.json', dict(request_sha256=io.digest(request),
        reserved_usd=held, tokenizer_sha256=io.sha(tokenizer_json), tokenizer_revision=REVISION))
    session = factory()
    try:
        response = session.get(io.BASE + '/models/' + MODEL + '/endpoints', timeout=(15, 30), allow_redirects=False)
        response.raise_for_status()
        catalog = response.json()
        io.write(out / 'endpoint_catalog.json', catalog)
        endpoint = next(e for e in catalog['data']['endpoints'] if e['tag'] == 'deepinfra/bf16')
        if (endpoint['context_length'] != io.CONTEXT or endpoint['quantization'] != 'bf16'
                or float(endpoint['pricing']['prompt']) != 1e-7 or float(endpoint['pricing']['completion']) != 1.5e-7
                or any(float(v) for k, v in endpoint['pricing'].items() if k not in {'prompt','completion','discount'} and v is not None)
                or not {'reasoning','structured_outputs','response_format','max_tokens'}.issubset(endpoint['supported_parameters'])):
            raise ValueError('Endpoint changed')
        io.write(out / 'reserved.json', dict(usd=held, request_sha256=io.digest(request)))
        print('SENDING ONE CAPABILITY REQUEST; max_tokens=4000; reasoning.max_tokens=' + str(m['thinking_budget']), flush=True)
        start = time.monotonic()
        response = session.post(io.BASE + '/chat/completions', json=request, timeout=(15, 600), allow_redirects=False)
        io.write(out / 'http.json', dict(status=response.status_code, seconds=time.monotonic()-start))
        raw = response.json()
        io.write(out / 'response.json', raw)
        cost = (raw.get('usage') or {}).get('cost')
        known = type(cost) in (float, int) and math.isfinite(cost) and cost >= 0
        ledger = dict(known_usd=cost if known else None, unsettled_reservation_usd=0 if known else held,
                      prior_charged_or_reserved_usd=prior, cumulative_charged_or_reserved_usd=prior+(cost if known else held))
        io.write(out / 'cost.json', ledger)
        if known and cost > held:
            raise ValueError('Cost exceeded reservation')
        result = assess(raw, request, tokenizer)
        result.update(http_status=response.status_code, costs=ledger, calls_sent=1, actions_applied=0)
        io.write(out / 'result.json', result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        session.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root', type=Path, required=True)
    p.add_argument('--thinking-budget', type=int, choices=(128, 3000), default=128)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--tokenizer-json', type=Path)
    p.add_argument('--tokenizer-lib', type=Path)
    p.add_argument('--previous-probe', type=Path)
    args = p.parse_args()
    if args.execute:
        if not args.tokenizer_json or not args.tokenizer_lib:
            p.error('Execution requires pinned local tokenizer files')
        execute(args.output_root, args.tokenizer_json, args.tokenizer_lib)
    else:
        prepare(args.output_root, args.thinking_budget, previous_probe=args.previous_probe)


if __name__ == '__main__':
    main()
