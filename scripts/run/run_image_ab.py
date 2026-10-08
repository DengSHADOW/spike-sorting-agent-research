"""Frozen raw/summary waveform A/B, original35 development states, offline default."""
from __future__ import annotations
import argparse
import base64
import copy
from decimal import Decimal
import hashlib
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run import run_skill_replay as r

PROTOCOL = ROOT/'configs/curation/image_ab_protocol_v1.json'
INDEX = ROOT/'output/real_split_manifest_20260903/manifest.json'
CODE = ('scripts/run/run_image_ab.py', 'scripts/analysis/score_image_ab.py',
        'src/agent/curation_observation_v1.py', 'src/cluster/manager.py',
        'src/io/matlab_loader.py', 'scripts/analysis/audit_real_action_sources.py', *r.CODE)


def checked(path, expected, sources):
    path = r.safe_path(ROOT, str(Path(path).relative_to(ROOT)) if Path(path).is_absolute() else path,
                       r.contract.load_protocol()['scope'])
    if r.file_hash(path) != expected:
        raise r.ReplayStop(f'Source hash mismatch: {path.name}')
    sources[str(path)] = expected
    return path


def assigns_hash(assigns):
    import numpy as np
    a = np.ascontiguousarray(assigns)
    h = hashlib.sha256()
    h.update(str(a.dtype).encode('ascii')); h.update(str(a.shape).encode('ascii')); h.update(a.tobytes())
    return h.hexdigest()


def without_waveforms(request, phase):
    obj = copy.deepcopy(request)
    for i in range(1 if phase == 'phase1' else 2):
        obj['input'][0]['content'][i+1]['image_url'] = '<WAVEFORM_VARIANT>'
    return obj


def paired_requests(case, metrics, images):
    result = {}
    for arm in ('raw', 'summary'):
        urls = ['data:image/png;base64,'+base64.b64encode(png).decode() for png in images[arm]]
        result[arm] = r.contract.build_request(case['phase'], metrics, urls,
                                              skill_version='v0', layout=case['image_layout'])
        result[arm]['service_tier'] = 'default'
    if r.canonical(without_waveforms(result['raw'], case['phase'])) != r.canonical(without_waveforms(result['summary'], case['phase'])):
        raise r.ReplayStop('A/B changed something other than waveform PNGs')
    return result


def render_case(out, case, manager, fs, original_metrics):
    from src.agent import curation_observation_v1 as obs
    phase = case['phase']
    cid = original_metrics['cluster_id' if phase == 'phase1' else 'small_cluster_id']
    target = None if phase == 'phase1' else original_metrics['large_cluster_id']
    current = obs.metrics(manager, phase, cid, target, sampling_rate=fs)
    count_fields = ('n_spikes', 'n_overclusters') if phase == 'phase1' else ('n_small', 'n_large')
    if any(current[k] != original_metrics[k] for k in count_fields):
        raise r.ReplayStop('Reconstructed state counts differ from original observation')
    wave = [obs._cluster(manager, c) for c in ([cid] if target is None else [cid,target])]
    limits = (min(float(w.min()) for _,w,_ in wave), max(float(w.max()) for _,w,_ in wave))
    originals = [(ROOT/item['path']).read_bytes() for item in case['images']]
    for png, item in zip(originals, case['images']):
        if hashlib.sha256(png).hexdigest() != item['sha256']:
            raise r.ReplayStop('Original PNG changed')
    all_images, records = {}, {}
    for arm, view in (('raw','raw_member_v1'), ('summary','summary_member_v1')):
        images, details = list(originals), []
        folder = out/'images'/case['case_id']/arm
        folder.mkdir(parents=True, exist_ok=False)
        for i, (cluster, (ids,w,_)) in enumerate(zip([cid] if target is None else [cid,target],wave)):
            images[i], meta = obs._waveform(ids,w,cluster,fs,view,
                limits=limits if target is not None and arm=='summary' else None)
            path = folder/f'waveform_{i}.png'
            with path.open('xb') as f: f.write(images[i])
            details.append(dict(path=str(path.relative_to(ROOT)), sha256=r.file_hash(path), display=meta))
        for item in case['images'][len(wave):]:
            details.append(dict(item))
        records[arm] = details
        all_images[arm] = images
    for i in range(len(wave)):
        if records['raw'][i]['display']['member_ids'] != records['summary'][i]['display']['member_ids']:
            raise r.ReplayStop('A/B waveform members differ')
    paired_requests(case,current,all_images)
    return dict(case_id=case['case_id'], dataset_id=case['dataset_id'], phase=phase,
                image_layout=case['image_layout'], assigns_sha256=assigns_hash(manager.assigns),
                metrics=current, images=records)


