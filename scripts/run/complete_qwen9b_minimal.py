"""Finish only the existing Qwen 9B minimal arm; offline plan by default.

Explicitly authorized 2026-10-14: keep completed CH31, mark CH3's impossible
SPLIT incomplete, then attempt CH20 and CH30. Never start the harness arm.
Frozen requests/controller/scoring and the original $25 cumulative cap remain.
"""
from __future__ import annotations

import argparse
import fcntl
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run import retry_qwen9b_transport as recovery

base = recovery.base
read, write, sha = base.read, base.write, base.sha
AMENDMENT = 'minimal_completion_v1'
INVALID_SPLIT = 'No last merge to split'


def source_hashes():
    return {str(Path(p).relative_to(base.ROOT)): sha(p) for p in (__file__, recovery.__file__)}


def checkpoint_hash(folder, name):
    import numpy as np
    path = folder/(name+'.npz')
    meta = read(path.with_suffix('.json'))
    if sha(path) != meta['sha256']:
        raise ValueError('Checkpoint file changed')
    with np.load(path, allow_pickle=False) as z:
        value = base.arrays_hash(z['assigns'], z['tree'])
    if value != meta['state_sha256']:
        raise ValueError('Checkpoint state mismatch')
    return value


def invalid_result(folder, row):
    """Only a preserved, genuinely impossible leaf SPLIT qualifies."""
    summary = read(folder/'summary.json')
    step = read(folder/f"step_{summary['calls']:05d}/request.json")
    state = checkpoint_hash(folder, 'stopped')
    observation = step['observation']
    if (summary['status'] != 'stopped' or summary['terminal_evaluation_allowed']
            or step['phase'] != 'phase1' or step['context'] is not None
            or observation['n_overclusters'] != 1 or state != step['state_sha256']):
        raise ValueError('Invalid SPLIT evidence/state mismatch')
    return dict(dataset_id=row['dataset_id'], arm='minimal', status='stopped_invalid_action',
        final_metrics=None, terminal_evaluation_allowed=False, state_sha256=state,
        calls=summary['calls'], checkpoint=str(folder/'stopped.npz'),
        controller_output=str(folder.relative_to(ROOT)),
        reason=INVALID_SPLIT+'; no substitute action, no retry, next channel')


def plan(out):
    """Read-only preparation: no client, key, HTTP, or output creation."""
    m = base.verify(out)
    status = read(out/'execution_status.json')
    if (status['status'] != 'stopped' or status.get('error') != dict(
            type='ControllerStop', cause='ValueError', message=INVALID_SPLIT)):
        raise ValueError('Expected the saved impossible SPLIT stop')
    allowances = recovery.saved_rounds(out)
    p = recovery.RetryProvider(out/'calls', budget=25,
        prior=m['prior_charged_or_reserved_usd'], allowances=allowances)
    ledger = p.ledger()
    if ledger != status['costs'] or ledger['charged_or_reserved_usd']+p.held > 25:
        raise ValueError('Ledger changed or insufficient budget')
    if ledger['by_arm']['system_v1_skill_v0']['attempts']:
        raise ValueError('Harness already attempted; scope differs')
    results = read(out/'results.json')
    if (len(results) != 1 or results[0]['dataset_id'] != m['datasets'][0]['dataset_id']
            or results[0]['status'] != 'completed' or results[0]['arm'] != 'minimal'):
        raise ValueError('Expected completed minimal CH31 only')
    completed = ROOT/results[0]['controller_output']
    if not completed.resolve().is_relative_to(out):
        raise ValueError('Completed result outside this experiment')
    if (read(completed/'evaluation.json') != results[0]
            or checkpoint_hash(completed, 'terminal') != results[0]['final_sha256']):
        raise ValueError('Completed result changed')
    calls = sorted((out/'calls').iterdir())
    last = calls[-1]
    meta, decision = read(last/'request.json'), read(last/'decision.json')
    dataset = m['datasets'][1]['dataset_id']
    prefix = f'{base.MODEL}/rollout/minimal/{dataset}/'
    if not meta['tag'].startswith(prefix) or decision['decision']['action'] != 'SPLIT':
        raise ValueError('Last response is not CH3 SPLIT')
    raw = last/'attempts'/decision['attempt']/'response.json'
    if decision['response_sha256'] != sha(raw):
        raise ValueError('Last response changed')
    failed = completed.parent/dataset
    incomplete = invalid_result(failed, m['datasets'][1])
    step = read(failed/f"step_{incomplete['calls']:05d}/request.json")
    if (meta['tag'] != prefix+str(incomplete['calls'])
            or meta['request_sha256'] != step['request_sha256']
            or len(calls) != results[0]['calls']+incomplete['calls']):
        raise ValueError('Request/journal boundary mismatch')
    paths = [f for f in (out/'calls').rglob('*') if f.is_file()]
    paths += [completed/n for n in ('evaluation.json', 'terminal.json', 'terminal.npz')]
    paths += [failed/n for n in ('summary.json', 'stopped.json', 'stopped.npz',
                               f"step_{incomplete['calls']:05d}/request.json")]
    return dict(kind='qwen9b-minimal-completion-v1', manifest_sha256=sha(out/'manifest.json'),
        sources=source_hashes(),
        evidence_hashes={str(p.relative_to(out)): sha(p) for p in paths},
        original_status=status, preserved_results=[results[0], incomplete],
        journal_cursor=len(calls), remaining_datasets=m['datasets'][2:],
        arms=['minimal'], budget_usd=25, planned_rollouts=4, harness_cancelled=True,
        policy='Only impossible leaf SPLIT stops its channel and advances; length unchanged. '
               'Other execution/parse/auth/provenance/budget errors and exhausted transport retries stop the run.',
        authorization='User 2026-10-14: ok，先继续吧。把minimal跑完就ok了. '
                      'Accept prior proposal: preserve CH31, mark CH3 incomplete, continue CH20/CH30; no harness.')


