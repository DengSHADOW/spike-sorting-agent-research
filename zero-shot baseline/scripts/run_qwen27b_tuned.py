"""Qwen3.5-27B via OpenRouter (DeepInfra FP8): official thinking-mode sampling, 4000-token cap.

Order: minimal zero-shot stepwise (294) -> minimal rollouts (4 channels) -> harness skill v0
rollouts (system_v1 + skill v0, same controller/observation; 4 channels).
Truncation: a stepwise sample is recorded as failed and the next sample runs; a rollout channel
is marked failed (state saved, no terminal score) and the next channel runs. Any other error stops
the campaign; uncertain calls are never resent automatically. Offline preparation unless --execute.
Reuses the frozen OpenRouter runner's transport, replay, budget and scoring code unchanged.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.run import run_openrouter_baseline as ob  # noqa: E402
from scripts.run import run_openrouter_smoke as transport  # noqa: E402
from scripts.run import run_minimal_prompt_baseline as minimal  # noqa: E402
from src.agent import curation_contract as contract  # noqa: E402
from src.pipeline.curation_engineering_v1 import CurationController  # noqa: E402

MODEL, MAX_OUTPUT, BUDGET = 'qwen/qwen3.5-27b', 4000, 25.
MAX_AUTHORIZED_BUDGET = 50.  # User 2026-10-13: Qwen round raised to $50 cumulative
SAMPLING = dict(temperature=1.0, top_p=0.95, top_k=20, min_p=0.0, presence_penalty=1.5, repetition_penalty=1.0)
SAMPLING_SOURCE = 'huggingface.co/Qwen/Qwen3.5-27B, thinking mode, general tasks (checked 2026-10-13)'
VARIANTS = ('minimal', 'skill_v0')
RUNS = ROOT / 'zero-shot baseline/runs'
PARENT = RUNS / 'openrouter_qwen_dual_20261012_8000_retry429_v5'
VERSION = 'qwen27b-tuned-sampling-4000-v1'
read, write, sha, digest = transport.read, transport.write, transport.sha, transport.digest


def tune(request):
    """Add only the declared sampling parameters to an otherwise frozen request."""
    if request['model'] != MODEL or request['max_tokens'] != MAX_OUTPUT or set(SAMPLING) & set(request):
        raise ValueError('Unexpected request before sampling is added')
    return dict(request, **SAMPLING)


def stepwise_request(job):
    return tune(ob.build_stepwise(job, MODEL, MAX_OUTPUT))


def rollout_request(variant, phase, obs, urls):
    if variant == 'minimal':
        old = minimal.build_request(phase, obs, urls)
    elif variant == 'skill_v0':
        old = contract.build_request(phase, obs, urls, skill_version='v0')
    else:
        raise ValueError('Unknown variant')
    return tune(ob.convert(old, MODEL, MAX_OUTPUT))


def code_hashes():
    paths = [Path(__file__), Path(ob.__file__), Path(transport.__file__), Path(minimal.__file__),
             Path(contract.__file__), ROOT / 'src/pipeline/curation_engineering_v1.py',
             ROOT / 'src/agent/curation_observation_v1.py', ROOT / 'scripts/analysis/curation_one_to_one.py',
             ROOT / 'scripts/analysis/collect_curation_stage6.py']
    return {str(p.relative_to(ROOT)): sha(p) for p in paths}


class TunedAsk:
    """OpenRouter ask shared by both variants; mirrors the frozen runner's Controller.ask."""
    VARIANT = None

    def setup(self):
        self.start_cost = self.provider.charged
        write(self.out / 'harness.json', dict(model=MODEL, route=transport.MODELS[MODEL], max_tokens=MAX_OUTPUT,
            sampling=SAMPLING, variant=self.VARIANT, skill='v0' if self.VARIANT == 'skill_v0' else None,
            thinking=True, truncation='channel fails; no action applied; no retry'))

    def ask(self, phase, cid, target=None, *, context=None):
        if self.status in {'stopped', 'completed'}:
            raise minimal.ControllerStop('Finished controller')
        self.status = 'running'
        try:
            key = (context or phase, int(cid), target, self.state_hash())
            if key in self.seen or self.calls >= self.max_calls:
                raise ValueError('Repeated state/call limit')
            if self.hashes() != self.frozen:
                raise ValueError('Prompt/config changed')
            obs, urls, pngs, display = minimal.observe(self.manager, phase, cid, target, sampling_rate=self.fs, view=self.view)
            request = rollout_request(self.VARIANT, phase, obs, urls)
            step = self.out / f'step_{self.calls + 1:05d}'
            step.mkdir()
            for png, meta in zip(pngs, display['images']):
                with (step / (meta['name'] + '.png')).open('xb') as f:
                    f.write(png)
            write(step / 'request.json', dict(phase=phase, context=context, observation=obs, display=display,
                                              request_sha256=digest(request), state_sha256=key[-1]))
            self.seen.add(key)
            self.calls += 1
            decision = self.provider.ask(request, f'{MODEL}/{self.VARIANT}/rollout/{self.dataset_id}/{self.calls}')
            self.charged = self.provider.charged - self.start_cost
            self.log('decision', phase=phase, cid=int(cid), target=target, **decision)
            return decision
        except BaseException as exc:
            self.status = 'stopped'
            self.charged = self.provider.charged - self.start_cost
            self.log('ABSTAIN_stop', phase=phase, cid=int(cid), error_type=type(exc).__name__, state_sha256=self.state_hash())
            raise minimal.ControllerStop('OpenRouter stopped; state preserved') from exc


