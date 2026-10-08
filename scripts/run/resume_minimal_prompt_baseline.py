"""Explicit budget-extension continuation; cached prefix replay is local, never re-billed."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import signal
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
from scripts.run import run_minimal_prompt_baseline as base
from scripts.analysis.collect_curation_stage6 import evaluate, deletion_audit

SCOPE = 'minimal-prompt-budget-extension-v1'
CH20, CH30 = base.DATASETS[2:]


class ReplayBoundary(Exception):
    pass


def read(path):
    return base.accounting.read_json(path)


def original(source):
    manifest = base.verify(source)
    status = read(source / 'execution_status.json')
    if status['status'] != 'stopped' or status['error'] != {'type': 'ControllerStop', 'cause': 'BudgetStop'}:
        raise ValueError('Only the audited budget stop may be resumed')
    if status['completed_datasets'] != list(base.DATASETS[:2]):
        raise ValueError('Unexpected completed dataset prefix')
    folder = source / 'datasets' / CH20
    summary = read(folder / 'summary.json')
    if summary['status'] != 'stopped' or summary['calls'] != 44:
        raise ValueError('Unexpected CH20 boundary')
    ledger = base.accounting.read_jsonl(source / 'provider_ledger.jsonl')
    reservations = {x['call'] for x in ledger if x['event'] == 'reserved'}
    usage = [x for x in ledger if x['event'] == 'usage']
    if reservations != {x['call'] for x in usage} or len(usage) != status['inference_calls']:
        raise ValueError('Unsettled or duplicate API charges')
    if abs(sum(x['conservative_usd'] for x in usage)-status['conservative_charged_or_reserved_usd']) > 1e-8:
        raise ValueError('Original ledger total mismatch')
    records = []
    for i in range(1, 46):
        step = folder / f'step_{i:05d}'
        request = read(step / 'request.json')
        raw = read(step / 'response.json') if i <= 44 else None
        if raw is not None:
            base.accounting.decision_from_response(raw, request['phase'])
        elif (step / 'response.json').exists():
            raise ValueError('Boundary request already answered')
        records.append(dict(request=request, raw=raw))
    if records[-1]['request']['state_sha256'] != summary['final_sha256']:
        raise ValueError('Budget boundary does not match stopped state')
    return manifest, status, records


def inventory(source):
    paths = [source / n for n in ('manifest.json', 'execution_status.json', 'provider_ledger.jsonl', 'score.json')]
    paths += [p for p in (source / 'datasets' / CH20).rglob('*') if p.is_file()]
    return {str(p.relative_to(source)): base.sha256(p) for p in sorted(paths)}


class PrefixProvider:
    """Return 44 saved responses only for byte-identical requests; thereafter live."""
    def __init__(self, records, live=None):
        self.records, self.live, self.index, self.pending = records, live, 0, None
        self.boundary_verified = False

    def count(self, request):
        if self.index < len(self.records):
            record = self.records[self.index]
            if base.digest(request) != record['request']['request_sha256']:
                raise ValueError('Reconstructed request differs from recorded request')
            if record['raw'] is not None:
                self.pending = base.digest(request)
                return record['raw']['usage']['input_tokens']
            self.boundary_verified = True
            if self.live is None:
                raise ReplayBoundary('Offline prefix and first unsent request verified')
        if self.live is None:
            raise ValueError('No live provider')
        return self.live.count(request)

    def respond(self, request):
        if self.index < len(self.records) and self.records[self.index]['raw'] is not None:
            if self.pending != base.digest(request):
                raise ValueError('Cached request changed after count')
            raw = copy.deepcopy(self.records[self.index]['raw'])
            self.pending = None
            self.index += 1
            return raw
        raw = self.live.respond(request)
        self.index += 1
        return raw


class PrefixController(base.MinimalController):
    def ask(self, phase, cid, target=None, *, context=None):
        p = self.provider
        if p.index < len(p.records):
            saved = p.records[p.index]['request']
            obs = saved['observation']
            source = obs.get('cluster_id', obs.get('small_cluster_id'))
            expected_target = obs.get('large_cluster_id')
            if (phase, int(cid), target, context, self.state_hash()) != (
                    saved['phase'], source, expected_target, saved.get('context'), saved['state_sha256']):
                raise ValueError('Reconstructed scheduler/state differs from recorded prefix')
        return super().ask(phase, cid, target, context=context)


def controller(source, out, records, provider, budget):
    row = next(x for x in read(source / 'manifest.json')['datasets'] if x['dataset_id'] == CH20)
    actor, fs = base.load_actor(row)
    return PrefixController(actor, CH20, out, provider=provider, budget_usd=budget, sampling_rate=fs)


def prepare(source, out):
    manifest, status, records = original(source)
    out.mkdir(parents=True, exist_ok=False)
    before = inventory(source)
    p = PrefixProvider(records)
    c = controller(source, out / 'offline_prefix', records, p, 100.)
    try:
        c.run()
    except base.ControllerStop as exc:
        if not isinstance(exc.__cause__, ReplayBoundary):
            raise
    else:
        raise ValueError('Expected a pending decision at the boundary')
    old = source / 'datasets' / CH20
    if p.index != 44 or not p.boundary_verified or c.state_hash() != read(old / 'stopped.json')['state_sha256']:
        raise ValueError('Prefix reconstruction failed')
    with np.load(old / 'stopped.npz', allow_pickle=False) as a, np.load(c.out / 'stopped.npz', allow_pickle=False) as b:
        if any(not np.array_equal(a[k], b[k]) for k in ('assigns', 'tree', 'modified', 'counter')):
            raise ValueError('Full checkpoint differs')
    if before != inventory(source):
        raise ValueError('Source changed during preparation')
    base.write_json(out / 'manifest.json', dict(version=SCOPE, source=str(source),
        source_files=before, resume_code_sha256=base.sha256(Path(__file__)),
        cumulative_budget_usd=100., prior_cost_usd=status['conservative_charged_or_reserved_usd'],
        additional_budget_authorized_usd=50., prefix_calls_reused=44, first_new_CH20_step=45,
        dataset_order=[CH20, CH30], prompt_hashes=manifest['prompt_hashes'],
        model=manifest['model'], effort=manifest['effort'], automatic_retries=0,
        proof='44 saved responses replayed offline; all state/request hashes and full stopped checkpoint identical; request 45 identical',
        pricing_source='https://developers.openai.com/api/docs/models/gpt-6-astra', pricing_checked_on='2026-10-07'))
    base.write_json(out / 'preflight.json', dict(status='passed', api_calls=0, manifest_sha256=base.sha256(out / 'manifest.json')))
    print('OFFLINE RESUME PASSED', base.sha256(out / 'manifest.json'), flush=True)


def verify(out):
    m = read(out / 'manifest.json')
    if m['version'] != SCOPE or read(out / 'preflight.json')['manifest_sha256'] != base.sha256(out / 'manifest.json'):
        raise ValueError('Resume manifest changed')
    source = base.output_path(m['source'])
    original(source)
    if m['source_files'] != inventory(source) or m['resume_code_sha256'] != base.sha256(Path(__file__)):
        raise ValueError('Frozen source or continuation code changed')
    return m, source


def authorize(out, budget):
    if type(budget) not in (int, float) or budget != 100. or (out / 'execution_started.json').exists():
        raise ValueError('Requires new cumulative $100 authorization; no repeated execution')
    path = out / 'authorize_execute.json'
    if path.is_symlink(): raise ValueError('Authorization symlink')
    a = read(path)
    if a.get('scope') != SCOPE or a.get('cumulative_budget_usd') != budget or a.get('manifest_sha256') != base.sha256(out / 'manifest.json'):
        raise ValueError('Authorization mismatch')


def collect(source, out, status):
    previous = read(source / 'score.json')
    rows = read(source / 'manifest.json')['datasets']
    results = copy.deepcopy(previous['datasets'])
    for row, result in zip(rows, results):
        folder = out / 'datasets' / row['dataset_id']
        if not (folder / 'summary.json').exists(): continue
        result.update(read(folder / 'summary.json'))
        result['final_metrics'] = None
        if result['status'] != 'completed': continue
        path = folder / 'terminal.npz'
        if base.sha256(path) != read(path.with_suffix('.json'))['sha256']: raise ValueError('Terminal changed')
        with np.load(path, allow_pickle=False) as a:
            final = a['assigns'].copy()
            if base.arrays_hash(final, a['tree']) != result['final_sha256']: raise ValueError('State mismatch')
        actor, fs = base.load_actor(row)
        mat = ROOT / row['mat_path']
        if base.h5py.is_hdf5(mat):
            with base.h5py.File(mat, 'r') as f: gt = f['spikes/curation/assigns'][:].flatten()
        else:
            gt = np.asarray(base.loadmat(str(mat), struct_as_record=False, squeeze_me=True, variable_names=['spikes'])['spikes'].curation.assigns).reshape(-1)
        result['final_metrics'] = evaluate(final, gt, actor.spike_times, fs)
        result['no_curation_metrics'] = evaluate(actor.assigns, gt, actor.spike_times, fs)
        result['deletions_containing_expert_spikes'] = deletion_audit(folder, base.accounting.read_jsonl(folder / 'events.jsonl'), gt)
    sw = previous['status']['stepwise_accounting']
    costs = dict(stepwise=sw, full_rollout=dict(
        calls=status['inference_calls']-sw['calls'],
        conservative_usd=status['conservative_charged_or_reserved_usd']-sw['conservative_usd'],
        standard_usd=status['standard_estimate_known_responses_usd']-sw['standard_usd'],
        tokens={k:status['tokens'][k]-sw['tokens'][k] for k in sw['tokens']}))
    base.write_json(out / 'score.json', dict(status=status, costs=costs, stepwise=previous['stepwise'], datasets=results,
        source_run=str(source), manifest_sha256=base.sha256(out / 'manifest.json')))
    dest = base.BASE / 'results' / out.name
    dest.mkdir(parents=True, exist_ok=False)
    lines = ['# Minimal prompt baseline continuation', '',
        f"Status: {status['status']}; source: {source.name}; cumulative cap $100.",
        'Same Astra/high, minimal prompts, observations and controller. Local replay of 44 saved CH20 decisions is not new inference or new expense.',
        'Exposed development data. Stepwise objects are supplied; no KEEP/NOT_MERGE reference labels. Terminal matching is many-to-one and can under-penalize fragmentation.', '',
        '| Evaluation | API calls | Conservative USD | Standard estimate USD |', '|---|---:|---:|---:|']
    for k,v in costs.items(): lines.append(f"| {k} | {v['calls']} | {v['conservative_usd']:.6f} | {v['standard_usd']:.6f} |")
    lines += ['', '| Dataset | Stepwise correct / eligible | Rollout status | Precision | Recall | F1 |', '|---|---:|---|---:|---:|---:|']
    for result in results:
        d = result['dataset_id']; a = previous['stepwise']['by_dataset'][d]; s = result.get('final_metrics')
        metrics = f"{s['overall_precision']:.4f} | {s['overall_recall']:.4f} | {s['overall_f1_score']:.4f}" if s else '— | — | —'
        lines.append(f"| {d} | {a['correct']}/{a['planned']} | {result['status']} | {metrics} |")
    lines += ['', f"Stop/error: {status['error']}. Partial trajectories are not terminal scores. Costs are estimates, not invoices.",
        'Original reports, data and frozen code are unchanged. Detailed tokens, checkpoint provenance and failure cases are in score.json and provider_ledger.jsonl.']
    with (dest / 'REPORT.md').open('x') as f: f.write('\n'.join(lines)+'\n')


def execute(out, budget):
    _, source = verify(out)
    authorize(out, budget)
    m, old, records = original(source)
    base.write_json(out / 'execution_started.json', dict(at=base.accounting.now(), cumulative_budget_usd=budget,
        manifest_sha256=base.sha256(out / 'manifest.json'), authorization_sha256=base.sha256(out / 'authorize_execute.json')))
    (out / 'responses').mkdir()
    live = None; status = 'stopped'; error = None; completed = list(old['completed_datasets']); started = monotonic()
    try:
        live = base.infra.LiveProvider(base.infra.make_client(), out, budget)
        live.charged = old['conservative_charged_or_reserved_usd']
        live.standard = old['standard_estimate_known_responses_usd']
        live.calls = old['inference_calls']; live.tokens = copy.deepcopy(old['tokens'])
        base.infra.append(out, dict(event='carry_forward', dataset_id='campaign', prior_calls=live.calls,
            prior_conservative_usd=live.charged, cumulative_budget_usd=budget, source_run=str(source)))
        live.dataset = CH20
        prefix_cost = sum(base.accounting.usage_cost(x['raw'])['conservative_usd'] for x in records if x['raw'] is not None)
        c = controller(source, out / 'datasets' / CH20, records, PrefixProvider(records, live), budget-live.charged+prefix_cost)
        c.run(); completed.append(CH20)
        print('CH20 COMPLETE; cumulative $',live.charged,flush=True)
        verify(out)
        row = next(r for r in m['datasets'] if r['dataset_id'] == CH30)
        actor, fs = base.load_actor(row)
        live.dataset = CH30; live.expected_first = {CH30:row['first_request_sha256']}
        c = base.MinimalController(actor, CH30, out / 'datasets' / CH30, provider=live,
            budget_usd=budget-live.charged, sampling_rate=fs)
        c.run(); completed.append(CH30); status='completed'
    except BaseException as exc:
        error = dict(type=type(exc).__name__, cause=type(exc.__cause__).__name__ if exc.__cause__ else None)
        raise
    finally:
        result = dict(status=status, error=error, completed_datasets=completed,
            inference_calls=live.calls if live else old['inference_calls'],
            tokens=live.tokens if live else old['tokens'],
            conservative_charged_or_reserved_usd=live.charged if live else old['conservative_charged_or_reserved_usd'],
            standard_estimate_known_responses_usd=live.standard if live else old['standard_estimate_known_responses_usd'],
            stepwise_accounting=old['stepwise_accounting'], seconds_this_segment=monotonic()-started, at=base.accounting.now())
        base.write_json(out / 'execution_status.json', result)
        collect(source, out, result)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True); p.add_argument('--output-root', required=True)
    p.add_argument('--execute', action='store_true'); p.add_argument('--cumulative-budget-usd',type=float)
    a=p.parse_args(); source=base.output_path(a.source); out=base.output_path(a.output_root)
    if source==out: raise ValueError('New continuation directory required')
    if a.execute:
        if read(out/'manifest.json')['source'] != str(source): raise ValueError('Source argument mismatch')
        def stop(signum,frame): raise KeyboardInterrupt('Explicit stop')
        signal.signal(signal.SIGTERM,stop)
        execute(out,a.cumulative_budget_usd)
    else: prepare(source,out)


if __name__=='__main__': main()
