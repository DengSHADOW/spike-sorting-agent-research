"""User-authorized retry after the audited 503; preserve frozen prior runs and costs."""
from __future__ import annotations
import argparse
import copy
from pathlib import Path
import signal
import sys
from time import monotonic

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import numpy as np
from scripts.run import resume_minimal_prompt_baseline as resume
base=resume.base
read=resume.read
SCOPE='minimal-prompt-manual503-retry-v1'


def audit_ledger(rows, status):
    carries=[r for r in rows if r['event']=='carry_forward']
    if len(carries)!=1: raise ValueError('Expected one prior-cost carry')
    pending={};known=0.; settled=set()
    for r in rows:
        if r['event']=='reserved':
            if r['call'] in pending or r['call'] in settled: raise ValueError('Duplicate call')
            pending[r['call']]=r
        elif r['event']=='usage':
            if r['call'] not in pending: raise ValueError('Unreserved usage')
            pending.pop(r['call']);settled.add(r['call']);known+=r['conservative_usd']
    failure=rows[-1]
    if failure.get('event')!='failure' or failure.get('status_code')!=503 or set(pending)!={failure.get('call')}:
        raise ValueError('Only the single recorded 503 can be retried')
    held=sum(r['reserved_usd'] for r in pending.values())
    expected=carries[0]['prior_conservative_usd']+known+held
    if abs(expected-status['conservative_charged_or_reserved_usd'])>1e-8:
        raise ValueError('Uncertain charges must remain reserved')
    if carries[0]['prior_calls']+len(settled)+len(pending)!=status['inference_calls']:
        raise ValueError('Call accounting mismatch')
    return held


def parent(source):
    _, root=resume.verify(source)
    old=read(source/'execution_status.json')
    if old['status']!='stopped' or old['error']!={'type':'ControllerStop','cause':'InternalServerError'}:
        raise ValueError('Not the authorized server-error stop')
    if old['completed_datasets']!=list(base.DATASETS[:2]): raise ValueError('Unexpected completed prefix')
    held=audit_ledger(base.accounting.read_jsonl(source/'provider_ledger.jsonl'),old)
    folder=source/'datasets'/resume.CH20
    summary=read(folder/'summary.json')
    if summary['calls']!=55 or summary['status']!='stopped': raise ValueError('Unexpected retry boundary')
    records=[]
    for i in range(1,56):
        step=folder/f'step_{i:05d}'; request=read(step/'request.json')
        raw=read(step/'response.json') if i<=54 else None
        if raw is not None: base.accounting.decision_from_response(raw,request['phase'])
        elif (step/'response.json').exists(): raise ValueError('Cannot retry an answered request')
        records.append(dict(request=request,raw=raw))
    if records[-1]['request']['state_sha256']!=summary['final_sha256']: raise ValueError('Boundary state mismatch')
    return root,old,records,held


def prepare(source,out):
    root,old,records,held=parent(source)
    out.mkdir(parents=True,exist_ok=False)
    hashes=resume.inventory(source)
    provider=resume.PrefixProvider(records)
    c=resume.controller(root,out/'offline_prefix',records,provider,100.)
    try: c.run()
    except base.ControllerStop as e:
        if not isinstance(e.__cause__,resume.ReplayBoundary): raise
    else: raise ValueError('Missing replay boundary')
    oldpath=source/'datasets'/resume.CH20/'stopped.npz'
    if base.sha256(oldpath)!=read(oldpath.with_suffix('.json'))['sha256']: raise ValueError('Checkpoint corrupted')
    with np.load(oldpath,allow_pickle=False) as a,np.load(c.out/'stopped.npz',allow_pickle=False) as b:
        if any(not np.array_equal(a[k],b[k]) for k in ('assigns','tree','modified','counter')): raise ValueError('Restored checkpoint differs')
    if provider.index!=54 or not provider.boundary_verified or hashes!=resume.inventory(source): raise ValueError('Replay verification failed')
    base.write_json(out/'manifest.json',dict(version=SCOPE,source=str(source),root=str(root),
        source_hashes=hashes,code_sha256=base.sha256(Path(__file__)),cumulative_budget_usd=100.,
        inherited_cost_and_reservation_usd=old['conservative_charged_or_reserved_usd'],
        inherited_unsettled_reservation_usd=held,prefix_responses_reused=54,first_retry_step=55,
        automatic_retries=0,note='Same prompts/model/controller. Manual retry of unreturned 503 request only; prior successes not resent.'))
    base.write_json(out/'preflight.json',dict(status='passed',api_calls=0,manifest_sha256=base.sha256(out/'manifest.json')))
    print('OFFLINE RETRY PASSED',base.sha256(out/'manifest.json'),flush=True)