def verify(out):
    m = base.verify(out)
    folder = out/AMENDMENT
    a = read(folder/'authorization.json')
    if read(folder/'seal.json')['authorization_sha256'] != sha(folder/'authorization.json'):
        raise ValueError('Authorization changed')
    expected_sources = source_hashes()
    if (a['sources'] != expected_sources or sha(folder/'runner.py') != sha(__file__)
            or a['manifest_sha256'] != sha(out/'manifest.json')
            or a['arms'] != ['minimal'] or not a['harness_cancelled'] or a['budget_usd'] != 25
            or a['remaining_datasets'] != m['datasets'][2:]):
        raise ValueError('Frozen scope/source changed')
    for name, expected in a['evidence_hashes'].items():
        p = (out/name).resolve()
        if not p.is_relative_to(out) or sha(p) != expected:
            raise ValueError('Historical evidence changed')
    recovery.saved_rounds(out)
    return m, a


def evaluate(controller, row, initial):
    try:
        return base.old.evaluate_rollout(controller, row, initial, allow_truncation=True)
    except base.minimal.ControllerStop as exc:
        if not isinstance(exc.__cause__, ValueError) or str(exc.__cause__) != INVALID_SPLIT:
            raise
        return invalid_result(controller.out, row)


class MinimalProvider(recovery.RetryProvider):
    def ask(self, request, tag, arm):
        if arm != 'minimal' or not tag.startswith(f'{base.MODEL}/rollout/minimal/'):
            raise ValueError('Minimal-only authorization; harness forbidden')
        return super().ask(request, tag, arm)


