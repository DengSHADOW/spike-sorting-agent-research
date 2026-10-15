"""Summary figure of all zero-shot results on the four annotated channels (read-only; values come from saved scores)."""
from pathlib import Path
import json
import sys
import textwrap

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / 'zero-shot baseline/runs'
OUT = ROOT / 'zero-shot baseline/results/figures'
CHANNELS = ['cM2-e004_004-006_CH31', 'cM2-e004_004-006_CH3', 'cM2-e004_011-015_CH20', 'cM2-e008_021-028_CH30']
SHORT = [c.rsplit('_', 1)[1] for c in CHANNELS]
ACTIONS = ['DISCARD', 'SPLIT', 'MERGE']

# Reference palette, categorical slots 1-4 (validated adjacent-pair set); color follows the model.
COLOR = {'opus': '#2a78d6', 'astra': '#eb6834', 'q27': '#1baf7a', 'q9': '#eda100', 'gemma': '#e87ba4'}
INK, INK2, GRID, REF = '#0b0b0b', '#52514e', '#e4e3df', '#8a8984'
NAME = {'opus': 'Claude Opus 5.5', 'astra': 'Astra (OpenAI)', 'q27': 'Qwen3.5-27B', 'q9': 'Qwen3.5-9B'}

# Gemma-4-31B minimal zero-shot rollout (one run per channel), transcribed from the Gemma4 report of 2026-10-13
# (REPORT.md, sections 4-5); its run files are not in this repository. User confirmed the same condition and code.
# F1: the report's F1; per its terminal description (CH30 keeps only unit 31; CH31 keeps unit 63 plus one 18,893-spike
# noise cluster; CH3/CH20 keep no expert spikes) the one-to-one F1 equals these values. Units matched and deleted
# expert spikes follow the same description (CH31 deleted = units 1 + 56 = 7,272 + 4,162).
GEMMA = dict(f1={'cM2-e004_004-006_CH31': 0.7195, 'cM2-e004_004-006_CH3': 0.0,
                 'cM2-e004_011-015_CH20': 0.0, 'cM2-e008_021-028_CH30': 0.7162},
             units=2, deleted=18364 + 18465 + 28823 + 11461 + 7272 + 4162)


def read(path):
    return json.loads(Path(path).read_text())


def stepwise_rows(score):
    """Accuracy per channel/overall (failures count as wrong) and recall per expert action over planned support."""
    acc = [score['by_dataset'][c]['correct'] / score['by_dataset'][c]['planned'] for c in CHANNELS]
    o = score['overall']
    acc.append(o['correct'] / o['planned'])
    rec = []
    for a in ACTIONS:
        v = o['per_class'][a]
        planned = v.get('planned_support', v.get('evaluated_support'))
        hit = round(v['recall'] * v['evaluated_support']) if v.get('recall') is not None else 0
        rec.append(hit / planned)
    return acc, rec, o


def load():
    opus = read(RUNS / 'minimal_opus55_high_dual_20261012_v3_full/score.json')
    astra_step = read(RUNS / 'minimal_astra_high_dual_20261007_v3/score.json')['stepwise']
    astra_term = read(ROOT / 'zero-shot baseline/results/minimal_astra_high_dual_20261007_resume150/'
                             'terminal_scores_20261012/terminal_scores.json')['datasets']
    q27 = read(RUNS / 'openrouter_qwen27b_tuned4000_20261013/attempt_003/score.json')
    q9 = read(RUNS / 'openrouter_qwen_dual_20261012_8000_retry429_v5/attempt_001/score.json')['stepwise']['qwen/qwen3.5-9b']
    refs = read(ROOT / 'output/annotated_offline_references_20261012/summary.json')['datasets']
    step = {'opus': stepwise_rows(opus['stepwise']), 'astra': stepwise_rows(astra_step),
            'q27': stepwise_rows(q27['stepwise']), 'q9': stepwise_rows(q9)}

    def by_id(rows):
        return {r['dataset_id']: r for r in rows}
    term = {'opus': by_id(opus['datasets']), 'astra': by_id(astra_term)}
    v0 = by_id(q27['rollouts']['skill_v0'])
    if any(v0[c]['status'] != 'completed' for c in CHANNELS):
        raise ValueError('27B skill v0 rollout incomplete')
    term['q27v0'] = v0
    minimal27 = {r['dataset_id']: r['status'] for r in q27['rollouts']['minimal']}
    ref = {}
    for c in CHANNELS:
        trials = refs[c]['trials']
        pick = lambda p: [t['scores']['one_to_one'] for t in trials if t['policy'] == p]
        ref[c] = dict(no_curation=pick('no_curation')[0], oracle=pick('oracle_purity_v1')[0],
                      random=float(np.mean([s['full_output']['f1'] for s in pick('random')])))
    return step, term, minimal27, ref


