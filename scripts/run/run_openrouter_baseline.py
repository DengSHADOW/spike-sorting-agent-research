"""Frozen Qwen minimal-prompt baseline: offline preparation unless --execute.

Runs both stepwise sets, then both models' four autonomous rollouts. Resumption
replays saved outcomes locally; uncertain requests are never automatically repeated.
Explicitly enabled bounded transport retries apply only to verified overload 429.
8000-token protocol records settled length failures without inventing actions.
No legacy skills, teacher feedback, SDK retries, provider fallback or JSON repair.
"""
from __future__ import annotations
import argparse
import copy
import fcntl
import json
import math
import shutil
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
from scripts.run import run_openrouter_smoke as transport
from scripts.run import run_minimal_prompt_baseline as minimal
from scripts.analysis import minimal_stepwise, curation_one_to_one

SOURCE = transport.SOURCE
MODELS = tuple(transport.MODELS)
SMOKES = ('openrouter_smoke_20261012_v1', 'openrouter_smoke_27b_fp8_20261012_v2')
read, write, digest, sha = transport.read, transport.write, transport.digest, transport.sha
OVERLOAD_RETRY = dict(delays_seconds=[30,60,120], max_retry_after_seconds=300,
    eligible='HTTP 429 / engine_overloaded / upstream_provider_shared_pool, expected provider, no choices or usage',
    accounting='Retain every rejected attempt reservation; reserve again before retry',
    scope='Transport only; identical request; no truncation/parse/timeout retries or provider fallback')


def is_overload(status, raw, request):
    err = raw.get('error') or {}; details = err.get('metadata') or {}
    return (status == 429 and err.get('code') == 429
        and details.get('provider_name') == transport.MODELS[request['model']][1]
        and details.get('provider_error_code') == 'engine_overloaded'
        and details.get('limit_source') == 'upstream_provider_shared_pool'
        and not raw.get('choices') and not raw.get('usage'))


def retry_reservations(folder):
    """Verify and count preserved rejected attempts, including on local replay."""
    total = 0.
    for i,d in enumerate(sorted((folder/'overload_attempts').glob('*')),1):
        if d.name != f'{i:03d}' or i > len(OVERLOAD_RETRY['delays_seconds']):
            raise ValueError('Invalid overload attempt sequence')
        meta,record = read(folder/'request.json'),read(d/'retry.json')
        if (record['request_sha256'] != meta['request_sha256']
            or record['reservation_usd'] != meta['reservation_usd']
            or record['response_sha256'] != sha(d/'response.json')
            or record['http_sha256'] != sha(d/'http.json')
            or not is_overload(read(d/'http.json')['status'],read(d/'response.json'),meta['request'])):
            raise ValueError('Overload attempt changed')
        total += record['reservation_usd']
    return total


def retry_delay(response, index):
    delay = OVERLOAD_RETRY['delays_seconds'][index]
    header = getattr(response,'headers',{}).get('Retry-After')
    if header is not None:
        try: seconds = float(header)
        except ValueError:
            try: seconds = (parsedate_to_datetime(header)-datetime.now(timezone.utc)).total_seconds()
            except (TypeError,ValueError,OverflowError): raise ValueError('Invalid Retry-After; no retry') from None
        if not math.isfinite(seconds) or seconds > OVERLOAD_RETRY['max_retry_after_seconds']:
            raise ValueError('Retry-After exceeds bounded wait; no retry')
        delay = max(delay,seconds)
    return delay


def convert(old, model, max_output=4000):
    """Change only API envelope and predeclared model/serving parameters."""
    parts = old['input'][0]['content']
    fmt = copy.deepcopy(old['text']['format']); fmt.pop('type')
    return dict(model=model, messages=[dict(role='system', content=old['instructions']),
        dict(role='user', content=[dict(type='text', text=parts[0]['text']), *[
            dict(type='image_url', image_url=dict(url=p['image_url'], detail=p['detail'])) for p in parts[1:]]])],
        max_tokens=max_output, reasoning=dict(enabled=True, exclude=False), stream=False,
        response_format=dict(type='json_schema', json_schema=fmt),
        provider=dict(only=[transport.MODELS[model][0]], allow_fallbacks=False,
                      require_parameters=True, quantizations=[transport.MODELS[model][0].split('/')[-1]]))


def code_hashes():
    paths = [Path(__file__), Path(transport.__file__), Path(curation_one_to_one.__file__),
             curation_one_to_one.RULES, ROOT/'scripts/analysis/minimal_stepwise.py']
    return dict(minimal.sources(), **{str(p.relative_to(ROOT)): sha(p) for p in paths})


def smoke_evidence():
    result, cost = {}, 0.
    for name in SMOKES:
        folder = ROOT/'zero-shot baseline/runs'/name
        summary = read(folder/'summary.json')
        result[name] = dict(summary_sha256=sha(folder/'summary.json'), summary=summary)
        cost += summary['charged_or_reserved_usd']
    # Two real-image responses per selected endpoint, with validated thinking/schema.
    for model, name in zip(MODELS, SMOKES):
        folder = ROOT/'zero-shot baseline/runs'/name
        for i in range(2):
            raw = read(folder/f'response_{i}.json')
            request = read(folder/f'request_{i}.json')
            if request['model'] != model or request['provider']['only'] != [transport.MODELS[model][0]]:
                raise ValueError('Smoke endpoint differs from campaign')
            transport.parse(raw, request)
            result[name][f'response_{i}_sha256'] = sha(folder/f'response_{i}.json')
    return result, cost


