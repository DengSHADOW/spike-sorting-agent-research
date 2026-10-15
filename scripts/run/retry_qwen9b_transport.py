"""Explicit, bounded recovery of an exhausted DeepInfra 429 round.

Default is read-only/offline. --execute records one user-authorized extra round
of four attempts for the trailing request only. Frozen prompts, runner, prior
attempts and manifest are untouched. Existing successes replay without HTTP.
"""
from __future__ import annotations

import argparse
import fcntl
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run import run_qwen9b_rollout_comparison as base

read, write, sha = base.read, base.write, base.sha
ALLOWANCES = {}


def saved_rounds(out):
    limits = {}
    for index, folder in enumerate(sorted((out/'transport_recovery').glob('round_*')), 1):
        if folder.name != f'round_{index:03d}':
            raise ValueError('Noncontiguous recovery rounds')
        m = read(folder/'authorization.json')
        if sha(folder/'runner.py') != m['recovery_script_sha256']:
            raise ValueError('Recovery snapshot changed')
        if m['manifest_sha256'] != sha(out/'manifest.json'):
            raise ValueError('Recovery manifest changed')
        for path, expected in m['previous_file_hashes'].items():
            if sha(out/path) != expected:
                raise ValueError('Prior recovery evidence changed')
        call = m['call']
        if (m['previous_attempts'] != limits.get(call, 4)
                or m['max_attempts'] != m['previous_attempts']+4):
            raise ValueError('Invalid bounded recovery allowance')
        limits[call] = m['max_attempts']
    return limits


def plan(out):
    """Validate the exhausted request and costs without creating a client."""
    manifest = base.verify(out)
    status = read(out/'execution_status.json')
    if status['status'] != 'stopped' or (status.get('error') or {}).get('cause') != 'TransportStopped':
        raise ValueError('Only an exhausted transport stop may open another round')
    limits = saved_rounds(out)
    calls = sorted((out/'calls').iterdir())
    if not calls:
        raise ValueError('No failed request')
    tail = calls[-1]
    attempts = sorted((tail/'attempts').iterdir())
    if (len(attempts) != limits.get(tail.name, 4)
            or (tail/'decision.json').exists() or (tail/'failure.json').exists()):
        raise ValueError('Round not exhausted or request already resolved/truncated')
    for i, a in enumerate(attempts, 1):
        raw, http = read(a/'response.json'), read(a/'http.json')
        meta = (raw.get('error') or {}).get('metadata') or {}
        if (a.name != f'{i:03d}' or http['status'] != 429 or raw.get('choices')
                or meta.get('provider_name') != 'DeepInfra'
                or meta.get('provider_error_code') != 'engine_overloaded'
                or meta.get('limit_source') != 'upstream_provider_shared_pool'):
            raise ValueError('Only confirmed shared-pool 429 may extend this round')
    ledger = base.JournalProvider(out/'calls', budget=25,
        prior=manifest['prior_charged_or_reserved_usd']).ledger()
    if ledger != status['costs']:
        raise ValueError('Stopped ledger changed')
    if ledger['charged_or_reserved_usd']+base.reservation() > 25:
        raise ValueError('Insufficient cumulative budget')
    return dict(call=tail.name, tag=read(tail/'request.json')['tag'],
        previous_attempts=len(attempts), max_attempts=len(attempts)+4,
        cumulative_charged_or_reserved_usd=ledger['charged_or_reserved_usd'],
        manifest_sha256=sha(out/'manifest.json'), recovery_script_sha256=sha(__file__),
        previous_file_hashes={str(f.relative_to(out)):sha(f) for f in tail.rglob('*') if f.is_file()},
        authorization='User explicitly requested another retry round: 继续重试. Same $25 cumulative cap; no new model/data/prompt settings.',
        policy='At most four extra attempts on this request; later decisions retain four-attempt bound. Length is never retried. Prior costs/reservations retained.')