def prepare(out):
    import numpy as np
    from src.cluster.manager import ClusterManager
    from src.io.matlab_loader import load_matlab_spikes
    from scripts.analysis.audit_real_action_sources import parse_action
    protocol = r.read_json(PROTOCOL)
    if r.file_hash(ROOT/protocol['case_file']) != protocol['case_sha256']:
        raise r.ReplayStop('Original35 changed')
    case_dir = (ROOT/protocol['case_file']).parent
    original_spec, payloads, cases = r.build_plan(case_dir, versions=['v0'], repeats=1)
    # This also checks all original image hashes/previews, without SDK access.
    sources = dict(original_spec['source_hashes'])
    prep = r.read_json(case_dir/'summary.json')
    checked(INDEX,prep['source_hashes'][str(INDEX.relative_to(ROOT))],sources)
    index = {x['dataset_id']:x for x in r.read_json(INDEX)['datasets']}
    originals = {c['case_id']: r.observation_from_preview(payloads[(c['case_id'],'v0')])[1] for c in cases}
    del payloads
    out.mkdir(parents=True, exist_ok=False)
    observations = {}
    for dataset in sorted({c['dataset_id'] for c in cases}):
        r.contract.require_development(dataset)
        selected = [c for c in cases if c['dataset_id']==dataset]
        row = index[dataset]
        mat = r.safe_path(ROOT,row['mat_path'],r.contract.load_protocol()['scope'])
        if mat.parent.name != dataset:
            raise r.ReplayStop('MAT path/dataset mismatch')
        checked(mat,row['mat_sha256'],sources)
        data = load_matlab_spikes(str(mat))
        def manager(assigns=None, tree=None):
            return ClusterManager(data['hierarchy_assigns'] if assigns is None else assigns,
                data['overcluster_assigns'], data['hierarchy_tree'].copy() if tree is None else tree.copy(),
                data['spiketimes'],data['waveforms'])
        expert = [c for c in selected if c['target_type']=='recorded_expert_edit']
        if expert:
            files = {c['source_jsonl'] for c in expert}
            if len(files)!=1:
                raise r.ReplayStop('Multiple expert sources for one dataset')
            path = next(iter(files))
            checked(path,prep['source_hashes'][path],sources)
            records = r.read_jsonl(ROOT/path)
            selected_ids = {c['source_sample_id']:c for c in expert}
            found = set(); m = manager()
            for step, sample in enumerate(records,1):
                if sample['dataset_id']!=dataset or sample['trajectory_step']!=step:
                    raise r.ReplayStop('Expert replay not consecutive/within dataset')
                if sample['mat_source']['sha256']!=row['mat_sha256']:
                    raise r.ReplayStop('Expert/MAT mismatch')
                if assigns_hash(m.assigns)!=sample['state_before']['assigns_sha256']:
                    raise r.ReplayStop('Expert pre-state hash mismatch')
                action,cid,target = parse_action(sample['expert_action_raw'])
                if sample['id'] in selected_ids:
                    c=selected_ids[sample['id']]
                    if c['state_before']['assigns_sha256']!=assigns_hash(m.assigns):
                        raise r.ReplayStop('Selected audit state mismatch')
                    observation=originals[c['case_id']]
                    if observation['cluster_id' if c['phase']=='phase1' else 'small_cluster_id']!=cid:
                        raise r.ReplayStop('Selected source cluster mismatch')
                    if c['phase']=='phase2' and observation['large_cluster_id']!=target:
                        raise r.ReplayStop('Selected target cluster mismatch')
                    observations[c['case_id']]=render_case(out,c,m,data['Fs'],observation)
                    found.add(sample['id'])
                if action=='split': m.split_last_merge(cid)
                elif action=='discard': m.discard_cluster(cid)
                else: m.merge_clusters([cid,target],target_id=target)
                if assigns_hash(m.assigns)!=sample['state_after']['assigns_sha256']:
                    raise r.ReplayStop('Expert post-state hash mismatch')
                m.history.clear(); m.history_index=-1
                if found==set(selected_ids): break
            if found!=set(selected_ids): raise r.ReplayStop('Missing selected expert state')
            del m
        for c in selected:
            if c['target_type']!='derived_terminal_constraint': continue
            if c['mat_sha256']!=row['mat_sha256']: raise r.ReplayStop('Historical MAT mismatch')
            path=checked(c['state_path'],c['state_sha256'],sources)
            with np.load(path,allow_pickle=False) as state:
                m=manager(state['assigns'],state['hierarchy_tree'])
            if len(m.assigns)!=len(data['waveforms']): raise r.ReplayStop('Historical state length mismatch')
            observations[c['case_id']]=render_case(out,c,m,data['Fs'],originals[c['case_id']])
            del m
        checked(mat,row['mat_sha256'],sources)
        print(f'Prepared {dataset}: {len(selected)} exact-state pairs',flush=True)
        del data
    if set(observations)!={c['case_id'] for c in cases}: raise r.ReplayStop('Incomplete case preparation')
    for name in (*r.FROZEN,'replay_scoring_v1.json','image_ab_protocol_v1.json','image_ab_budget.json'):
        path=r.contract.CONFIG_DIR/name; sources[str(path)]=r.file_hash(path)
    for name in CODE:
        sources[str(ROOT/name)]=r.file_hash(ROOT/name)
    r.write_new(out/'observations.json',observations)
    spec,payloads = assemble(out,cases,observations,sources)
    r.write_new(out/'manifest.json',dict(created_at=r.now(),state='dry-run-complete',spec=spec))
    with (out/'requests.jsonl').open('x') as f:
        for job in spec['jobs']: r.log_row(f,job)
    r.write_new(out/'dry_run.json',dict(network_calls=0,planned_calls=len(spec['jobs']),
        pair_invariance_checked_cases=len(cases),manifest_sha256=r.file_hash(out/'manifest.json')))
    print('Dry run complete: 35 pairs / 70 calls, no SDK/client/network',flush=True)


