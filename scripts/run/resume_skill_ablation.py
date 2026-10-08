"""Explicit, single user-authorized continuation after a response-free HTTP 500.

Never edits the parent run or repeats a saved response. SDK retries remain zero.
The prior failed-call reservation remains charged against the campaign cap.
"""
from __future__ import annotations
import argparse
import copy
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run import run_skill_ablation as a
from scripts.run import run_skill_replay as r

FORMAT = 'skill-ablation-manual-continuation-v1'
FILES = ('manifest.json', 'execution_status.json', 'responses.jsonl', 'usage.jsonl',
         'budget_events.jsonl', 'errors.jsonl', 'estimate.json', 'authorize_execute.json')


def remaining_jobs(spec, responses, status):
    n = len(responses)
    if status['state'] != 'stopped' or status['valid_responses'] != n:
        raise r.ReplayStop('Parent must be stopped with a complete valid response prefix')
    if n >= len(spec['jobs']) or status['attempted_calls'] != n+1:
        raise r.ReplayStop('Ambiguous failure/attempt count; do not resend')
    failure = status.get('failure') or {}
    if failure.get('status_code') != 500 or failure.get('job_id') != spec['jobs'][n]['job_id']:
        raise r.ReplayStop('Only explicitly audited response-free HTTP 500 supported')
    for job, response in zip(spec['jobs'], responses):
        if (response['job_id'] != job['job_id'] or
                response['request_sha256'] != job['request_sha256']):
            raise r.ReplayStop('Saved response prefix/hash mismatch')
        r.decision_from_response(response['response'], job['phase'])
    return copy.deepcopy(spec['jobs'][n:])


def build(source):
    source = r.experiment_root(source)
    parent, payloads, cases = a.verify(source)
    status = r.read_json(source/'execution_status.json')
    responses = r.read_jsonl(source/'responses.jsonl')
    jobs = remaining_jobs(parent, responses, status)
    usage = r.read_jsonl(source/'usage.jsonl')
    events = r.read_jsonl(source/'budget_events.jsonl')
    if ([x['job_id'] for x in usage] != [x['job_id'] for x in responses] or
            events[-1]['state'] != 'reserved' or events[-1]['job_id'] != jobs[0]['job_id']):
        raise r.ReplayStop('Parent usage/reservation does not match stopped prefix')
    for row, response in zip(usage, responses):
        expected = r.usage_cost(response['response'])
        if any(row[k] != v for k, v in expected.items()):
            raise r.ReplayStop('Parent usage mismatch')
    spent = Decimal(str(status['charged_or_reserved_usd']))
    calculated = sum((Decimal(str(x['conservative_usd'])) for x in usage), Decimal(0)) + Decimal(str(events[-1]['reserved_usd']))
    if abs(spent-calculated) > Decimal('0.00000001'):
        raise r.ReplayStop('Parent charged/reserved balance mismatch')
    cap = Decimal(str(parent['budget_cap_usd']))
    available = (cap-spent).quantize(Decimal('.000001'), rounding=ROUND_DOWN)
    estimate = r.read_json(source/'estimate.json')
    if (estimate['manifest_sha256'] != r.file_hash(source/'manifest.json') or
            estimate['scoring_sha256'] != parent['scoring_sha256'] or estimate['pricing'] != r.PRICES):
        raise r.ReplayStop('Parent estimate binding mismatch')
    by_id = {x['job_id']: x for x in estimate['per_call']}
    selected = [by_id[j['job_id']] for j in jobs]
    for row, job in zip(selected, jobs):
        if row['request_sha256'] != job['request_sha256'] or row['conservative_reserved_usd'] != r.reservation(row['input_tokens']):
            raise r.ReplayStop('Continuation estimate/request mismatch')
    reserved = sum((Decimal(str(x['conservative_reserved_usd'])) for x in selected), Decimal(0))
    if available <= 0 or reserved > available:
        raise r.BudgetStop('Entire continuation does not fit cumulative cap')
    spec = copy.deepcopy(parent)
    spec.update(format=FORMAT, jobs=jobs, planned_calls=len(jobs),
                continuation=dict(source=str(source), source_hashes={f:r.file_hash(source/f) for f in FILES},
                  retained_responses=len(responses), campaign_cap_usd=float(cap),
                  prior_charged_or_reserved_usd=float(spent), available_budget_usd=float(available),
                  unresolved_prior_reservation_usd=events[-1]['reserved_usd'],
                  whole_remaining_reserve_usd=float(reserved),
                  code_sha256=r.file_hash(Path(__file__))))
    revised = copy.deepcopy(estimate)
    revised.update(per_call=selected, token_count_calls=0, inference_calls=0, created_at=r.now(),
                   reused_estimate_sha256=r.file_hash(source/'estimate.json'),
                   totals={k:sum(x[k] for x in selected) for k in estimate['totals']},
                   note='Same remaining request bytes; no recount. Prior failed request may be billed and is still reserved.')
    return spec, payloads, cases, revised


def prepare(source, out):
    spec, payloads, _, estimate = build(source)
    r.prepare(out, spec, payloads)
    estimate['manifest_sha256'] = r.file_hash(out/'manifest.json')
    r.write_new(out/'estimate.json', estimate)
    print({'remaining_calls':len(spec['jobs']), **spec['continuation'],
           'estimate_sha256':r.file_hash(out/'estimate.json')}, flush=True)


def verify(out):
    old = r.read_json(out/'manifest.json')['spec']
    if old['format'] != FORMAT:
        raise r.ReplayStop('Not a continuation run')
    spec, payloads, cases, _ = build(Path(old['continuation']['source']))
    if r.canonical(old) != r.canonical(spec) or r.read_jsonl(out/'requests.jsonl') != spec['jobs']:
        raise r.ReplayStop('Frozen continuation changed')
    if r.read_json(out/'dry_run.json')['manifest_sha256'] != r.file_hash(out/'manifest.json'):
        raise r.ReplayStop('Continuation manifest hash changed')
    return spec, payloads, cases


def no_other_attempt(out, source):
    for marker in (ROOT/'output').glob('curation_skill_*/execution_started.json'):
        if marker.parent.resolve() == out.resolve():
            continue
        manifest = r.read_json(marker.parent/'manifest.json').get('spec', {})
        if manifest.get('format') == FORMAT and manifest['continuation']['source'] == source:
            raise r.ReplayStop('Another continuation already attempted; explicit new accounting required')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path)
    p.add_argument('--output-root', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args(argv)
    out = r.experiment_root(args.output_root)
    if not args.execute:
        if args.source is None:
            raise r.ReplayStop('Source required for offline preparation')
        prepare(args.source, out)
        return
    spec, payloads, _ = verify(out)
    no_other_attempt(out, spec['continuation']['source'])
    budget = spec['continuation']['available_budget_usd']
    estimates = r.authorization(out, budget, spec)
    reserve = sum((Decimal(str(x['conservative_reserved_usd'])) for x in estimates.values()), Decimal(0))
    if reserve > Decimal(str(budget)):
        raise r.BudgetStop('Remaining round exceeds available budget')
    a.enable_credentials()
    print(r.execute(out, spec, payloads, budget), flush=True)


if __name__ == '__main__':
    main()
