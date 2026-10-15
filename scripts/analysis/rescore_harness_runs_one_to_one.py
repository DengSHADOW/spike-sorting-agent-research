"""Read-only rescoring of every saved rule-prompt (harness) rollout with the project one-to-one core.

Also recomputes the historical many-to-one F1 so each run can be checked against its documented value.
No model calls; inputs are hash-checked MATs and saved terminal assignments.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.run import run_minimal_prompt_baseline as minimal  # noqa: E402
from scripts.run import run_openrouter_smoke as transport  # noqa: E402
from scripts.analysis import curation_one_to_one  # noqa: E402
from scripts.analysis.collect_curation_stage6 import evaluate as many_to_one  # noqa: E402

CH = {'CH31': 'cM2-e004_004-006_CH31', 'CH3': 'cM2-e004_004-006_CH3',
      'CH20': 'cM2-e004_011-015_CH20', 'CH30': 'cM2-e008_021-028_CH30'}
O = 'output/'
LR = O + 'legacy_reproduction/gpt51_{c}_'
RUNS = [  # (key, label, harness note, path template per short channel)
    ('hist_gpt51', 'GPT-5.1 upstream archive (historical)', 'upstream pure.py', O + 'main_gpt-5.1/{C}/final_assigns.npy'),
    ('hist_gpt41', 'GPT-4.1 upstream archive (historical)', 'upstream pure.py', O + 'main_gpt-4.1/{C}/final_assigns.npy'),
    ('neuheu', 'NeuHeu baseline.py (served gpt-4o)', 'upstream baseline.py', O + 'baseline_vlm_all_channels/{C}/final_assigns.npy'),
    ('ablation', 'Upstream ablation, no metrics (archive)', 'upstream pure.py', O + 'ablation_no_metrics/{C}/final_assigns.npy'),
    ('strict_0922', 'GPT-5.1 strict CH30 prompt on all channels (09-22)', 'upstream, protocol-mismatch stress test',
     LR + 'artifact_recovered_full_rollout_20260922/final_assigns.npy'),
    ('cautious_0923', 'GPT-5.1 cautious source prompt (09-23)', 'upstream pure.py',
     LR + 'cautious_source_full_rollout_20260923/final_assigns.npy'),
    ('protected_0923', 'GPT-5.1 cautious + delete protection (09-23)', 'local protected controller',
     LR + 'cautious_delete_protected_v2_seed0_full_rollout_20260923{retry}/final_assigns.npy'),
    ('gpt51_0928', 'GPT-5.1 medium, pinned upstream (09-28)', 'upstream bfcca625',
     O + 'upstream_baseline_bfcca625_gpt51_seed0_budget4000_20260928/{C}/final_assigns.npy'),
    ('astra_0930', 'Astra high, pinned upstream, unprotected (09-29/30)', 'upstream bfcca625',
     O + 'astra_high_channel_guards_budget70_resume_20260930/{C}/final_assigns.npy'),
]


def path_for(template, short):
    retry = '_retry1' if short == 'CH20' else ''
    return ROOT / template.format(C=short, c=short.lower(), retry=retry)


def score(assigns, gt, times, fs):
    o = curation_one_to_one.evaluate(assigns, gt)
    m = many_to_one(assigns, gt, times, fs)
    return dict(one_to_one_f1=o['full_output']['f1'], precision=o['full_output']['precision'],
                recall=o['full_output']['recall'], units_matched=o['n_matched_units'], expert_units=o['n_expert_units'],
                discarded_expert_spikes=o['discarded_expert_spikes'],
                retained_noise_spikes=o['retained_reference_noise_spikes'],
                many_to_one_f1=m.get('overall_f1_score'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root', type=Path, required=True)
    out = p.parse_args().output_root
    out.mkdir(parents=True, exist_ok=False)
    rows = {r['dataset_id']: r for r in transport.read(
        ROOT / 'zero-shot baseline/runs/openrouter_qwen_dual_20261012_8000_retry429_v5/manifest.json')['datasets']}
    result = dict(scoring_sha256=curation_one_to_one.evaluate(np.array([1]), np.array([1]))['scoring_sha256'],
                  runs={}, missing=[])
    gts = {}
    for short, ds in CH.items():
        actor, fs = minimal.load_actor(rows[ds])  # verifies the MAT hash
        import h5py
        from scipy.io import loadmat
        path = ROOT / rows[ds]['mat_path']
        if h5py.is_hdf5(path):
            with h5py.File(path, 'r') as f:
                gt = f['spikes/curation/assigns'][:].flatten()
        else:
            gt = np.asarray(loadmat(str(path), struct_as_record=False, squeeze_me=True,
                                    variable_names=['spikes'])['spikes'].curation.assigns).reshape(-1)
        gts[short] = (gt, actor.spike_times, fs, actor.assigns.copy())
    # Old no-curation floor: initial hierarchy clusters, those under 5000 spikes removed (upstream final filter).
    floor = {}
    for short, (gt, times, fs, init) in gts.items():
        a = init.copy()
        ids, counts = np.unique(a[a > 0], return_counts=True)
        a[np.isin(a, ids[counts < 5000])] = 0
        floor[short] = score(a, gt, times, fs)
        floor[short]['all_initial_one_to_one_f1'] = curation_one_to_one.evaluate(init, gt)['full_output']['f1']
    result['runs']['no_curation_ge5000'] = dict(label='No curation, keep initial clusters >= 5000 spikes',
                                               harness='upstream final filter', channels=floor)
    for key, label, harness, template in RUNS:
        chans = {}
        for short, (gt, times, fs, _) in gts.items():
            f = path_for(template, short)
            if not f.exists():
                result['missing'].append(str(f.relative_to(ROOT)))
                continue
            a = np.load(f).reshape(-1)
            if a.shape != gt.shape:
                raise ValueError(f'{f}: shape {a.shape} != {gt.shape}')
            chans[short] = dict(score(a, gt, times, fs), source=str(f.relative_to(ROOT)),
                                source_sha256=transport.sha(f))
        result['runs'][key] = dict(label=label, harness=harness, channels=chans)
    (out / 'scores.json').write_text(json.dumps(result, indent=2) + '\n')
    for key, run in result['runs'].items():
        cells = [f"{s}: {run['channels'][s]['one_to_one_f1']:.4f}/{run['channels'][s]['many_to_one_f1']:.4f}"
                 if s in run['channels'] else f'{s}: —' for s in CH]
        print(key, ' | '.join(cells))
    print('missing:', result['missing'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
