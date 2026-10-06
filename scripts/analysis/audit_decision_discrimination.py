"""Read-only: classify every Phase-1 decision by the expert composition of the judged cluster.

Answers whether a model's KEEP/SPLIT/DISCARD carries information about MAT
curation.assigns. Uses states/call_XXXXX.npz (state before each request); never calls a model.
A cluster is unit_pure / noise_pure when >= --purity of its spikes are one expert unit / label 0.
Judgments on fragments of the same unit are not independent samples.
"""
import argparse
from collections import Counter, defaultdict
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


def category(labels, purity):
    vals, cnts = np.unique(labels, return_counts=True)
    comp = {int(k): int(v) for k, v in zip(vals, cnts)}
    n = len(labels)
    pos = {k: v for k, v in comp.items() if k != 0}
    unit, top = max(pos.items(), key=lambda kv: kv[1]) if pos else (None, 0)
    if top / n >= purity:
        return 'unit_pure', unit, comp
    if comp.get(0, 0) / n >= purity:
        return 'noise_pure', None, comp
    return 'mixed', unit, comp


def audit(out, gt, purity):
    decisions = [json.loads(x) for x in (out / 'decisions.jsonl').read_text().splitlines() if x.strip()]
    confusion = defaultdict(Counter)
    exposure = Counter()
    pure_spikes = Counter()
    discarded_expert = Counter()
    for dec in decisions:
        if dec['stage'] != 'phase1':
            continue
        with np.load(out / 'states' / f"call_{dec['api_call_id']:05d}.npz") as state:
            mask = state['assigns'] == dec['cluster_id']
        if not mask.any():
            continue
        cat, unit, comp = category(gt[mask], purity)
        confusion[cat][dec['action']] += 1
        if cat == 'unit_pure':
            exposure[unit] += 1
            pure_spikes[dec['action']] += int(mask.sum())
        if dec['action'] == 'DISCARD':
            for k, v in comp.items():
                if k:
                    discarded_expert[k] += v
    return {'status': json.loads((out / 'status.json').read_text()).get('status'),
            'phase1_decisions': sum(sum(c.values()) for c in confusion.values()),
            'confusion': {k: dict(v) for k, v in confusion.items()},
            'phase1_judgments_on_pure_expert_units': {str(k): v for k, v in exposure.items()},
            'unit_pure_spikes_by_action': dict(pure_spikes),
            'expert_spikes_in_discarded_clusters': {str(k): v for k, v in discarded_expert.items()},
            'source_hashes': {f: sha(out / f) for f in ('decisions.jsonl', 'status.json')}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='append', required=True, metavar='NAME=ROOT',
                        help='rollout root with protocol.json and per-channel outputs; repeatable')
    parser.add_argument('--channels', nargs='+', default=['CH30', 'CH31', 'CH20', 'CH3'])
    parser.add_argument('--purity', type=float, default=0.9)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    runs = dict(x.split('=', 1) for x in args.run)
    runs = {k: Path(v).resolve() for k, v in runs.items()}
    protocols = {k: json.loads((v / 'protocol.json').read_text()) for k, v in runs.items()}
    first = next(iter(runs.values()))
    sys.path.insert(0, str(first / 'upstream'))
    from src.io.matlab_loader import load_matlab_spikes
    args.output_root.mkdir(parents=True, exist_ok=False)
    result = {'api_calls_added': 0, 'purity': args.purity, 'runs': {k: str(v) for k, v in runs.items()},
              'channels': {}}
    for ch in args.channels:
        mats = {p['mat_files'][ch]['sha256'] for p in protocols.values()}
        mat = next(iter(protocols.values()))['mat_files'][ch]
        assert mats == {sha(Path(mat['path']))}, f'{ch}: MAT hash mismatch across runs'
        gt = np.asarray(load_matlab_spikes(mat['path'])['curation_assigns']).astype(int)
        result['channels'][ch] = {name: audit(root / ch, gt, args.purity)
                                  for name, root in runs.items() if (root / ch / 'decisions.jsonl').exists()}
        for name, row in result['channels'][ch].items():
            print(ch, name, row['status'], row['confusion'], row['phase1_judgments_on_pure_expert_units'], flush=True)
    (args.output_root / 'decision_discrimination.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
