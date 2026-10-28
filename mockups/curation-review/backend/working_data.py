"""Private portable curation inputs. No inference; original files remain read-only.

Packages keep atomic statistics, spike/time membership vectors and all waveform rows.
They are downloaded explicitly, never copied into the public Hosting snapshot.
"""
from __future__ import annotations
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import os
import subprocess
import sys
import threading
import tempfile

import numpy as np
import h5py
from import_jacob import ROOT, DATASETS, arrays_hash, load_inputs, scoped_row, sha256

SCHEMA = 'spikeagent-working-dataset-v1'
MAX_BYTES = 256 * 1024 * 1024


def state_hash(assigns, tree):
    a=np.asarray(assigns,dtype='<i4');t=np.asarray(tree,dtype='<f8')
    header=f'human-curation-state-v1\n{len(a)}\n{t.shape[1]}\n'.encode()
    return hashlib.sha256(header+a.tobytes()+t.tobytes()).hexdigest()


def encode(array,dtype):
    a=np.asarray(array,dtype=dtype)
    return dict(dtype=dtype,length=a.size,data=base64.b64encode(a.tobytes()).decode())


def reference_records(dataset, inputs):
    """Match expert edits to exact pre-action states; never infer KEEP labels."""
    path=ROOT/'output/real_manifest_action_dataset_20260914/datasets'/dataset/'samples.jsonl'
    if dataset not in DATASETS or not path.is_file():return [],{}
    sys.path.insert(0,str(ROOT))
    from src.cluster.manager import ClusterManager
    from import_agent_study import assignment_hash, parse_reference
    fs,times,waves,over,assigns,tree=inputs
    m=ClusterManager(assigns,over,tree.copy(),times,waves)
    records=[];state_map={}
    expected_mat=scoped_row(dataset)[0]['mat_sha256']
    for line in path.read_text().splitlines():
        if not line.strip():continue
        r=json.loads(line)
        if r['mat_source']['sha256']!=expected_mat:
            raise ValueError('Reference MAT integrity mismatch')
        if assignment_hash(m.assigns)!=r['state_before']['assigns_sha256']:
            raise ValueError('Reference pre-state mismatch')
        digest=state_hash(m.assigns,m.hierarchy_tree)
        state_map[arrays_hash(m.assigns,m.hierarchy_tree)]=digest
        raw=r['expert_action_raw'].split()
        # Restoration of discarded noise is replayed but is not a curation task.
        if raw==['s','0']:
            m.split_last_merge(0)
        else:
            action,source,target=parse_reference(r['expert_action_raw'])
            records.append(dict(state_sha256=digest,review_state_sha256=arrays_hash(m.assigns,m.hierarchy_tree),source=source,target=target,
                phase='phase2' if action=='MERGE' else 'phase1',action=action,
                reason=r.get('label_reason') or None,reference_id=r['id'],
                annotation_source=r['annotation_source'],label_kind='Jacob historical reference'))
            if action=='SPLIT':m.split_last_merge(source)
            elif action=='DISCARD':m.discard_cluster(source)
            else:m.merge_clusters([source,target],target_id=target)
        if assignment_hash(m.assigns)!=r['state_after']['assigns_sha256']:
            raise ValueError('Reference post-state mismatch')
        m.history.clear();m.history_index=-1
    return records,state_map