class MinimalTuned(TunedAsk, minimal.MinimalController):
    VARIANT = 'minimal'

    def __init__(self, manager, dataset_id, out, *, provider, budget_usd, sampling_rate):
        super().__init__(manager, dataset_id, out, provider=provider, budget_usd=budget_usd, sampling_rate=sampling_rate)
        self.setup()

    def hashes(self):
        return minimal.prompt_hashes()


class SkillTuned(TunedAsk, CurationController):
    VARIANT = 'skill_v0'

    def __init__(self, manager, dataset_id, out, *, provider, budget_usd, sampling_rate):
        minimal.require_dataset(dataset_id)
        super().__init__(manager, dataset_id, out, provider=provider, budget_usd=budget_usd, max_calls=10000,
                         sampling_rate=sampling_rate, skill='v0', view='raw_member_v1')
        self.setup()

    def hashes(self):
        return contract.config_hashes()


CONTROLLERS = dict(minimal=MinimalTuned, skill_v0=SkillTuned)


def inherited_cost():
    """Everything charged or reserved so far in this Qwen round ($25 cumulative)."""
    pm = ob.verify(PARENT)
    p = ob.Provider(PARENT / 'calls', budget=BUDGET, prior=pm['prior_charged_or_reserved_usd'],
                    max_output=pm['max_output_tokens'], record_length_failures=True)
    return pm, p.charged


def prepare(out):
    if out.exists():
        raise ValueError('Output directory exists; use a new name')
    pm, prior = inherited_cost()
    jobs = read(transport.SOURCE / 'manifest.json')['stepwise']['jobs']
    requests = []
    for job in jobs:
        r = stepwise_request(job)
        if r['provider']['only'] != [transport.MODELS[MODEL][0]] or not r['provider']['require_parameters']:
            raise ValueError('Route not pinned')
        requests.append(dict(job_id=job['job_id'], sha256=digest(r)))
    out.mkdir()
    manifest = dict(version=VERSION, model=MODEL, route=transport.MODELS[MODEL], max_output_tokens=MAX_OUTPUT,
        sampling=SAMPLING, sampling_source=SAMPLING_SOURCE, thinking=True, budget_usd=BUDGET,
        budget_note='Same $25 cumulative Qwen round; prior includes smokes, all 9B calls and the 10 27B calls at 8000.',
        prior_charged_or_reserved_usd=prior, parent=str(PARENT.relative_to(ROOT)),
        parent_manifest_sha256=sha(PARENT / 'manifest.json'), datasets=pm['datasets'],
        variants=dict(minimal='zero-shot minimal prompt (same prompt files as Opus/Astra minimal)',
                      skill_v0='harness: configs/curation/system_v1.txt + skill_v0, same controller and observation v1'),
        order='27B minimal stepwise; minimal rollouts CH31/CH3/CH20/CH30; skill_v0 rollouts same channels',
        stepwise_requests=requests, prompt_hashes=minimal.prompt_hashes(), contract_hashes=contract.config_hashes(),
        code_hashes=code_hashes(), transport_retry_policy=ob.OVERLOAD_RETRY,
        truncation_policy='4000-token length: stepwise sample failed, continue; rollout channel failed, next channel',
        other_failures='Stop the campaign; uncertain calls are never resent automatically',
        not_reused='Parent 27B calls (8000, provider-default sampling) are cost only, never predictions',
        authorization='User 2026-10-13: run Qwen 27B zero-shot (stepwise + rollout) and harness skill v0 on four '
                      'channels via OpenRouter; tune sampling; truncation beyond 4000 fails the channel')
    write(out / 'manifest.json', manifest)
    return manifest


