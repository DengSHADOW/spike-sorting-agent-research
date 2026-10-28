"""Create local private gateway settings once. Never prints access credentials."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
from urllib.request import urlopen

from private_gateway import digest_key, file_hash

DATASETS = (
    'cM2-e004_004-006_CH3', 'cM2-e004_004-006_CH31',
    'cM2-e004_011-015_CH17', 'cM2-e004_011-015_CH20', 'cM2-e008_021-028_CH30',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package-root', type=Path, required=True)
    parser.add_argument('--local-api', default='http://127.0.0.1:8765')
    args = parser.parse_args()
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path = args.output / 'config.json'
    key_path = args.output / 'owner-access.txt'
    if config_path.exists() or key_path.exists():
        raise SystemExit('Existing access settings preserved. Use the existing configuration.')
    rows = json.load(urlopen(args.local_api + '/api/curation/datasets', timeout=15))['datasets']
    packages = []
    for dataset in DATASETS:
        row = next(r for r in rows if r['dataset_id'] == dataset)
        name = dataset + '-' + row['source_sha256'][:12] + '.full-v3.working.json.gz'
        path = args.package_root / name
        packages.append(dict(dataset_id=dataset, file=name, source_sha256=row['source_sha256'],
                             n_spikes=row['n_spikes'], bytes=path.stat().st_size, sha256=file_hash(path)))
    key, salt = secrets.token_urlsafe(32), secrets.token_hex(16)
    config = dict(schema='spikeagent-private-gateway-v1',
                  origins=['https://spikeagent-review-20261023.web.app',
                           'https://spikeagent-review-20261023.firebaseapp.com'],
                  package_root=str(args.package_root.resolve()), packages=packages,
                  accounts=[dict(id='owner', name='Project owner', active=True, salt=salt,
                                 key_hash=digest_key(key, salt), datasets=list(DATASETS))])
    with key_path.open('x') as f:
        f.write(key + '\n')
    with config_path.open('x') as f:
        json.dump(config, f, indent=2)
        f.write('\n')
    print(json.dumps(dict(config=str(config_path), owner_key_file=str(key_path), datasets=len(packages),
                          total_bytes=sum(r['bytes'] for r in packages), public_uploads=0)))


if __name__ == '__main__':
    main()