def package(dataset, path, metadata=None, include_references=False):
    if h5py.is_hdf5(path):
        with h5py.File(path,'r') as f:
            seen=set();pending=[f]
            while pending:
                group=pending.pop();address=h5py.h5o.get_info(group.id).addr
                if address in seen:continue
                seen.add(address)
                if len(seen)>10000:raise ValueError('HDF5 structure is too complex')
                for name in group:
                    if isinstance(group.get(name,getlink=True),h5py.ExternalLink):raise ValueError('External HDF5 links are not accepted')
                    item=group[name]
                    if isinstance(item,h5py.Group):pending.append(item)
                    elif isinstance(item,h5py.Dataset) and (item.is_virtual or item.external):raise ValueError('External HDF5 storage is not accepted')
            if 'spikes/waveforms' in f and f['spikes/waveforms'].size>50_000_000:raise ValueError('Too many waveform values')
    try:fs,times,waves,over,assigns,tree=inputs=load_inputs(path)
    except (KeyError,AttributeError,IndexError,TypeError) as exc:
        raise ValueError('MAT requires Fs, spiketimes, waveforms, overcluster.assigns, hierarchy.assigns and hierarchy.tree') from exc
    if not 1<=len(times)<=2_000_000 or not 2<=waves.shape[1]<=1000 or waves.size>50_000_000 or tree.shape[1]>100000:
        raise ValueError('Dataset dimensions exceed the working importer limits')
    if max(np.max(assigns),np.max(over))>2**31-1:
        raise ValueError('Cluster labels exceed Int32 range')
    pairs=np.stack([assigns.astype(np.int32),over.astype(np.int32)],axis=1)
    unique,which=np.unique(pairs,axis=0,return_inverse=True)
    if len(unique)>10000:raise ValueError('Too many atomic components')
    atoms=[]
    for i,(initial,oc) in enumerate(unique):
        ids=np.flatnonzero(which==i);w=waves[ids];ordered=ids[np.argsort(times[ids],kind='stable')]
        picked=ordered[np.linspace(0,len(ids)-1,min(12,len(ids)),dtype=int)]
        atoms.append(dict(initial_cluster=int(initial),overcluster=int(oc),n=len(ids),
            sum=np.sum(w,axis=0,dtype=np.float64).tolist(),
            sumsq=np.sum(np.square(w,dtype=np.float64),axis=0).tolist(),
            waveform_min=float(w.min()),waveform_max=float(w.max()),
            traces=[dict(row=int(row),waveform=waves[row].tolist()) for row in picked]))
    references,state_map=reference_records(dataset,inputs) if include_references else ([],{})
    suggestions=[]
    registry=ROOT/'output/curation_review_local/manager/registry.json'
    for item in json.loads(registry.read_text()) if registry.is_file() and include_references else []:
        b=json.loads((Path(item['path'])/'dataset.json').read_text())
        if b['dataset_id']!=dataset or b.get('study_agent_id') not in ('Agent_1','Agent_2'):continue
        for c in b['cases']:
            if c['state_sha256'] not in state_map:continue
            ids={u['id']:u['cluster_id'] for u in c['units']}
            suggestions.append(dict(state_sha256=state_map[c['state_sha256']],phase=c['phase_id'],
                source=ids[c['source']],target=ids[c['pair'][1]] if c['pair'] else None,
                agent_id=c['study_agent_id'],action=c['ai'],rationale=c.get('rationale'),
                reference_id=c['id'],protocol_note=c.get('comparison_protocol_note')))
    snapshots = []
    if include_references:
        from dataset_inputs import DatasetManager
        from review_analysis import AnalysisService
        manager = DatasetManager.__new__(DatasetManager)
        manager.lock = threading.RLock(); manager.bundles = {}; manager.paths = {}
        for item in json.loads(registry.read_text()) if registry.is_file() else []:
            candidate = json.loads((Path(item['path']) / 'dataset.json').read_text())
            if candidate['dataset_id'] == dataset:
                manager._load(item['id'], Path(item['path']))
        service = AnalysisService(manager)
        seen = set()
        verified = {}
        actor_checksum = arrays_hash(over, times, waves)
        mat_checksum = sha256(path)
        first_rows = np.zeros(len(unique), dtype=int)
        for atom in range(len(unique)):
            first_rows[atom] = np.flatnonzero(which == atom)[0]
        for bundle in manager.bundles.values():
            if bundle['provenance'].get('actor_input_sha256') != actor_checksum:
                raise ValueError('Private snapshot actor input mismatch')
            for case in bundle['cases']:
                if case['mat_sha256'] != mat_checksum:
                    raise ValueError('Private snapshot recording mismatch')
                if case['state_sha256'] not in verified:
                    verified[case['state_sha256']] = service._assignments(case, bundle, inputs, return_tree=True)
                assigned, saved_tree = verified[case['state_sha256']]
                human_hash = state_hash(assigned, saved_tree)
                if case['state_sha256'] not in seen:
                    labels = assigned[first_rows].astype(int)
                    if not np.array_equal(labels[which], assigned):
                        raise ValueError('Saved state splits an immutable atomic component')
                    snapshots.append(dict(archive_state_sha256=case['state_sha256'], state_sha256=human_hash,
                        labels=labels.tolist(), tree=saved_tree.tolist()))
                    seen.add(case['state_sha256'])
                if case.get('study_agent_id') in ('Agent_1', 'Agent_2'):
                    ids = {u['id']: u['cluster_id'] for u in case['units']}
                    if not any(row.get('reference_id') == case['id'] for row in suggestions):
                        suggestions.append(dict(state_sha256=human_hash, phase=case['phase_id'], source=ids[case['source']],
                            target=ids[case['pair'][1]] if case['pair'] else None, agent_id=case['study_agent_id'],
                            action=case['ai'], rationale=case.get('rationale'), reference_id=case['id'],
                            protocol_note=case.get('comparison_protocol_note')))
    # Preserve every original value: Float32 only when it is exactly lossless.
    f32=waves.astype('<f4');dtype='<f4' if np.array_equal(waves,f32.astype(waves.dtype)) else '<f8'
    raw_waves=np.asarray(waves,dtype=dtype,order='C')
    waveform_vector=encode(raw_waves.reshape(-1),dtype)
    waveform_vector.update(shape=list(waves.shape),sha256=hashlib.sha256(raw_waves.tobytes()).hexdigest())
    result=dict(schema=SCHEMA,dataset_id=dataset,source_sha256=sha256(path),
        sampling_rate_hz=fs,n_spikes=len(times),n_samples=waves.shape[1],
        arrays=dict(times=encode(times,'<f8'),amplitudes=encode(waves.max(axis=1).astype(float)-waves.min(axis=1).astype(float),'<f8'),
                    atom_index=encode(which,'<i4'),waveforms=waveform_vector),
        atoms=atoms,tree=tree.tolist(),initial_state_sha256=state_hash(assigns,tree),
        metadata=metadata or {},references=references,agent_suggestions=suggestions,
        new_model_calls=0,package_revision=3,review_snapshots=snapshots,all_waveforms_included=True,trace_sampling='up to 12 chronological member waveforms per atomic component')
    return result


