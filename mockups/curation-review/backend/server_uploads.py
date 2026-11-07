"""Chunked private uploads and bounded offline conversion. Never a public file server."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

MAX_UPLOAD = 256 * 1024 * 1024
CHUNK_BYTES = 4 * 1024 * 1024


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, allow_nan=False))
    temporary.chmod(0o600)
    temporary.replace(path)


class ServerUploads:
    def __init__(self, service):
        self.service = service
        self.root = service.storage / 'uploads'
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.registry = self.root / 'registry.json'
        self.lock = threading.RLock()
        self.conversion = threading.BoundedSemaphore(1)
        if not self.registry.exists():
            atomic_json(self.registry, [])
        for path in self.root.glob('*/upload.json'):
            data = json.loads(path.read_text())
            if data['status'] in ('queued', 'validating'):
                data.update(status='received', message='Server restarted. Validate this upload again.')
                atomic_json(path, data)

    def record(self, owner, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise ValueError('Upload not found')
        path = self.root / identifier / 'upload.json'
        if not path.is_file():
            raise ValueError('Upload not found')
        value = json.loads(path.read_text())
        if value['owner'] != owner:
            raise ValueError('Upload not found')
        return path, value

    def summary(self, value):
        folder = self.root / value['id']
        files = [{'name': f['name'], 'size': f['size'],
                  'received': (folder / ('input-'+str(i))).stat().st_size if (folder / ('input-'+str(i))).exists() else 0}
                 for i, f in enumerate(value['files'])]
        return {'upload_id': value['id'], 'status': value['status'], 'files': files,
                'received': sum(f['received'] for f in files), 'total': sum(f['size'] for f in files),
                'message': value.get('message', ''), 'result': value.get('result'), 'chunk_bytes': CHUNK_BYTES}

    def create(self, owner, body):
        files, recording = body.get('files'), body.get('recording_id', '')
        if not isinstance(recording, str) or not 1 <= len(recording) <= 120:
            raise ValueError('Enter the recording/session name')
        recording = recording.strip()
        if not recording:raise ValueError('Enter the recording/session name')
        if not isinstance(files, list) or not 1 <= len(files) <= 16:
            raise ValueError('Choose a MAT, sorted-spike JSON, or Phy/Kilosort files')
        seen, total = set(), 0
        for f in files:
            if (not isinstance(f, dict) or set(f) != {'name', 'size'} or not isinstance(f['name'], str)
                    or not 1 <= len(f['name']) <= 150 or any(c in f['name'] for c in '/\\\x00')
                    or f['name'].startswith('.') or f['name'] in seen or type(f['size']) is not int or f['size'] < 1
                    or Path(f['name']).suffix.lower() not in ('.mat', '.json', '.gz', '.npy', '.dat', '.bin', '.tsv')):
                raise ValueError('Invalid, duplicate or unsupported filename')
            seen.add(f['name']); total += f['size']
        if total > MAX_UPLOAD:
            raise ValueError('Upload limit is 256 MiB total')
        if shutil.disk_usage(self.root).free < total + 1024**3:
            raise ValueError('Server storage is full; no upload was started')
        with self.lock:
            records = [json.loads(p.read_text()) for p in self.root.glob('*/upload.json')]
            owned_bytes = sum(sum(f['size'] for f in r['files']) for r in records if r['owner'] == owner)
            if owned_bytes + total > 512 * 1024 * 1024 or sum(sum(f['size'] for f in r['files']) for r in records) + total > 4 * 1024**3:
                raise ValueError('Private upload capacity reached')
            identifier = uuid.uuid4().hex
            folder = self.root / identifier
            folder.mkdir(mode=0o700)
            value = {'id': identifier, 'owner': owner, 'files': files, 'recording_id': recording,
                     'status': 'uploading', 'created_at': time.time(), 'message': ''}
            atomic_json(folder / 'upload.json', value)
            return self.summary(value)

    def chunk(self, owner, identifier, index, offset, data):
        with self.lock:
            _, value = self.record(owner, identifier)
            if value['status'] != 'uploading' or not 0 <= index < len(value['files']):
                raise ValueError('This upload is not accepting bytes')
            size = value['files'][index]['size']
            if not 0 <= offset < size or not data or len(data) > CHUNK_BYTES or offset + len(data) > size:
                raise ValueError('Invalid upload chunk')
            path = self.root / identifier / ('input-'+str(index))
            current = path.stat().st_size if path.exists() else 0
            if offset < current:
                with path.open('rb') as f:
                    f.seek(offset)
                    if f.read(len(data)) != data:
                        raise ValueError('Upload bytes differ; choose the original file')
            elif offset == current:
                with path.open('ab') as f:
                    path.chmod(0o600); f.write(data)
            else:
                raise ValueError('Upload offset changed. Resume from the received byte count.')
            return self.summary(value)

    def finish(self, owner, identifier):
        with self.lock:
            path, value = self.record(owner, identifier)
            if value['status'] in ('complete', 'queued', 'validating'):
                return self.summary(value)
            summary = self.summary(value)
            if summary['received'] != summary['total']:
                raise ValueError('Upload is incomplete')
            if not self.conversion.acquire(blocking=False):
                raise ValueError('Another dataset is being validated. Retry shortly.')
            value.update(status='queued', message='Upload received. Validating actual spike data…')
            atomic_json(path, value)
            threading.Thread(target=self.convert, args=(path, value), daemon=True).start()
            return self.summary(value)

    def convert(self, path, value):
        folder = path.parent
        try:
            value.update(status='validating', message='Validating waveforms, times and cluster assignments…')
            atomic_json(path, value)
            files = [{**f, 'path': str(folder / ('input-'+str(i)))} for i, f in enumerate(value['files'])]
            hashes = []
            for f in files:
                h = hashlib.sha256()
                with Path(f['path']).open('rb') as stream:
                    for part in iter(lambda: stream.read(1024*1024), b''):h.update(part)
                hashes.append(h.hexdigest())
            source = hashes[0] if len(files) == 1 else hashlib.sha256('\n'.join(f['name']+':'+h for f, h in zip(files, hashes)).encode()).hexdigest()
            self.check_protected(files, hashes)
            dataset = 'upload-'+value['id']
            output = folder / 'dataset.working.json.gz'
            manifest = folder / 'conversion.json'
            atomic_json(manifest, {'files': files, 'recording_id': value['recording_id'], 'dataset_id': dataset,
                                  'source_sha256': source, 'output': str(output)})
            backend = Path(__file__).parent
            env = {**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
            if len(files) == 1 and files[0]['name'].lower().endswith('.mat'):
                command = [sys.executable, '-B', str(backend/'working_data.py'), '--convert', files[0]['path'],
                           '--dataset', dataset, '--output', str(output), '--metadata', json.dumps({'recording_id': value['recording_id'], 'independence_status': 'not assessed'})]
            else:
                command = [self.service.node, '--max-old-space-size=2048', str(backend/'convert_server_upload.cjs'), str(manifest)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=150, env=env)
            try:info = json.loads(result.stdout)
            except ValueError:raise ValueError('Input validation failed or exceeded the conversion resource limit')
            if result.returncode or not output.is_file():
                raise ValueError(info.get('error', 'Input could not be converted'))
            raw = output.read_bytes()
            row = {'dataset_id': dataset, 'owner': value['owner'], 'path': str(output), 'bytes': len(raw),
                   'sha256': hashlib.sha256(raw).hexdigest(), 'n_spikes': info['n_spikes'],
                   'recording_id': value['recording_id'], 'source_kind': 'private_upload'}
            with self.lock:
                rows = json.loads(self.registry.read_text())
                rows = [r for r in rows if r['dataset_id'] != dataset] + [row]
                atomic_json(self.registry, rows)
                value.update(status='complete', message='Upload validated. Ready for Merge / Split.',
                             result={'dataset_id': dataset, 'n_spikes': info['n_spikes'], 'recording_id': value['recording_id']})
                atomic_json(path, value)
        except Exception as exc:
            value.update(status='failed', message=str(exc)[:400] or 'Validation failed')
            atomic_json(path, value)
        finally:
            self.conversion.release()

    def check_protected(self, files, hashes):
        # Respect the existing study's reserved/quarantined MAT policy, even if renamed.
        root = Path(__file__).resolve().parents[3]
        manifest = root/'output/real_split_manifest_20260903/manifest.json'
        protocol = json.loads((root/'configs/curation/protocol_v1.json').read_text())['scope']
        blocked = protocol['reserved_after_prior_exposure'] + protocol['quarantined_blocks']
        if manifest.is_file():
            for row in json.loads(manifest.read_text())['datasets']:
                if row['mat_sha256'] in hashes and row['dataset_id'].rsplit('_CH', 1)[0] in blocked:
                    raise ValueError('This file is reserved for a separate study and cannot be imported here')
