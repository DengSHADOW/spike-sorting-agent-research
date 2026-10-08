"""Verified CH30 budget continuation; frozen old runs/code are never edited."""
from __future__ import annotations

import argparse
import base64
import copy
import math
from pathlib import Path
import signal
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
from scripts.run import retry_minimal_prompt_baseline as retry

resume, base, read = retry.resume, retry.base, retry.read
SCOPE = 'minimal-ch30-budget-continuation-v1'


def audit_ledger(rows, old):
    carries = [r for r in rows if r['event'] == 'carry_forward']
    if len(carries) != 1:
        raise ValueError('Expected one carry-forward')
    pending, settled, total = {}, set(), carries[0]['prior_conservative_usd']
    for row in rows:
        if row['event'] == 'reserved':
            if row['call'] in pending or row['call'] in settled:
                raise ValueError('Duplicate reservation')
            pending[row['call']] = row
        elif row['event'] == 'usage':
            if row['call'] not in pending:
                raise ValueError('Unreserved usage')
            pending.pop(row['call']); settled.add(row['call'])
            total += row['conservative_usd']
        elif row['event'] == 'failure':
            raise ValueError('Budget continuation cannot retry provider failures')
    if pending or abs(total-old['conservative_charged_or_reserved_usd']) > 1e-8:
        raise ValueError('Unsettled or mismatched ledger')
    if carries[0]['prior_calls'] + len(settled) != old['inference_calls']:
        raise ValueError('Call total mismatch')
    if carries[0]['unsettled_reservation_usd'] != old['inherited_unsettled_reservation_usd']:
        raise ValueError('Inherited failed reservation lost')


def parent(source):
    _, root, _, _, held = retry.verify(source)
    old = read(source/'execution_status.json')
    if old['status'] != 'stopped' or old['error'] != {'type':'ControllerStop', 'cause':'BudgetStop'}:
        raise ValueError('Requires an audited budget stop')
    if old['completed_datasets'] != list(base.DATASETS[:3]):
        raise ValueError('Only CH30 may remain')
    audit_ledger(base.accounting.read_jsonl(source/'provider_ledger.jsonl'), old)
    if held != old['inherited_unsettled_reservation_usd']:
        raise ValueError('Inherited reserve changed')
    folder = source/'datasets'/resume.CH30
    summary = read(folder/'summary.json')
    if summary['status'] != 'stopped' or summary['calls'] != 1060:
        raise ValueError('Unexpected CH30 recovery boundary')
    records = []
    for i in range(1, summary['calls']+2):
        step = folder/f'step_{i:05d}'
        raw = read(step/'response.json') if i <= summary['calls'] else None
        if raw is None and (step/'response.json').exists():
            raise ValueError('Boundary already answered')
        request = read(step/'request.json')
        if raw is not None:
            base.accounting.decision_from_response(raw, request['phase'])
        records.append(dict(request=request, raw=raw, folder=step))
    if records[-1]['request']['state_sha256'] != summary['final_sha256']:
        raise ValueError('Boundary state mismatch')
    return root, old, records


def inventory(source):
    paths = [source/name for name in ('manifest.json','execution_status.json','score.json','provider_ledger.jsonl')]
    paths += [p for p in (source/'datasets'/resume.CH30).rglob('*') if p.is_file()]
    return {str(p.relative_to(source)):base.sha256(p) for p in sorted(paths)}