def assemble(out,cases,observations,sources):
    protocol=r.read_json(PROTOCOL)
    if (protocol['repeats']!=1 or protocol['skill']!='v0' or protocol['model']!='gpt-6-astra'
        or protocol['reasoning_effort']!='high' or protocol['max_output_tokens']!=4000):
        raise r.ReplayStop('Protocol outside registered image experiment')
    payloads,jobs={},[]
    for c in cases:
        r.contract.require_development(c['dataset_id'])
        o=observations[c['case_id']]
        if o['dataset_id']!=c['dataset_id'] or o['phase']!=c['phase'] or o['image_layout']!=c['image_layout']:
            raise r.ReplayStop('Observation scope mismatch')
        images={}
        for arm in ('raw','summary'):
            images[arm]=[]
            for item in o['images'][arm]:
                path=r.safe_path(ROOT,item['path'],r.contract.load_protocol()['scope'])
                png=path.read_bytes()
                if hashlib.sha256(png).hexdigest()!=item['sha256'] or not png.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise r.ReplayStop('A/B image hash/type mismatch')
                images[arm].append(png)
        req=paired_requests(c,o['metrics'],images)
        common=r.digest(without_waveforms(req['raw'],c['phase']))
        for arm in ('raw','summary'):
            payloads[(c['case_id'],arm)]=req[arm]
            jobs.append(dict(job_id=f"{c['case_id']}--{arm}--r1",case_id=c['case_id'],version=arm,repeat=1,
                dataset_id=c['dataset_id'],phase=c['phase'],target_type=c['target_type'],image_layout=c['image_layout'],
                image_count=len(images[arm]),request_sha256=r.digest(req[arm]),non_waveform_sha256=common))
    random.Random(protocol['seed']).shuffle(jobs)
    for i,j in enumerate(jobs): j['order']=i
    spec=dict(format='image-ab-paid-v1',case_dir=str((ROOT/protocol['case_file']).parent),
        versions=['raw','summary'],skill='v0',repeats=1,seed=protocol['seed'],mode='synchronous',
        model=r.contract.load_protocol()['model'],service_tier='default',sdk=r.sdk_capabilities(),pricing=r.PRICES,
        scoring_sha256=r.file_hash(r.SCORING),protocol_sha256=r.file_hash(PROTOCOL),
        observations_sha256=r.file_hash(out/'observations.json'),source_hashes=sources,
        jobs=jobs,planned_calls=len(jobs),pair_invariance_checked_cases=len(cases),
        budget_cap_usd=r.read_json(ROOT/protocol['budget_file'])['cumulative_budget_usd'],disclaimer=protocol['disclaimer'])
    return spec,payloads