class Provider(ob.Provider):
    """Frozen provider; only the cumulative cap may exceed its built-in $25 check, never $50."""
    def __init__(self, out, *, budget, **kw):
        if not BUDGET <= budget <= MAX_AUTHORIZED_BUDGET:
            raise ValueError('Budget outside the authorized range')
        super().__init__(out, budget=BUDGET, **kw)
        self.budget = budget


INVALID_SPLIT = 'No last merge to split'  # model chose SPLIT on a single initial subcluster


def amendments(out):
    return [read(p) for p in sorted(out.glob('*_amendment_*.json'), key=lambda p: p.name.rsplit('_', 1)[1])]


def amend_policy(out):
    """Offline: record that a non-executable SPLIT on a leaf fails only that channel."""
    m = read(out / 'manifest.json')
    chain = amendments(out)
    previous = chain[-1]['code_hashes'] if chain else m['code_hashes']
    record = dict(kind='policy', code_hashes=code_hashes(), previous_code_hashes=previous,
        changed_files=sorted(k for k in code_hashes() if previous.get(k) != code_hashes()[k]),
        policy=f'A rollout decision that the controller cannot execute because of "{INVALID_SPLIT}" '
               '(SPLIT on a single initial subcluster) marks that channel stopped_invalid_action, like a '
               'truncation: state restored and saved, no action imputed, no retry, next channel runs. '
               'All other execution failures still stop the campaign.',
        reason='Unforeseen case on 2026-10-13 (27B minimal CH31 step 130, cluster 293, n_overclusters=1); '
               'decided by Claude as the closest extension of the user rule "truncation fails the channel"; '
               'user had said to run without interruption ("放心跑"). Reversible: raw responses are kept.')
    write(out / f'policy_amendment_{len(chain) + 1:03d}.json', record)
    return record


def evaluate(controller, row, initial):
    """Frozen evaluation, plus: a non-executable leaf SPLIT fails only this channel."""
    try:
        return ob.evaluate_rollout(controller, row, initial, allow_truncation=True)
    except minimal.ControllerStop as exc:
        cause = exc.__cause__
        if not (isinstance(cause, ValueError) and str(cause) == INVALID_SPLIT and controller.status == 'stopped'):
            raise
        return dict(dataset_id=row['dataset_id'], status='stopped_invalid_action', final_metrics=None,
                    terminal_evaluation_allowed=False, state_sha256=controller.state_hash(),
                    checkpoint=str(controller.out / 'stopped.npz'),
                    reason='SPLIT on a single initial subcluster; not executable; no action applied, no retry')


def amend_budget(out, budget):
    """Offline: record a user-authorized cap increase and the code that enforces it."""
    m = read(out / 'manifest.json')
    chain = amendments(out)
    current = next((a['budget_usd'] for a in reversed(chain) if 'budget_usd' in a), m['budget_usd'])
    if not current < budget <= MAX_AUTHORIZED_BUDGET:
        raise ValueError('Amendment must raise the cap and stay within the authorized maximum')
    previous = chain[-1]['code_hashes'] if chain else m['code_hashes']
    record = dict(kind='budget', budget_usd=budget, previous_budget_usd=current, code_hashes=code_hashes(),
                  previous_code_hashes=previous, changed_files=sorted(k for k in code_hashes() if previous.get(k) != code_hashes()[k]),
                  authorization='User 2026-10-13: Qwen round budget raised to $50 cumulative ("预算提到50了，放心跑")',
                  scope='Only the cumulative cap changes; model, sampling, output limit, prompts, order and failure policy unchanged')
    write(out / f'budget_amendment_{len(chain) + 1:03d}.json', record)
    return record


