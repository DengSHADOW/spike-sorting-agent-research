"""Explicit, optional suggestions from operator-enabled local models only.

All dataset loading and observation construction stays inside this backend. HTTP
responses contain job progress and decisions, never internal paths or raw state.
"""
from __future__ import annotations
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import threading
import sys
import time
import urllib.request
from urllib.parse import urlsplit

from server_uploads import atomic_json

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:sys.path.insert(0, str(ROOT))
SLOTS = {'Agent_1': 'qwen27b', 'Agent_2': 'gemma4'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ServerAgents:
    def __init__(self, service):
        self.service = service
        self.root = service.storage/'agent_jobs'
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.config_path = service.storage/'local-agents.json'
        if not self.config_path.exists():
            atomic_json(self.config_path, {'enabled': False, 'max_calls': 32,
                        'agents': {slot: {'base_url': 'http://127.0.0.1:8000'} for slot in SLOTS}})
        self.slot = threading.BoundedSemaphore(1)
        for path in self.root.glob('*/job.json'):
            job = json.loads(path.read_text())
            if job['status'] in ('queued', 'running'):
                job.update(status='failed', message='Server restarted before this Agent job finished.')
                atomic_json(path, job)

    def transport(self, url, timeout):
        parts = urlsplit(url)
        if parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not parts.port or parts.path not in ('', '/') or parts.query or parts.username:
            raise ValueError('Only operator-configured loopback inference is enabled')
        opener = urllib.request.build_opener(NoRedirect())
        def send(method, path, body=None):
            request = urllib.request.Request(url.rstrip('/')+path, method=method,
                         data=None if body is None else json.dumps(body).encode(),
                         headers={'Content-Type': 'application/json'})
            with opener.open(request, timeout=timeout) as response:
                return json.load(response)
        return send

    def settings(self):
        return json.loads(self.config_path.read_text())

    def options(self):
        config = self.settings()
        profiles = json.loads((ROOT/'configs/controller_v2/models.json').read_text())['models']
        rows = []
        for slot, key in SLOTS.items():
            available, reason = False, 'Local Agent inference is not enabled on this Runpod.'
            if config.get('enabled') is True:
                try:
                    send = self.transport(config['agents'][slot]['base_url'], 1)
                    names = [r['id'] for r in send('GET', '/v1/models')['data']]
                    available = names == [profiles[key]['served_name']]
                    reason = '' if available else 'This Agent is not currently served on Runpod.'
                except Exception:
                    reason = 'This Agent is offline. Manual curation is available.'
            rows.append({'agent_id': slot, 'label': slot.replace('_', ' '), 'available': available,
                         'reason': reason, 'max_calls': min(128, max(1, int(config.get('max_calls', 32))))})
        return {'agents': rows, 'auto_run': False, 'paid_providers_enabled': False}

    def read_job(self, owner, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9-]{36}', identifier):
            raise ValueError('Agent job not found')
        path = self.root/identifier/'job.json'
        if not path.is_file():raise ValueError('Agent job not found')
        job = json.loads(path.read_text())
        if job['owner'] != owner:raise ValueError('Agent job not found')
        return job

    @staticmethod
    def summary(job):
        return {k: job.get(k) for k in ('job_id', 'workspace_id', 'agent_id', 'status', 'completed', 'total', 'message')}

    def start(self, owner, body):
        if set(body) != {'workspace_id', 'version', 'source', 'target', 'agent_id', 'scope', 'confirm', 'request_id'} or body['confirm'] is not True or body['scope'] not in ('current', 'all'):
            raise ValueError('Choose an Agent and explicitly start this suggestion job')
        identifier = body['request_id']
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9-]{36}', identifier):raise ValueError('Invalid job ID')
        if (self.root/identifier/'job.json').exists():return self.summary(self.read_job(owner, identifier))
        option = next((r for r in self.options()['agents'] if r['agent_id'] == body['agent_id']), None)
        if not option or not option['available']:raise ValueError(option['reason'] if option else 'Choose Agent 1 or Agent 2')
        view = self.service.rpc({'op': 'view', 'owner': owner, **{k: body[k] for k in ('workspace_id', 'version', 'source', 'target')}})
        if view['state']['version'] != body['version']:raise ValueError('Workspace changed. Reopen it before running the Agent.')
        tasks = ([{'source': u['id'], 'target': None, 'split': u['split']} for u in view['units']]
                 if body['scope'] == 'all' else [{'source': view['selected']['source'], 'target': view['selected']['target'],
                     'split': next(u['split'] for u in view['units'] if u['id'] == view['selected']['source'])}])
        if not tasks:raise ValueError('No active clusters')
        if not self.slot.acquire(blocking=False):raise ValueError('An Agent job is already running. Try later.')
        folder = self.root/identifier;folder.mkdir(mode=0o700)
        job = {'job_id': identifier, 'owner': owner, 'workspace_id': body['workspace_id'], 'version': body['version'],
               'state_sha256': view['state_sha256'], 'agent_id': body['agent_id'], 'status': 'queued',
               'completed': 0, 'total': len(tasks), 'message': 'Queued; no sorting changes will be applied.'}
        atomic_json(folder/'job.json', job)
        threading.Thread(target=self.run, args=(folder, job, tasks, option['max_calls']), daemon=True).start()
        return self.summary(job)

    def private_manager(self, job):
        # This helper is not an API operation. Nothing it reads is returned to HTTP.
        import numpy as np
        from src.cluster.manager import ClusterManager
        with self.service.rpc_lock:
            state = json.loads((self.service.storage/(job['workspace_id']+'.json')).read_text())
            if state['reviewer'] != job['owner'] or state['version'] != job['version']:raise ValueError('Workspace changed during Agent preparation')
            rows = self.service.rows + [r for r in json.loads(self.service.uploads.registry.read_text()) if r['owner'] == job['owner']]
            row = next(r for r in rows if r['dataset_id'] == state['dataset_id'])
            packed = Path(row['path']).read_bytes()
            if hashlib.sha256(packed).hexdigest() != row['sha256']:raise ValueError('Input integrity check failed')
            data = json.loads(gzip.decompress(packed))
        def decode(key):
            a = data['arrays'][key]
            return np.frombuffer(base64.b64decode(a['data']), dtype=a['dtype'])
        index = decode('atom_index');assigns = np.asarray(state['labels'], dtype=np.int32)[index].copy()
        for r, value in state.get('row_labels', {}).items():assigns[int(r)] = value['cluster']
        over = np.asarray([a['overcluster'] for a in data['atoms']], dtype=np.int32)[index]
        waves = decode('waveforms').reshape(data['n_spikes'], data['n_samples'])
        return ClusterManager(assigns, over, np.asarray(state['tree']).reshape(4, -1).copy(), decode('times'), waves), data['sampling_rate_hz']

    def run(self, folder, job, tasks, limit):
        try:
            from src.agent.curation_decider_v2 import ModelDecider
            from src.agent.vllm_provider import VllmProvider
            settings = self.settings()
            profile = json.loads((ROOT/'configs/controller_v2/models.json').read_text())['models'][SLOTS[job['agent_id']]]
            provider = VllmProvider(profile, folder/'requests.jsonl', timeout=180,
                        transport=self.transport(settings['agents'][job['agent_id']]['base_url'], 180))
            provider.check()  # GET only; inference begins only after the explicit start request.
            manager, fs = self.private_manager(job)
            decider = ModelDecider(provider)
            started = time.monotonic()
            for i, task in enumerate(tasks):
                if i >= limit or time.monotonic() - started >= 1800:
                    job.update(status='partial', message='Job limit reached. Remaining clusters have no new suggestion.');break
                job.update(status='running', message='Inspecting cluster '+str(task['source']))
                atomic_json(folder/'job.json', job)
                target = task['target'];kind = 'single' if target is None else 'pair'
                allowed = (['KEEP']+(['SPLIT'] if task['split'] else [])+['DISCARD']) if kind == 'single' else ['MERGE', 'NOT_MERGE']
                case = folder/str(i);case.mkdir(mode=0o700)
                decision = decider.decide(manager, kind, task['source'], target, allowed, folder=case, sampling_rate=fs)
                with self.service.rpc_lock:
                    path = self.service.storage/(job['workspace_id']+'.json')
                    state = json.loads(path.read_text())
                    if state['reviewer'] != job['owner'] or state['version'] != job['version'] or state['state_sha256'] != job['state_sha256']:
                        raise ValueError('Workspace changed; the Agent result was not attached to the new state')
                    result = {'agent_id': job['agent_id'], 'source': task['source'], 'target': target,
                              'phase': 'phase1' if target is None else 'phase2', 'state_sha256': job['state_sha256'],
                              'action': decision['action'], 'rationale': decision['rationale'],
                              'origin': 'server-current-state-request', 'job_id': job['job_id']}
                    state['agent_suggestions'] = state.get('agent_suggestions', [])+[result]
                    atomic_json(path, state)
                job['completed'] += 1
                atomic_json(folder/'job.json', job)
            else:job.update(status='complete', message='Suggestions saved. Your grouping is unchanged.')
        except Exception:
            job.update(status='failed', message='Agent could not finish. Check its local service and reopen the current grouping; no sorting changes were applied.')
        finally:
            atomic_json(folder/'job.json', job)
            self.slot.release()
