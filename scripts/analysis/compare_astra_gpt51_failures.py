"""Read-only cross-run attribution; writes a new report, never calls a model.

Exact-input matches establish observed call-level disagreement, not a controlled
estimate of the model-only effect (reasoning effort and request date differ).
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def composition(labels):
    return {str(int(k)): int(v) for k, v in zip(*np.unique(labels, return_counts=True))}


def read_state(out, call):
    with np.load(out / 'states' / f'call_{call:05d}.npz') as s:
        return {k: s[k].copy() for k in s.files}


def matched_mask(assigns, gt, status):
    result = np.zeros(len(gt), dtype=bool)
    for cid in np.unique(assigns[assigns > 0]):
        mask = assigns == cid
        values, counts = np.unique(gt[mask & (gt > 0)], return_counts=True)
        if len(values):
            result |= mask & (gt == values[np.argmax(counts)])
    assert int(result.sum()) == status['performance']['total_tp']
    return result


def verify_inputs(out, requests):
    with (out / 'vlm_inputs/vlm_call_log.csv').open() as stream:
        saved = {int(x['call_id']): x for x in csv.DictReader(stream)}
    for request in requests:
        assert hashlib.sha256(request['prompt'].encode()).hexdigest() == request['prompt_sha256']
        entry = saved[request['call_id']]
        prompt = out / 'vlm_inputs' / entry['prompt_file']
        assert sha(prompt) == request['prompt_sha256']
        images = [out / 'vlm_inputs' / x for x in entry['image_files'].split(';')]
        assert [sha(x) for x in images] == request['image_sha256_in_order']


def compare(old, new, ch, protocols):
    from src.io.matlab_loader import load_matlab_spikes
    paths = [old / ch, new / ch]
    requests = [rows(p / 'requests.jsonl') for p in paths]
    decisions = [rows(p / 'decisions.jsonl') for p in paths]
    statuses = [json.loads((p / 'status.json').read_text()) for p in paths]
    assert all(s['status'] == 'complete' for s in statuses)
    for p, req, dec in zip(paths, requests, decisions):
        assert len(req) == len(dec)
        assert all(r['call_id'] == d['api_call_id'] for r, d in zip(req, dec))
        verify_inputs(p, req)
    mat = protocols[0]['mat_files'][ch]
    assert mat['sha256'] == protocols[1]['mat_files'][ch]['sha256'] == sha(Path(mat['path']))
    meta = load_matlab_spikes(mat['path'])
    gt = meta['curation_assigns']
    finals = [np.load(p / 'final_assigns.npy') for p in paths]
    matched = [matched_mask(a, gt, s) for a, s in zip(finals, statuses)]

    def fate(mask):
        return {name: {'retained_tp': int(np.sum(mask & m)),
                       'retained_gt_zero': int(np.sum(mask & (a > 0) & (gt == 0))),
                       'retained_gt_positive_mismatch': int(np.sum(mask & (a > 0) & (gt > 0) & ~m)),
                       'deleted_expert': int(np.sum(mask & (a == 0) & (gt > 0)))}
                for name, a, m in zip(('gpt51', 'astra'), finals, matched)}

    key = lambda r: (r['prompt_sha256'], tuple(r['image_sha256_in_order']),
                     json.dumps(r['text_format'], sort_keys=True))
    index = {}
    for req, dec in zip(requests[0], decisions[0]):
        index.setdefault(key(req), []).append((req, dec))
    pairs = []
    for req, dec in zip(requests[1], decisions[1]):
        for old_req, old_dec in index.get(key(req), []):
            left, right = read_state(paths[0], old_req['call_id']), read_state(paths[1], req['call_id'])
            assert dec['stage'] == old_dec['stage'] == 'phase1'
            lm = left['assigns'] == old_dec['cluster_id']
            rm = right['assigns'] == dec['cluster_id']
            assert np.array_equal(lm, rm)
            pairs.append({'gpt51_call': old_req['call_id'], 'astra_call': req['call_id'],
                          'cluster_id': dec['cluster_id'], 'same_cluster_membership': True,
                          'whole_state_equal': all(np.array_equal(left[k], right[k]) for k in left),
                          'prompt_sha256': req['prompt_sha256'], 'image_sha256_in_order': req['image_sha256_in_order'],
                          'gpt51_action': old_dec['action'], 'astra_action': dec['action'],
                          'gpt51_rationale': old_dec['rationale'], 'astra_rationale': dec['rationale'],
                          'gt_composition': composition(gt[lm]), 'final_fate': fate(lm)})
    first = next(p for p in pairs if p['gpt51_call'] == p['astra_call'] and p['gpt51_action'] != p['astra_action'])
    assert first['whole_state_equal']
    n = first['gpt51_call']
    assert all(key(a) == key(b) and da['action'] == db['action'] for a, b, da, db in
               zip(requests[0][:n-1], requests[1][:n-1], decisions[0][:n-1], decisions[1][:n-1]))

    cid = 31 if ch == 'CH30' else 63
    unit_calls = [next(d for d in ds if d.get('cluster_id') == cid and d['stage'] == 'phase1') for ds in decisions]
    masks = [read_state(p, d['api_call_id'])['assigns'] == cid for p, d in zip(paths, unit_calls)]
    assert np.array_equal(*masks)
    unit_requests = [rr[d['api_call_id']-1] for rr, d in zip(requests, unit_calls)]
    return {'channel': ch, 'api_calls_added': 0, 'saved_prompt_and_images_hash_verified': True,
            'first_divergence': first, 'exact_input_pairs': pairs,
            'exact_input_pair_count': len(pairs),
            'exact_input_disagreements': sum(p['gpt51_action'] != p['astra_action'] for p in pairs),
            'dominant_expert_unit': {'initial_cluster_id': cid, 'same_membership': True,
                'gt_composition': composition(gt[masks[0]]), 'gpt51_decision': unit_calls[0], 'astra_decision': unit_calls[1],
                'prompt_equal': unit_requests[0]['prompt_sha256'] == unit_requests[1]['prompt_sha256'],
                'images_equal_by_position': [a == b for a, b in zip(unit_requests[0]['image_sha256_in_order'], unit_requests[1]['image_sha256_in_order'])],
                'final_fate': fate(masks[0])},
            'terminal_tp_transition': {'both_correct': int(np.sum(matched[0] & matched[1])),
                'gpt51_only_correct': int(np.sum(matched[0] & ~matched[1])),
                'astra_only_correct': int(np.sum(~matched[0] & matched[1]))},
            'actions': {name: dict(Counter(d['stage'] + ':' + d['action'] for d in ds))
                        for name, ds in zip(('gpt51', 'astra'), decisions)},
            'performance': {name: s['performance'] for name, s in zip(('gpt51','astra'), statuses)},
            'source_hashes': {str(p / f): sha(p / f) for p in paths for f in
                              ('requests.jsonl','decisions.jsonl','final_assigns.npy','status.json')}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpt51-root', type=Path, required=True)
    parser.add_argument('--astra-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    roots = [args.gpt51_root.resolve(), args.astra_root.resolve()]
    protocols = [json.loads((p / 'protocol.json').read_text()) for p in roots]
    assert protocols[0]['source_hashes'] == protocols[1]['source_hashes']
    for root, protocol in zip(roots, protocols):
        for rel, expected in protocol['source_hashes'].items():
            assert sha(root / 'upstream' / rel) == expected
    sys.path.insert(0, str(roots[0] / 'upstream'))
    args.output_root.mkdir(parents=True, exist_ok=False)
    for ch in ('CH30','CH31'):
        result = compare(*roots, ch, protocols)
        (args.output_root / f'{ch}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('exact_input_pairs','source_hashes')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