class WorkingInputs:
    def __init__(self, storage):
        self.root=Path(storage)/'working_inputs';self.root.mkdir(parents=True,exist_ok=True);self.root.chmod(0o700)
        self.lock=threading.Lock()
        self.references_cache={}

    def references(self,dataset):
        if dataset not in DATASETS:raise ValueError('Unknown reference dataset')
        if dataset not in self.references_cache:
            row,_=scoped_row(dataset);legacy=self.root/(dataset+'-'+row['mat_sha256'][:12]+'.working.json.gz')
            self.references_cache[dataset]=json.loads(gzip.decompress(legacy.read_bytes() if legacy.exists() else self.get(dataset)))['references']
        return self.references_cache[dataset]

    def datasets(self):
        rows=[]
        for dataset in DATASETS:
            row,path=scoped_row(dataset)
            rows.append(dict(dataset_id=dataset,n_spikes=row['n_spikes'],source_sha256=row['mat_sha256'],
                metadata=dict(recording_id=dataset.rsplit('_CH',1)[0],prior_exposure='development / previously exposed',independence='not a pristine held-out recording')))
        for p in self.root.glob('upload-*.meta.json'):rows.append(json.loads(p.read_text()))
        return rows

    def get(self,dataset):
        if dataset in DATASETS:
            row,path=scoped_row(dataset);meta=dict(recording_id=dataset.rsplit('_CH',1)[0],
                prior_exposure='development / previously exposed',independence='not a pristine held-out recording')
            target=self.root/(dataset+'-'+row['mat_sha256'][:12]+'.full-v3.working.json.gz')
        else:
            if not re.fullmatch(r'upload-[a-f0-9]{24}',dataset):raise ValueError('Unknown working dataset')
            record=json.loads((self.root/(dataset+'.meta.json')).read_text());path=self.root/(dataset+'.mat');meta=record['metadata']
            if sha256(path)!=record['source_sha256']:raise ValueError('Uploaded MAT integrity mismatch')
            target=self.root/(dataset+'.full-v2.working.json.gz')
        with self.lock:
            if not target.exists():
                result=package(dataset,path,meta,include_references=dataset in DATASETS)
                content=json.dumps(result,separators=(',',':'),allow_nan=False).encode()
                tmp=target.with_suffix('.tmp');tmp.write_bytes(gzip.compress(content,compresslevel=3));tmp.replace(target)
        return target.read_bytes()

    def upload(self,stream,length,name,recording_id):
        if not 0<length<=MAX_BYTES:raise ValueError('Choose a MAT file up to 256 MiB')
        if not isinstance(name,str) or len(name)>150 or not name.lower().endswith('.mat'):raise ValueError('Choose a MAT file')
        if not isinstance(recording_id,str) or not 1<=len(recording_id.strip())<=120:raise ValueError('Enter the recording/session ID')
        if not self.lock.acquire(blocking=False):raise ValueError('Another dataset conversion is running; retry shortly')
        tmp=None
        try:
            fd,name_tmp=tempfile.mkstemp(prefix='.incoming-',suffix='.mat',dir=self.root);os.close(fd);tmp=Path(name_tmp)
            remaining=length
            with tmp.open('wb') as f:
                while remaining:
                    chunk=stream.read(min(1024*1024,remaining))
                    if not chunk:raise ValueError('Incomplete upload')
                    f.write(chunk);remaining-=len(chunk)
            digest=sha256(tmp)
            # Never bypass protected recording policy by renaming a file.
            protocol=json.loads((ROOT/'configs/curation/protocol_v1.json').read_text())['scope']
            manifest=json.loads((ROOT/'output/real_split_manifest_20260903/manifest.json').read_text())['datasets']
            for row in manifest:
                if row['mat_sha256']==digest:
                    block=row['dataset_id'].rsplit('_CH',1)[0]
                    if block in protocol['reserved_after_prior_exposure']+protocol['quarantined_blocks']:
                        raise ValueError('This MAT belongs to a protected recording; it is not a new unseen dataset')
                    if row['dataset_id'] in DATASETS:return dict(dataset_id=row['dataset_id'],reused=True)
            dataset='upload-'+digest[:24]
            meta=dict(original_filename=Path(name).name,recording_id=recording_id.strip(),
                prior_exposure='unknown; user-supplied file',independence='not independently verified')
            target=self.root/(dataset+'.full-v2.working.json.gz');converted=target.with_suffix('.converting')
            env=os.environ.copy();env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
            try:
                completed=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--convert',str(tmp),'--dataset',dataset,'--output',str(converted),'--metadata',json.dumps(meta)],capture_output=True,text=True,timeout=120,env=env)
                if completed.returncode:
                    try:detail=json.loads(completed.stdout).get('error','MAT conversion failed')
                    except ValueError:detail='MAT conversion failed or exceeded its resource limit'
                    raise ValueError(detail)
                info=json.loads(completed.stdout);converted.replace(target)
            except subprocess.TimeoutExpired as exc:raise ValueError('MAT conversion exceeded 120 seconds') from exc
            finally:converted.unlink(missing_ok=True)
            destination=self.root/(dataset+'.mat');tmp.replace(destination)
            record=dict(dataset_id=dataset,n_spikes=info['n_spikes'],source_sha256=digest,metadata=meta)
            (self.root/(dataset+'.meta.json')).write_text(json.dumps(record))
            return record
        finally:
            if tmp is not None:tmp.unlink(missing_ok=True)
            self.lock.release()

    def import_path(self,name,recording_id):
        if not isinstance(name,str) or len(name)>1000:raise ValueError('Enter a MAT path inside this project')
        path=Path(name);path=(path if path.is_absolute() else ROOT/path).resolve()
        if not path.is_relative_to(ROOT) or path.suffix.lower()!='.mat' or any(part.startswith('.') or part in ('Resume','spike-sorting-private-backup-20261019') for part in path.relative_to(ROOT).parts):
            raise ValueError('Use a regular MAT file inside the project data/upload folders')
        scope=json.loads((ROOT/'configs/curation/protocol_v1.json').read_text())['scope']
        if any(block in str(path.relative_to(ROOT)) for block in scope['reserved_after_prior_exposure']+scope['quarantined_blocks']):
            raise ValueError('This path belongs to a protected recording, not a new unseen input')
        size=path.stat().st_size
        if not 0<size<=MAX_BYTES:raise ValueError('Choose a MAT file up to 256 MiB')
        with path.open('rb') as stream:return self.upload(stream,size,path.name,recording_id)


if __name__=='__main__':
    import argparse
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(2_500_000_000,2_500_000_000))
    resource.setrlimit(resource.RLIMIT_CPU,(90,90))
    parser=argparse.ArgumentParser();parser.add_argument('--convert',type=Path,required=True);parser.add_argument('--dataset',required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--metadata',required=True)
    args=parser.parse_args()
    try:
        data=package(args.dataset,args.convert,json.loads(args.metadata))
        args.output.write_bytes(gzip.compress(json.dumps(data,separators=(',',':'),allow_nan=False).encode(),compresslevel=3))
        print(json.dumps(dict(n_spikes=data['n_spikes'])))
    except Exception as exc:
        print(json.dumps(dict(error=str(exc)[:500] or 'Input conversion failed')));sys.exit(1)
