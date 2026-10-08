"""Read-only run observer and offline report collection; never sends requests.

On provider/parse failure, preserve the stop and do not assign substitute actions.
No skill adoption, third round, automatic retries or credential access.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run import run_skill_replay as r
from scripts.analysis import score_skill_iterations as reports


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--campaign', type=Path, required=True)
    p.add_argument('--output-root', type=Path, required=True)
    p.add_argument('--wait', action='store_true')
    args = p.parse_args(argv)
    run, out = r.experiment_root(args.run_dir), r.experiment_root(args.output_root)
    if out.exists():
        raise ValueError('Report output must be new')
    status_path = run/'execution_status.json'
    while not status_path.exists():
        if not args.wait:
            print('Run not finished; no report or request generated')
            return 0
        time.sleep(10)
    # The writer closes a small final JSON immediately; wait for a complete file.
    for _ in range(3):
        try:
            status = r.read_json(status_path)
            break
        except json.JSONDecodeError:
            time.sleep(1)
    else:
        raise ValueError('Incomplete final status; no automatic inference retry')
    if status['state'] != 'completed':
        out.mkdir(parents=True, exist_ok=False)
        r.write_new(out/'STOP.json', dict(run_dir=str(run), status=status, api_calls_by_collector=0,
            note='Incomplete experiment: no winner or replacement actions. Original responses and usage retained.'))
        print(json.dumps(status, ensure_ascii=False), flush=True)
        return 2
    reports.main(['--campaign', str(args.campaign), '--rules',
                  str(ROOT/'configs/curation/replay_scoring_v3.json'), '--output-root', str(out)])
    print('Collected offline report:', out, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