def cached_request(record):
    saved = record['request']; phase = saved['phase']; folder = record['folder']
    images = saved['display']['images']
    if [x['name'] for x in images] != base.VIEWS[phase]:
        raise ValueError('Cached image order changed')
    urls = []
    for item in images:
        path = folder/(item['name']+'.png')
        if base.sha256(path) != item['sha256']:
            raise ValueError('Cached image hash changed')
        urls.append('data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode())
    request = base.build_request(phase, saved['observation'], urls)
    if base.digest(request) != saved['request_sha256']:
        raise ValueError('Cached request hash changed')
    return request


class CachedController(resume.PrefixController):
    """Replay saved decisions locally, verifying scheduler/state at every step.

    Existing images are hash-checked, not redrawn. At the first unsent step,
    the frozen controller regenerates observations/images and verifies the
    entire request hash. All subsequent work uses its unchanged ask/run/apply.
    """
    def ask(self, phase, cid, target=None, *, context=None):
        p = self.provider
        if p.index >= len(p.records) or p.records[p.index]['raw'] is None:
            return super().ask(phase, cid, target, context=context)
        record = p.records[p.index]; saved = record['request']; obs = saved['observation']
        if self.status in {'stopped','completed'}:
            raise ValueError('Controller finished')
        state = self.state_hash()
        if (phase, int(cid), target, context, state) != (
            saved['phase'], obs.get('cluster_id',obs.get('small_cluster_id')),
            obs.get('large_cluster_id'), saved.get('context'), saved['state_sha256']):
            raise ValueError('Cached scheduler/state mismatch')
        key = (context or phase, int(cid), target, state)
        if key in self.seen or self.calls >= self.max_calls or base.prompt_hashes() != self.frozen:
            raise ValueError('Replay invariant changed')
        request = cached_request(record)
        p.count(request)
        raw = p.respond(request)
        decision = base.accounting.decision_from_response(raw, phase)
        self.status = 'running'; self.seen.add(key); self.calls += 1
        self.charged += base.accounting.usage_cost(raw)['conservative_usd']
        self.log('cached_response', source=str(record['folder']), request_sha256=base.digest(request), new_api_cost_usd=0)
        self.log('decision', phase=phase, cid=int(cid), target=target, **decision)
        if self.calls % 100 == 0:
            print(f'LOCAL REPLAY {self.calls}/1060; no API requests', flush=True)
        return decision


def controller(root, out, records, provider, budget):
    row = next(r for r in read(root/'manifest.json')['datasets'] if r['dataset_id'] == resume.CH30)
    actor, fs = base.load_actor(row)
    return CachedController(actor, resume.CH30, out, provider=provider, budget_usd=budget, sampling_rate=fs)


def compare_checkpoint(source, c):
    path = source/'datasets'/resume.CH30/'stopped.npz'
    if base.sha256(path) != read(path.with_suffix('.json'))['sha256']:
        raise ValueError('Checkpoint corrupted')
    with np.load(path, allow_pickle=False) as a, np.load(c.out/'stopped.npz', allow_pickle=False) as b:
        if any(not np.array_equal(a[k],b[k]) for k in ('assigns','tree','modified','counter')):
            raise ValueError('Full restored checkpoint differs')


def prepare(source, out, budget):
    if type(budget) not in (int,float) or not math.isfinite(budget) or budget <= 100:
        raise ValueError('Explicit increased cumulative budget required')
    root, old, records = parent(source)
    hashes = inventory(source)
    out.mkdir(parents=True, exist_ok=False)
    provider = resume.PrefixProvider(records)
    c = controller(root, out/'offline_prefix', records, provider, budget)
    try:
        c.run()
    except base.ControllerStop as exc:
        if not isinstance(exc.__cause__, resume.ReplayBoundary): raise
    else:
        raise ValueError('Missing unsent boundary')
    compare_checkpoint(source,c)
    if provider.index != 1060 or not provider.boundary_verified or hashes != inventory(source):
        raise ValueError('Replay validation failed')
    base.write_json(out/'manifest.json', dict(version=SCOPE, source=str(source), root=str(root),
        source_hashes=hashes, code_sha256=base.sha256(Path(__file__)), cumulative_budget_usd=budget,
        inherited_cost_and_reservation_usd=old['conservative_charged_or_reserved_usd'],
        inherited_unsettled_reservation_usd=old['inherited_unsettled_reservation_usd'],
        prefix_responses_reused=1060, first_new_step=1061, automatic_retries=0,
        note='Same model, prompts, observations, controller. Only CH30 continues; cached prefix is not new inference.'))
    base.write_json(out/'preflight.json',dict(status='passed',api_calls=0,manifest_sha256=base.sha256(out/'manifest.json')))
    print('OFFLINE CONTINUATION PASSED',base.sha256(out/'manifest.json'),flush=True)


def verify(out):
    m = read(out/'manifest.json'); source = base.output_path(m['source'])
    root, old, records = parent(source)
    if (m['version'] != SCOPE or read(out/'preflight.json')['manifest_sha256'] != base.sha256(out/'manifest.json')
        or m['code_sha256'] != base.sha256(Path(__file__)) or m['source_hashes'] != inventory(source)
        or m['root'] != str(root)):
        raise ValueError('Frozen continuation inputs changed')
    return m, source, root, old, records


def authorize(out, budget):
    if type(budget) not in (int,float) or not math.isfinite(budget) or budget <= 100:
        raise ValueError('Invalid cumulative budget')
    if (out/'execution_started.json').exists(): raise ValueError('Already started')
    path = out/'authorize_execute.json'
    if path.is_symlink(): raise ValueError('Authorization symlink')
    auth = read(path); m = read(out/'manifest.json')
    if (auth.get('scope') != SCOPE or type(auth.get('cumulative_budget_usd')) not in (int,float)
        or auth['cumulative_budget_usd'] != budget or m['cumulative_budget_usd'] != budget
        or auth.get('manifest_sha256') != base.sha256(out/'manifest.json')):
        raise ValueError('Authorization mismatch')


def collect(source, root, out, status):
    # A small new local input view lets the frozen collector preserve CH20's
    # completed retry result, instead of reverting it to the original partial.
    view = out/'collector_inputs'; view.mkdir()
    base.write_json(view/'manifest.json',read(root/'manifest.json'))
    base.write_json(view/'score.json',read(source/'score.json'))
    resume.collect(view,out,status)
    report = base.BASE/'results'/out.name/'REPORT.md'
    text = report.read_text()
    text = text.replace('cumulative cap $100',f"cumulative cap ${read(out/'manifest.json')['cumulative_budget_usd']:g}")
    text = text.replace('Local replay of 44 saved CH20 decisions','Local replay of 1060 saved CH30 decisions')
    text += f"\nPrior completed results: {source.name}. Inherited unresolved 503 reserve ${status['inherited_unsettled_reservation_usd']:.7f} is included, not confirmed spend. Calls count attempts.\n"
    report.write_text(text)


def execute(out,budget):
    m,source,root,old,records = verify(out); authorize(out,budget)
    base.write_json(out/'execution_started.json',dict(at=base.accounting.now(), cumulative_budget_usd=budget,
        manifest_sha256=base.sha256(out/'manifest.json'),authorization_sha256=base.sha256(out/'authorize_execute.json')))
    (out/'responses').mkdir()
    live=None; state='stopped'; error=None; completed=list(old['completed_datasets']); started=monotonic()
    try:
        live=base.infra.LiveProvider(base.infra.make_client(),out,budget)
        live.charged=old['conservative_charged_or_reserved_usd']; live.standard=old['standard_estimate_known_responses_usd']
        live.calls=old['inference_calls']; live.tokens=copy.deepcopy(old['tokens']); live.dataset=resume.CH30
        base.infra.append(out,dict(event='carry_forward',dataset_id='campaign',prior_calls=live.calls,
            prior_conservative_usd=live.charged,unsettled_reservation_usd=old['inherited_unsettled_reservation_usd'],
            cumulative_budget_usd=budget,source_run=str(source)))
        prefix_cost=sum(base.accounting.usage_cost(r['raw'])['conservative_usd'] for r in records if r['raw'] is not None)
        c=controller(root,out/'datasets'/resume.CH30,records,resume.PrefixProvider(records,live),budget-live.charged+prefix_cost)
        c.run(); completed.append(resume.CH30); state='completed'
    except BaseException as exc:
        error=dict(type=type(exc).__name__,cause=type(exc.__cause__).__name__ if exc.__cause__ else None)
        raise
    finally:
        status=dict(status=state,error=error,completed_datasets=completed,
            inference_calls=live.calls if live else old['inference_calls'],tokens=live.tokens if live else old['tokens'],
            conservative_charged_or_reserved_usd=live.charged if live else old['conservative_charged_or_reserved_usd'],
            standard_estimate_known_responses_usd=live.standard if live else old['standard_estimate_known_responses_usd'],
            inherited_unsettled_reservation_usd=old['inherited_unsettled_reservation_usd'],
            stepwise_accounting=old['stepwise_accounting'],seconds_this_segment=monotonic()-started,at=base.accounting.now())
        base.write_json(out/'execution_status.json',status)
        collect(source,root,out,status)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True); p.add_argument('--output-root',required=True)
    p.add_argument('--cumulative-budget-usd',required=True,type=float); p.add_argument('--execute',action='store_true')
    a=p.parse_args(); source=base.output_path(a.source); out=base.output_path(a.output_root)
    if source==out: raise ValueError('New directory required')
    if a.execute:
        if read(out/'manifest.json')['source'] != str(source): raise ValueError('Source mismatch')
        def stop(signum,frame): raise KeyboardInterrupt('Explicit stop')
        signal.signal(signal.SIGTERM,stop)
        execute(out,a.cumulative_budget_usd)
    else: prepare(source,out,a.cumulative_budget_usd)


if __name__=='__main__': main()