def retry_lineage(parent):
    """Explicit, single retry of a settled trailing length failure; never unknown calls."""
    parent = Path(parent).resolve()
    old = verify(parent, archived=True)
    folders = sorted((parent/'calls').iterdir())
    if not folders or any(not p.is_dir() or p.name != f'{i:06d}' for i,p in enumerate(folders,1)):
        raise ValueError('Invalid parent call sequence')
    hashes, reusable = {}, []
    for folder in folders:
        meta = read(folder/'request.json')
        raw = read(folder/'response.json')
        settled = read(folder/'settled.json')
        cost = settled['cost']
        if type(cost) not in (int,float) or not math.isfinite(cost) or cost < 0 or cost != raw.get('usage',{}).get('cost'):
            raise ValueError('Unsettled/invalid parent cost: retry forbidden')
        if (folder/'decision.json').exists():
            saved = read(folder/'decision.json')
            if saved['response_sha256'] != sha(folder/'response.json') or saved['decision'] != transport.parse(raw,meta['request']):
                raise ValueError('Parent decision changed')
            reusable.append(folder)
        else:
            if folder != folders[-1] or read(folder/'http.json')['status'] != 200:
                raise ValueError('Only trailing settled length failure can be retried')
            choices = raw.get('choices',[])
            if (raw.get('error') or len(choices) != 1 or choices[0].get('finish_reason') != 'length'
                or raw.get('model') != meta['request']['model']
                or raw.get('provider') != transport.MODELS[raw['model']][1]
                or '/stepwise/' not in meta['tag']):
                raise ValueError('Only settled stepwise length failure can be retried')
            failed_cost = cost
        for f in folder.iterdir():
            if f.is_file(): hashes[str(f.relative_to(parent))] = sha(f)
    if len(reusable) != len(folders)-1:
        raise ValueError('Exactly one trailing failed call required')
    manifest_requests = old['requests']
    for i,folder in enumerate(folders):
        r,expected = read(folder/'request.json'),manifest_requests[i]
        if r['request_sha256'] != expected['sha256'] or r['tag'] != f"{expected['model']}/stepwise/{expected['job_id']}":
            raise ValueError('Parent request is outside frozen order')
    return dict(path=str(parent.relative_to(ROOT)), manifest_sha256=sha(parent/'manifest.json'),
        call_hashes=hashes, inherited_prior_usd=old['prior_charged_or_reserved_usd'],
        failed_cost_usd=failed_cost, successful_calls_reused=len(reusable),
        retry_tag=read(folders[-1]/'request.json')['tag'],
        policy='User-requested single retry at unchanged 4000-token limit; no automatic retry; original failure retained'), reusable


def historical_costs(parent):
    """Carry forward the latest stopped campaign's complete ledger, not predictions."""
    parent = Path(parent).resolve()
    old = verify(parent, archived=True)
    summaries = sorted(parent.glob('attempt_*/summary.json'))
    if not summaries or read(summaries[-1])['status'] not in ('stopped','completed','completed_with_failures'):
        raise ValueError('Cost history must have a finalized execution summary')
    hashes = {str(summaries[-1].relative_to(parent)):sha(summaries[-1])}
    total = old['prior_charged_or_reserved_usd']
    for i,folder in enumerate(sorted((parent/'calls').iterdir()),1):
        if not folder.is_dir() or folder.name != f'{i:06d}': raise ValueError('Invalid cost history')
        meta = read(folder/'request.json')
        if (folder/'settled.json').exists():
            cost = read(folder/'settled.json')['cost']
            if cost != read(folder/'response.json').get('usage',{}).get('cost'):
                raise ValueError('Historical response/ledger cost mismatch')
        else:
            cost = meta['reservation_usd']
        if type(cost) not in (int,float) or not math.isfinite(cost) or cost < 0:
            raise ValueError('Invalid historical cost')
        total += cost + retry_reservations(folder)
        hashes.update({str(f.relative_to(parent)):sha(f) for f in folder.rglob('*') if f.is_file()})
    recorded = read(summaries[-1])['charged_or_reserved_including_smoke']
    if recorded is None or not math.isclose(recorded,total,abs_tol=1e-10):
        raise ValueError('Historical cumulative accounting mismatch')
    return dict(path=str(parent.relative_to(ROOT)),manifest_sha256=sha(parent/'manifest.json'),
                file_hashes=hashes,total_charged_or_reserved_usd=total,
                successful_predictions_reused=0)


def build_stepwise(job, model, max_output):
    request = transport.build(SOURCE,job,model)
    request['max_tokens'] = max_output
    return request


