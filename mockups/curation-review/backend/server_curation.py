"""Server-side human curation. Only raster images, aggregate metrics and logs leave here.

Original MATs and full working packages are never served by this API. Node runs
the same tested executor as the browser edition, inside private persisted sessions.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import secrets
import select
import shutil
import sqlite3
import subprocess
import threading
import time

PUBLIC_CHANNELS = {
    'cM2-e004_004-006_CH3', 'cM2-e004_004-006_CH31',
    'cM2-e004_011-015_CH20', 'cM2-e008_021-028_CH30',
}
SESSION_SECONDS = 30 * 24 * 3600
MAX_BODY = 64 * 1024


class CurationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class ServerCuration:
    def __init__(self, packages, storage, node=None):
        self.storage = Path(storage).resolve()
        self.storage.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.storage.chmod(0o700)
        rows = []
        for dataset in sorted(PUBLIC_CHANNELS):
            if dataset not in packages:
                raise ValueError('Approved server channel missing')
            row, path = packages[dataset]
            rows.append({**row, 'path': str(path)})
        self.rows = rows
        config = self.storage / 'worker-config.json'
        config.write_text(json.dumps({'packages': rows, 'storage': str(self.storage)}))
        config.chmod(0o600)
        self.config = config
        self.node = node or shutil.which('node')
        if not self.node:
            raise ValueError('Node is required for the curation executor')
        from server_uploads import ServerUploads
        self.uploads = ServerUploads(self)
        config.write_text(json.dumps({'packages': rows, 'storage': str(self.storage),
                                      'upload_registry': str(self.uploads.registry)}))
        self.worker = None
        self.rpc_lock = threading.Lock()
        self.render_lock = threading.Lock()
        self.compute_slots = threading.BoundedSemaphore(2)
        self.images = OrderedDict()
        self.image_bytes = 0
        self.pca_models = OrderedDict()
        self.auth_lock = threading.Lock()
        self.created = []
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL)')
        (self.storage / 'sessions.sqlite3').chmod(0o600)
        from server_agents import ServerAgents
        self.agents = ServerAgents(self)

    def db(self):
        return sqlite3.connect(self.storage / 'sessions.sqlite3', timeout=10)

    def session(self):
        with self.auth_lock:
            now = time.time()
            self.created = [v for v in self.created if now - v < 3600]
            if len(self.created) >= 100:
                raise CurationError('Too many new sessions. Try later.', 429)
            with self.db() as db:
                db.execute('DELETE FROM sessions WHERE expires < ?', (now,))
                if db.execute('SELECT count(*) FROM sessions').fetchone()[0] >= 2000:
                    raise CurationError('Server session capacity reached', 429)
                token = secrets.token_urlsafe(32)
                db.execute('INSERT INTO sessions VALUES(?,?,?)',
                           (hashlib.sha256(token.encode()).hexdigest(), secrets.token_hex(16), now + SESSION_SECONDS))
            self.created.append(now)
        return {'token': token, 'expires_in': SESSION_SECONDS}

    def identity(self, header):
        if not isinstance(header, str) or not header.startswith('Bearer ') or len(header) > 200:
            raise CurationError('Reopen your browser session to connect', 401)
        with self.db() as db:
            row = db.execute('SELECT owner FROM sessions WHERE token_hash=? AND expires>?',
                             (hashlib.sha256(header[7:].encode()).hexdigest(), time.time())).fetchone()
            if row:
                db.execute('UPDATE sessions SET expires=? WHERE token_hash=?',
                           (time.time() + SESSION_SECONDS, hashlib.sha256(header[7:].encode()).hexdigest()))
        if not row:
            raise CurationError('Browser session expired. Start a new session.', 401)
        return row[0]

    def rpc(self, payload):
        with self.rpc_lock:
            if self.worker is None or self.worker.poll() is not None:
                self.worker = subprocess.Popen(
                    [self.node, '--max-old-space-size=4096', str(Path(__file__).with_name('server_curation_worker.cjs')), str(self.config)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1, env={'PATH': os.environ.get('PATH', ''), 'NODE_ENV': 'production'})
            try:
                self.worker.stdin.write(json.dumps(payload, separators=(',', ':'), allow_nan=False) + '\n')
                self.worker.stdin.flush()
                ready, _, _ = select.select([self.worker.stdout], [], [], 110)
                if not ready:
                    self.worker.kill()
                    raise CurationError('Calculation timed out. Reload to check the saved state.', 503)
                response = json.loads(self.worker.stdout.readline())
            except (BrokenPipeError, json.JSONDecodeError, OSError) as exc:
                if self.worker:
                    self.worker.kill()
                raise CurationError('Calculation service unavailable; saved sessions are retained.', 503) from exc
            if not response.get('ok'):
                raise CurationError(response.get('error', 'Calculation failed'), response.get('status', 400))
            return response['result']

    def close(self):
        if self.worker and self.worker.poll() is None:
            self.worker.terminate()
            self.worker.wait(timeout=10)
        if self.worker:
            self.worker.stdin.close()
            self.worker.stdout.close()

    def request(self, operation, body, authorization):
        if not isinstance(body, dict):
            raise CurationError('Expected a JSON object')
        if operation == 'session':
            if body:
                raise CurationError('Session request must be empty')
            return self.session(), 'application/json'
        owner = self.identity(authorization)
        if operation == 'agent-options':
            if body:raise CurationError('Invalid Agent request')
            return self.agents.options(), 'application/json'
        if operation == 'agent-start':
            try:return self.agents.start(owner, body), 'application/json'
            except ValueError as exc:raise CurationError(str(exc)) from exc
        if operation == 'agent-status':
            if set(body) != {'job_id'}:raise CurationError('Invalid Agent request')
            return self.agents.summary(self.agents.read_job(owner, body['job_id'])), 'application/json'
        if operation == 'upload-create':
            if set(body) != {'files', 'recording_id'}:
                raise CurationError('Invalid upload request')
            try:return self.uploads.create(owner, body), 'application/json'
            except ValueError as exc:raise CurationError(str(exc)) from exc
        if operation in ('upload-status', 'upload-finish'):
            if set(body) != {'upload_id'}:
                raise CurationError('Invalid upload request')
            if operation == 'upload-finish':
                try:return self.uploads.finish(owner, body['upload_id']), 'application/json'
                except ValueError as exc:raise CurationError(str(exc)) from exc
            return self.uploads.summary(self.uploads.record(owner, body['upload_id'])[1]), 'application/json'
        common = {'workspace_id', 'version', 'source', 'target', 'revision_id'}
        allowed = {
            'datasets': set(), 'workspaces': set(), 'history': set(),
            'open': {'dataset_id', 'workspace_id', 'checkpoint', 'source', 'target'},
            'view': common | {'preview_id'},
            'preview': common | {'action'},
            'confirm': common | {'preview_id', 'choice', 'request_id'},
            'undo': common | {'request_id'}, 'export': common,
            'revision-plan': common | {'event_id'}, 'revision-start': common | {'event_id'}, 'revision-cancel': common,
            'correlation': common | {'preview_id'},
            'figure': common | {'preview_id', 'figure', 'cluster_id', 'mode', 'axes', 'isolate', 'opacity', 'width'},
            'pca-selection': common | {'polygon', 'axes'},
        }
        if operation not in allowed:
            raise CurationError('Operation not available', 404)
        if set(body) - allowed[operation]:
            raise CurationError('Unsupported request fields')
        payload = {**body, 'op': operation, 'owner': owner}
        if not self.compute_slots.acquire(timeout=15):
            raise CurationError('Server is computing another preview. Retry shortly.', 429)
        try:
            if operation == 'figure':
                spec = self.rpc({**payload, 'op': 'render-spec'})
                cache_key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
                with self.render_lock:
                    if cache_key in self.images:
                        png = self.images[cache_key]
                        self.images.move_to_end(cache_key)
                    else:
                        png = self.render(spec)
                        self.images[cache_key] = png
                        self.image_bytes += len(png)
                        while self.image_bytes > 64 * 1024 * 1024:
                            _, old = self.images.popitem(last=False)
                            self.image_bytes -= len(old)
                if spec.get('preview_id') and spec['figure'] == 'waves' and spec['options']['isolate'] is None:
                    self.rpc({**payload, 'op': 'preview-seen'})
                return png, 'image/png'
            if operation == 'pca-selection':
                return self.select_pca(payload), 'application/json'
            return self.rpc(payload), 'application/json'
        finally:
            self.compute_slots.release()

    def arrays(self, spec):
        import numpy as np
        # Paths originate exclusively in the private worker, never in an HTTP body.
        for key in ('wave_path', 'time_path', 'amp_path', 'labels_path', 'initial_path'):
            if Path(spec[key]).resolve().parent != self.storage:
                raise CurationError('Invalid private render input', 503)
        waves = np.memmap(spec['wave_path'], dtype=spec['dtype'], mode='r', shape=(spec['n_spikes'], spec['n_samples']))
        labels = np.memmap(spec['labels_path'], dtype='<i4', mode='r', shape=(spec['n_spikes'],))
        return waves, labels

    def pca(self, spec, waves):
        import numpy as np
        key = spec['wave_path']
        if key in self.pca_models:
            self.pca_models.move_to_end(key)
            return self.pca_models[key]
        initial = np.memmap(spec['initial_path'], dtype='<i4', mode='r', shape=(len(waves),))
        rows = initial > 0
        real = np.asarray(waves[rows], dtype=np.float64)
        mean = real.mean(axis=0)
        real -= mean
        cov = real.T @ real / max(1, len(real) - 1)
        values, vectors = np.linalg.eigh(cov)
        order = np.argsort(-values, kind='stable')
        values = np.maximum(values[order], 0)
        vectors = vectors[:, order[:3]]
        for k in range(vectors.shape[1]):
            if vectors[np.argmax(abs(vectors[:, k])), k] < 0:
                vectors[:, k] *= -1
        scores = (np.asarray(waves, dtype=np.float64) - mean) @ vectors
        if scores.shape[1] < 3:
            scores = np.pad(scores, ((0, 0), (0, 3 - scores.shape[1])))
        result = (scores, [float(v / values.sum()) if values.sum() else 0. for v in values[:3]])
        self.pca_models[key] = result
        while len(self.pca_models) > 2:
            self.pca_models.popitem(last=False)
        return result

    def pca_bounds(self, spec, waves, labels):
        import numpy as np
        scores, variance = self.pca(spec, waves)
        pc = spec['options']['axes']
        points = scores[np.isin(labels, [g['id'] for g in spec['groups']])][:, [0, pc]]
        low, high = points.min(axis=0), points.max(axis=0)
        pad = np.maximum((high - low) * .04, 1e-6)
        return scores, variance, (low[0] - pad[0], high[0] + pad[0], low[1] - pad[1], high[1] + pad[1])

    def select_pca(self, payload):
        import numpy as np
        from matplotlib.path import Path as Polygon
        polygon = payload.get('polygon')
        if (not isinstance(polygon, list) or not 3 <= len(polygon) <= 512
                or any(not isinstance(p, list) or len(p) != 2
                       or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in p) for p in polygon)):
            raise CurationError('Draw a valid polygon inside the PCA plot')
        if payload.get('target') is not None:
            raise CurationError('Select one source cluster to split')
        with self.render_lock:
            spec = self.rpc({**payload, 'op': 'render-spec', 'figure': 'pca'})
            waves, labels = self.arrays(spec)
            scores, _, (lx, hx, ly, hy) = self.pca_bounds(spec, waves, labels)
            normalized = np.column_stack(((scores[:, 0] - lx) / (hx - lx), (hy - scores[:, spec['options']['axes']]) / (hy - ly)))
            rows = np.flatnonzero((labels == payload['source']) & Polygon(polygon).contains_points(normalized))
            # Row indices travel only between private backend components.
            response = self.rpc({**payload, 'op': 'preview', 'action': 'SPLIT', 'manual_rows': rows.tolist()})
        return response

    def render(self, spec):
        import numpy as np
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from PIL import Image, ImageDraw
        waves, labels = self.arrays(spec)
        thumb = spec['figure'] == 'thumbnail'
        width = 360 if thumb else spec['options']['width']
        height = 160 if thumb else round(width * 650 / 1200)
        fig = Figure(figsize=(width / 100, height / 100), dpi=100, facecolor='white')
        ax = fig.add_axes([0, 0, 1, 1] if thumb else [.17, .16, .80, .79])
        ax.set_axisbelow(True)
        ax.grid(color='#d4e0e8', linewidth=1)
        ax.tick_params(colors='#345168', labelsize=18 if not thumb else 12)
        for spine in ax.spines.values():
            spine.set_visible(False)
        groups = spec['groups']
        draw_groups = [(i, g) for i, g in enumerate(groups) if spec['options']['isolate'] in (None, g['id'])]
        if spec['figure'] in ('waves', 'thumbnail'):
            all_rows = np.flatnonzero(np.isin(labels, [g['id'] for g in groups]))
            low, high = float(waves[all_rows].min()), float(waves[all_rows].max())
            pad = max((high - low) * .04, 1e-6)
            ax.set_ylim(low - pad, high + pad)
            x = np.arange(spec['n_samples']) / spec['fs'] * 1000
            ax.set_xlim(float(x[0]), float(x[-1]))
            raster_width, raster_height = (width, height) if thumb else (round(width * .80), round(height * .79))
            raster = Image.new('RGB', (raster_width, raster_height), 'white')
            pen = ImageDraw.Draw(raster, 'RGBA')
            pixels_x = np.rint(np.linspace(0, raster_width - 1, spec['n_samples'])).astype(int).tolist()
            # Large group first, small group last; every real member contributes.
            for i, g in sorted(draw_groups, key=lambda item: -item[1]['n']):
                ids = np.flatnonzero(labels == g['id'])
                if spec['options']['mode'] == 'sample24':
                    times = np.memmap(spec['time_path'], dtype='<f8', mode='r', shape=(len(labels),))
                    ids = ids[np.argsort(times[ids], kind='stable')]
                    ids = ids[np.linspace(0, len(ids) - 1, min(24, len(ids)), dtype=int)]
                alpha = (.28 if len(ids) <= 24 else .035) * spec['options']['opacity'][i]
                rgb = tuple(int(g['color'][k:k + 2], 16) for k in (1, 3, 5))
                for start in range(0, len(ids), 4096):
                    block = waves[ids[start:start + 4096]]
                    pixels_y = np.rint((high + pad - block) / (high - low + 2 * pad) * (raster_height - 1)).astype(np.int32)
                    # Draw every complete member path; no averages, envelopes or stride.
                    for row in pixels_y:
                        pen.line(list(zip(pixels_x, row.tolist())), fill=(*rgb, max(1, round(alpha * 255))), width=1)
            rgba = np.asarray(raster.convert('RGBA')).copy()
            rgba[:, :, 3] = np.where(np.any(rgba[:, :, :3] != 255, axis=2), 255, 0)
            ax.imshow(rgba, extent=(float(x[0]), float(x[-1]), low - pad, high + pad), aspect='auto', interpolation='nearest', zorder=2)
            if not thumb:
                ax.set_xlabel('Time (ms)', color='#345168', fontsize=20)
                ax.set_ylabel('Amplitude (source units)', color='#345168', fontsize=20)
        elif spec['figure'] == 'pca':
            scores, _, bounds = self.pca_bounds(spec, waves, labels)
            ax.set_xlim(bounds[0:2]); ax.set_ylim(bounds[2:4])
            for _, g in draw_groups:
                points = scores[labels == g['id']]
                ax.scatter(points[:, 0], points[:, spec['options']['axes']], s=1, alpha=.2, color=g['color'], rasterized=True)
            ax.set_xlabel('PC1 (source units)', color='#345168', fontsize=20)
            ax.set_ylabel('PC' + str(spec['options']['axes'] + 1) + ' (source units)', color='#345168', fontsize=20)
        else:
            times = np.memmap(spec['time_path'], dtype='<f8', mode='r', shape=(len(labels),))
            amps = np.memmap(spec['amp_path'], dtype='<f8', mode='r', shape=(len(labels),))
            for _, g in draw_groups:
                ids = np.flatnonzero(labels == g['id'])
                ax.scatter(times[ids], amps[ids], s=1, alpha=.2, color=g['color'], rasterized=True)
            ax.set_xlabel('Recording time (s)', color='#345168', fontsize=20)
            ax.set_ylabel('Peak-to-trough amplitude', color='#345168', fontsize=20)
        if thumb:
            ax.set_axis_off()
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        stream = BytesIO()
        Image.fromarray(np.asarray(canvas.buffer_rgba())).save(stream, format='PNG', compress_level=5)
        fig.clear()
        return stream.getvalue()
