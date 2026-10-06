"""Read-only: score model-free reference policies with the upstream pooled metric.

Metric follows pinned upstream eval/metrics.py: per curated cluster take the best-overlapping
expert unit (many-to-one allowed), FP = cluster size - TP, FN = total expert spikes - TP.
Before scoring, the re-implementation must reproduce every saved complete rollout F1.
Never calls a model.
"""
import argparse
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


def score(assigns, gt):
    total_gt = int(np.sum(gt > 0))
    tp = fp = 0
    for cid in np.unique(assigns[assigns > 0]):
        mask = assigns == cid
        pos = gt[mask & (gt > 0)]
        best = int(np.bincount(pos).max()) if pos.size else 0
        tp += best
        fp += int(mask.sum()) - best
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / total_gt if total_gt else 0.0
    return {'precision': p, 'recall': r, 'f1': 2 * p * r / (p + r) if p + r else 0.0, 'tp': tp, 'fp': fp}


def size_filter(assigns, minimum):
    out = assigns.copy()
    ids, counts = np.unique(out[out > 0], return_counts=True)
    for cid in ids[counts < minimum]:
        out[out == cid] = 0
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol-root', type=Path, required=True,
                        help='rollout root whose protocol.json and upstream/ define MAT files and loader')
    parser.add_argument('--check-run', type=Path, action='append', default=[],
                        help='rollout roots whose complete channels must be reproduced by the metric')
    parser.add_argument('--channels', nargs='+', default=['CH3', 'CH20', 'CH30', 'CH31'])
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    root = args.protocol_root.resolve()
    protocol = json.loads((root / 'protocol.json').read_text())
    sys.path.insert(0, str(root / 'upstream'))
    from src.io.matlab_loader import load_matlab_spikes
    args.output_root.mkdir(parents=True, exist_ok=False)
    rows, checks = {}, []
    for ch in args.channels:
        mat = protocol['mat_files'][ch]
        assert sha(Path(mat['path'])) == mat['sha256']
        meta = load_matlab_spikes(mat['path'])
        gt = np.asarray(meta['curation_assigns']).astype(int)
        init = np.asarray(meta['hierarchy_assigns']).astype(int)
        for run in args.check_run:
            status_path = run / ch / 'status.json'
            if not status_path.exists():
                continue
            status = json.loads(status_path.read_text())
            if status.get('status') != 'complete':
                continue
            mine = score(np.load(run / ch / 'final_assigns.npy'), gt)['f1']
            saved = status['performance']['overall_f1_score']
            assert abs(mine - saved) < 1e-6, (str(run), ch, saved, mine)
            checks.append({'run': str(run), 'channel': ch, 'saved_f1': saved, 'recomputed_f1': mine})
        ids, counts = np.unique(init[init > 0], return_counts=True)
        rows[ch] = {
            'expert_label0_fraction': float(np.mean(gt == 0)),
            'expert_units': {str(k): int(v) for k, v in zip(*np.unique(gt[gt > 0], return_counts=True))},
            'initial_clusters_ge5000': int(np.sum(counts >= 5000)),
            'keep_all_then_5000_filter': score(size_filter(init, 5000), gt),
            'keep_all_no_filter': score(init, gt),
            'keep_largest_only': score(np.where(init == ids[np.argmax(counts)], init, 0), gt),
        }
        print(ch, {k: round(v['f1'], 4) for k, v in rows[ch].items() if isinstance(v, dict) and 'f1' in v}, flush=True)
    policies = ('keep_all_then_5000_filter', 'keep_all_no_filter', 'keep_largest_only')
    macro = {p: float(np.mean([rows[ch][p]['f1'] for ch in rows])) for p in policies}
    print('macro F1', {k: round(v, 4) for k, v in macro.items()})
    (args.output_root / 'model_free_baselines.json').write_text(json.dumps(
        {'api_calls_added': 0, 'metric_reproduction_checks': checks, 'channels': rows, 'macro_f1': macro},
        ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