def rate_limit_lineage(parent):
    """User-authorized retry of a known trailing upstream 429; keep its reservation."""
    parent = Path(parent).resolve()
    history = historical_costs(parent)
    old = read(parent/'manifest.json')
    if old['max_output_tokens'] != 8000 or not old.get('record_length_failures'):
        raise ValueError('Rate-limit continuation requires the frozen 8000 protocol')
    folders = sorted((parent/'calls').iterdir())
    last = folders[-1]
    meta,raw = read(last/'request.json'),read(last/'response.json')
    err = raw.get('error') or {};details = err.get('metadata') or {}
    if (read(last/'http.json')['status'] != 429 or err.get('code') != 429
        or details.get('provider_name') != transport.MODELS[meta['request']['model']][1]
        or details.get('provider_error_code') != 'engine_overloaded'
        or details.get('limit_source') != 'upstream_provider_shared_pool'
        or raw.get('choices') or raw.get('usage') or (last/'settled.json').exists()
        or (last/'decision.json').exists() or (last/'failure.json').exists()):
        raise ValueError('Not a known uncompleted upstream rate-limit response; no retry')
    held = meta['reservation_usd']
    _,_,ip,op = transport.MODELS[meta['request']['model']]
    if not math.isclose(held,1.1*(transport.CONTEXT*ip+8000*op),abs_tol=1e-12):
        raise ValueError('Rate-limit reservation mismatch')
    valid=truncated=0
    for i,folder in enumerate(folders):
        r=read(folder/'request.json');expected=old['requests'][i]
        if r['request_sha256']!=expected['sha256'] or r['tag']!=f"{expected['model']}/stepwise/{expected['job_id']}":
            raise ValueError('Rate-limit continuation outside frozen stepwise order')
        if folder==last: break
        response=read(folder/'response.json')
        if not (folder/'settled.json').exists() or read(folder/'http.json')['status']!=200:
            raise ValueError('Unsettled prefix cannot be reused')
        if (folder/'decision.json').exists():
            decision=read(folder/'decision.json')
            if decision['response_sha256']!=sha(folder/'response.json') or decision['decision']!=transport.parse(response,r['request']):
                raise ValueError('Prefix decision changed')
            valid+=1
        elif (folder/'failure.json').exists():
            failure=read(folder/'failure.json')
            if (failure!=dict(kind='length',response_sha256=sha(folder/'response.json'),request_sha256=r['request_sha256'])
                or not is_length_response(response,r['request'])):
                raise ValueError('Prefix truncation changed')
            truncated+=1
        else: raise ValueError('Unknown prefix outcome')
    return dict(path=history['path'],manifest_sha256=history['manifest_sha256'],file_hashes=history['file_hashes'],
                inherited_prior_usd=old['prior_charged_or_reserved_usd'],retained_429_reservation_usd=held+retry_reservations(last),
                cumulative_before_retry_usd=history['total_charged_or_reserved_usd'],
                valid_outcomes_reused=valid,truncations_reused=truncated,retry_tag=meta['tag'],
                policy='Explicit user-requested retry of rejected HTTP 429 only. Prior length failures remain failures; no auto retry/fallback.'),folders[:-1]


def prepare(out, retry_from=None, *, max_output=4000, cost_history_from=None, rate_limit_from=None, retry_overload=False):
    if retry_overload and not rate_limit_from:
        raise ValueError('Bounded overload retry requires explicit rate-limit continuation')
    if max_output not in (4000,8000): raise ValueError('Unapproved output limit')
    if retry_from and (max_output != 4000 or cost_history_from or rate_limit_from):
        raise ValueError('Legacy retry cannot change conditions or combine ledgers')
    if rate_limit_from and (cost_history_from or max_output!=8000):
        raise ValueError('Rate-limit continuation cannot change conditions or combine ledgers')
    if max_output == 8000 and not (cost_history_from or rate_limit_from):
        raise ValueError('8000-token campaign must carry prior expenses')
    lineage, reusable = retry_lineage(retry_from) if retry_from else (None, [])
    rate_lineage=None
    if rate_limit_from: rate_lineage,reusable=rate_limit_lineage(rate_limit_from)
    history = historical_costs(cost_history_from) if cost_history_from else None
    source_manifest = read(SOURCE/'manifest.json')
    jobs = source_manifest['stepwise']['jobs']
    if len(jobs) != 294 or source_manifest['stepwise']['action_counts'] != {'DISCARD':158,'SPLIT':126,'MERGE':10}:
        raise ValueError('Audited scope changed')
    requests = []
    for model in MODELS:
        for job in jobs:
            minimal.require_dataset(job['dataset_id'])
            request = build_stepwise(job, model, max_output)
            requests.append(dict(model=model, job_id=job['job_id'], sha256=digest(request)))
    rows = minimal.selected_rows(minimal.DATASETS)
    evidence, prior = smoke_evidence()
    if lineage:
        prior = lineage['inherited_prior_usd'] + lineage['failed_cost_usd']
    if history: prior = history['total_charged_or_reserved_usd']
    if rate_lineage: prior=rate_lineage['inherited_prior_usd']+rate_lineage['retained_429_reservation_usd']
    manifest = dict(version='openrouter-minimal-dual-v1', budget_usd=25, prior_charged_or_reserved_usd=prior,
        models=transport.MODELS, max_output_tokens=max_output, thinking=True, sampling='provider defaults',
        source_manifest_sha256=sha(SOURCE/'manifest.json'), prompt_hashes=minimal.prompt_hashes(),
        code_hashes=code_hashes(), environment=minimal.infra.environment(), smoke_evidence=evidence,
        targets_sha256=sha(SOURCE/'stepwise_targets.jsonl'), datasets=rows, requests=requests,
        scoring_sha256=sha(curation_one_to_one.RULES),
        scheduler='Unchanged engineering_v1, including preserved/ineligible SPLIT in phase2 prevalidation',
        order='9B stepwise, 27B stepwise; then 9B and 27B autonomous CH31/CH3/CH20/CH30',
        no_curation='Score initial assignments with same evaluators',
        assessment='One-to-one primary; historical many-to-one secondary; stepwise separate; exposed development data',
        safety='Stop on any API/parse/truncation/budget error. No auto retry/fallback. 10000 calls per rollout.',
        authorization='User approved four real channels to OpenRouter/DeepInfra/Novita, $25 incl smoke; approved 27B FP8')
    if history: manifest['cost_history'] = history
    if max_output == 8000:
        manifest.update(version='openrouter-minimal-dual-8000-v3',record_length_failures=True,
            request_timeout_seconds=600,
            safety='One attempt per sample: settled length = failed stepwise (continue next sample) or stopped rollout (continue next dataset). Other errors stop campaign. No retry/fallback.',
            scoring_policy='Report attempted/valid/failed/unattempted, valid-only accuracy, correct/all-planned, and full-attempt accuracy only when all attempted. No imputed action.',
            authorization=manifest['authorization']+'; user approved fresh 8000-token evaluation and explicit failure policy')
    if lineage:
        old = read(Path(ROOT/lineage['path'])/'manifest.json')
        for key in ('requests','models','max_output_tokens','thinking','prompt_hashes','datasets','scoring_sha256','budget_usd'):
            if old[key] != json.loads(json.dumps(manifest[key])):
                raise ValueError('Retry changes frozen experimental conditions: '+key)
        manifest['resume_parent'] = lineage
    if rate_lineage:
        old=read(ROOT/rate_lineage['path']/'manifest.json')
        for key in ('requests','models','max_output_tokens','thinking','prompt_hashes','datasets','scoring_sha256',
                    'budget_usd','record_length_failures','scoring_policy','request_timeout_seconds'):
            if old[key]!=json.loads(json.dumps(manifest[key])):
                raise ValueError('Rate-limit retry changes experimental conditions: '+key)
        manifest['rate_limit_parent']=rate_lineage
        if old.get('transport_retry_policy') and (not retry_overload or old['transport_retry_policy'] != OVERLOAD_RETRY):
            raise ValueError('Cannot change existing bounded transport retry policy')
    if retry_overload:
        manifest['transport_retry_policy']=copy.deepcopy(OVERLOAD_RETRY)
        manifest['safety'] += ' Exception: user-authorized bounded retries for verified upstream overload only; see transport_retry_policy.'
        manifest['authorization'] += '; user approved bounded retries of verified overload 429'
    out.mkdir(parents=True, exist_ok=False)
    write(out/'manifest.json', manifest)
    write(out/'preflight.json', dict(manifest_sha256=sha(out/'manifest.json'), api_calls=0,
                                    requests_verified=len(requests)))
    if lineage or rate_lineage:
        (out/'calls').mkdir()
        for folder in reusable:
            shutil.copytree(folder,out/'calls'/folder.name)
    # Preserve executable versions alongside hashes before any paid request.
    for path in manifest['code_hashes']:
        target = out/'code_snapshot'/path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as f:
            f.write((ROOT/path).read_bytes())
    return manifest