class RetryProvider(base.JournalProvider):
    """Original request journal and validation, with per-request retry allowances."""
    def __init__(self, *args, allowances=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowances = dict(ALLOWANCES if allowances is None else allowances)

    def ask(self, request, tag, arm):
        if (request['model'] != base.MODEL or request['max_tokens'] != base.MAX_OUTPUT
                or request.get('reasoning') != base.reasoning_config()
                or any(request.get(k) != v for k, v in base.SAMPLING.items()) or arm not in base.ARMS):
            raise ValueError('Unapproved inference settings')
        self.cursor += 1
        folder = self.out/f'{self.cursor:06d}'
        meta = dict(tag=tag, arm=arm, request_sha256=base.digest(request), request=base.transport.redact_images(request))
        if folder.exists():
            if read(folder/'request.json') != meta:
                raise ValueError('Replay divergence')
        else:
            folder.mkdir(); write(folder/'request.json', meta); (folder/'attempts').mkdir()
        limit = self.allowances.get(folder.name, 4)
        tries = sorted((folder/'attempts').iterdir())
        if (limit < 4 or limit % 4 or len(tries) > limit
                or any(p.name != f'{i:03d}' for i, p in enumerate(tries, 1))):
            raise ValueError('Invalid retry sequence/allowance')
        for i, a in enumerate(tries):
            decision = self.outcome(folder, a, request)
            if decision is not None:
                if i != len(tries)-1:
                    raise ValueError('Attempt after success')
                return decision
        for number in range(len(tries)+1, limit+1):
            in_round = (number-1) % 4
            if in_round:
                delay = base.DELAYS[in_round-1]
                previous = folder/'attempts'/f'{number-1:03d}'
                if (previous/'http.json').exists():
                    ra = read(previous/'http.json').get('retry_after')
                    if ra is not None:
                        try: value = float(ra)
                        except (TypeError, ValueError): raise base.TransportStopped('Invalid Retry-After') from None
                        if not math.isfinite(value) or value > 300:
                            raise base.TransportStopped('Retry-After above bounded recovery window')
                        delay = max(delay, value)
                print(f'RECOVERY {tag}: attempt {number}/{limit} after {delay:g}s', flush=True)
                self.sleeper(delay)
            self.charged = self.ledger()['charged_or_reserved_usd']
            if self.charged+self.held > self.budget:
                raise ValueError('Insufficient cumulative budget')
            a = folder/'attempts'/f'{number:03d}'; a.mkdir()
            write(a/'reserved.json', dict(usd=self.held, request_sha256=meta['request_sha256']))
            self.charged += self.held
            base.atomic(self.out.parent/'progress.json', dict(status='request_in_flight', tag=tag,
                attempt=number, decision_index=self.cursor, charged_or_reserved_usd=self.charged))
            if self.session is None:
                self.session = base.transport.make_session()
            import requests
            try:
                self.sent += 1; start = time.monotonic()
                response = self.session.post(base.transport.BASE+'/chat/completions', json=request,
                    timeout=(15,600), allow_redirects=False)
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
        raise base.TransportStopped('Authorized bounded round exhausted; all costs/reservations preserved')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root', required=True, type=Path)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--resume', action='store_true', help='Resume the last authorized round, never allocate more attempts')
    a = p.parse_args(); out = a.output_root.absolute()
    if out.resolve() != out or out.parent != ROOT/'zero-shot baseline/runs':
        p.error('Expected existing direct child of baseline/runs')
    if a.resume and not a.execute:
        p.error('--resume requires --execute')
    # Separate lock serializes recovery invocations; parent lock detects an active runner.
    with (out/'execution.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = None if a.resume else plan(out)
        if not a.execute:
            print(base.json.dumps(dict(mode='dry-run', api_calls=0, recovery=record))); return 0
        recovery = out/'transport_recovery'; recovery.mkdir(exist_ok=True)
        with (recovery/'execution.lock').open('a') as recovery_lock:
            fcntl.flock(recovery_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not a.resume:
                folder = recovery/f'round_{len(list(recovery.glob("round_*")))+1:03d}'
                folder.mkdir(exist_ok=False)
                (folder/'runner.py').write_bytes(Path(__file__).read_bytes())
                write(folder/'authorization.json', record)
            rounds = sorted(recovery.glob('round_*'))
            if not rounds or read(rounds[-1]/'authorization.json')['recovery_script_sha256'] != sha(__file__):
                raise ValueError('Recovery implementation changed or authorization missing')
            ALLOWANCES.update(saved_rounds(out))
            # base.execute takes the same parent lock itself, while this recovery lock remains held.
            fcntl.flock(lock, fcntl.LOCK_UN)
            original = base.JournalProvider
            base.JournalProvider = RetryProvider
            try:
                result = base.execute(out, budget=25, resume=True)
            finally:
                base.JournalProvider = original
            print(base.json.dumps(result))
            return 0 if result['status'].startswith('completed') else 1


if __name__ == '__main__':
    sys.exit(main())
