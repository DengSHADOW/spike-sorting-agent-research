"""Offline anonymous review study from three genuine CH30 prediction archives.

Reconstructs expert pre-action states in memory, verifies every recorded state
hash, and writes NEW review bundles. No model/provider/controller runner imports,
requests, original-data edits or terminal-label reads. The identity key stays in
an ignored private file, outside published bundles.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from datetime import datetime,timezone

import numpy as np
from import_jacob import ROOT,arrays_hash,scoped_row,load_inputs,state_units,write_json,sha256
sys.path.insert(0,str(ROOT))
from src.cluster.manager import ClusterManager

STUDY='anonymous-agents-v1'
DATASET='cM2-e008_021-028_CH30'
REFERENCE=ROOT/'output/real_manifest_action_dataset_20260914/datasets'/DATASET/'samples.jsonl'
QROOT=ROOT/'zero-shot baseline/runs/openrouter_qwen27b_tuned4000_20261013'
GROOT=ROOT/'output/runpod_collected/gemma4_e4b_ch30_action_v2_full90_h100_20260921'
CROOT=ROOT/'zero-shot baseline/runs/minimal_opus55_high_dual_20261012_v3_full'
PROTOCOL_NOTE='Matched recording and pre-action state; historical prompts, image rendering and response settings differ. Reviewer evidence is reconstructed, not a verified replay of each model input. No controlled performance ranking.'


def read(path):return json.loads(path.read_text())
def lines(path):return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def assignment_hash(assigns):
    a=np.ascontiguousarray(assigns)
    return hashlib.sha256(str(a.dtype).encode('ascii')+str(a.shape).encode('ascii')+a.tobytes()).hexdigest()


def parse_reference(text):
    parts=text.split()
    if len(parts)==2 and parts[0].lower()=='s':return 'SPLIT',int(parts[1]),None
    if len(parts)==3 and parts[0].lower()=='m':
        target,source=map(int,parts[1:]);return ('DISCARD' if target==0 else 'MERGE'),source,None if target==0 else target
    raise ValueError('Unsupported reference action')


def indexed(rows,field):
    selected=[r for r in rows if r.get('dataset_id')==DATASET or r.get(field,'').startswith(DATASET+'_')]
    result={r[field]:r for r in selected}
    if len(result)!=90 or len(result)!=len(selected):raise ValueError('Expected exactly 90 unique CH30 predictions')
    return result


def predictions():
    qm=read(QROOT/'manifest.json')
    if qm['model']!='qwen/qwen3.5-27b':raise ValueError('Unexpected primary model archive')
    q=indexed(lines(QROOT/'attempt_003/stepwise_progress.jsonl'),'job_id')
    qrequests={r['job_id']:r['sha256'] for r in qm['stepwise_requests']}
    collection=read(GROOT/'collection_manifest.json')
    for name in ['artifacts/detail_predictions.csv','artifacts/run_manifest.json']:
        record=next(r for r in collection['artifacts'] if r['path']==name)
        if sha256(GROOT/name)!=record['sha256']:raise ValueError('Collected archive integrity mismatch')
    gm=read(GROOT/'artifacts/run_manifest.json')
    if gm['model']!='google/gemma-4-E4B-it' or gm['n_samples']!=90:raise ValueError('Unexpected second model archive')
    with (GROOT/'artifacts/detail_predictions.csv').open() as stream:g=indexed(list(csv.DictReader(stream)),'id')
    cm=read(CROOT/'manifest.json')
    if cm['model']!='claude-opus-5-5':raise ValueError('Unexpected optional reference archive')
    closed=indexed(lines(CROOT/'stepwise_predictions.jsonl'),'job_id')
    closed_jobs={r['job_id']:r for r in cm['stepwise']['jobs'] if r['dataset_id']==DATASET}
    output={slot:{} for slot in ['Agent_1','Agent_2','Agent_3']}
    for identity,record in q.items():
        output['Agent_1'][identity]=dict(action=record['action'],rationale=record.get('rationale'),phase=record['phase'],request_sha256=qrequests[identity])
    for identity,record in g.items():
        raw=json.loads(record['raw_response'])
        if raw['action']!=record['pred_action']:raise ValueError('Stored prediction differs from raw response')
        output['Agent_2'][identity]=dict(action=raw['action'],rationale=raw.get('rationale'),phase='phase2' if record['stage']=='merge' else 'phase1',request_sha256=None)
    for identity,record in closed.items():
        if record['request_sha256']!=closed_jobs[identity]['request_sha256']:raise ValueError('Reference request hash mismatch')
        output['Agent_3'][identity]=dict(action=record['action'],rationale=record.get('rationale'),phase=record['phase'],request_sha256=record['request_sha256'])
    private=dict(study_id=STUDY,agents={
        'Agent_1':dict(model=qm['model'],manifest=str((QROOT/'manifest.json').relative_to(ROOT)),manifest_sha256=sha256(QROOT/'manifest.json'),predictions_sha256=sha256(QROOT/'attempt_003/stepwise_progress.jsonl')),
        'Agent_2':dict(model=gm['model'],manifest=str((GROOT/'artifacts/run_manifest.json').relative_to(ROOT)),manifest_sha256=sha256(GROOT/'artifacts/run_manifest.json'),predictions_sha256=sha256(GROOT/'artifacts/detail_predictions.csv')),
        'Agent_3':dict(model=cm['model'],manifest=str((CROOT/'manifest.json').relative_to(ROOT)),manifest_sha256=sha256(CROOT/'manifest.json'),predictions_sha256=sha256(CROOT/'stepwise_predictions.jsonl'))})
    return output,private,closed_jobs


def build(destination):
    destination=destination.resolve()
    allowed=(ROOT/'output/curation_review_local').resolve()
    if not destination.is_relative_to(allowed) or destination==allowed or destination.exists():raise ValueError('Choose a NEW study child under output/curation_review_local')
    rows=lines(REFERENCE)
    if len(rows)!=90 or any(r['dataset_id']!=DATASET for r in rows):raise ValueError('Invalid reference scope')
    pred,private,jobs=predictions()
    row,path=scoped_row(DATASET);inputs=load_inputs(path)
    fs,times,waves,over,assigns,tree=inputs
    manager=ClusterManager(assigns,over,tree,times,waves)
    if len(times)!=row['n_spikes']:raise ValueError('Input count mismatch')
    destination.mkdir(parents=True,exist_ok=False)
    shared=destination/'shared'
    for folder in ['states','evidence']:(shared/folder).mkdir(parents=True)
    cases={slot:[] for slot in pred};evidence={};checkpoints={};cache={}
    for number,reference in enumerate(rows,1):
        identity=reference['id'];action,source,target=parse_reference(reference['expert_action_raw'])
        phase='phase2' if action=='MERGE' else 'phase1'
        if (reference['trajectory_step']!=number or reference['mat_source']['sha256']!=row['mat_sha256']
                or assignment_hash(manager.assigns)!=reference['state_before']['assigns_sha256']):raise ValueError('Reference pre-state hash mismatch')
        annotation=reference['annotation_source']
        annotation_path=(ROOT/annotation['path']).resolve()
        if not annotation_path.is_relative_to(ROOT) or sha256(annotation_path)!=annotation['sha256']:raise ValueError('Reference provenance mismatch')
        job=jobs[identity]
        if (job['source'],job['target'],job['phase'],job['state_before_sha256'])!=(source,target,phase,reference['state_before']['assigns_sha256']):raise ValueError('Historical agent objects/state differ from reference')
        state=arrays_hash(manager.assigns,manager.hierarchy_tree)
        units,details=state_units(manager.assigns,manager.hierarchy_tree,over,waves,times,fs,row['channel'],cache)
        sizes={u['cluster_id']:u['spikes'] for u in units}
        if sizes.get(source)!=reference['state_before']['source_n_spikes']:raise ValueError('Reference source count mismatch')
        if state not in evidence:
            np.savez_compressed(shared/'states'/(state+'.npz'),assigns=manager.assigns,tree=manager.hierarchy_tree)
            checkpoints[state]=dict(file=state+'.npz',sha256=sha256(shared/'states'/(state+'.npz')))
            write_json(shared/'evidence'/(state+'.json'),dict(units=details,sampling_rate_hz=fs,waveform_units='source units',time_units='seconds',synthetic=False))
            evidence[state]=dict(file=state+'.json',sha256=sha256(shared/'evidence'/(state+'.json')))
        unit=next(u for u in units if u['cluster_id']==source)
        comparison=DATASET+'--reference-'+str(number).zfill(6)
        for slot,records in pred.items():
            prediction=records[identity]
            if prediction['phase']!=phase or prediction['action'] not in (['KEEP','SPLIT','DISCARD'] if phase=='phase1' else ['MERGE','NOT_MERGE','DISCARD']):raise ValueError('Prediction phase/action mismatch')
            rationale=prediction['rationale']
            if rationale and re.search(r'qwen|gemma|claude|anthropic|openai',rationale,re.I):raise ValueError('Identity-bearing rationale requires explicit review before publishing')
            response_hash=hashlib.sha256(json.dumps(prediction,sort_keys=True).encode()).hexdigest()
            cid=comparison+'--'+slot.lower()
            cases[slot].append(dict(id=cid,synthetic=False,review_mode='blind_comparison',study_id=STUDY,comparison_case_id=comparison,
                study_agent_id=slot,rationale_available=bool(rationale),response_sha256=response_hash,
                evidence_scope='reconstructed-reviewer-evidence',comparison_protocol_note=PROTOCOL_NOTE,
                dataset_id=DATASET,mat_path=row['mat_path'],channel=row['channel'],source=unit['id'],title=f'Case {number:02d} · Cluster {source}',
                step_id='reference_'+str(number).zfill(6),step_number=number,phase='Pair comparison' if target else 'Cluster review',phase_id=phase,
                pair=[unit['id'],f'U-{target:03d}'] if target else None,ai=prediction['action'],rationale=rationale,
                summary='Historical response for this matched pre-action state. '+('Inspect the retained explanation.' if rationale else 'No rationale was retained for this action-only response.'),
                risk='Matched historical case',brief=f'Case {number:02d} · {phase} · {prediction["action"]}',spikes=unit['spikes'],split=unit['split'],units=units,
                run_id='study:'+STUDY+':'+slot,state_id=state,state_sha256=state,
                request_sha256=prediction['request_sha256'],request_hash_status='Historical request hash recorded; model input images not reverified for this study.' if prediction['request_sha256'] else 'Original request payload/hash not retained in the collected archive.',
                image_sha256={},images=[],agent_id=slot,agent_kind='historical-model-response',
                metrics=dict(sampling_rate_hz=fs,cluster_id=source,n_spikes=unit['spikes']),displayed_trace_count=min(24,unit['spikes']),
                checkpoint_sha256=checkpoints[state]['sha256'],mat_sha256=row['mat_sha256'],evidence_id=state,evidence_url='/api/evidence/'+cid,waveform_units='source units'))
        if action=='SPLIT':manager.split_last_merge(source)
        elif action=='DISCARD':manager.discard_cluster(source)
        else:manager.merge_clusters([source,target],target_id=target)
        if assignment_hash(manager.assigns)!=reference['state_after']['assigns_sha256']:raise ValueError('Reference post-state hash mismatch')
        manager.history.clear();manager.history_index=-1
        if number%10==0:print(f'Verified matched states {number}/90; no model calls',flush=True)
    registry=[]
    for slot,items in cases.items():
        folder=destination/slot.lower();folder.mkdir()
        for name in ['states','evidence']:shutil.copytree(shared/name,folder/name)
        (folder/'assets').mkdir()
        provenance=dict(mat_path=row['mat_path'],mat_sha256=row['mat_sha256'],actor_input_sha256=arrays_hash(over,times,waves),
            reference_samples_sha256=sha256(REFERENCE),reference_actions_used_for_state_reconstruction=True,expert_terminal_fields_accessed=False,
            matched_expert_answers_in_case_payload=False,new_model_calls=0,note=PROTOCOL_NOTE)
        bundle=dict(schema='spikeagent-jacob-bundle-v1',dataset_id=DATASET,source_kind='comparison',run_status='historical_fixed_states',
            study_id=STUDY,study_agent_id=slot,optional_reference=slot=='Agent_3',created_at=datetime.now(timezone.utc).isoformat(),synthetic=False,
            n_spikes=len(times),sampling_rate_hz=fs,cases=items,assets={},evidence_files=evidence,study_checkpoints=checkpoints,provenance=provenance)
        write_json(folder/'dataset.json',bundle)
        registry.append(dict(id=STUDY+'-'+slot.lower(),path=str(folder)))
    write_json(destination/'private_agent_mapping.json',private)
    write_json(destination/'registry_entries.json',registry)
    write_json(destination/'IMPORT_REPORT.json',dict(study_id=STUDY,dataset_id=DATASET,matched_cases=90,agents=3,public_review_cases=270,
        new_model_calls=0,original_files_modified=False,protocols_identical=False,rationale_available=dict(Agent_1=True,Agent_2=False,Agent_3=True)))
    return registry


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    print(json.dumps(build(parser.parse_args().output)),flush=True)