def verify(out, archived=False):
    m = read(out/'manifest.json')
    if m.get('transport_retry_policy') and m['transport_retry_policy'] != OVERLOAD_RETRY:
        raise ValueError('Transport retry policy changed')
    if read(out/'preflight.json')['manifest_sha256'] != sha(out/'manifest.json'):
        raise ValueError('Manifest changed')
    codes = {p:sha(out/'code_snapshot'/p) for p in m['code_hashes']} if archived else code_hashes()
    if m['code_hashes'] != codes or m['environment'] != minimal.infra.environment():
        raise ValueError('Frozen code/dependencies changed')
    if m['prompt_hashes'] != minimal.prompt_hashes() or m['source_manifest_sha256'] != sha(SOURCE/'manifest.json'):
        raise ValueError('Frozen inputs changed')
    if m['targets_sha256'] != sha(SOURCE/'stepwise_targets.jsonl') or m['scoring_sha256'] != sha(curation_one_to_one.RULES):
        raise ValueError('Scoring targets/rules changed')
    if m['datasets'] != minimal.selected_rows(minimal.DATASETS):
        raise ValueError('MATs changed')
    evidence, cost = smoke_evidence()
    if 'resume_parent' in m:
        lineage,_ = retry_lineage(ROOT/m['resume_parent']['path'])
        if lineage != m['resume_parent']: raise ValueError('Retry parent changed')
        cost = lineage['inherited_prior_usd'] + lineage['failed_cost_usd']
    if 'cost_history' in m:
        history = historical_costs(ROOT/m['cost_history']['path'])
        if history != m['cost_history']: raise ValueError('Historical ledger changed')
        cost = history['total_charged_or_reserved_usd']
    if 'rate_limit_parent' in m:
        lineage,_=rate_limit_lineage(ROOT/m['rate_limit_parent']['path'])
        if lineage!=m['rate_limit_parent']: raise ValueError('Rate-limit parent changed')
        cost=lineage['inherited_prior_usd']+lineage['retained_429_reservation_usd']
    if m['smoke_evidence'] != evidence or m['prior_charged_or_reserved_usd'] != cost:
        raise ValueError('Prior accounting changed')
    return m


class TruncatedResponse(ValueError):
    """Settled, known-provider length failure; not a curation action."""


def is_length_response(raw, request):
    choices = raw.get('choices',[])
    return (not raw.get('error') and raw.get('model') == request['model']
            and raw.get('provider') == transport.MODELS[request['model']][1]
            and len(choices) == 1 and choices[0].get('finish_reason') == 'length')