def verify(out):
    m = read(out / 'manifest.json')
    expected = m['code_hashes']
    for a in amendments(out):
        if a['previous_code_hashes'] != expected:
            raise ValueError('Broken amendment chain')
        expected = a['code_hashes']
        m['budget_usd'] = a.get('budget_usd', m['budget_usd'])
    if m['version'] != VERSION or expected != code_hashes():
        raise ValueError('Code changed since preparation')
    if m['prompt_hashes'] != minimal.prompt_hashes() or m['contract_hashes'] != contract.config_hashes():
        raise ValueError('Prompt/config changed since preparation')
    return m


def check_sampling_support(provider, attempt):
    r = provider.session.get(transport.BASE + '/models/' + MODEL + '/endpoints', timeout=(15, 30), allow_redirects=False)
    r.raise_for_status()
    e = next(x for x in r.json()['data']['endpoints'] if x['tag'] == transport.MODELS[MODEL][0])
    missing = set(SAMPLING) - set(e['supported_parameters'])
    write(attempt / 'sampling_support.json', dict(endpoint=e['tag'], supported=sorted(e['supported_parameters']),
                                                   missing=sorted(missing)))
    if missing:
        raise ValueError(f'Endpoint lacks sampling parameters: {sorted(missing)}')


def f1(result):
    return result['one_to_one']['full_output']['f1'] if result.get('status') == 'completed' else None


def report(attempt, m, status, error, step_score, results, summary):
    s = step_score['overall']
    lines = ['# Qwen3.5-27B tuned-sampling run', '',
             f'Status: {status}. Output limit {MAX_OUTPUT}; sampling {json.dumps(SAMPLING)}; cumulative cap ${m["budget_usd"]}.',
             'Stepwise and rollout are separate evaluations. Truncations are failures, never actions or retries.', '',
             '## Stepwise (minimal prompt)', '',
             '| Planned | Attempted | Valid | Truncated | Correct | Full accuracy | Valid-only accuracy |',
             '|---:|---:|---:|---:|---:|---:|---:|',
             '| ' + ' | '.join(str(s[k]) for k in ('planned', 'attempted', 'valid', 'failed', 'correct',
                                                    'accuracy', 'accuracy_on_valid')) + ' |', '',
             '## Rollouts (one-to-one F1, IoU >= 0.5, all output; many-to-one in score.json)', '',
             '| Variant | Dataset | Status | One-to-one F1 | No-curation F1 |', '|---|---|---|---:|---:|']
    for variant, rows in results.items():
        for r in rows:
            nc = r['no_curation_one_to_one']['full_output']['f1'] if r.get('status') == 'completed' else None
            lines.append(f'| {variant} | {r["dataset_id"]} | {r["status"]} | {f1(r)} | {nc} |')
    lines += ['', f'Cumulative charged/reserved for the Qwen round: {summary["charged_or_reserved_cumulative"]}.',
              f'Campaign error: {error}.']
    with (attempt / 'REPORT.md').open('x') as f:
        f.write('\n'.join(lines) + '\n')