def verify(out):
    saved=r.read_json(out/'manifest.json')['spec']
    if saved['format']!='image-ab-paid-v1': raise r.ReplayStop('Not an image experiment')
    for path,h in saved['source_hashes'].items(): checked(path,h,{})
    protocol=r.read_json(PROTOCOL)
    if r.file_hash(ROOT/protocol['case_file'])!=protocol['case_sha256']: raise r.ReplayStop('Original35 changed')
    cases=r.load_cases(Path(saved['case_dir']),r.read_json(r.SCORING))
    current,payloads=assemble(out,cases,r.read_json(out/'observations.json'),saved['source_hashes'])
    if r.canonical(saved)!=r.canonical(current) or r.read_jsonl(out/'requests.jsonl')!=current['jobs']:
        raise r.ReplayStop('Frozen image experiment changed')
    if r.file_hash(out/'manifest.json')!=r.read_json(out/'dry_run.json')['manifest_sha256']:
        raise r.ReplayStop('Manifest changed')
    return current,payloads,cases


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root',type=Path,required=True)
    mode=p.add_mutually_exclusive_group(); mode.add_argument('--estimate',action='store_true'); mode.add_argument('--execute',action='store_true')
    args=p.parse_args(argv); out=r.experiment_root(args.output_root)
    if not(args.estimate or args.execute): prepare(out); return
    spec,payloads,_=verify(out)
    if args.execute:
        est=r.authorization(out,spec['budget_cap_usd'],spec)
        total=sum((Decimal(str(x['conservative_reserved_usd'])) for x in est.values()),Decimal(0))
        prior=Decimal(0)
        for marker in (ROOT/'output').glob('curation_image_*/execution_started.json'):
            if marker.parent==out: continue
            state=marker.parent/'execution_status.json'
            if not state.exists(): raise r.ReplayStop('Previous image execution unsettled')
            prior+=Decimal(str(r.read_json(state)['charged_or_reserved_usd']))
        if prior: raise r.ReplayStop('Prior image execution: explicit cumulative continuation required')
        if total>Decimal(str(spec['budget_cap_usd'])): raise r.BudgetStop('Whole image round exceeds cap')
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env',override=False)
    result=r.execute(out,spec,payloads,spec['budget_cap_usd']) if args.execute else r.estimate(out,spec,payloads)
    print(result if args.execute else result['totals'],flush=True)


if __name__=='__main__': main()
