"""Add the ours agents' expert-replay decisions and rationales to the two public packages (2026-11-02).

Copies a public release (hardlinks), puts the live site's code on top (--live-code; the live site was newer than any
local release), then rewrites only the CH3 / CH31 packages, their manifest, the Studio files that match and show
suggestions (curation-core.js, review-working.js; review-studio.css hides the Jacob reference panel) and the cache tags
of those files in the HTML pages. Nothing is deployed and no model is called.

Suggestions per page (output/shadow/website_agents_20261102/README.md):
- Agent_1: ours on the same channel, its online run (C1 seed 0; rules and cases learned up to that step);
- Agent_2: ours learned on the other channel, frozen (the C1 transfer test, seed 0).
Each replay step is attached to the website state of the same expert step (the package's references map replay
states to website states). A single-cluster step whose request seed equals that cluster's seed on the initial
grouping (--initial-requests, the offline dry run) had the same input there, so it is also attached to the initial
grouping. --suggestions adds frozen-agent answers on the initial grouping (website_initial_suggestions.py), if run.

    .venv/bin/python mockups/curation-review/backend/build_agent_suggestions_share.py \
        --baseline output/firebase_free_share/20261028-grouping-revision-v1 --live-code <downloaded live code> \
        --destination output/firebase_free_share/20261102-agent-suggestions-v1 \
        --initial-requests output/shadow/website_agents_20261102/code/output/dryrun_offline
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

from build_server_share import ROOT, hashes

CACHE_VERSION = 'agent-suggestions-20261102-1'
FILES = ['curation-core.js', 'review-working.js', 'review-studio.css']
C1 = ROOT/'output/shadow/thinking_proposer_20261101/code/output'
RUNS = {'cM2-e004_004-006_CH3': {'Agent_1': ('seed0/ch3', 'ours learning online on CH3 (rules and cases so far)'),
                                 'Agent_2': ('transfer/c1_ch31_to_ch3_s0', 'ours learned on CH31, frozen')},
        'cM2-e004_004-006_CH31': {'Agent_1': ('seed0/ch31', 'ours learning online on CH31 (rules and cases so far)'),
                                  'Agent_2': ('transfer/c1_ch3_to_ch31_s0', 'ours learned on CH3, frozen')}}


def replace(path, data):
    if path.exists():path.unlink()  # break the hardlink to the baseline before writing
    path.write_bytes(data)


def replay_rows(data, slot, run, label, initial_seeds):
    """Website suggestion rows for one replay run, in expert order."""
    refs = {}
    for r in data['references']:
        refs.setdefault((r['review_state_sha256'], r['source'], r.get('target')), r)
    replay = C1/run/'replay'
    decisions = [json.loads(l) for l in open(replay/'events.jsonl') if '"event": "decision"' in l]
    steps = sorted(replay.glob('step_*'))
    if len(decisions) != len(steps):raise ValueError(f'{run}: decisions and steps differ')
    rows, start = [], data['initial_state_sha256']
    for i, (step, d) in enumerate(zip(steps, decisions), 1):
        q = json.loads((step/'question.json').read_text())
        sim = json.loads((step/'simulation.json').read_text())
        if (d['cid'], d['target'], d['agent_action']) != (q['cid'], q['target'], sim['agent_action']):
            raise ValueError(f'{run} {step.name}: decision does not match the step')
        ref = refs.get((q['state_sha256'], q['cid'], q['target']))
        if ref is None:raise ValueError(f'{run} {step.name}: no website state for this expert step')
        base = {'phase': ref['phase'], 'source': ref['source'], 'target': ref.get('target'), 'agent_id': slot,
                'action': d['agent_action'], 'rationale': d['rationale'], 'origin': 'expert-replay',
                'offered_actions': q['allowed'], 'agent': label, 'model': 'Qwen/Qwen3.5-27B',
                'note': f'Expert replay, step {i} of {len(steps)}: {label}.'}
        rows.append({'state_sha256': ref['state_sha256'], **base})
        seed = json.loads((step/'request_content.json').read_text())['seed']
        if q['target'] is None and ref['state_sha256'] != start and initial_seeds.get(q['cid']) == seed:
            rows.append({'state_sha256': start, **base, 'note': base['note']+' Same input as on the initial grouping.'})
    return rows


def agent_build(baseline, destination, live_code, initial_requests, suggestions=None, include_leaf_keep=False):
    if destination.exists():raise ValueError('Destination exists')
    shutil.copytree(baseline, destination, copy_function=os.link)
    public, dist = destination/'public', ROOT/'mockups/curation-review/dist'
    overlay = sorted(f.name for f in live_code.iterdir() if f.is_file() and f.suffix in ('.html', '.js', '.css'))
    for name in overlay:
        replace(public/name, (live_code/name).read_bytes())
    run = json.loads(suggestions.read_text()) if suggestions else None
    if run and (run.get('schema') != 'website-initial-agent-suggestions-v1' or run.get('scripted_offline') is not False):
        raise ValueError('Initial-grouping suggestions must come from the real model run')
    manifest_path = public/'public-data/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    counts = {}
    for row in manifest['datasets']:
        old = public/row['url'].lstrip('/')
        raw = old.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['sha256'] or len(raw) != row['bytes']:raise ValueError('Baseline package changed')
        data = json.loads(gzip.decompress(raw))
        if data.get('agent_suggestions'):raise ValueError('Baseline package already has Agent suggestions')
        start, dataset = data['initial_state_sha256'], data['dataset_id']
        target = dataset.rsplit('_', 1)[1].lower()
        seeds = {json.loads(f.read_text())['cid']: json.loads((f.parent/'request_content.json').read_text())['seed']
                 for f in (initial_requests/f'{target}_Agent_1').glob('cluster_*/question.json')}
        rows = []
        if run:  # frozen agents on the initial grouping, first, so that replay rows take precedence
            asked = [r for r in run['rows'] if r['dataset'] == dataset
                     and r['variant'] in ({'trained', 'leaf_keep'} if include_leaf_keep else {'trained'})]
            replay_state = [s['archive_state_sha256'] for s in data['review_snapshots'] if s['state_sha256'] == start]
            if len(replay_state) != 1 or {r['replay_state_sha256'] for r in asked} != set(replay_state):
                raise ValueError('Initial grouping does not match the replay state')
            rows += [{'state_sha256': start, 'phase': 'phase1', 'source': r['cid'], 'target': None, 'agent_id': r['slot'],
                      'action': r['action'], 'rationale': r['rationale'], 'origin': 'precomputed-initial-grouping',
                      'offered_actions': r['allowed'], 'agent': f"ours learned on {r['agent_source'].upper()}, frozen",
                      'model': 'Qwen/Qwen3.5-27B', 'note': f"Initial grouping: ours learned on {r['agent_source'].upper()}, frozen."}
                     for r in sorted(asked, key=lambda r: (r['slot'], r['cid']))]
        for slot, (path, label) in RUNS[dataset].items():
            rows += replay_rows(data, slot, path, label, seeds)
        data['agent_suggestions'] = rows
        data['new_model_calls'] = len(rows)
        data['package_revision'] = data.get('package_revision', 0)+1
        packed = gzip.compress(json.dumps(data, separators=(',', ':')).encode(), mtime=0)
        checksum = hashlib.sha256(packed).hexdigest()
        name = dataset+'.'+checksum[:16]+'.bin'
        old.unlink()
        (old.parent/name).write_bytes(packed)
        row.update(bytes=len(packed), sha256=checksum, url='/public-data/packages/'+name)
        counts[dataset] = {slot: {'expert_replay': sum(r['agent_id'] == slot and r['origin'] == 'expert-replay' and r['state_sha256'] != start for r in rows),
                                  'initial_grouping': sum(r['agent_id'] == slot and r['state_sha256'] == start for r in rows)}
                           for slot in ('Agent_1', 'Agent_2')}
    replace(manifest_path, (json.dumps(manifest, indent=2)+'\n').encode())
    for name in FILES:
        replace(public/name, (dist/name).read_bytes())
    pages = []
    for page in sorted(public.glob('*.html')):
        text = page.read_text()
        new = text
        for name in FILES:
            new = re.sub(rf'((?:src|href)="{re.escape(name)})(?:\?[^"]*)?"', rf'\1?{CACHE_VERSION}"', new)
        if new != text:
            replace(page, new.encode());pages.append(page.name)
    old, new = hashes(baseline/'public'), hashes(public)
    changed = sorted(f for f in set(old) | set(new) if old.get(f) != new.get(f))
    expected = set(FILES) | set(overlay) | set(pages) | {'public-data/manifest.json'} | {
        'public-data/packages/'+Path(r['url']).name for r in manifest['datasets']} | {
        f for f in old if f.startswith('public-data/packages/')}
    if set(changed) - expected:raise ValueError(f'Unexpected changes: {sorted(set(changed) - expected)}')
    index = (public/'index.html').read_text()
    assert new['index.html'] == new['review-studio.html'] and f'review-studio.css?{CACHE_VERSION}' in index
    assert f'review-working.js?{CACHE_VERSION}' in index and f'curation-core.js?{CACHE_VERSION}' in index
    assert '#jacob-reference-panel{display:none!important}' in (public/'review-studio.css').read_text()
    report = {'release': destination.name, 'baseline_release': baseline.name,
              'live_code_overlay': overlay, 'datasets': manifest['datasets'], 'agent_suggestions': counts,
              'agents': {d: {s: v[1] for s, v in slots.items()} for d, slots in RUNS.items()},
              'replay_runs': {d: {s: str(C1/v[0]) for s, v in slots.items()} for d, slots in RUNS.items()},
              'initial_grouping_frozen_run': str(suggestions) if suggestions else None, 'leaf_keep_included': include_leaf_keep,
              'changed_files': changed, 'jacob_reference_panel': 'hidden in Studio (review-studio.css)', 'deployed': False,
              'new_model_calls_in_build': 0, 'private_uploads_published': False, 'credentials_published': 0}
    (destination/'BUILD_REPORT.json').unlink()
    (destination/'BUILD_REPORT.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--baseline', type=Path, required=True);parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--live-code', type=Path, required=True, help='top-level .html/.js/.css files downloaded from the live site')
    parser.add_argument('--initial-requests', type=Path, required=True, help='offline dry run of website_initial_suggestions.py')
    parser.add_argument('--suggestions', type=Path, help='real run of website_initial_suggestions.py (optional)')
    parser.add_argument('--include-leaf-keep', action='store_true')
    args = parser.parse_args()
    print(json.dumps(agent_build(args.baseline, args.destination, args.live_code, args.initial_requests,
                                 args.suggestions, args.include_leaf_keep), indent=2))
