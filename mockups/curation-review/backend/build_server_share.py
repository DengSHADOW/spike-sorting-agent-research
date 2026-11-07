"""Stage the existing static site with server-computed Studio; never copy data packages."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil

ROOT = Path(__file__).resolve().parents[3]


def hashes(root):
    return {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in root.rglob('*') if f.is_file()}


def build(baseline, destination, endpoint):
    if destination.exists():
        raise ValueError('Use a new release directory')
    if not re.fullmatch(r'https://[A-Za-z0-9.-]+/', endpoint):
        raise ValueError('Explicit HTTPS compute endpoint required')
    shutil.copytree(baseline, destination)
    public = destination / 'public'
    dist = ROOT / 'mockups/curation-review/dist'
    # Retire the old three-column page; retain saved records in Studio History/Revisions.
    (public/'saved-review.html').unlink(missing_ok=True)
    names = ['review-studio.html', 'review-studio.js', 'review-studio.css', 'review-working.js', 'server-review.js', 'curation-core.js',
             'data-sources.html', 'data-sources.js', 'data-sources.css',
             'review-quality.js', 'waveform-view.js', 'waveform-worker.js', 'review-pca.js', 'activity-core.js', 'activity-ui.js']
    for name in names:
        content = (dist / name).read_text()
        if name.endswith('.html'):
            content = re.sub(r'(src|href)="([^"\s]+\.(?:js|css))(?:\?[^"\s]+)?"', r'\1="\2?data-entry-20261028-1"', content)
            content = re.sub(r'<script src="cloud-auth.js[^\"]*"></script>', '', content)
            content = content.replace('<script src="app.js', '<script src="static-share.js?v=6"></script>\n  <script src="app.js', 1)
            content = content.replace('<script src="review-working.js', '<script src="server-review.js?data-entry-20261028-1" data-endpoint="'+endpoint+'"></script><script src="review-working.js', 1)
            content = content.replace('<script src="data-sources.js?data-entry-20261028-1">','<script src="data-sources.js?data-entry-20261028-1" data-endpoint="'+endpoint+'">')
            # The private-package reader remains disabled on the server-computed page.
            content = content.replace('Browse shared library', 'Browse reason library')
            content = content.replace('The lower-left Reviewer button selects your identity. To continue your review history on another computer, copy this reviewer key and use it there. This local SSH workspace has no account login.', 'Reviewer profiles keep sessions separate in this browser. Server operations are saved on Runpod; export the operation log to keep a report.')
        (public / name).write_text(content)
    shutil.copyfile(public/'review-studio.html',public/'index.html')
    hosting_path = destination/'firebase.json'
    hosting = json.loads(hosting_path.read_text())
    headers = hosting['hosting'].get('headers', [])
    headers = [r for r in headers if r['source'] not in ('**/*.html', '/')]
    for row in headers:
        if row['source'] == '**/*.@(html|js|css)':row['source'] = '**/*.@(js|css)'
    headers += [{'source':'**/*.html','headers':[{'key':'Cache-Control','value':'no-cache'}]},
                {'source':'/','headers':[{'key':'Cache-Control','value':'no-cache'}]}]
    hosting['hosting']['headers'] = headers
    hosting['hosting']['redirects'] = [r for r in hosting['hosting'].get('redirects', []) if r['source'] != '/saved-review.html'] + [{'source':'/saved-review.html','destination':'/review-studio.html','type':302}]
    hosting_path.write_text(json.dumps(hosting,indent=2)+'\n')
    old, new = hashes(baseline / 'public'), hashes(public)
    changed = [f for f in old.keys() & new.keys() if old[f] != new[f]]
    removed = sorted(set(old) - set(new))
    assert set(removed) <= {'saved-review.html'}
    assert set(changed) <= set(names) | {'index.html'}
    assert set(new) - set(old) <= {'server-review.js','saved-review.html','data-sources.html','data-sources.js','data-sources.css'}
    assert all(old[f] == new[f] for f in old if f.endswith(('.png', '.json')))
    assert new['index.html'] == new['review-studio.html']
    assert not any(f.endswith(('.gz', '.bin', '.npz', '.npy', '.mat', '.sqlite3', '.txt')) for f in new)
    # Scan credentials without printing them or copying private configuration.
    private = ROOT / 'output/curation_private_backend/owner-access.txt'
    if private.exists():
        key = private.read_text().strip().encode()
        assert key and all(key not in f.read_bytes() for f in public.rglob('*') if f.is_file())
    report = {'release': destination.name, 'source_release': baseline.name, 'changed_files': changed,
              'new_files': sorted(set(new)-set(old)), 'removed_files': removed, 'public_files': len(new), 'default_entry': 'server curation workspace',
              'all_existing_public_evidence_unchanged': True, 'numeric_packages_published': 0,
              'credentials_published': 0, 'computation': 'Runpod server', 'model_requests': 0}
    (destination / 'BUILD_REPORT.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--endpoint', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.baseline, args.destination, args.endpoint), indent=2))