def execute(out, resume=False):
    m = verify(out)
    lock = (out / 'execution.lock').open('a')
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    attempts = sorted(out.glob('attempt_*'))
    if attempts and not resume:
        raise ValueError('Existing execution: explicit --resume required')
    if not attempts and resume:
        raise ValueError('No execution to resume')
    attempt = out / f'attempt_{len(attempts) + 1:03d}'
    attempt.mkdir()
    provider, status, error = None, 'stopped', None
    predictions, failures, results = [], [], {v: [] for v in VARIANTS}
    try:
        provider = Provider(out / 'calls', budget=m['budget_usd'], prior=m['prior_charged_or_reserved_usd'],
                               max_output=MAX_OUTPUT, record_length_failures=True, retry_overload=True)
        ob.check_endpoints(provider, attempt)
        check_sampling_support(provider, attempt)
        jobs = read(transport.SOURCE / 'manifest.json')['stepwise']['jobs']
        expected = {r['job_id']: r['sha256'] for r in m['stepwise_requests']}
        for job in jobs:
            request = stepwise_request(job)
            if digest(request) != expected[job['job_id']]:
                raise ValueError('Frozen stepwise request changed')
            sample = dict(job_id=job['job_id'], dataset_id=job['dataset_id'], phase=job['phase'])
            try:
                record = dict(sample, **provider.ask(request, f'{MODEL}/stepwise/{job["job_id"]}'))
                predictions.append(record)
            except ob.TruncatedResponse:
                record = dict(sample, failure='length', call=provider.cursor)
                failures.append(record)
            transport.log(attempt / 'stepwise_progress.jsonl', record)
        write(attempt / 'stepwise.json', ob.score_stepwise(predictions, failures))
        for variant in VARIANTS:
            for row in m['datasets']:
                verify(out)
                actor, fs = minimal.load_actor(row)
                initial = actor.assigns.copy()
                c = CONTROLLERS[variant](actor, row['dataset_id'], attempt / variant / row['dataset_id'],
                    provider=provider, budget_usd=max(0., m['budget_usd'] - provider.charged), sampling_rate=fs)
                result = evaluate(c, row, initial)
                write(c.out / 'evaluation.json', result)
                results[variant].append(result)
                print(f'ROLLOUT {variant} {row["dataset_id"]} {result["status"]} f1={f1(result)}', flush=True)
                del c, actor, initial
        status = ('completed_with_failures' if failures or any(r['status'] != 'completed' for rows in results.values()
                                                               for r in rows) else 'completed')
    except BaseException as exc:
        error = dict(type=type(exc).__name__, cause=type(exc.__cause__).__name__ if exc.__cause__ else None,
                     message=str(exc) if isinstance(exc, ValueError) else 'See call response/error; no retry')
    finally:
        if provider:
            provider.close()
        step_score = ob.score_stepwise(predictions, failures)
        summary = dict(status=status, error=error, known_new_cost=provider.known if provider else None,
            charged_or_reserved_cumulative=provider.charged if provider else None,
            new_http_calls_this_attempt=provider.sent if provider else 0,
            stepwise_valid=len(predictions), stepwise_truncated=len(failures),
            rollouts={v: [dict(dataset_id=r['dataset_id'], status=r['status'], one_to_one_f1=f1(r)) for r in rows]
                      for v, rows in results.items()})
        write(attempt / 'summary.json', summary)
        write(attempt / 'cost_breakdown.json', ob.cost_breakdown(out, m['prior_charged_or_reserved_usd']))
        write(attempt / 'score.json', dict(stepwise=step_score, rollouts=results))
        report(attempt, m, status, error, step_score, results, summary)
        lock.close()
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root', required=True, type=Path)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--amend-budget', type=float, help='Offline: raise the cumulative cap (max $50, user-authorized)')
    p.add_argument('--amend-invalid-split-policy', action='store_true', help='Offline: leaf SPLIT fails only the channel')
    a = p.parse_args()
    out = a.output_root.absolute()
    if out.resolve() != out or out.parent != RUNS:
        p.error('Use a direct child of zero-shot baseline/runs')
    if a.resume and not a.execute:
        p.error('--resume requires --execute')
    if a.amend_invalid_split_policy:
        if a.execute:
            p.error('Amend offline first, then execute with --resume')
        print(json.dumps(amend_policy(out)))
        return 0
    if a.amend_budget is not None:
        if a.execute:
            p.error('Amend offline first, then execute with --resume')
        print(json.dumps(amend_budget(out, a.amend_budget)))
        return 0
    if a.execute:
        summary = execute(out, a.resume)
        print(json.dumps(summary))
        return 0 if summary['status'] in ('completed', 'completed_with_failures') else 1
    m = prepare(out)
    print(json.dumps(dict(mode='dry-run', stepwise_requests=len(m['stepwise_requests']), api_calls=0,
                          prior_usd=m['prior_charged_or_reserved_usd'], manifest_sha256=sha(out / 'manifest.json'))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