class Provider:
    def __init__(self, out, *, budget, prior, session=None, max_output=4000, record_length_failures=False, retry_overload=False):
        if not math.isfinite(budget) or not 0 < budget <= 25 or not 0 <= prior <= budget:
            raise ValueError('Invalid campaign budget')
        self.out, self.budget, self.prior = Path(out), budget, prior
        if max_output not in (4000,8000): raise ValueError('Unapproved output limit')
        self.max_output, self.record_length_failures = max_output, record_length_failures
        self.retry_overload = retry_overload
        self.out.mkdir(exist_ok=True)
        self.session, self.cursor = session, 0
        self.charged, self.known, self.sent = prior, 0., 0
        folders = sorted(p for p in self.out.iterdir() if p.is_dir())
        for i, folder in enumerate(folders, 1):
            if folder.name != f'{i:06d}': raise ValueError('Noncontiguous call history')
            if not (folder/'decision.json').exists():
                if not record_length_failures or not (folder/'failure.json').exists():
                    raise ValueError('Failed/uncertain call present: no automatic resend or skip')
                self.validate_failure(folder)
            settled = read(folder/'settled.json')
            if not math.isfinite(settled['cost']) or settled['cost'] < 0:
                raise ValueError('Invalid recorded cost')
            self.charged += settled['cost'] + retry_reservations(folder); self.known += settled['cost']

    def validate_failure(self, folder):
        meta,raw,failed = read(folder/'request.json'),read(folder/'response.json'),read(folder/'failure.json')
        if (failed != dict(kind='length',response_sha256=sha(folder/'response.json'),request_sha256=meta['request_sha256'])
            or meta['request']['max_tokens'] != self.max_output
            or read(folder/'http.json')['status'] != 200
            or not is_length_response(raw,meta['request'])
            or read(folder/'settled.json')['cost'] != raw.get('usage',{}).get('cost')):
            raise ValueError('Invalid recorded truncation')

    def ask(self, request, tag):
        if request['max_tokens'] != self.max_output: raise ValueError('Request output limit changed')
        self.cursor += 1
        folder = self.out/f'{self.cursor:06d}'
        h = digest(request)
        if folder.exists():
            meta = read(folder/'request.json')
            if meta['request_sha256'] != h or meta['tag'] != tag:
                raise ValueError('Replay request/response divergence')
            if (folder/'failure.json').exists() and self.record_length_failures:
                self.validate_failure(folder)
                raise TruncatedResponse('Recorded truncation replayed locally; no resend')
            saved = read(folder/'decision.json')
            if saved['response_sha256'] != sha(folder/'response.json'):
                raise ValueError('Replay request/response divergence')
            decision = transport.parse(read(folder/'response.json'), request)
            if saved['decision'] != decision:
                raise ValueError('Recorded decision mismatch')
            return decision
        _,_,ip,op = transport.MODELS[request['model']]
        held = 1.1*(transport.CONTEXT*ip+self.max_output*op)
        if self.charged + held > self.budget: raise ValueError('Insufficient campaign budget')
        folder.mkdir()
        write(folder/'request.json', dict(tag=tag, request_sha256=h, reservation_usd=held,
                                         request=transport.redact_images(request)))
        self.charged += held
        if self.session is None: self.session = transport.make_session()
        try:
            retry_index = 0
            while True:
                self.sent += 1
                start = time.monotonic()
                response = self.session.post(transport.BASE+'/chat/completions', json=request,
                                             timeout=(15, 600 if self.max_output==8000 else 240), allow_redirects=False)
                write(folder/'http.json', dict(status=response.status_code, seconds=time.monotonic()-start))
                raw = response.json()
                write(folder/'response.json', raw)
                if not (self.retry_overload and is_overload(response.status_code,raw,request)
                        and retry_index < len(OVERLOAD_RETRY['delays_seconds'])):
                    break
                delay = retry_delay(response,retry_index)
                if self.charged + held > self.budget:
                    raise ValueError('HTTP 429 upstream overload; insufficient budget for retry reservation')
                saved = folder/'overload_attempts'/f'{retry_index+1:03d}'
                saved.mkdir(parents=True,exist_ok=False)
                (folder/'response.json').rename(saved/'response.json')
                (folder/'http.json').rename(saved/'http.json')
                write(saved/'retry.json',dict(request_sha256=h,reservation_usd=held,delay_seconds=delay,
                    response_sha256=sha(saved/'response.json'),http_sha256=sha(saved/'http.json')))
                self.charged += held
                retry_index += 1
                print(f'{self.cursor} {tag} HTTP 429 upstream overload; retry {retry_index}/3 in {delay:g}s; reservation retained',flush=True)
                time.sleep(delay)
            cost = (raw.get('usage') or {}).get('cost')
            if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
                write(folder/'settled.json', dict(cost=cost, usage=raw['usage'], provider=raw.get('provider')))
                self.charged += cost-held; self.known += cost
                if cost > held or self.charged > self.budget:
                    raise ValueError('Usage exceeded reservation')
            if response.status_code != 200:
                detail = ' upstream overload' if is_overload(response.status_code,raw,request) else ''
                raise ValueError(f'HTTP {response.status_code}{detail}; stopped after {retry_index} retries; see response.json')
            response.raise_for_status()
            if not (folder/'settled.json').exists():
                raise ValueError('Missing cost: reservation retained')
            if self.record_length_failures and response.status_code == 200 and is_length_response(raw,request):
                write(folder/'failure.json',dict(kind='length',response_sha256=sha(folder/'response.json'),request_sha256=h))
                print(f'{self.cursor} {tag} TRUNCATED (no action) ${self.charged:.6f}',flush=True)
                raise TruncatedResponse('Output truncated; settled sample failure, no automatic retry')
            decision = transport.parse(raw, request)
            write(folder/'decision.json', dict(decision=decision, response_sha256=sha(folder/'response.json')))
            print(f'{self.cursor} {tag} {decision["action"]} ${self.charged:.6f}', flush=True)
            return decision
        except BaseException as exc:
            write(folder/'error.json', dict(type=type(exc).__name__,
                message=str(exc) if isinstance(exc, ValueError) else 'See response; no retry'))
            raise

    def close(self):
        if self.session is not None: self.session.close()


