"""Read-only audit of engineering_v1 scheduling effects in completed rollouts (no model calls).

Per run and channel: Phase 1 / re-check / Phase 2 decision counts, expert spikes lost in re-check DISCARD,
Phase 2 merge composition, clusters left unresolved, and the offline counterfactual in which unresolved
small clusters are deleted (upstream semantics; model answers unchanged because later requests do not
depend on whether an earlier source was kept).
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import h5py
import numpy as np
from scipy.io import loadmat

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.analysis import curation_one_to_one  # noqa: E402
from scripts.run import run_openrouter_smoke as transport  # noqa: E402

RUNS = ROOT / 'zero-shot baseline/runs'
DATASETS = ['cM2-e004_004-006_CH31', 'cM2-e004_004-006_CH3', 'cM2-e004_011-015_CH20', 'cM2-e008_021-028_CH30']
ROWS = {r['dataset_id']: r for r in transport.read(
    RUNS / 'openrouter_qwen_dual_20261012_8000_retry429_v5/manifest.json')['datasets']}


def ground_truth(ds):
    path = ROOT / ROWS[ds]['mat_path']
    if transport.sha(path) != ROWS[ds]['mat_sha256']:
        raise ValueError(f'MAT changed: {path}')
    if h5py.is_hdf5(path):
        with h5py.File(path, 'r') as f:
            return f['spikes/curation/assigns'][:].flatten()
    return np.asarray(loadmat(str(path), struct_as_record=False, squeeze_me=True,
                              variable_names=['spikes'])['spikes'].curation.assigns).reshape(-1)


def run_dirs():
    astra = {r['dataset_id']: (ROOT / r['terminal_path']).parent for r in transport.read(
        ROOT / 'zero-shot baseline/results/minimal_astra_high_dual_20261007_resume150/'
               'terminal_scores_20261012/terminal_scores.json')['datasets']}
    return {
        'opus_minimal': {ds: RUNS / 'minimal_opus55_high_dual_20261012_v3_full/datasets' / ds for ds in DATASETS},
        'astra_minimal': astra,
        'qwen27b_skill_v0': {ds: RUNS / 'openrouter_qwen27b_tuned4000_20261013/attempt_003/skill_v0' / ds
                             for ds in DATASETS},
    }


def composition(assigns, gt, cid):
    values, counts = np.unique(gt[assigns == cid], return_counts=True)
    return {int(v): int(c) for v, c in zip(values, counts)}


ASTRA_LINEAGE = ['minimal_astra_high_dual_20261007_v3', 'minimal_astra_high_dual_20261007_resume100',
                 'minimal_astra_high_dual_20261007_retry503', 'minimal_astra_high_dual_20261007_resume150']


def step_request(d, k):
    """A resumed channel keeps earlier step folders in its parent runs; search the lineage."""
    for folder in [d] + [RUNS / n / 'datasets' / d.name for n in ASTRA_LINEAGE]:
        f = folder / f'step_{k:05d}' / 'request.json'
        if f.exists():
            return json.loads(f.read_text())
    raise FileNotFoundError(f'step {k} of {d}')


def audit_channel(d, gt):
    events = [json.loads(line) for line in (d / 'events.jsonl').read_text().splitlines()]
    context = {e['calls']: step_request(d, e['calls']).get('context') for e in events if e['event'] == 'decision'}
    counts, merges = Counter(), Counter()
    recheck, ops, recheck_loss, noise_into_units = [], 0, 0, 0
    for e in events:
        if e['event'] == 'decision':
            kind = ('recheck' if context[e['calls']] == 'phase2_prevalidation'
                    else 'phase2' if e['phase'] == 'phase2' else 'phase1')
            counts[f'{kind}_{e["action"]}'] += 1
            if kind == 'recheck':
                recheck.append([int(e['cid']), e['action']])
        elif e['event'] == 'executed':
            ops += 1
            before = None
            if e['action'] in ('MERGE', 'DISCARD'):
                before = np.load(d / f'operation_{ops:05d}_before.npz')['assigns']
            if e['action'] == 'DISCARD' and context.get(e['calls']) == 'phase2_prevalidation':
                recheck_loss += int(((before == e['cid']) & (gt > 0)).sum())
            if e['action'] == 'MERGE':
                s, t = composition(before, gt, e['cid']), composition(before, gt, e['target'])
                sd, td = max(s, key=s.get), max(t, key=t.get)
                kind = ('noise_to_noise' if sd == 0 and td == 0 else 'noise_to_unit' if sd == 0
                        else 'unit_to_noise' if td == 0 else 'same_unit' if sd == td else 'unit_to_other_unit')
                merges[kind] += 1
                if td != 0:
                    noise_into_units += s.get(0, 0)
        elif e['event'] in ('preserved_unresolved', 'preserved_ineligible'):
            counts[e['event']] += 1
    terminal = np.load(d / 'terminal.npz')['assigns']
    unresolved = [int(e['cid']) for e in events if e['event'] == 'preserved_unresolved']
    alt = terminal.copy()
    alt[np.isin(alt, unresolved)] = 0
    actual, cf = curation_one_to_one.evaluate(terminal, gt), curation_one_to_one.evaluate(alt, gt)
    return dict(
        decisions=dict(sorted(counts.items())), recheck=recheck,
        phase2_skipped=not any(k.startswith('phase2_') for k in counts),
        recheck_discard_expert_spikes_lost=recheck_loss, merges=dict(merges),
        noise_spikes_merged_into_expert_clusters=noise_into_units,
        unresolved_clusters=len(unresolved),
        unresolved_noise_spikes=int(sum(((terminal == c) & (gt == 0)).sum() for c in unresolved)),
        unresolved_expert_spikes=int(sum(((terminal == c) & (gt > 0)).sum() for c in unresolved)),
        retained_noise_spikes=int(((terminal > 0) & (gt == 0)).sum()),
        one_to_one_f1=actual['full_output']['f1'], units_matched=actual['n_matched_units'],
        counterfactual_delete_unresolved=dict(
            one_to_one_f1=cf['full_output']['f1'], units_matched=cf['n_matched_units'],
            extra_expert_spikes_deleted=cf['discarded_expert_spikes'] - actual['discarded_expert_spikes'])),


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root', type=Path, required=True)
    out = p.parse_args().output_root
    out.mkdir(parents=True, exist_ok=False)
    gts = {ds: ground_truth(ds) for ds in DATASETS}
    result = dict(scoring_sha256=curation_one_to_one.evaluate(np.array([1]), np.array([1]))['scoring_sha256'],
                  controller_sha256=transport.sha(ROOT / 'src/pipeline/curation_engineering_v1.py'),
                  manager_sha256=transport.sha(ROOT / 'src/cluster/manager.py'), runs={})
    for name, dirs in run_dirs().items():
        result['runs'][name] = {ds: audit_channel(dirs[ds], gts[ds])[0] for ds in DATASETS}
    (out / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
    for name, chans in result['runs'].items():
        f1 = [c['one_to_one_f1'] for c in chans.values()]
        cf = [c['counterfactual_delete_unresolved']['one_to_one_f1'] for c in chans.values()]
        merges = Counter()
        for c in chans.values():
            merges.update(c['merges'])
        print(f'{name}: mean F1 {np.mean(f1):.4f} -> {np.mean(cf):.4f} if unresolved deleted; merges {dict(merges)}')
        for ds, c in chans.items():
            print(f'   {ds[-4:]} recheck={c["recheck"]} phase2_skipped={c["phase2_skipped"]} '
                  f'unresolved={c["unresolved_clusters"]} noise={c["unresolved_noise_spikes"]}/{c["retained_noise_spikes"]} '
                  f'F1 {c["one_to_one_f1"]:.4f}->{c["counterfactual_delete_unresolved"]["one_to_one_f1"]:.4f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
