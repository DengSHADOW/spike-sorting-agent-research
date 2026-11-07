"""Publish the two explicitly authorized complete demo packages, and enable local execution."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil

from build_server_share import ROOT, build, hashes

DATASETS = ['cM2-e004_004-006_CH3', 'cM2-e004_004-006_CH31']
CACHE_VERSION = 'grouping-revision-20261028-1'


def public_build(baseline, destination, endpoint, config_path):
    build(baseline, destination, endpoint)
    public = destination/'public'
    dist = ROOT/'mockups/curation-review/dist'
    config = json.loads(config_path.read_text())
    packages = {r['dataset_id']: r for r in config['packages']}
    package_root = Path(config['package_root']).resolve(strict=True)
    target = public/'public-data/packages';target.mkdir(parents=True)
    manifest = {'schema':'spikeagent-public-datasets-v1','datasets':[],
                'access':'public, downloadable full numerical inputs','source_mat_modified':False}
    for dataset in DATASETS:
        row = packages[dataset]
        source = (package_root/row['file']).resolve(strict=True)
        if source.parent != package_root or source.is_symlink():raise ValueError('Unexpected package source')
        raw = source.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        if checksum != row['sha256'] or len(raw) != row['bytes']:raise ValueError('Input package changed')
        data = json.loads(gzip.decompress(raw))
        if data['dataset_id'] != dataset or data['n_spikes'] != row['n_spikes'] or not data['arrays'].get('waveforms'):
            raise ValueError('Public package lacks actual complete waveforms')
        if data['metadata'].keys()-{'recording_id','prior_exposure','independence'}:raise ValueError('Review package metadata before publishing')
        name = dataset+'.'+checksum[:16]+'.bin'
        shutil.copyfile(source, target/name)
        manifest['datasets'].append({'dataset_id':dataset,'label':dataset.split('_')[-1],
           'n_spikes':data['n_spikes'],'bytes':len(raw),'sha256':checksum,
           'source_sha256':data['source_sha256'],'initial_state_sha256':data['initial_state_sha256'],
           'url':'/public-data/packages/'+name,'jacob_records':len(data.get('references',[]))})
    (public/'public-data/manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for name in ['public-data.js','public-curation.js']:
        shutil.copyfile(dist/name,public/name)
    # The private-compute page remains available, including existing server sessions/uploads.
    server_html=(public/'review-studio.html').read_text().replace('data-entry-20261028-1',CACHE_VERSION)
    (public/'server-curation.html').write_text(server_html)
    script=f'<script src="public-data.js?{CACHE_VERSION}" data-manifest="/public-data/manifest.json"></script>'
    import re
    browser_html=re.sub(r'<script src="server-review\.js[^\"]*" data-endpoint="[^\"]+"></script>',script,server_html)
    if browser_html==server_html:raise ValueError('Server module was not replaced')
    browser_html=browser_html.replace('<script src="inputs.js', f'<script src="public-curation.js?{CACHE_VERSION}"></script><script src="inputs.js',1)
    browser_html=browser_html.replace('data-entry-20261028-1',CACHE_VERSION)
    browser_html=browser_html.replace('Reviewer profiles keep sessions separate in this browser. Server operations are saved on Runpod; export the operation log to keep a report.', 'Public-demo operations are saved in this browser for each reviewer. Export your session to keep a backup or move to another computer.')
    (public/'review-studio.html').write_text(browser_html)
    (public/'index.html').write_text(browser_html)
    data_html=(public/'data-sources.html').read_text().replace('<script src="data-sources.js',script+'<script src="data-sources.js',1).replace('data-entry-20261028-1',CACHE_VERSION)
    (public/'data-sources.html').write_text(data_html)
    # Keep the shared History renderer and its projection API on the same version on older pages too.
    for page in public.glob('*.html'):
        page.write_text(re.sub(r'src="(activity-(?:core|ui)\.js)(?:\?[^"\s]*)?"',rf'src="\1?{CACHE_VERSION}"',page.read_text()))
    hosting_path=destination/'firebase.json';hosting=json.loads(hosting_path.read_text())
    hosting['hosting']['headers'] += [
      {'source':'/public-data/packages/**','headers':[{'key':'Content-Type','value':'application/octet-stream'},{'key':'Cache-Control','value':'public, max-age=31536000, immutable'}]},
      {'source':'/public-data/manifest.json','headers':[{'key':'Cache-Control','value':'no-cache'}]}]
    hosting_path.write_text(json.dumps(hosting,indent=2)+'\n')
    old,new=hashes(baseline/'public'),hashes(public)
    numeric=[f for f in new if f.endswith(('.bin','.gz','.npy','.npz','.mat'))]
    assert set(numeric)=={'public-data/packages/'+p['url'].rsplit('/',1)[1] for p in manifest['datasets']}
    assert all(old[f]==new[f] for f in old if f.endswith(('.png','.json')))
    assert new['index.html']==new['review-studio.html']
    # No access keys, upload registries, reviewer states or other channel packages are copied.
    secret=(ROOT/'output/curation_private_backend/owner-access.txt').read_text().strip().encode()
    assert secret and all(secret not in f.read_bytes() for f in public.rglob('*') if f.is_file())
    report={'release':destination.name,'datasets':manifest['datasets'],
            'compressed_bytes':sum(r['bytes'] for r in manifest['datasets']),
            'numeric_packages_published':2,'other_numeric_packages_published':0,
            'existing_evidence_unchanged':True,'private_uploads_published':False,'credentials_published':0,
            'default_computation':'browser, first channel immediately; other channel automatically cached in background','server_mode_page':'server-curation.html',
            'new_model_calls':0, 'retired_review_page':'saved-review.html',
            'removed_files':['saved-review.html'] if (baseline/'public/saved-review.html').exists() else []}
    (destination/'BUILD_REPORT.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--endpoint',required=True);parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(public_build(args.baseline,args.destination,args.endpoint,args.config),indent=2))