def execute(out, *, resume=False):
    with (out/'execution.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        folder = out/AMENDMENT
        if not resume:
            a = plan(out)
            folder.mkdir(exist_ok=False)
            write(folder/'authorization.json', a)
            (folder/'runner.py').write_bytes(Path(__file__).read_bytes())
            write(folder/'seal.json', dict(authorization_sha256=sha(folder/'authorization.json')))
        m, a = verify(out)
        if resume and read(out/'execution_status.json')['status'].startswith('completed'):
            raise ValueError('Minimal campaign already finished; no requests allowed')
        attempt = out/f'attempt_{len(list(out.glob("attempt_*")))+1:03d}'
        attempt.mkdir(exist_ok=False)
        results = list(a['preserved_results'])
        provider = None
        status, error = 'stopped', None
        try:
            provider = MinimalProvider(out/'calls', budget=25,
                prior=m['prior_charged_or_reserved_usd'], allowances=recovery.saved_rounds(out))
            provider.cursor = a['journal_cursor']
            if provider.ledger()['by_arm']['system_v1_skill_v0']['attempts']:
                raise ValueError('Unexpected harness calls')
            base.atomic(out/'progress.json', dict(status='endpoint_check', scope='minimal-only',
                preserved_rollouts=2, remaining_new_rollouts=2))
            base.check_endpoint(provider, attempt)
            for row in a['remaining_datasets']:
                verify(out)
                base.atomic(out/'progress.json', dict(status='loading_dataset', arm='minimal',
                    dataset_id=row['dataset_id'], finished_rollouts=len(results),
                    charged_or_reserved_usd=provider.ledger()['charged_or_reserved_usd']))
                actor, fs = base.minimal.load_actor(row)
                initial = actor.assigns.copy()
                c = base.Controller(actor, row['dataset_id'], attempt/'minimal'/row['dataset_id'],
                    provider=provider, arm='minimal', sampling_rate=fs)
                result = evaluate(c, row, initial)
                result.update(arm='minimal', calls=c.calls, operations=c.operations,
                    controller_output=str(c.out.relative_to(ROOT)))
                write(c.out/'evaluation.json', result)
                results.append(result)
                base.atomic(out/'results.json', results)
                base.atomic(out/'cost_breakdown.json', provider.ledger())
                print('ROLLOUT minimal', row['dataset_id'], result['status'], flush=True)
                del c, actor, initial
            status = 'completed_with_failures' if any(r['status'] != 'completed' for r in results) else 'completed'
        except BaseException as exc:
            cause = exc.__cause__ or exc
            error = dict(type=type(exc).__name__, cause=type(cause).__name__,
                message=str(cause) if isinstance(cause, (ValueError, base.TransportStopped)) else 'See saved journal')
        finally:
            ledger = provider.ledger() if provider else None
            if provider:
                provider.close()
            summary = dict(status=status, error=error, scope='minimal-only',
                completed_rollouts=sum(r['status'] == 'completed' for r in results),
                stopped_rollouts=sum(r['status'] != 'completed' for r in results),
                planned_rollouts=4, unresolved_rollouts=4-len(results), harness_cancelled=True,
                new_http_attempts=provider.sent if provider else 0, costs=ledger,
                authorization_sha256=sha(folder/'authorization.json'))
            write(attempt/'summary.json', summary)
            base.atomic(out/'execution_status.json', summary)
            base.atomic(out/'progress.json', summary)
            base.atomic(out/'results.json', results)
            if ledger:
                base.atomic(out/'cost_breakdown.json', ledger)
            lines = ['# Qwen3.5-9B minimal-only completion', '', f'Status: {status}',
                'Scope amendment 2026-10-14: no harness. CH31 reused; CH3 incomplete, not rescored.',
                'Impossible leaf SPLIT stops that channel; no substitute action or response retry.',
                'Original prompt/controller/observations/scoring/sampling and 8000 total / requested 6000 thinking unchanged.',
                '6000 thinking hard cap unverified. Exposed development data, not held-out generalization.', '',
                '| Dataset | Status | Calls | Precision | Recall | F1 (one-to-one, full output) |',
                '|---|---|---:|---:|---:|---:|']
            for r in results:
                metric = r.get('one_to_one', {}).get('full_output', {})
                values = [str(metric.get(k, 'N/A')) for k in ('precision', 'recall', 'f1')]
                lines.append(f"| {r['dataset_id']} | {r['status']} | {r['calls']} | "+' | '.join(values)+' |')
            lines += ['', 'Incomplete rollouts have no final score. Many-to-one diagnostics remain in results.json.',
                'Costs include historical expenditure and unknown reservations, not all confirmed charges.',
                '```json', base.json.dumps(ledger, indent=2), '```', '', f'Error: {error}']
            report = '\n'.join(lines)+'\n'
            (attempt/'REPORT.md').write_text(report)
            (out/'REPORT.md').write_text(report)
        return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--resume', action='store_true')
    a = p.parse_args()
    out = a.output_root.absolute()
    if out.resolve() != out or out.parent != ROOT/'zero-shot baseline/runs':
        p.error('Expected existing direct child of baseline/runs')
    if a.resume and not a.execute:
        p.error('--resume requires --execute')
    if not a.execute:
        result = plan(out)
        print(base.json.dumps(dict(mode='dry-run', api_calls=0, scope='minimal-only',
            preserved=[(r['dataset_id'], r['status']) for r in result['preserved_results']],
            remaining=[r['dataset_id'] for r in result['remaining_datasets']],
            prior_charged_or_reserved_usd=result['original_status']['costs']['charged_or_reserved_usd'])))
        return 0
    result = execute(out, resume=a.resume)
    print(base.json.dumps(result))
    return 0 if result['status'].startswith('completed') else 1


if __name__ == '__main__':
    sys.exit(main())
