"""Qwen 9B: Astra minimal inputs vs frozen system_v1/skill_v0.

Offline preparation by default. Only --execute opens a client. Existing results,
prompts, controller and scoring are not edited. Length failure terminates that
dataset, never synthesizes an action. Transport recovery is bounded and journaled.
New requests use 8000 total output tokens and request a 6000 thinking budget;
the latter is not a verified server-side hard cap. Never resume a 4000-token run.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run import run_openrouter_baseline as old
from src.agent import curation_contract as contract
from src.pipeline.curation_engineering_v1 import CurationController, arrays_hash

minimal, transport = old.minimal, old.transport
read, write, sha, digest = transport.read, transport.write, transport.sha, transport.digest
MODEL = 'qwen/qwen3.5-9b'
ARMS = ('minimal', 'system_v1_skill_v0')
SAMPLING = dict(temperature=1.0, top_p=0.95, top_k=20, min_p=0.0,
                presence_penalty=1.5, repetition_penalty=1.0)
MAX_OUTPUT = 8000
THINKING_BUDGET = 6000
ASTRA_MAX_OUTPUT = 4000
DELAYS = (30, 60, 120)
CONFIGS = ('system_v1.txt', 'skill_v0.json', 'protocol_v1.json')


def atomic(path, obj):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def config_hashes():
    return {name: sha(contract.CONFIG_DIR/name) for name in CONFIGS}


def code_hashes():
    return dict(old.code_hashes(), **{str(Path(__file__).relative_to(ROOT)): sha(__file__)})


def reasoning_config():
    return dict(enabled=True, exclude=False, max_tokens=THINKING_BUDGET)


def reservation():
    _, _, ip, op = transport.MODELS[MODEL]
    return 1.1 * (transport.CONTEXT * ip + MAX_OUTPUT * op)


def build_request(arm, phase, obs, urls):
    if arm not in ARMS:
        raise ValueError('Unapproved arm')
    source = (minimal.build_request(phase, obs, urls) if arm == 'minimal' else
              contract.build_request(phase, obs, urls, skill_version='v0'))
    request = old.convert(source, MODEL, MAX_OUTPUT)
    request['reasoning'] = reasoning_config()
    request.update(SAMPLING)
    return request


def astra_evidence():
    m = read(transport.SOURCE/'manifest.json')
    if (m['prompt_hashes'] != minimal.prompt_hashes() or m['code_hashes'] != minimal.sources()
            or m['environment'] != minimal.infra.environment() or m['max_output_tokens'] != ASTRA_MAX_OUTPUT):
        raise ValueError('Astra frozen prompt/code/environment differs; do not claim identical baseline')
    return m


def historical_costs(parent, seen=()):
    """Read-only lineage: legacy campaign -> journaled rollout -> paid probes.

    Never reuse predictions, settle old reservations, or open an SDK/client.
    Recompute journal costs with its historical limit, not today's reservation.
    """
    parent = Path(parent).resolve()
    if not parent.is_relative_to(ROOT/'zero-shot baseline/runs') or parent in seen:
        raise ValueError('Invalid/cyclic cost history')
    m = read(parent/'manifest.json')
    journal = str(m.get('version', '')).startswith('qwen9b-rollout-comparison-')
    probe = m.get('kind') == 'qwen9b-independent-thinking-budget-capability-probe'
    if not journal and not probe:
        return old.historical_costs(parent)
    seen = (*seen, parent)
    if journal:
        inherited = historical_costs(ROOT/m['cost_history']['path'], seen)
        if inherited != m['cost_history']:
            raise ValueError('Historical inherited ledger changed')
        if read(parent/'preflight.json')['manifest_sha256'] != sha(parent/'manifest.json'):
            raise ValueError('Historical manifest changed')
        if m['prior_charged_or_reserved_usd'] != inherited['total_charged_or_reserved_usd']:
            raise ValueError('Historical prior cost mismatch')
        # Read-only reconstruction; __init__ would use the new output limit.
        p = JournalProvider.__new__(JournalProvider)
        p.out, p.prior = parent/'calls', m['prior_charged_or_reserved_usd']
        _, _, ip, op = transport.MODELS[m['model']]
        p.held = 1.1*(transport.CONTEXT*ip + m['max_output_tokens']*op)
        ledger = p.ledger()
        status = read(parent/'execution_status.json')
        if (status['status'] not in ('stopped', 'completed', 'completed_with_failures')
                or status['costs'] != ledger or read(parent/'cost_breakdown.json') != ledger
                or read(parent/'progress.json') != status):
            raise ValueError('Historical journal changed or still running')
        total = ledger['charged_or_reserved_usd']
        paths = [parent/n for n in ('preflight.json', 'execution_status.json',
                                   'cost_breakdown.json', 'progress.json')]
        paths += [f for f in (parent/'calls').rglob('*') if f.is_file()]
    else:
        source = ROOT/m['source']
        base = historical_costs(source, seen)
        if (sha(source/'manifest.json') != m['source_manifest_sha256']
                or sha(source/'cost_breakdown.json') != m['source_cost_sha256']):
            raise ValueError('Probe source ledger changed')
        previous = m.get('previous_probe')
        inherited = base
        if previous:
            prev = ROOT/previous['path']
            if (sha(prev/'result.json') != previous['result_sha256']
                    or read(prev/'manifest.json')['source'] != m['source']):
                raise ValueError('Previous probe ledger changed')
            inherited = historical_costs(prev, seen)
        costs, result = read(parent/'cost.json'), read(parent/'result.json')
        raw_cost = read(parent/'response.json').get('usage', {}).get('cost')
        if type(raw_cost) not in (int, float) or not math.isfinite(raw_cost) or raw_cost < 0:
            raise ValueError('Probe has no settled valid cost; do not discard reservation')
        prior = inherited['total_charged_or_reserved_usd']
        expected = dict(known_usd=raw_cost, unsettled_reservation_usd=0,
            prior_charged_or_reserved_usd=prior,
            cumulative_charged_or_reserved_usd=prior+raw_cost)
        if (m['prior_charged_or_reserved_usd'] != prior or costs != expected
                or result['costs'] != expected or result['calls_sent'] != 1):
            raise ValueError('Probe cumulative accounting mismatch')
        total = expected['cumulative_charged_or_reserved_usd']
        paths = [parent/n for n in ('cost.json', 'result.json', 'response.json',
                                   'http.json', 'reserved.json', 'execution_started.json')]
    if not math.isfinite(total) or not 0 <= total <= 25:
        raise ValueError('Invalid cumulative historical cost')
    return dict(path=str(parent.relative_to(ROOT)), manifest_sha256=sha(parent/'manifest.json'),
        file_hashes={str(f.relative_to(parent)):sha(f) for f in sorted(paths)},
        inherited_history=inherited, total_charged_or_reserved_usd=total,
        successful_predictions_reused=0)


def prepare(out, parent):
    source = astra_evidence()
    history = historical_costs(parent)
    rows = minimal.selected_rows(minimal.DATASETS)
    out.mkdir(parents=True, exist_ok=False)
    previews = []
    for row, saved in zip(rows, source['datasets']):
        if any(saved[k] != v for k, v in row.items()):
            raise ValueError('Astra dataset mismatch')
        actor, fs = minimal.load_actor(row)
        cid = int(actor.get_active_clusters()[0])
        obs, urls, pngs, display = minimal.observe(actor, 'phase1', cid, sampling_rate=fs, view='raw_member_v1')
        original = minimal.build_request('phase1', obs, urls)
        if digest(original) != saved['first_request_sha256']:
            raise ValueError('Initial request is not byte-identical to Astra')
        folder = out/'preflight'/row['dataset_id']; folder.mkdir(parents=True)
        for png, meta in zip(pngs, display['images']):
            if hashlib.sha256(png).hexdigest() != meta['sha256']:
                raise ValueError('Image hash mismatch')
            (folder/(meta['name']+'.png')).write_bytes(png)
        requests = {arm: build_request(arm, 'phase1', obs, urls) for arm in ARMS}
        # Same actual images and numerical observation; different prompt bundles.
        assert requests[ARMS[0]]['messages'][1]['content'][1:] == requests[ARMS[1]]['messages'][1]['content'][1:]
        for arm, request in requests.items():
            write(folder/(arm+'.json'), transport.redact_images(request))
        previews.append(dict(dataset_id=row['dataset_id'], astra_request_sha256=digest(original),
            requests={k: digest(v) for k, v in requests.items()}, images=display['images']))
        print('PREFLIGHT', row['dataset_id'], 'Astra source input verified; Qwen token limits differ; no API', flush=True)
        del actor
    m = dict(version='qwen9b-rollout-comparison-8000-thinking6000-v2', model=MODEL, arms=list(ARMS),
        datasets=rows, max_output_tokens=MAX_OUTPUT, sampling=SAMPLING, thinking=True,
        reasoning=reasoning_config(), thinking_budget_enforcement='unverified',
        per_attempt_reservation_usd=reservation(),
        route=list(transport.MODELS[MODEL]), budget_usd=25,
        cost_history=history, prior_charged_or_reserved_usd=history['total_charged_or_reserved_usd'],
        astra_manifest_sha256=sha(transport.SOURCE/'manifest.json'),
        prompt_hashes=minimal.prompt_hashes(), config_hashes=config_hashes(),
        code_hashes=code_hashes(), environment=minimal.infra.environment(),
        scoring_sha256=sha(old.curation_one_to_one.RULES), previews=previews,
        order='All four minimal rollouts, then all four frozen system_v1/skill_v0 rollouts; no stepwise, no 27B',
        controller='Unchanged CurationController.run/apply/checkpoint, raw_member_v1; 10000 decision safety limit',
        comparison='Prompt-bundle comparison, NOT skill-only ablation: system, skill and observation text formatting differ; images, numeric values, scheduler and scoring identical',
        astra_differences='Same prompt bytes, input definitions, plotting and controller. Model, API envelope/provider, Qwen sampling and transport retries differ. Astra: high/4000 total. Qwen: thinking enabled/8000 total, requested 6000 thinking cap with unverified enforcement; no guaranteed 2000-token answer reserve.',
        sampling_source='https://huggingface.co/Qwen/Qwen3.5-9B',
        sampling_caveat='Official general thinking sampling, user-selected 8000 total output cap and requested 6000 thinking budget; no guarantee against truncation or enforced thinking cap',
        failure_policy='length stops dataset without action; next dataset. Incomplete terminals have no final scores. No truncation retries.',
        recovery_policy=dict(max_attempts_per_decision=4, delays_seconds=list(DELAYS),
            retry='429/502/503/504 without choices, connection/timeout, interrupted in-flight; retain known cost or entire reservation for every attempt',
            stop='Budget/auth/parse/provenance/config errors or exhausted transport retries; never silently repair or fall back'),
        authorization='User 2026-10-13 set 8000 total/6000 thinking for 9B only. Existing four-channel scope and cumulative $25 remain; configuration preparation itself sends no inference. No expert labels/comments sent.')
    write(out/'manifest.json', m)
    for path in m['code_hashes']:
        dest = out/'code_snapshot'/path; dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT/path).read_bytes())
    write(out/'preflight.json', dict(manifest_sha256=sha(out/'manifest.json'), api_calls=0,
        astra_initial_requests_identical=4, preview_requests=8))
    return m


def verify(out):
    m = read(out/'manifest.json')
    if read(out/'preflight.json')['manifest_sha256'] != sha(out/'manifest.json'):
        raise ValueError('Manifest changed')
    if (m['model'] != MODEL or m['arms'] != list(ARMS) or m['max_output_tokens'] != MAX_OUTPUT
            or m.get('reasoning') != reasoning_config()
            or m.get('thinking_budget_enforcement') != 'unverified'
            or m.get('per_attempt_reservation_usd') != reservation()
            or m['sampling'] != SAMPLING or m['code_hashes'] != code_hashes()
            or m['prompt_hashes'] != minimal.prompt_hashes() or m['config_hashes'] != config_hashes()
            or m['environment'] != minimal.infra.environment()
            or m['scoring_sha256'] != sha(old.curation_one_to_one.RULES)):
        raise ValueError('Frozen experiment changed')
    astra_evidence()
    if m['astra_manifest_sha256'] != sha(transport.SOURCE/'manifest.json') or m['datasets'] != minimal.selected_rows(minimal.DATASETS):
        raise ValueError('Frozen source changed')
    if historical_costs(ROOT/m['cost_history']['path']) != m['cost_history']:
        raise ValueError('Inherited costs changed')
    return m


class TransportStopped(RuntimeError):
    pass


class JournalProvider:
    kind = 'openrouter-qwen9b-journaled'

    def __init__(self, out, *, budget, prior, session=None, sleeper=time.sleep):
        if not math.isfinite(budget) or not 0 < budget <= 25 or not 0 <= prior <= budget:
            raise ValueError('Invalid campaign budget')
        self.out, self.budget, self.prior = Path(out), budget, prior
        self.out.mkdir(exist_ok=True)
        self.session, self.sleeper, self.cursor, self.sent = session, sleeper, 0, 0
        self.max_output = MAX_OUTPUT
        self.held = reservation()
        folders = sorted(self.out.iterdir())
        if any(not f.is_dir() or f.name != f'{i:06d}' for i, f in enumerate(folders, 1)):
            raise ValueError('Noncontiguous call ledger')
        self.charged = self.ledger()['charged_or_reserved_usd']

    def ledger(self):
        groups = {arm: dict(attempts=0, valid=0, truncated=0, known_usd=0.,
            unsettled_reservation_usd=0., input_tokens=0, output_tokens=0, reasoning_tokens=0,
            cached_input_tokens=0) for arm in ARMS}
        for folder in sorted(self.out.iterdir()):
            meta = read(folder/'request.json'); g = groups[meta['arm']]
            g['valid'] += int((folder/'decision.json').exists())
            g['truncated'] += int((folder/'failure.json').exists())
            for a in sorted((folder/'attempts').glob('*')):
                reserve = read(a/'reserved.json')
                if reserve['usd'] != self.held or reserve['request_sha256'] != meta['request_sha256']:
                    raise ValueError('Reservation changed')
                g['attempts'] += 1
                if (a/'settled.json').exists():
                    settled = read(a/'settled.json'); raw = read(a/'response.json')
                    if (settled['response_sha256'] != sha(a/'response.json') or
                            settled['cost'] != raw.get('usage', {}).get('cost')):
                        raise ValueError('Settled response changed')
                    g['known_usd'] += settled['cost']
                    usage = raw['usage']
                    for k, src in [('input_tokens','prompt_tokens'), ('output_tokens','completion_tokens')]:
                        g[k] += usage.get(src, 0) or 0
                    g['reasoning_tokens'] += (usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0) or 0
                    g['cached_input_tokens'] += (usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0) or 0
                else:
                    g['unsettled_reservation_usd'] += self.held
        return dict(inherited_charged_or_reserved_usd=self.prior, by_arm=groups,
            charged_or_reserved_usd=self.prior+sum(g['known_usd']+g['unsettled_reservation_usd'] for g in groups.values()))

    def settle(self, a, raw):
        cost = (raw.get('usage') or {}).get('cost')
        if type(cost) in (float, int) and math.isfinite(cost) and cost >= 0:
            record = dict(cost=cost, response_sha256=sha(a/'response.json'))
            if (a/'settled.json').exists():
                if read(a/'settled.json') != record: raise ValueError('Cost changed')
            else: write(a/'settled.json', record)
            self.charged = self.ledger()['charged_or_reserved_usd']
            if cost > self.held or self.charged > self.budget:
                raise ValueError('Cost exceeded reservation/budget')

    def outcome(self, folder, a, request):
        """Can finish a locally saved response after interruption; never sends."""
        if not (a/'response.json').exists():
            return None
        raw, http = read(a/'response.json'), read(a/'http.json')
        self.settle(a, raw)
        if http['status'] in (429, 502, 503, 504) and not raw.get('choices'):
            return None
        if http['status'] != 200: raise ValueError(f"Nonrecoverable HTTP {http['status']}")
        if not (a/'settled.json').exists(): raise ValueError('No known cost; reservation retained')
        if old.is_length_response(raw, request):
            record = dict(kind='length', attempt=a.name, response_sha256=sha(a/'response.json'))
            if (folder/'failure.json').exists():
                if read(folder/'failure.json') != record: raise ValueError('Truncation record changed')
            else: write(folder/'failure.json', record)
            raise old.TruncatedResponse(f'{MAX_OUTPUT} token limit; no action; next dataset')
        decision = transport.parse(raw, request)
        if raw['usage'].get('completion_tokens', 0) > MAX_OUTPUT:
            raise ValueError('Provider exceeded output cap')
        record = dict(decision=decision, attempt=a.name, response_sha256=sha(a/'response.json'))
        if (folder/'decision.json').exists():
            if read(folder/'decision.json') != record: raise ValueError('Decision changed')
        else: write(folder/'decision.json', record)
        return decision

    def ask(self, request, tag, arm):
        if (request['model'] != MODEL or request['max_tokens'] != MAX_OUTPUT
                or request.get('reasoning') != reasoning_config()
                or any(request.get(k) != v for k, v in SAMPLING.items()) or arm not in ARMS):
            raise ValueError('Unapproved inference settings')
        self.cursor += 1
        folder = self.out/f'{self.cursor:06d}'
        meta = dict(tag=tag, arm=arm, request_sha256=digest(request), request=transport.redact_images(request))
        if folder.exists():
            if read(folder/'request.json') != meta: raise ValueError('Replay divergence')
        else:
            folder.mkdir(); write(folder/'request.json', meta); (folder/'attempts').mkdir()
        tries = sorted((folder/'attempts').iterdir())
        if any(p.name != f'{i:03d}' for i, p in enumerate(tries, 1)) or len(tries) > 4:
            raise ValueError('Invalid retry sequence')
        for i, a in enumerate(tries):
            decision = self.outcome(folder, a, request)
            if decision is not None:
                if i != len(tries)-1: raise ValueError('Attempt after success')
                return decision
        for number in range(len(tries)+1, 5):
            if number > 1:
                delay = DELAYS[number-2]
                previous = folder/'attempts'/f'{number-1:03d}'
                if (previous/'http.json').exists():
                    ra = read(previous/'http.json').get('retry_after')
                    if ra is not None:
                        try: value = float(ra)
                        except (TypeError, ValueError): raise TransportStopped('Invalid Retry-After') from None
                        if not math.isfinite(value) or value > 300: raise TransportStopped('Retry-After above bounded recovery window')
                        delay = max(delay, value)
                print(f'RECOVERY {tag}: attempt {number}/4 after {delay:g}s; previous cost/reservation retained', flush=True)
                self.sleeper(delay)
            self.charged = self.ledger()['charged_or_reserved_usd']
            if self.charged+self.held > self.budget: raise ValueError('Insufficient cumulative budget')
            a = folder/'attempts'/f'{number:03d}'; a.mkdir()
            write(a/'reserved.json', dict(usd=self.held, request_sha256=meta['request_sha256']))
            self.charged += self.held
            atomic(self.out.parent/'progress.json', dict(status='request_in_flight', tag=tag,
                attempt=number, decision_index=self.cursor, charged_or_reserved_usd=self.charged))
            if self.session is None: self.session = transport.make_session()
            import requests
            try:
                self.sent += 1; start = time.monotonic()
                response = self.session.post(transport.BASE+'/chat/completions', json=request,
                    timeout=(15, 600), allow_redirects=False)
                write(a/'http.json', dict(status=response.status_code, seconds=time.monotonic()-start,
                    retry_after=response.headers.get('Retry-After')))
                write(a/'response.json', response.json())
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, TimeoutError) as exc:
                write(a/'transport_error.json', dict(type=type(exc).__name__, unknown_billing=True))
                continue
            decision = self.outcome(folder, a, request)
            if decision is not None:
                print(f'{self.cursor} {tag} {decision["action"]} cumulative ${self.charged:.6f}', flush=True)
                return decision
        raise TransportStopped('Four attempts exhausted; all costs/reservations preserved')

    def close(self):
        if self.session is not None: self.session.close()


class Controller(CurationController):
    """Keep inherited run/apply/checkpoint; change only prompt/provider boundary."""
    def __init__(self, manager, dataset_id, out, *, provider, arm, sampling_rate):
        minimal.require_dataset(dataset_id)
        if manager.history or arm not in ARMS: raise ValueError('Fresh actor and frozen arm required')
        self.manager, self.dataset_id, self.out = manager, dataset_id, Path(out)
        self.out.mkdir(parents=True, exist_ok=False)
        self.provider, self.arm, self.fs = provider, arm, sampling_rate
        self.view, self.max_calls = 'raw_member_v1', 10000
        self.calls, self.operations, self.charged, self.seen = 0, 0, 0., set()
        self.start_cost, self.status = provider.charged, 'ready'
        self.input_hash = arrays_hash(manager.overcluster_assigns, manager.spike_times, manager.waveforms)
        self.frozen = dict(minimal=minimal.prompt_hashes(), harness=config_hashes())
        write(self.out/'manifest.json', dict(dataset_id=dataset_id, arm=arm, view=self.view,
            sampling_rate=sampling_rate, input_sha256=self.input_hash, prompt_hashes=self.frozen,
            model=MODEL, sampling=SAMPLING, max_tokens=MAX_OUTPUT, max_calls=self.max_calls,
            reasoning=reasoning_config(), thinking_budget_enforcement='unverified',
            auto_discard_threshold=0, final_minimum_threshold=0, small_cluster_threshold=4000,
            skill=None if arm=='minimal' else 'v0', legacy_system=False))
        self.checkpoint('initial')

    def ask(self, phase, cid, target=None, *, context=None):
        if self.status in {'completed', 'stopped'}: raise minimal.ControllerStop('Finished controller')
        self.status = 'running'
        try:
            key = (context or phase, int(cid), target, self.state_hash())
            if key in self.seen or self.calls >= self.max_calls: raise ValueError('Repeated state/call limit')
            if self.frozen != dict(minimal=minimal.prompt_hashes(), harness=config_hashes()):
                raise ValueError('Prompt changed')
            obs, urls, pngs, display = minimal.observe(self.manager, phase, cid, target, sampling_rate=self.fs, view=self.view)
            request = build_request(self.arm, phase, obs, urls)
            step = self.out/f'step_{self.calls+1:05d}'; step.mkdir()
            for png, meta in zip(pngs, display['images']):
                if hashlib.sha256(png).hexdigest() != meta['sha256']: raise ValueError('PNG mismatch')
                (step/(meta['name']+'.png')).write_bytes(png)
            write(step/'request.json', dict(phase=phase, context=context, observation=obs, display=display,
                request_sha256=digest(request), state_sha256=key[-1], payload=transport.redact_images(request)))
            self.calls += 1; self.seen.add(key)
            tag = f'{MODEL}/rollout/{self.arm}/{self.dataset_id}/{self.calls}'
            decision = self.provider.ask(request, tag, self.arm)
            self.charged = self.provider.charged-self.start_cost
            self.log('decision', phase=phase, cid=int(cid), target=target, **decision)
            return decision
        except BaseException as exc:
            self.charged = self.provider.charged-self.start_cost; self.status = 'stopped'
            self.log('ABSTAIN_stop', phase=phase, cid=int(cid), target=target,
                error_type=type(exc).__name__, state_sha256=self.state_hash())
            raise minimal.ControllerStop('Stopped; state preserved, no action') from exc


def check_endpoint(provider, folder):
    if provider.session is None: provider.session = transport.make_session()
    response = provider.session.get(transport.BASE+'/models/'+MODEL+'/endpoints', timeout=(15, 30), allow_redirects=False)
    response.raise_for_status(); catalog = response.json()
    write(folder/'endpoint.json', catalog)
    e = next(e for e in catalog['data']['endpoints'] if e['tag'] == transport.MODELS[MODEL][0])
    required = set(SAMPLING) | {'reasoning','structured_outputs','response_format','max_tokens'}
    if (not required <= set(e['supported_parameters']) or e['quantization'] != 'bf16'
            or e['context_length'] != transport.CONTEXT
            or [float(e['pricing'][k]) for k in ('prompt','completion')] != list(transport.MODELS[MODEL][2:])
            or any(float(v) for k,v in e['pricing'].items() if k not in {'prompt','completion','discount'} and v is not None)):
        raise ValueError('Pinned endpoint parameters/price changed')


def execute(out, *, budget, resume=False):
    m = verify(out)
    if budget != m['budget_usd']: raise ValueError('Budget differs from approved manifest')
    lock = (out/'execution.lock').open('a'); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    attempts = sorted(out.glob('attempt_*'))
    if bool(attempts) != resume: raise ValueError('Use --resume only for an existing execution')
    attempt = out/f'attempt_{len(attempts)+1:03d}'; attempt.mkdir()
    provider = None; results = []; status = 'stopped'; error = None
    try:
        provider = JournalProvider(out/'calls', budget=budget, prior=m['prior_charged_or_reserved_usd'])
        check_endpoint(provider, attempt)
        for arm in ARMS:
            for row in m['datasets']:
                verify(out)
                atomic(out/'progress.json', dict(status='loading_dataset', arm=arm, dataset_id=row['dataset_id'],
                    finished_rollouts=len(results), charged_or_reserved_usd=provider.charged))
                actor, fs = minimal.load_actor(row); initial = actor.assigns.copy()
                c = Controller(actor, row['dataset_id'], attempt/arm/row['dataset_id'],
                    provider=provider, arm=arm, sampling_rate=fs)
                result = old.evaluate_rollout(c, row, initial, allow_truncation=True)
                result.update(arm=arm, calls=c.calls, operations=c.operations, controller_output=str(c.out.relative_to(ROOT)))
                write(c.out/'evaluation.json', result); results.append(result)
                atomic(out/'results.json', results)
                atomic(out/'cost_breakdown.json', provider.ledger())
                print('ROLLOUT', arm, row['dataset_id'], result['status'], flush=True)
                del c, actor, initial
        status = 'completed' if all(r['status']=='completed' for r in results) else 'completed_with_failures'
    except BaseException as exc:
        cause = exc.__cause__ or exc
        error = dict(type=type(exc).__name__, cause=type(cause).__name__,
                     message=str(cause) if isinstance(cause, (ValueError, TransportStopped)) else 'See saved journal; no secrets printed')
    finally:
        ledger = provider.ledger() if provider else None
        if provider: provider.close()
        summary = dict(status=status, error=error, completed_rollouts=sum(r['status']=='completed' for r in results),
            stopped_rollouts=sum(r['status']!='completed' for r in results), planned_rollouts=8,
            new_http_attempts=provider.sent if provider else 0, costs=ledger)
        write(attempt/'summary.json', summary); atomic(out/'execution_status.json', summary)
        atomic(out/'progress.json', summary)
        if ledger: atomic(out/'cost_breakdown.json', ledger)
        lines = ['# Qwen3.5-9B rollout comparison', '', f'Status: {status}',
            'Two prompt bundles; same frozen controller/plots. Exposed development channels, not held-out generalization.',
            f'{MAX_OUTPUT} total generated tokens includes thinking; reasoning.max_tokens={THINKING_BUDGET} requested, enforcement unverified. No guaranteed 2000-token answer reserve.',
            'Truncated runs are incomplete, not F1=0 or fabricated KEEP.',
            'No stepwise experiment and no 27B. Official Qwen sampling differs from Astra inference settings.', '',
            '| Arm | Dataset | Status | Calls | One-to-one F1 |', '|---|---|---|---:|---:|']
        for r in results:
            metric = r.get('one_to_one', {}).get('full_output', {}).get('f1', 'N/A')
            lines.append(f"| {r['arm']} | {r['dataset_id']} | {r['status']} | {r['calls']} | {metric} |")
        lines += ['', 'Costs (known usage and unsettled reservations separated):', '```json',
                  json.dumps(ledger, indent=2), '```', '', f'Error: {error}']
        report = '\n'.join(lines)+'\n'
        (attempt/'REPORT.md').write_text(report)
        (out/'REPORT.md').write_text(report)
        lock.close()
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root', type=Path, required=True)
    p.add_argument('--cost-history-from', type=Path)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--budget-usd', type=float, default=25)
    a = p.parse_args(); out = a.output_root.absolute()
    if out.resolve()!=out or out.parent != ROOT/'zero-shot baseline/runs': p.error('Use a new direct child of baseline/runs')
    if a.execute:
        if a.cost_history_from: p.error('Cost history is frozen during preparation')
        result = execute(out, budget=a.budget_usd, resume=a.resume)
        print(json.dumps(result)); return 0 if result['status'].startswith('completed') else 1
    if a.resume or not a.cost_history_from: p.error('Preparation requires --cost-history-from, never --resume')
    m = prepare(out, a.cost_history_from.absolute())
    print(json.dumps(dict(mode='dry-run', api_calls=0, planned_rollouts=8, model=MODEL,
        prior_charged_or_reserved_usd=m['prior_charged_or_reserved_usd'], manifest_sha256=sha(out/'manifest.json'))))
    return 0


if __name__ == '__main__': sys.exit(main())
