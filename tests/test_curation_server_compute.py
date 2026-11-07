"""Server executor/privacy contract tests. Tiny deterministic fixture, no model calls."""
import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import uuid
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import numpy as np

BACKEND = Path(__file__).resolve().parents[1] / 'mockups/curation-review/backend'
sys.path.insert(0, str(BACKEND))
from server_curation import ServerCuration, CurationError, PUBLIC_CHANNELS
from working_data import state_hash
from private_gateway import handler_for


def vector(a, dtype):
    a = np.asarray(a, dtype=dtype)
    raw = a.tobytes()
    return {'dtype': dtype, 'length': a.size, 'data': base64.b64encode(raw).decode(),
            'sha256': hashlib.sha256(raw).hexdigest(), 'shape': list(a.shape)}


class ComputeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        # Deliberately simple fixture: one tree split plus an unsplittable leaf.
        waves = np.array([[i, -i - 1, i / 3] for i in range(12)], dtype='<f4')
        atom_index = np.repeat([0, 1, 2], 4)
        tree = [[1], [2], [1], [0]]
        atoms = []
        for k in range(3):
            w = waves[atom_index == k]
            atoms.append({'initial_cluster': [1, 1, 3][k], 'overcluster': k + 1, 'n': 4,
                          'sum': w.astype(float).sum(axis=0).tolist(),
                          'sumsq': (w.astype(float) ** 2).sum(axis=0).tolist(), 'traces': []})
        cls.packages = {}
        for dataset in sorted(PUBLIC_CHANNELS):
            data = {'schema': 'spikeagent-working-dataset-v1', 'dataset_id': dataset,
                    'source_sha256': hashlib.sha256(dataset.encode()).hexdigest(),
                    'n_spikes': 12, 'n_samples': 3, 'sampling_rate_hz': 30000,
                    'atoms': atoms, 'tree': tree,
                    'arrays': {'times': vector(np.arange(12) * .001, '<f8'),
                               'amplitudes': vector(np.ptp(waves, axis=1), '<f8'),
                               'atom_index': vector(atom_index, '<i4'), 'waveforms': vector(waves, '<f4')},
                    'initial_state_sha256': state_hash(np.repeat([1, 1, 3], 4), np.array(tree)),
                    'references': [], 'agent_suggestions': []}
            packed = gzip.compress(json.dumps(data).encode())
            target = root / (dataset + '.gz')
            target.write_bytes(packed)
            cls.packages[dataset] = ({'dataset_id': dataset, 'bytes': len(packed),
                                      'sha256': hashlib.sha256(packed).hexdigest(), 'n_spikes': 12}, target)
        cls.service = ServerCuration(cls.packages, root / 'private')

    @classmethod
    def tearDownClass(cls):
        cls.service.close()
        cls.temp.cleanup()

    def setUp(self):
        self.token = self.service.request('session', {}, '')[0]['token']
        self.auth = 'Bearer ' + self.token
        self.dataset = sorted(PUBLIC_CHANNELS)[0]
        self.view = self.call('open', {'dataset_id': self.dataset, 'source': 1})
        self.params = {'workspace_id': self.view['state']['workspace_id'], 'version': 0, 'source': 1, 'target': None}

    def call(self, op, body=None, auth=None):
        return self.service.request(op, body or {}, auth or self.auth)[0]

    def assert_private(self, value):
        forbidden = {'arrays', 'waveforms', 'times', 'amplitudes', 'assigns', 'labels', 'row_labels',
                     'manual_rows', 'atom_index', 'traces', 'scores', 'tree', 'wave_path', 'labels_path'}
        if isinstance(value, dict):
            self.assertFalse(forbidden.intersection(value), forbidden.intersection(value))
            for v in value.values():
                self.assert_private(v)
        elif isinstance(value, list):
            for v in value:
                self.assert_private(v)

    def test_split_requires_seen_preview_and_confirms_exactly_then_undo(self):
        before = self.view['state_sha256']
        preview = self.call('preview', {**self.params, 'action': 'SPLIT'})
        self.assertEqual([g['n'] for g in preview['groups']], [4, 4])
        confirm = {**self.params, 'preview_id': preview['preview']['id'], 'request_id': str(uuid.uuid4()),
                   'choice': {'action': 'SPLIT', 'reason': 'Two populations'}}
        with self.assertRaisesRegex(CurationError, 'Inspect'):
            self.call('confirm', confirm)
        png = self.call('figure', {**self.params, 'preview_id': preview['preview']['id'], 'figure': 'waves'})
        self.assertTrue(png.startswith(b'\x89PNG'))
        applied = self.call('confirm', confirm)
        self.assertEqual(applied['state']['version'], 1)
        self.assertTrue(all(next(u for u in applied['units'] if u['id'] == cid)['operation']['choice']['action'] == 'SPLIT' for cid in [1, 2]))
        self.assertEqual(self.call('confirm', confirm)['state']['version'], 1)  # Retransmit is idempotent.
        undo = self.call('undo', {**self.params, 'version': 1, 'request_id': str(uuid.uuid4())})
        self.assertEqual(undo['state_sha256'], before)
        for v in [preview, applied, undo, self.call('history'), self.call('export', {**self.params, 'version': 2})]:
            self.assert_private(v)

    def test_merge_preview_keeps_base_readonly_and_sessions_independent(self):
        raw_hashes = {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, (_, p) in self.packages.items()}
        pair = {**self.params, 'target': 3}
        preview = self.call('preview', {**pair, 'action': 'MERGE'})
        self.assertEqual(preview['groups'][0]['n'], 12)
        self.assertEqual(self.call('view', self.params)['state_sha256'], self.view['state_sha256'])
        self.call('figure', {**pair, 'preview_id': preview['preview']['id'], 'figure': 'waves'})
        applied = self.call('confirm', {**pair, 'preview_id': preview['preview']['id'], 'request_id': str(uuid.uuid4()), 'choice': {'action': 'MERGE', 'reason': 'Same unit'}})
        self.assertEqual(len(applied['units']), 1)
        other = 'Bearer ' + self.service.request('session', {}, '')[0]['token']
        initial = self.call('open', {'dataset_id': self.dataset}, other)
        self.assertEqual(initial['state_sha256'], self.view['state_sha256'])
        with self.assertRaisesRegex(CurationError, 'not found'):
            self.call('view', self.params, other)
        self.assertEqual(raw_hashes, {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, (_, p) in self.packages.items()})

    def test_no_raw_routes_no_arbitrary_paths_and_no_ch17(self):
        self.assertEqual(len(self.call('datasets')['datasets']), 4)
        for op in ['package', 'download', 'assigns', 'upload', 'render-spec']:
            with self.assertRaises(CurationError):
                self.call(op)
        for body in [{'dataset_id': 'cM2-e004_011-015_CH17'}, {'dataset_id': '../../etc/passwd'}]:
            with self.assertRaises(CurationError):
                self.call('open', body)
        with self.assertRaises(CurationError):
            self.call('preview', {**self.params, 'action': 'SPLIT', 'manual_rows': [0]})
        with self.assertRaises(CurationError):
            self.call('figure', {**self.params, 'figure': 'waves', 'wave_path': '/etc/passwd'})
        with self.assertRaises(CurationError):
            self.service.request('view', self.params, '')
        self.assert_private(self.view)

    def test_stale_version_rejected_and_leaf_manual_split_computed_on_server(self):
        keep = self.call('confirm', {**self.params, 'request_id': str(uuid.uuid4()), 'choice': {'action': 'KEEP'}})
        with self.assertRaisesRegex(CurationError, 'another tab'):
            self.call('preview', {**self.params, 'action': 'SPLIT'})
        leaf = {**self.params, 'version': 1, 'source': 3}
        self.assertTrue(self.call('preview', {**leaf, 'action': 'SPLIT'})['manual_split_required'])
        # PCA is drawn from real fixture rows; the client supplies a polygon, never row IDs.
        polygon = [[.45, 0], [1, 0], [1, 1], [.45, 1]]
        preview = self.call('pca-selection', {**leaf, 'axes': 1, 'polygon': polygon})
        self.assertEqual(sum(g['n'] for g in preview['groups']), 4)
        self.assertGreater(preview['preview']['selection_count'], 0)
        self.assertLess(preview['preview']['selection_count'], 4)
        self.assert_private(preview)

    def test_restart_restores_auth_and_saved_workspace(self):
        applied = self.call('confirm', {**self.params, 'request_id': str(uuid.uuid4()), 'choice': {'action': 'KEEP'}})
        self.service.close()
        self.service.worker = None
        restored = self.call('view', self.params)
        self.assertEqual(restored['state']['version'], 1)
        self.assertEqual(restored['state_sha256'], applied['state_sha256'])

    def test_preview_survives_worker_restart_without_applying(self):
        preview = self.call('preview', {**self.params, 'action': 'SPLIT'})
        self.service.close()
        self.service.worker = None
        restored = self.call('view', {**self.params, 'preview_id': preview['preview']['id']})
        self.assertEqual(restored['state']['version'], 0)
        self.assertEqual(restored['state_sha256'], self.view['state_sha256'])
        self.assertEqual(restored['preview']['id'], preview['preview']['id'])
        self.assert_private(restored)


    def revision_split(self):
        preview = self.call('preview', {**self.params, 'action': 'SPLIT'})
        self.call('figure', {**self.params, 'preview_id': preview['preview']['id'], 'figure': 'waves'})
        return self.call('confirm', {**self.params, 'preview_id': preview['preview']['id'],
                                    'request_id': str(uuid.uuid4()), 'choice': {'action': 'SPLIT', 'reason': 'Two fixture populations'}})

    def test_grouping_revision_preview_cancel_commit_and_independent_reviews(self):
        split = self.revision_split()
        event_id = split['units'][0]['operation']['id']
        kept = self.call('confirm', {**self.params, 'version': 1, 'source': 2,
                                    'request_id': str(uuid.uuid4()), 'choice': {'action': 'KEEP'}})
        kept = self.call('confirm', {**self.params, 'version': 2, 'source': 3,
                                    'request_id': str(uuid.uuid4()), 'choice': {'action': 'KEEP'}})
        current = {**self.params, 'version': 3, 'source': 2}
        plan = self.call('revision-plan', {**current, 'event_id': event_id})
        self.assertEqual([e['action'] for e in plan['removed']], ['SPLIT', 'KEEP'])
        self.assertEqual([e['source'] for e in plan['preserved']], [3])
        draft = self.call('revision-start', {**current, 'event_id': event_id})
        revision = {**current, 'source': 1, 'revision_id': draft['revision']['id']}
        self.assertEqual([(u['id'], u['n']) for u in draft['units']], [(1, 8), (3, 4)])
        self.assertEqual(self.call('view', current)['state_sha256'], split['state_sha256'])
        self.assertEqual(self.call('view', current)['state']['version'], 3)
        self.assertTrue(self.call('figure', {**revision, 'figure': 'thumbnail', 'cluster_id': 1}).startswith(b'\x89PNG'))
        canceled = self.call('revision-cancel', revision)
        self.assertEqual(canceled['state']['version'], 3)
        self.assertEqual(canceled['state_sha256'], split['state_sha256'])
        draft = self.call('revision-start', {**current, 'event_id': event_id})
        revision['revision_id'] = draft['revision']['id']
        body = {**revision, 'request_id': str(uuid.uuid4()), 'choice': {'action': 'KEEP', 'reason': 'Replace original split'}}
        applied = self.call('confirm', body)
        self.assertEqual([u['id'] for u in applied['units']], [1, 3])
        self.assertEqual(applied['state_sha256'], self.view['state_sha256'])
        self.assertTrue(all(u['operation'] is None for u in applied['units']))
        self.assertTrue(all(u['reviewed'] for u in applied['units']))
        self.assertEqual(self.call('confirm', body)['state']['version'], applied['state']['version'])
        for value in [plan, draft, canceled, applied]:
            self.assert_private(value)

    def test_grouping_revision_stale_tab_cannot_overwrite_new_saved_work(self):
        split = self.revision_split()
        event_id = split['units'][0]['operation']['id']
        draft = self.call('revision-start', {**self.params, 'version': 1, 'event_id': event_id})
        newer = self.call('confirm', {**self.params, 'version': 1, 'source': 3,
                                     'request_id': str(uuid.uuid4()), 'choice': {'action': 'KEEP'}})
        with self.assertRaisesRegex(CurationError, 'changed'):
            self.call('confirm', {**self.params, 'version': 1, 'revision_id': draft['revision']['id'],
                                  'request_id': str(uuid.uuid4()), 'choice': {'action': 'KEEP'}})
        self.assertEqual(self.call('view', {**self.params, 'version': 2})['state']['version'], newer['state']['version'])
        self.assertEqual(len(newer['units']), 3)

    def test_merge_revision_restores_both_inputs_and_survives_worker_restart(self):
        pair = {**self.params, 'target': 3}
        preview = self.call('preview', {**pair, 'action': 'MERGE'})
        self.call('figure', {**pair, 'preview_id': preview['preview']['id'], 'figure': 'waves'})
        merged = self.call('confirm', {**pair, 'preview_id': preview['preview']['id'], 'request_id': str(uuid.uuid4()), 'choice': {'action': 'MERGE', 'reason': 'fixture merge'}})
        event_id = merged['units'][0]['operation']['id']
        draft = self.call('revision-start', {**pair, 'source': 3, 'target': None, 'version': 1, 'event_id': event_id})
        args = {**pair, 'version': 1, 'revision_id': draft['revision']['id']}
        self.service.worker.kill(); self.service.worker.wait()
        restored = self.call('view', args)
        self.assertEqual([u['id'] for u in restored['units']], [1, 3])
        applied = self.call('confirm', {**args, 'request_id': str(uuid.uuid4()), 'choice': {'action': 'NOT_MERGE', 'reason': 'Keep originals separate'}})
        self.assertEqual([u['id'] for u in applied['units']], [1, 3])
        self.assertEqual(applied['state_sha256'], self.view['state_sha256'])
        self.assertTrue(all(u['operation'] is None for u in applied['units']))
        self.assert_private(restored); self.assert_private(applied)


class HttpComputeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ComputeTests.setUpClass.__func__(cls)
        app = SimpleNamespace(curation=cls.service, origins={'https://review.example'}, identity=lambda header: None)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(app))
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        ComputeTests.tearDownClass.__func__(cls)

    def post(self, name, body=None, origin='https://review.example', token=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        if token:
            headers['Authorization'] = 'Bearer ' + token
        return urlopen(Request(self.base+'/api/compute/'+name, data=json.dumps(body or {}).encode(), headers=headers), timeout=10)

    def test_origin_authentication_and_no_numeric_download_endpoints(self):
        for origin in [None, 'https://wrong.example']:
            with self.assertRaises(HTTPError) as caught:
                self.post('session', origin=origin)
            self.assertEqual(caught.exception.code, 403)
        with self.post('session') as response:
            token = json.load(response)['token']
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'https://review.example')
            self.assertIn('no-store', response.headers['Cache-Control'])
        with self.assertRaises(HTTPError) as caught:
            self.post('datasets')
        self.assertEqual(caught.exception.code, 401)
        with self.post('datasets', token=token) as response:
            self.assertEqual(len(json.load(response)['datasets']), 4)
        for path, method in [('/api/compute/package', 'POST'), ('/api/compute/package', 'GET'), ('/api/private/package', 'GET')]:
            headers = {'Origin': 'https://review.example', 'Content-Type': 'application/json', 'Authorization': 'Bearer '+token}
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(self.base+path, data=b'{}' if method == 'POST' else None, method=method, headers=headers))
            self.assertIn(caught.exception.code, [401, 404, 405])

    def test_chunked_http_upload_validation_and_private_open(self):
        import time
        with self.post('session') as response:token=json.load(response)['token']
        raw=json.dumps({'sampling_rate_hz':30000,'times_seconds':[0,.01,.02,.03],
                        'spike_clusters':[1,1,2,2],'waveforms':[[1,-1,0],[2,-2,0],[3,-3,0],[4,-4,0]]}).encode()
        with self.post('upload-create',{'recording_id':'HTTP test','files':[{'name':'spikes.json','size':len(raw)}]},token=token) as response:
            upload=json.load(response)
        headers={'Origin':'https://review.example','Content-Type':'application/octet-stream','Authorization':'Bearer '+token}
        offset=0
        for block in [raw[:len(raw)//2],raw[len(raw)//2:]]:
            url=self.base+'/api/compute/upload-chunk?upload_id='+upload['upload_id']+'&file=0&offset='+str(offset)
            with urlopen(Request(url,data=block,headers=headers),timeout=10) as response:
                value=json.load(response);self.assertEqual(value['received'],offset+len(block))
            offset+=len(block)
        with self.post('upload-finish',{'upload_id':upload['upload_id']},token=token) as response:result=json.load(response)
        for _ in range(100):
            if result['status'] in ('complete','failed'):break
            time.sleep(.05)
            with self.post('upload-status',{'upload_id':upload['upload_id']},token=token) as response:result=json.load(response)
        self.assertEqual(result['status'],'complete',result)
        with self.post('open',{'dataset_id':result['result']['dataset_id']},token=token) as response:
            view=json.load(response);self.assertEqual([u['n'] for u in view['units']],[2,2])


if __name__ == '__main__':
    unittest.main()