class Controller(minimal.MinimalController):
    def __init__(self, *args, model, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = model
        self.start_cost = self.provider.charged
        write(self.out/'openrouter.json', dict(model=model, route=transport.MODELS[model], max_tokens=self.provider.max_output,
                                              thinking=True, skill=None))

    def ask(self, phase, cid, target=None, *, context=None):
        if self.status in {'stopped', 'completed'}: raise minimal.ControllerStop('Finished controller')
        self.status = 'running'
        try:
            key = (context or phase, int(cid), target, self.state_hash())
            if key in self.seen or self.calls >= self.max_calls: raise ValueError('Repeated state/call limit')
            if minimal.prompt_hashes() != self.frozen: raise ValueError('Prompt changed')
            obs, urls, pngs, display = minimal.observe(self.manager, phase, cid, target, sampling_rate=self.fs, view=self.view)
            request = convert(minimal.build_request(phase, obs, urls), self.model,self.provider.max_output)
            step = self.out/f'step_{self.calls+1:05d}'; step.mkdir()
            for png, meta in zip(pngs, display['images']):
                with (step/(meta['name']+'.png')).open('xb') as f: f.write(png)
            write(step/'request.json', dict(phase=phase, context=context, observation=obs, display=display,
                                            request_sha256=digest(request), state_sha256=key[-1]))
            self.seen.add(key); self.calls += 1
            tag = f'{self.model}/rollout/{self.dataset_id}/{self.calls}'
            decision = self.provider.ask(request, tag)
            self.charged = self.provider.charged-self.start_cost
            self.log('decision', phase=phase, cid=int(cid), target=target, **decision)
            return decision
        except BaseException as exc:
            self.status = 'stopped'; self.charged = self.provider.charged-self.start_cost
            self.log('ABSTAIN_stop', phase=phase, cid=int(cid), error_type=type(exc).__name__, state_sha256=self.state_hash())
            raise minimal.ControllerStop('OpenRouter stopped; state preserved') from exc


def check_endpoints(provider, out):
    if provider.session is None: provider.session = transport.make_session()
    for model in MODELS:
        r = provider.session.get(transport.BASE+'/models/'+model+'/endpoints', timeout=(15,30), allow_redirects=False)
        r.raise_for_status(); data = r.json()
        write(out/(model.split('/')[-1]+'_endpoint.json'), data)
        e = next(x for x in data['data']['endpoints'] if x['tag'] == transport.MODELS[model][0])
        if e['context_length'] != transport.CONTEXT or e['quantization'] != transport.MODELS[model][0].split('/')[-1]:
            raise ValueError('Endpoint/context changed')
        if not {'structured_outputs','reasoning','response_format','max_tokens'} <= set(e['supported_parameters']):
            raise ValueError('Required parameters unsupported')
        if [float(e['pricing'][k]) for k in ['prompt','completion']] != list(transport.MODELS[model][2:]):
            raise ValueError('Endpoint prices changed')
        if any(float(v) for k,v in e['pricing'].items() if k not in {'prompt','completion','discount'} and v is not None):
            raise ValueError('Extra endpoint pricing')


def score_block(targets,predictions,failures):
    t = {r['job_id']:r for r in targets}
    failed_ids = {r['job_id'] for r in failures}
    valid_ids = {r['job_id'] for r in predictions}
    if len(failed_ids)!=len(failures) or not failed_ids<=set(t) or failed_ids&valid_ids:
        raise ValueError('Duplicate/unplanned failure or failure also scored as success')
    result = minimal_stepwise.score(targets,predictions)
    attempted = len(predictions)+len(failures)
    result.update(attempted=attempted,failed=len(failures),unattempted=len(t)-attempted,
                  failure_rate=len(failures)/attempted if attempted else None,
                  correct_over_planned=result['correct']/len(t) if t else None,
                  accuracy=result['correct']/len(t) if t and attempted==len(t) else None,
                  note='Full accuracy = correct/all planned only after every sample attempted; failures get no action and count as non-correct. Valid-only accuracy is separate.')
    for action,metrics in result['per_class'].items():
        metrics['failed_support'] = sum(t[j]['expert_action']==action for j in failed_ids)
        metrics['recall_over_planned_support'] = (result['confusion_matrix'][action][action]/metrics['planned_support']
                                                   if metrics['planned_support'] else None)
    return result


def score_stepwise(predictions, failures=()):
    targets = [json.loads(line) for line in (SOURCE/'stepwise_targets.jsonl').read_text().splitlines()]
    return dict(overall=score_block(targets, predictions,failures),failures=list(failures),
        by_dataset={d:score_block([t for t in targets if t['dataset_id']==d],
                    [p for p in predictions if p['dataset_id']==d],[f for f in failures if f['dataset_id']==d]) for d in minimal.DATASETS},
        by_phase={s:score_block([t for t in targets if t['phase']==s],
                  [p for p in predictions if p['phase']==s],[f for f in failures if f['phase']==s]) for s in ('phase1','phase2')},
        mismatches=[dict(p, expert_action=next(t['expert_action'] for t in targets if t['job_id']==p['job_id']))
                    for p in predictions if p['action'] != next(t['expert_action'] for t in targets if t['job_id']==p['job_id'])])


def score_terminal(controller, row, initial):
    if controller.status != 'completed': raise ValueError('Cannot score partial rollout as final')
    import h5py
    from scipy.io import loadmat
    from scripts.analysis.collect_curation_stage6 import evaluate, deletion_audit
    path = ROOT/row['mat_path']
    if sha(path) != row['mat_sha256']: raise ValueError('MAT changed')
    if h5py.is_hdf5(path):
        with h5py.File(path,'r') as f: gt = f['spikes/curation/assigns'][:].flatten()
    else:
        gt = np.asarray(loadmat(str(path),struct_as_record=False,squeeze_me=True,variable_names=['spikes'])['spikes'].curation.assigns).reshape(-1)
    m = controller.manager
    result = dict(dataset_id=row['dataset_id'], status='completed', final_sha256=controller.state_hash(),
        one_to_one=curation_one_to_one.evaluate(m.assigns, gt),
        many_to_one=evaluate(m.assigns,gt,m.spike_times,controller.fs),
        no_curation_one_to_one=curation_one_to_one.evaluate(initial,gt),
        no_curation_many_to_one=evaluate(initial,gt,m.spike_times,controller.fs))
    events = [json.loads(line) for line in (controller.out/'events.jsonl').read_text().splitlines()]
    result['expert_spike_deletion_cases'] = deletion_audit(controller.out,events,gt)
    return result


def evaluate_rollout(controller,row,initial, *, allow_truncation=False):
    try:
        controller.run()
    except minimal.ControllerStop as exc:
        if not allow_truncation or not isinstance(exc.__cause__,TruncatedResponse): raise
        if controller.status != 'stopped': raise ValueError('Truncated controller did not stop')
        return dict(dataset_id=row['dataset_id'],status='stopped_truncated',final_metrics=None,
                    terminal_evaluation_allowed=False,state_sha256=controller.state_hash(),
                    checkpoint=str(controller.out/'stopped.npz'),reason='length; no action applied, no retry')
    return score_terminal(controller,row,initial)


def cost_breakdown(out, prior):
    groups = {}
    for folder in sorted((out/'calls').glob('*')):
        if not folder.is_dir(): continue
        meta = read(folder/'request.json')
        model = meta['request']['model']
        stage = 'stepwise' if '/stepwise/' in meta['tag'] else 'rollout'
        g = groups.setdefault(model,{}).setdefault(stage,dict(calls=0,valid=0,truncated=0,
            known_usd=0.,unsettled_reservation_usd=0.,input_tokens=0,output_tokens=0,reasoning_tokens=0,cached_input_tokens=0))
        g['calls']+=1;g['valid']+=int((folder/'decision.json').exists());g['truncated']+=int((folder/'failure.json').exists())
        n_retries = len(list((folder/'overload_attempts').glob('*')))
        g['overload_retries'] = g.get('overload_retries',0) + n_retries
        g['calls'] += n_retries
        g['unsettled_reservation_usd'] += retry_reservations(folder)
        if (folder/'settled.json').exists():
            settled=read(folder/'settled.json');usage=settled.get('usage',{})
            g['known_usd']+=settled['cost']
            g['input_tokens']+=usage.get('prompt_tokens',0) or 0
            g['output_tokens']+=usage.get('completion_tokens',0) or 0
            g['reasoning_tokens']+=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens',0) or 0
            g['cached_input_tokens']+=(usage.get('prompt_tokens_details') or {}).get('cached_tokens',0) or 0
        else: g['unsettled_reservation_usd']+=meta['reservation_usd']
    return dict(inherited_charged_or_reserved_usd=prior,by_model_and_stage=groups,
                note='Known cost is provider-reported, not a billing invoice. Unsettled reservations are not claimed expenditure. Includes recorded failures, no repeated billing of local replays.')


def execute(out, budget=25, resume=False):
    m = verify(out)
    if budget != m['budget_usd']: raise ValueError('Budget differs from approved manifest')
    lock = (out/'execution.lock').open('a')
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    attempts = sorted(out.glob('attempt_*'))
    if attempts and not resume: raise ValueError('Existing execution: explicit --resume required')
    if not attempts and resume: raise ValueError('No execution to resume')
    attempt = out/f'attempt_{len(attempts)+1:03d}'; attempt.mkdir()
    provider, status, error = None, 'stopped', None
    predictions = {model:[] for model in MODELS}; results = {model:[] for model in MODELS}
    failures = {model:[] for model in MODELS}
    try:
        provider = Provider(out/'calls',budget=budget,prior=m['prior_charged_or_reserved_usd'],
                            max_output=m['max_output_tokens'],record_length_failures=m.get('record_length_failures',False),
                            retry_overload=bool(m.get('transport_retry_policy')))
        check_endpoints(provider,attempt)
        jobs = read(SOURCE/'manifest.json')['stepwise']['jobs']
        expected = {(r['model'],r['job_id']):r['sha256'] for r in m['requests']}
        for model in MODELS:
            for job in jobs:
                request = build_stepwise(job,model,m['max_output_tokens'])
                if digest(request) != expected[(model,job['job_id'])]: raise ValueError('Frozen request changed')
                sample = dict(job_id=job['job_id'],dataset_id=job['dataset_id'],phase=job['phase'])
                try:
                    decision = provider.ask(request,f'{model}/stepwise/{job["job_id"]}')
                except TruncatedResponse:
                    if not m.get('record_length_failures',False): raise
                    record = dict(sample,failure='length',call=provider.cursor)
                    failures[model].append(record)
                else:
                    record = dict(sample,**decision)
                    predictions[model].append(record)
                transport.log(attempt/'stepwise_progress.jsonl',dict(model=model,**record))
            write(attempt/(model.split('/')[-1]+'_stepwise.json'),score_stepwise(predictions[model],failures[model]))
        for model in MODELS:
            for row in m['datasets']:
                verify(out)
                actor,fs = minimal.load_actor(row); initial = actor.assigns.copy()
                c = Controller(actor,row['dataset_id'],attempt/model.split('/')[-1]/row['dataset_id'],
                    provider=provider,budget_usd=max(0,budget-provider.charged),sampling_rate=fs,model=model)
                result = evaluate_rollout(c,row,initial,allow_truncation=m.get('record_length_failures',False))
                write(c.out/'evaluation.json',result); results[model].append(result)
                print(f'ROLLOUT {model} {row["dataset_id"]} {result["status"]}',flush=True)
                del c,actor,initial
        status = ('completed_with_failures' if any(failures.values()) or
                  any(r['status']!='completed' for rows in results.values() for r in rows) else 'completed')
    except BaseException as exc:
        error = dict(type=type(exc).__name__,cause=type(exc.__cause__).__name__ if exc.__cause__ else None,
                     message=str(exc) if isinstance(exc,ValueError) else 'See call response/error; no retry')
    finally:
        if provider: provider.close()
        summary = dict(status=status,error=error,known_new_campaign_cost=provider.known if provider else None,
            charged_or_reserved_including_smoke=provider.charged if provider else None,
            new_http_calls_this_attempt=provider.sent if provider else 0,
            predictions_completed={k:len(v) for k,v in predictions.items()},
            stepwise_failures={k:len(v) for k,v in failures.items()},
            completed_rollouts={k:sum(r['status']=='completed' for r in v) for k,v in results.items()},
            stopped_rollouts={k:sum(r['status']!='completed' for r in v) for k,v in results.items()})
        write(attempt/'summary.json',summary)
        write(attempt/'cost_breakdown.json',cost_breakdown(out,m['prior_charged_or_reserved_usd']))
        scores = {k:score_stepwise(v,failures[k]) for k,v in predictions.items()}
        write(attempt/'score.json',dict(stepwise=scores,rollouts=results))
        lines = ['# Qwen minimal-prompt baseline', '',f'Status: {status}; output limit: {m["max_output_tokens"]}.',
                 'Stepwise and autonomous rollout are separate evaluations. Truncations are failures, never actions or retries.',
                 '', '| Model | Planned | Attempted | Valid | Failed | Correct | Full accuracy | Valid-only accuracy |',
                 '|---|---:|---:|---:|---:|---:|---:|---:|']
        for model,score in scores.items():
            s=score['overall']
            lines.append('| '+model+' | '+' | '.join(str(s[k]) for k in ('planned','attempted','valid','failed','correct','accuracy','accuracy_on_valid'))+' |')
        lines += ['',f'Cumulative charged/reserved: {summary["charged_or_reserved_including_smoke"]}; includes inherited expenses.',
                  'Only completed rollouts receive terminal scores; details and failures: score.json.',f'Campaign error: {error}.']
        with (attempt/'REPORT.md').open('x') as f: f.write('\n'.join(lines)+'\n')
        lock.close()
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root',required=True,type=Path)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--retry-truncated-from',type=Path,
                   help='Offline prepare a new lineage; explicitly retry one settled length failure, unchanged settings')
    p.add_argument('--budget-usd',type=float,default=25)
    p.add_argument('--max-output-tokens',type=int,choices=(4000,8000),default=4000,help='Offline preparation only; execution uses frozen manifest')
    p.add_argument('--cost-history-from',type=Path,help='Carry all old costs into a fresh evaluation, no response reuse')
    p.add_argument('--retry-rate-limited-from',type=Path,help='Explicit continuation after known upstream 429; reuse valid/truncated outcomes and keep reservation')
    p.add_argument('--retry-overload',action='store_true',help='Freeze bounded transport-only 429 retries in a new continuation manifest')
    a = p.parse_args(); out = a.output_root.absolute()
    if out.resolve()!=out or out.parent!=ROOT/'zero-shot baseline/runs': p.error('Use a direct child of baseline/runs')
    if a.resume and not a.execute: p.error('--resume requires --execute')
    if a.retry_truncated_from and (a.execute or a.resume): p.error('Prepare retry lineage offline, then explicitly execute it')
    if a.execute and (a.cost_history_from or a.max_output_tokens!=4000 or a.retry_rate_limited_from or a.retry_overload): p.error('Execution settings are read from frozen manifest; prepare first')
    if a.execute:
        summary = execute(out,a.budget_usd,a.resume); print(json.dumps(summary))
        return 0 if summary['status'] in ('completed','completed_with_failures') else 1
    m = prepare(out,a.retry_truncated_from,max_output=a.max_output_tokens,cost_history_from=a.cost_history_from,
                rate_limit_from=a.retry_rate_limited_from,retry_overload=a.retry_overload); print(json.dumps(dict(mode='dry-run',requests_verified=len(m['requests']),api_calls=0,
                                           manifest_sha256=sha(out/'manifest.json'))))
    return 0


if __name__ == '__main__': sys.exit(main())