def verify(out):
    m=read(out/'manifest.json')
    if m['version']!=SCOPE or read(out/'preflight.json')['manifest_sha256']!=base.sha256(out/'manifest.json'):
        raise ValueError('Manifest changed')
    source=base.output_path(m['source'])
    root,old,records,held=parent(source)
    if str(root)!=m['root'] or m['source_hashes']!=resume.inventory(source) or m['code_sha256']!=base.sha256(Path(__file__)):
        raise ValueError('Frozen retry inputs changed')
    return source,root,old,records,held


def authorize(out):
    if (out/'execution_started.json').exists(): raise ValueError('Already started; do not replay paid requests')
    p=out/'authorize_execute.json'
    if p.is_symlink(): raise ValueError('Authorization symlink')
    a=read(p)
    if a.get('scope')!=SCOPE or type(a.get('cumulative_budget_usd')) not in (int,float) or a['cumulative_budget_usd']!=100 or a.get('manifest_sha256')!=base.sha256(out/'manifest.json'):
        raise ValueError('Authorization mismatch')


def collect(root,out,status,held):
    # Reuse the frozen metric collector; clarify prefix length and unresolved cost
    # in this newly generated report only. No historical report is edited.
    resume.collect(root,out,status)
    report=base.BASE/'results'/out.name/'REPORT.md'
    text=report.read_text()
    expected='Local replay of 44 saved CH20 decisions'
    if expected not in text: raise ValueError('Unexpected report template')
    text=text.replace(expected,'Local replay of 54 saved CH20 decisions')
    text+=f'\nInherited unresolved 503 reservation: ${held:.7f}, already included in conservative rollout cost, not confirmed spend. API-call totals count attempts, including the failed request.\n'
    report.write_text(text)


def execute(out):
    source,root,old,records,held=verify(out);authorize(out)
    base.write_json(out/'execution_started.json',dict(at=base.accounting.now(),cumulative_budget_usd=100,
        manifest_sha256=base.sha256(out/'manifest.json'),authorization_sha256=base.sha256(out/'authorize_execute.json')))
    (out/'responses').mkdir()
    live=None;state='stopped';error=None;completed=list(old['completed_datasets']);started=monotonic()
    try:
        live=base.infra.LiveProvider(base.infra.make_client(),out,100.)
        live.charged=old['conservative_charged_or_reserved_usd'];live.standard=old['standard_estimate_known_responses_usd']
        live.calls=old['inference_calls'];live.tokens=copy.deepcopy(old['tokens'])
        base.infra.append(out,dict(event='carry_forward',dataset_id='campaign',prior_calls=live.calls,
            prior_conservative_usd=live.charged,unsettled_reservation_usd=held,cumulative_budget_usd=100.,source_run=str(source)))
        live.dataset=resume.CH20
        prefix_cost=sum(base.accounting.usage_cost(x['raw'])['conservative_usd'] for x in records if x['raw'] is not None)
        c=resume.controller(root,out/'datasets'/resume.CH20,records,resume.PrefixProvider(records,live),100.-live.charged+prefix_cost)
        c.run();completed.append(resume.CH20)
        print('CH20 COMPLETE; cumulative',live.charged,flush=True)
        verify(out)
        row=next(r for r in read(root/'manifest.json')['datasets'] if r['dataset_id']==resume.CH30)
        actor,fs=base.load_actor(row);live.dataset=resume.CH30
        live.expected_first={resume.CH30:row['first_request_sha256']}
        c=base.MinimalController(actor,resume.CH30,out/'datasets'/resume.CH30,provider=live,budget_usd=100.-live.charged,sampling_rate=fs)
        c.run();completed.append(resume.CH30);state='completed'
    except BaseException as exc:
        error=dict(type=type(exc).__name__,cause=type(exc.__cause__).__name__ if exc.__cause__ else None)
        raise
    finally:
        status=dict(status=state,error=error,completed_datasets=completed,
            inference_calls=live.calls if live else old['inference_calls'],tokens=live.tokens if live else old['tokens'],
            conservative_charged_or_reserved_usd=live.charged if live else old['conservative_charged_or_reserved_usd'],
            standard_estimate_known_responses_usd=live.standard if live else old['standard_estimate_known_responses_usd'],
            inherited_unsettled_reservation_usd=held,stepwise_accounting=old['stepwise_accounting'],seconds_this_segment=monotonic()-started,at=base.accounting.now())
        base.write_json(out/'execution_status.json',status)
        collect(root,out,status,held)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True);p.add_argument('--output-root',required=True);p.add_argument('--execute',action='store_true')
    a=p.parse_args();source=base.output_path(a.source);out=base.output_path(a.output_root)
    if source==out: raise ValueError('Use a new directory')
    if a.execute:
        if read(out/'manifest.json')['source']!=str(source): raise ValueError('Source mismatch')
        def stop(signum,frame): raise KeyboardInterrupt('Explicit stop')
        signal.signal(signal.SIGTERM,stop);execute(out)
    else:prepare(source,out)


if __name__=='__main__':main()
