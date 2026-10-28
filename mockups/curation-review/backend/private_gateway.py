"""Small, read-only gateway for explicitly approved private curation packages.

Independent of the local review API: no MAT paths, uploads, model calls, or
filesystem routes are exposed. TLS terminates at the existing Runpod proxy.
Credentials and package inventory live outside the public Hosting directory.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

ITERATIONS = 210_000
SESSION_SECONDS = 8 * 3600
MAX_LOGIN_BYTES = 2048


def digest_key(key, salt):
    return hashlib.pbkdf2_hmac('sha256', key.encode(), bytes.fromhex(salt), ITERATIONS).hex()


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


class PrivateGateway:
    def __init__(self, config_path, clock=time.monotonic):
        self.config_path = Path(config_path)
        self.clock = clock
        self.lock = threading.Lock()
        self.sessions = {}
        self.attempts = []
        self.transfers = threading.BoundedSemaphore(2)
        config = self.config()
        self.origins = set(config['origins'])
        if not self.origins or any(not s.startswith('https://') for s in self.origins):
            raise ValueError('Explicit HTTPS website origins are required')
        root = Path(config['package_root']).resolve(strict=True)
        self.packages = {}
        for row in config['packages']:
            original = root / row['file']
            path = original.resolve(strict=True)
            if path.parent != root or not path.is_file() or original.is_symlink():
                raise ValueError('Invalid approved package path')
            if path.stat().st_size != row['bytes'] or file_hash(path) != row['sha256']:
                raise ValueError('Approved package integrity mismatch')
            self.packages[row['dataset_id']] = (dict(row), path)
        self.signatures = {k: (p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_size)
                           for k, (_, p) in self.packages.items()}
        if not self.packages or not config.get('accounts'):
            raise ValueError('Configure approved packages and accounts')

    def config(self):
        return json.loads(self.config_path.read_text())

    def login(self, key):
        with self.lock:
            current = self.clock()
            self.attempts = [t for t in self.attempts if current - t < 60]
            if len(self.attempts) >= 10:
                return None, 429
            self.attempts.append(current)
        if not isinstance(key, str) or not 20 <= len(key) <= 200:
            return None, 401
        for account in self.config()['accounts']:
            if account.get('active') is True and hmac.compare_digest(
                    digest_key(key, account['salt']), account['key_hash']):
                token = secrets.token_urlsafe(32)
                with self.lock:
                    self.sessions = {k: v for k, v in self.sessions.items() if v[1] > current}
                    if len(self.sessions) >= 100:
                        return None, 429
                    self.sessions[hashlib.sha256(token.encode()).hexdigest()] = (
                        account['id'], current + SESSION_SECONDS, account['key_hash'])
                return dict(token=token, expires_in=SESSION_SECONDS,
                            name=account.get('name', account['id'])), 200
        return None, 401

    def identity(self, header):
        if not header.startswith('Bearer ') or len(header) > 256:
            return None
        token_hash = hashlib.sha256(header[7:].encode()).hexdigest()
        with self.lock:
            session = self.sessions.get(token_hash)
        if not session or session[1] <= self.clock():
            return None
        # Reload the allowlist on every request: revocation/key rotation is immediate.
        for account in self.config()['accounts']:
            if (account['id'] == session[0] and account.get('active') is True
                    and hmac.compare_digest(account['key_hash'], session[2])):
                return account
        return None


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'SpikePrivateData/1'

        def setup(self):
            super().setup()
            self.connection.settimeout(20)

        def log_message(self, fmt, *args):
            # Never log credentials, Authorization, response bodies, or query strings.
            pass

        def response_headers(self, code, length=0, content_type='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(length))
            self.send_header('Cache-Control', 'no-store, private')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Vary', 'Origin')
            origin = self.headers_in.get('Origin')
            if origin in app.origins:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Access-Control-Expose-Headers', 'X-Package-SHA256, Content-Length')

        def reply(self, data, code=200):
            body = json.dumps(data, separators=(',', ':')).encode()
            self.response_headers(code, len(body))
            self.end_headers()
            self.wfile.write(body)

        def guard_origin(self, required=False):
            self.headers_in = self.headers
            origin = self.headers_in.get('Origin')
            if (origin and origin not in app.origins) or (required and origin not in app.origins):
                self.reply(dict(error='Website origin is not allowed'), 403)
                return False
            return True

        def do_OPTIONS(self):
            if not self.guard_origin(required=True):
                return
            if self.headers_in.get('Access-Control-Request-Method') not in ('GET', 'POST'):
                self.reply(dict(error='Method not allowed'), 405)
                return
            self.response_headers(204)
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
            self.send_header('Access-Control-Max-Age', '600')
            self.end_headers()

        def do_GET(self):
            if not self.guard_origin():
                return
            parts = urlsplit(self.path)
            if parts.path == '/health':
                self.reply(dict(status='ok', authentication_required=True, model_calls_enabled=False))
                return
            account = app.identity(self.headers_in.get('Authorization', ''))
            if account is None:
                self.reply(dict(error='Connect with your private access key', authentication_required=True), 401)
                return
            allowed = set(account.get('datasets', []))
            if parts.path == '/api/private/datasets':
                rows = [{k: v for k, v in row.items() if k != 'file'}
                        for dataset, (row, _) in app.packages.items() if dataset in allowed]
                self.reply(dict(datasets=rows, name=account.get('name', account['id'])))
                return
            query = parse_qs(parts.query)
            dataset = query.get('dataset_id', [''])[0]
            if (parts.path != '/api/private/package' or set(query) != {'dataset_id'}
                    or len(query['dataset_id']) != 1 or dataset not in allowed or dataset not in app.packages):
                self.reply(dict(error='Approved dataset not found'), 404)
                return
            if not app.transfers.acquire(blocking=False):
                self.reply(dict(error='Another data transfer is in progress; retry shortly'), 429)
                return
            try:
                row, path = app.packages[dataset]
                # Approved packages are immutable; detect replacements before streaming.
                stat = path.stat()
                if (stat.st_ino, stat.st_mtime_ns, stat.st_size) != app.signatures[dataset]:
                    self.reply(dict(error='Private package needs revalidation'), 503)
                    return
                self.response_headers(200, row['bytes'], 'application/gzip')
                self.send_header('X-Package-SHA256', row['sha256'])
                self.send_header('Content-Disposition', 'attachment; filename="dataset.working.json.gz"')
                self.end_headers()
                with path.open('rb') as f:
                    for chunk in iter(lambda: f.read(128 * 1024), b''):
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                app.transfers.release()

        def do_POST(self):
            if not self.guard_origin(required=True):
                return
            if self.path == '/api/private/logout':
                token = self.headers_in.get('Authorization', '')
                with app.lock:
                    app.sessions.pop(hashlib.sha256(token[7:].encode()).hexdigest(), None)
                self.reply(dict(disconnected=True))
                return
            if self.path != '/api/private/login':
                self.reply(dict(error='Route not found'), 404)
                return
            try:
                length = int(self.headers_in.get('Content-Length', '0'))
                if not 0 < length <= MAX_LOGIN_BYTES or self.headers_in.get_content_type() != 'application/json':
                    raise ValueError()
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError()
            except (ValueError, TypeError):
                self.reply(dict(error='Invalid login request'), 400)
                return
            result, code = app.login(body.get('key'))
            self.reply(result or dict(error='Too many connection attempts; retry in a minute' if code == 429
                                     else 'Access key is invalid or has been revoked'), code)

        def do_HEAD(self):
            self.headers_in = self.headers
            self.response_headers(405)
            self.end_headers()

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8888)
    args = parser.parse_args()
    app = PrivateGateway(args.config)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(app))
    server.daemon_threads = True
    print('Private data gateway ready; authentication required; model calls disabled', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