def style(ax):
    ax.spines[['top', 'right']].set_visible(False)
    ax.spines[['left', 'bottom']].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def grouped(ax, groups, series, width=0.19, label_groups=(), fmt='{:.0%}'):
    x = np.arange(len(groups))
    n = len(series)
    for i, (key, vals, kw) in enumerate(series):
        pos = x + (i - (n - 1) / 2) * (width + 0.02)
        bars = ax.bar(pos, vals, width, color=kw.get('color', COLOR.get(key)), hatch=kw.get('hatch'),
                      edgecolor=kw.get('edge', 'white'), linewidth=kw.get('lw', 0.8), label=kw['label'], zorder=3)
        for gi, (b, v) in enumerate(zip(bars, vals)):
            if gi in label_groups:
                ax.text(b.get_x() + b.get_width() / 2, v + 0.015, fmt.format(v), ha='center', va='bottom',
                        fontsize=7.5, color=INK2)
    ax.set_xticks(x, groups)
    return x


def main():
    step, term, minimal27, ref = load()
    order = ['opus', 'astra', 'q27', 'q9']
    fig, axes = plt.subplots(2, 2, figsize=(14, 10.2))
    fig.patch.set_facecolor('#fcfcfb')

    # A: stepwise accuracy
    ax = axes[0, 0]
    style(ax)
    grouped(ax, SHORT + ['Overall'], [(k, step[k][0], dict(label=NAME[k])) for k in order],
            label_groups=range(5))
    ax.set_ylim(0, 0.75)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_title('A  Stepwise accuracy on expert actions (n = 54 / 97 / 53 / 90; 294 total)',
                 loc='left', fontsize=11, color=INK)
    q9 = step['q9'][2]
    ax.text(0.99, 0.97, f'Qwen3.5-9B: {q9["failed"]} of 294 truncated (counted wrong)',
            transform=ax.transAxes, ha='right', va='top', fontsize=8, color=INK2)

    # B: recall by expert action
    ax = axes[0, 1]
    style(ax)
    grouped(ax, [f'{a}\n(n = {n})' for a, n in zip(ACTIONS, (158, 126, 10))],
            [(k, step[k][1], dict(label=NAME[k])) for k in order], label_groups=range(3))
    ax.set_ylim(0, 1.12)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_title('B  Stepwise recall by expert action (no KEEP / NOT_MERGE labels)', loc='left', fontsize=11, color=INK)

    # C: rollout one-to-one F1
    ax = axes[1, 0]
    style(ax)
    f1 = lambda rows: [rows[c]['one_to_one']['full_output']['f1'] for c in CHANNELS]
    vals = {'opus': f1(term['opus']), 'astra': f1(term['astra']), 'q27v0': f1(term['q27v0'])}
    series = [('opus', vals['opus'] + [np.mean(vals['opus'])], dict(label='Opus 5.5 (minimal)')),
              ('astra', vals['astra'] + [np.mean(vals['astra'])], dict(label='Astra (minimal)')),
              ('q27v0', vals['q27v0'] + [np.mean(vals['q27v0'])],
               dict(label='Qwen3.5-27B (skill v0 rule prompt)', color='white', edge=COLOR['q27'], hatch='////', lw=1.2)),
              ('gemma', [GEMMA['f1'][c] for c in CHANNELS] + [np.mean([GEMMA['f1'][c] for c in CHANNELS])],
               dict(label='Gemma-4-31B (minimal; from Gemma4 report)'))]
    x = grouped(ax, SHORT + ['Mean'], series, width=0.19, label_groups=range(5), fmt='{:.2f}')
    nc = [ref[c]['no_curation']['full_output']['f1'] for c in CHANNELS]
    rnd = [ref[c]['random'] for c in CHANNELS]
    ax.scatter(x, nc + [np.mean(nc)], marker='_', s=900, color=INK, linewidths=2, zorder=4, label='No curation (floor)')
    ax.scatter(x, rnd + [np.mean(rnd)], marker='D', s=26, facecolor='white', edgecolor=INK2, zorder=4,
               label='Random policy, mean of 10 seeds')
    ax.axhline(1.0, color=REF, linestyle=(0, (4, 3)), linewidth=1.2, zorder=2)
    ax.text(x[-1] + 0.4, 1.02, 'Restricted oracle = 1.0 (all channels)', va='bottom', ha='right', fontsize=8, color=INK2)
    ax.set_ylim(0, 1.18)
    ax.set_title('C  Full rollout: one-to-one F1 (IoU ≥ 0.5, all output counted)', loc='left', fontsize=11, color=INK)
    stopped = sum(s == 'stopped_invalid_action' for s in minimal27.values())
    ax.text(0.01, 0.97, f'Qwen3.5-27B minimal: {stopped}/4 channels stopped on a non-executable SPLIT (no score)\n'
                        'Qwen3.5-9B: rollout not run',
            transform=ax.transAxes, ha='left', va='top', fontsize=8, color=INK2)
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.08), ncol=3, fontsize=8, frameon=False, markerscale=0.6)

    # D: expert units recovered and expert spikes deleted
    ax = axes[1, 1]
    style(ax)
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    units = lambda rows: sum(rows[c]['one_to_one']['n_matched_units'] for c in CHANNELS)
    lost = lambda rows: sum(rows[c]['one_to_one']['discarded_expert_spikes'] for c in CHANNELS)
    rows = [('Restricted oracle', 9, 0, dict(color=REF)),
            ('Opus 5.5 (minimal)', units(term['opus']), lost(term['opus']), dict(color=COLOR['opus'])),
            ('Astra (minimal)', units(term['astra']), lost(term['astra']), dict(color=COLOR['astra'])),
            ('Qwen3.5-27B (skill v0)', units(term['q27v0']), lost(term['q27v0']),
             dict(color='white', edgecolor=COLOR['q27'], hatch='////', linewidth=1.2)),
            ('Gemma-4-31B (minimal)', GEMMA['units'], GEMMA['deleted'], dict(color=COLOR['gemma'])),
            ('No curation', sum(ref[c]['no_curation']['n_matched_units'] for c in CHANNELS), 0, dict(color=INK))]
    y = np.arange(len(rows))[::-1]
    for yi, (label, u, l, kw) in zip(y, rows):
        ax.barh(yi, u, 0.5, zorder=3, **kw)
        note = f'{u} / 9' + (f'   ·   {l:,} expert spikes deleted' if l else '   ·   0 expert spikes deleted')
        ax.text(u + 0.12, yi, note, va='center', fontsize=8.5, color=INK2)
    ax.set_yticks(y, [r[0] for r in rows])
    ax.set_xlim(0, 13.5)
    ax.set_xticks(range(0, 10))
    ax.set_xlabel('Expert units matched across the four channels (9 in total)', fontsize=9, color=INK2)
    ax.set_title('D  Expert units recovered after rollout', loc='left', fontsize=11, color=INK)

    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=4, frameon=False, fontsize=10, bbox_to_anchor=(0.5, 0.995))
    fig.suptitle('Zero-shot curation baselines on four annotated development channels', y=1.03, fontsize=14, color=INK)
    note = ('Same engineering_v1 controller and observation v1 for all runs; one run per condition. Minimal = minimal zero-shot prompt; '
             'skill v0 = system_v1 + rule skill (different prompt, not a model ranking). Opus 5.5: high effort, 8000-token cap. '
             'Astra: high, 4000. Qwen via OpenRouter/DeepInfra; 27B: FP8, official thinking-mode sampling, 4000; '
             '9B: BF16, provider-default sampling, 8000. Gemma-4-31B (google/gemma-4-31b-it via OpenRouter): values from the Gemma4 report '
             '(2026-10-13), run files not in this repository; no stepwise run.\nChannels are previously inspected development data, not a held-out test. '
             'Restricted oracle uses expert labels (label-privileged reachability bound, not a fair model baseline).')
    note = '\n'.join(textwrap.fill(part, 175) for part in note.split('\n'))
    fig.text(0.01, -0.01, note, fontsize=8, color=INK2, va='top')
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    OUT.mkdir(parents=True, exist_ok=True)
    for ext in ('png', 'svg'):
        fig.savefig(OUT / f'zero_shot_summary_20261013.{ext}', dpi=200, bbox_inches='tight', facecolor=fig.get_facecolor())
    print(OUT / 'zero_shot_summary_20261013.png')
    return 0


if __name__ == '__main__':
    sys.exit(main())
