"""Exercise the actual HTTP authentication boundary, without cloud credentials."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

spec = importlib.util.spec_from_file_location('private_gateway', Path(__file__).resolve().parents[1] /
    'mockups/curation-review/backend/private_gateway.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)
ORIGIN = 'https://spikeagent-review-20261023.web.app'
KEY = 'test-owner-key-with-enough-entropy-for-offline-tests'


class PrivateGatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.package = self.root / 'approved.gz'
        self.package.write_bytes(b'real-package-fixture-only')
        self.config = dict(origins=[ORIGIN], package_root=str(self.root), packages=[dict(
            dataset_id='approved', file='approved.gz', bytes=self.package.stat().st_size,
            sha256=gateway.file_hash(self.package), source_sha256='a'*64, n_spikes=1)],
            accounts=[dict(id='owner', name='Owner', salt='ab'*16,
                           key_hash=gateway.digest_key(KEY, 'ab'*16), active=True, datasets=['approved'])])
        self.path = self.root / 'config.json'
        self.save_config()
        self.time = 1
        self.app = gateway.PrivateGateway(self.path, clock=lambda: self.time)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), gateway.handler_for(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def save_config(self):
        self.path.write_text(json.dumps(self.config))

    def call(self, path, method='GET', token=None, body=None, origin=ORIGIN, headers=None):
        h = {'Origin': origin} if origin else {}
        if token:
            h['Authorization'] = 'Bearer ' + token
        if body is not None:
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        req = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                      method=method, headers=h)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, dict(response.headers), response.read()

    def login(self):
        code, headers, data = self.call('/api/private/login', 'POST', body={'key': KEY})
        self.assertEqual(code, 200)
        self.assertEqual(headers['Access-Control-Allow-Origin'], ORIGIN)
        return json.loads(data)['token']

    def test_unauthorized_and_nonroutes_never_return_data(self):
        for path in ['/api/private/datasets', '/api/private/package?dataset_id=approved',
                     '/approved.gz', '/api/curation/package?dataset_id=approved', '/../../config.json']:
            code, headers, data = self.call(path)
            self.assertEqual(code, 401)
            self.assertNotIn(self.package.read_bytes(), data)
            self.assertIn('no-store', headers['Cache-Control'])
        self.assertEqual(self.call('/health')[0], 200)
        self.assertNotIn('approved', self.call('/health')[2].decode())

    def test_authenticated_allowlist_integrity_and_cors(self):
        token = self.login()
        code, _, data = self.call('/api/private/datasets', token=token)
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(data)['datasets']), 1)
        self.assertNotIn('file', json.loads(data)['datasets'][0])
        code, headers, data = self.call('/api/private/package?dataset_id=approved', token=token)
        self.assertEqual(code, 200)
        self.assertEqual(data, self.package.read_bytes())
        self.assertEqual(headers['X-Package-SHA256'], gateway.file_hash(self.package))
        self.assertEqual(int(headers['Content-Length']), len(data))
        for query in ['dataset_id=other', 'dataset_id=../../config.json',
                      'dataset_id=approved&dataset_id=other', 'dataset_id=approved&path=config.json']:
            self.assertEqual(self.call('/api/private/package?' + query, token=token)[0], 404)
        self.assertEqual(self.call('/api/private/package?dataset_id=approved', token=token,
                                   origin='https://evil.example')[0], 403)
        self.assertEqual(self.call('/api/private/login', 'POST', body={'key': KEY}, origin=None)[0], 403)

    def test_revocation_expiry_rotation_and_logout(self):
        token = self.login()
        self.time += gateway.SESSION_SECONDS + 1
        self.assertEqual(self.call('/api/private/datasets', token=token)[0], 401)
        token = self.login()
        self.config['accounts'][0]['active'] = False
        self.save_config()
        self.assertEqual(self.call('/api/private/datasets', token=token)[0], 401)
        self.config['accounts'][0]['active'] = True
        self.config['accounts'][0]['key_hash'] = '0'*64
        self.save_config()
        self.assertEqual(self.call('/api/private/datasets', token=token)[0], 401)
        self.config['accounts'][0]['key_hash'] = gateway.digest_key(KEY, 'ab'*16)
        self.save_config()
        token = self.login()
        self.assertEqual(self.call('/api/private/logout', 'POST', token=token)[0], 200)
        self.assertEqual(self.call('/api/private/datasets', token=token)[0], 401)

    def test_throttling_and_changed_package(self):
        token = self.login()
        self.package.write_bytes(b'x'*self.package.stat().st_size)
        self.assertEqual(self.call('/api/private/package?dataset_id=approved', token=token)[0], 503)
        for _ in range(9):
            self.assertEqual(self.call('/api/private/login', 'POST', body={'key': 'wrong'})[0], 401)
        self.assertEqual(self.call('/api/private/login', 'POST', body={'key': KEY})[0], 429)


if __name__ == '__main__':
    unittest.main()
