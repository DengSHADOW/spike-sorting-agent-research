"""Offline budget-only revision; reuse counts ONLY for identical frozen requests."""
import argparse
import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run import run_skill_ablation as a
from scripts.run import run_skill_replay as r


def prepare_revision(source, out):
    source, out = r.experiment_root(source), r.experiment_root(out)
    if (source/'execution_started.json').exists():
        raise r.ReplayStop('Cannot rebind an executed/attempted experiment')
    old = r.read_json(source/'manifest.json')['spec']
    estimate = r.read_json(source/'estimate.json')
    if (estimate['manifest_sha256'] != r.file_hash(source/'manifest.json') or
            r.read_json(source/'dry_run.json')['manifest_sha256'] != r.file_hash(source/'manifest.json') or
            estimate['scoring_sha256'] != old['scoring_sha256']):
        raise r.ReplayStop('Source binding mismatch')
    if r.read_jsonl(source/'requests.jsonl') != old['jobs']:
        raise r.ReplayStop('Source request plan changed')
    current, _, _, skills = a.build_plan()
    normalized = copy.deepcopy(current)
    normalized['budget_cap_usd'] = old['budget_cap_usd']
    normalized['frozen_extensions'][str(a.BUDGET)] = old['frozen_extensions'][str(a.BUDGET)]
    if r.canonical(normalized) != r.canonical(old):
        raise r.ReplayStop('Revision changes more than budget; counts cannot be reused')
    for name, skill in skills.items():
        if r.read_json(source/'skills'/f'{name}.json') != skill:
            raise r.ReplayStop('Source candidate changed')
    counts = {x['request_sha256']: x['input_tokens'] for x in r.read_jsonl(source/'token_counts.jsonl')}
    if len(estimate['per_call']) != len(current['jobs']):
        raise r.ReplayStop('Count inventory incomplete')
    for row, job in zip(estimate['per_call'], current['jobs']):
        if (row['job_id'] != job['job_id'] or row['request_sha256'] != job['request_sha256'] or
                counts[job['request_sha256']] != row['input_tokens'] or
                row['conservative_reserved_usd'] != r.reservation(row['input_tokens'])):
            raise r.ReplayStop('Count/request/cost mismatch')
    a.prepare(out)
    revised = copy.deepcopy(estimate)
    revised.update(created_at=r.now(), manifest_sha256=r.file_hash(out/'manifest.json'),
                   token_count_calls=0, reused_token_count_calls=len(counts),
                   source_estimate=dict(path=str(source/'estimate.json'), sha256=r.file_hash(source/'estimate.json')),
                   note='Budget-only rebind; all 210 request bytes/hashes identical. No new counting or inference.')
    r.write_new(out/'estimate.json', revised)
    r.write_new(out/'budget_gate.json', a.whole_round_gate(out, current, current['budget_cap_usd']))
    print('Ready:', out, 'estimate_sha256:', r.file_hash(out/'estimate.json'))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output-root', type=Path, required=True)
    args = p.parse_args()
    prepare_revision(args.source, args.output_root)
