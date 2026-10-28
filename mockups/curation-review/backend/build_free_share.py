"""Build a standalone free Firebase Hosting snapshot; no backend or model calls.

Only approved derived evidence is public. Raw MAT/NPZ, databases, credentials,
workbooks, private uploads and cloud service code never enter the output.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import threading

import numpy as np
from dataset_inputs import DatasetManager, ALLOWED
from import_jacob import sha256
from jacob_reasons import DEFAULT_SEED, load_seed
from review_analysis import AnalysisService, population_statistics, compare_populations, interval_statistics
from review_correlograms import CorrelogramCache, snapshot as correlogram_snapshot

ROOT=Path(__file__).resolve().parents[3]
PROJECT='spikeagent-review-20261023'
STATIC=ROOT/'mockups/curation-review/dist'


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        stream.write('\n')


def copy_verified(source,target,expected):
    source=source.resolve(strict=True)
    if not source.is_relative_to(ROOT) or sha256(source)!=expected:raise ValueError('Source integrity mismatch')
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if sha256(target)!=expected:raise ValueError('Conflicting public asset')
        return
    shutil.copyfile(source,target)
    if sha256(target)!=expected:raise ValueError('Copy integrity mismatch')


def copy_full_waveform_images(public):
    # Explicit user consent received 2026-10-27 for the existing five channels'
    # full-member PNGs and necessary cluster/state/rendering metadata.
    cache=ROOT/'output/curation_review_local/review_waveforms_full_v1'
    if not (cache/'BUILD_REPORT.json').is_file():
        raise ValueError('Prepare the offline all-waveform images with build_pair_waveforms.py first')
    states=list(cache.glob('*--*.json'));needed={};values=[]
    atom_fields={'n_waveforms','raw_low','raw_high','image_low','image_high','width','height','all_members','mean','sd_band','url','sha256'}
    for state in states:
        value=json.loads(state.read_text())
        if value.get('schema')!='review-all-waveform-layers-v1' or not value.get('all_members') or value.get('random_sampling'):
            raise ValueError('Unexpected full-waveform evidence')
        for unit in value['units'].values():
            if sum(value['atoms'][i]['n_waveforms'] for i in unit['atoms'])!=unit['n_spikes']:raise ValueError('Incomplete waveform count')
        for atom in value['atoms'].values():
            path=(cache/atom['url'].removeprefix('/review-waveforms/')).resolve()
            if not path.is_relative_to(cache) or path.suffix!='.png' or sha256(path)!=atom['sha256']:raise ValueError('Waveform image integrity mismatch')
            needed[path]=atom['sha256']
        value['atoms']={key:{k:v for k,v in atom.items() if k in atom_fields} for key,atom in value['atoms'].items()}
        values.append((state.name,value))
    target=public/'review-waveforms';(target/'atoms').mkdir(parents=True,exist_ok=True)
    for name,value in values:(target/name).write_text(json.dumps(value,separators=(',',':'))+'\n')
    for path in needed:shutil.copyfile(path,target/'atoms'/path.name)
    return dict(states=len(states),images=len(needed),raw_waveform_arrays=False)


def copy_pca_images(public):
    cache=ROOT/'output/curation_review_local/review_pca_v1';target=public/'review-pca'
    if not (cache/'BUILD_REPORT.json').is_file():raise ValueError('Prepare PCA image evidence first with review_pca.py')
    needed={};states=list(cache.glob('*--*.json'));(target/'atoms').mkdir(parents=True,exist_ok=True)
    if len(states)!=94:raise ValueError('PCA snapshot scope changed')
    for path in states:
        value=json.loads(path.read_text())
        if value.get('schema')!='review-pca-layers-v1' or value['dataset_id'] not in ALLOWED or not value.get('all_members') or value.get('sampled') or value.get('normalized'):raise ValueError('Invalid public PCA evidence')
        for unit in value['units'].values():
            if sum(value['atoms'][i]['n_spikes'] for i in unit['atoms'])!=unit['n_spikes']:raise ValueError('PCA membership mismatch')
        for atom in value['atoms'].values():
            for view in atom['views']:
                source=(cache/view['url'].removeprefix('/review-pca/')).resolve()
                if not source.is_relative_to(cache) or source.suffix!='.png' or sha256(source)!=view['sha256']:raise ValueError('PCA image integrity mismatch')
                needed[source]=view['sha256']
        write(target/path.name,value)
    for source in needed:shutil.copyfile(source,target/'atoms'/source.name)
    return dict(states=len(states),images=len(needed),all_members=True,raw_features_published=False)


def build(destination):
    destination=destination.resolve()
    if not destination.is_relative_to(ROOT/'output/firebase_free_share') or destination==ROOT/'output/firebase_free_share':
        raise ValueError('Use a new child under output/firebase_free_share')
    if destination.exists():raise ValueError('Destination exists; select a new build directory')
    manager=DatasetManager.__new__(DatasetManager)
    manager.lock=threading.RLock();manager.bundles={};manager.paths={}
    registry=json.loads((ROOT/'output/curation_review_local/manager/registry.json').read_text())
    for row in registry:manager._load(row['id'],Path(row['path']))
    active=manager.summaries()
    visible={item['id'] for item in active}
    manager.bundles={key:value for key,value in manager.bundles.items() if key in visible}
    manager.paths={key:value for key,value in manager.paths.items() if key in visible}
    study=any(b.get('study_id') for b in manager.bundles.values())
    if study:
        comparison=[b for b in manager.bundles.values() if b.get('study_id')]
        expected_initial={'cM2-e004_004-006_CH3':25,'cM2-e004_004-006_CH31':17,
                          'cM2-e004_011-015_CH20':12,'cM2-e008_021-028_CH30':18,'cM2-e004_011-015_CH17':10}
        initial=[b for b in manager.bundles.values() if b['source_kind']=='initial']
        if (len(comparison)!=2 or {b['study_agent_id'] for b in comparison}!={'Agent_1','Agent_2'}
                or any(len(b['cases'])!=90 for b in comparison) or len(manager.bundles)!=7
                or len(initial)!=len(expected_initial)
                or {b['dataset_id']:len(b['cases']) for b in initial}!=expected_initial
                or len(manager.all_cases())!=262):
            raise ValueError('Review the changed matched-agent publication scope')
    elif len(manager.bundles)!=6 or len(manager.all_cases())!=166:
        raise ValueError('Review the changed publication scope before building')
    seed=load_seed(DEFAULT_SEED)
    destination.mkdir(parents=True,exist_ok=False)
    public=destination/'public';public.mkdir()
    for name in ('working-import.js','review-quality.js','review-pca.js','pca-core.js','index.html','style.css','review-working.js','queue-gallery.js','review-correlograms.js','review-reasons.js','review-core.js','app.js','inputs.js','analysis.js','agent-study.js','static-share.js','reviewer-profiles.js','review-audit.js','closed-agent.js','curation.html','curation.css','curation-core.js','curation-ui.js','activity-core.js','activity-ui.js','activity.css','waveform-worker.js','waveform-view.js','curation-reasons.js','curation-workspace.js'):
        shutil.copyfile(STATIC/name,public/name)
    page=(public/'index.html').read_text()
    page=page.replace('<script src="cloud-auth.js?v=1"></script>','')
    page=page.replace('<script src="app.js','<script src="static-share.js?v=6"></script>\n  <script src="app.js',1)
    page=page.replace('Browse shared library','Browse reason library')
    page=page.replace('The lower-left Reviewer button selects your identity. To continue your review history on another computer, copy this reviewer key and use it there. This local SSH workspace has no account login.','The lower-left Reviewer button selects browser-local identities. Export and restore feedback JSON to continue on another computer. Names and keys are not account logins.')
    if 'cloud-auth.js' in page or 'static-share.js' not in page:raise ValueError('Unexpected frontend script layout')
    (public/'index.html').write_text(page)
    working=(public/'curation.html').read_text().replace('name="curation-mode" content="local"','name="curation-mode" content="browser"').replace('<script src="curation-ui.js','<script src="static-share.js?v=6"></script><script src="curation-ui.js',1)
    (public/'curation.html').write_text(working)
    # Publication-specific explanations; leave the Runpod edition's text intact.
    app=(public/'app.js').read_text()
    app=app.replace('Reviews, revisions, reasons and reviewer names are saved on this server.','Reviews, revisions, new reasons and reviewer names stay in this browser. Export JSON to share or transfer feedback.')
    app=app.replace('Use Data & inputs to upload/import approved MAT files, select views, manage reason files or restore feedback.','Use Data & inputs to select published views, manage browser-local reason files or restore a feedback export. MAT uploads are available on the Runpod version.')
    (public/'app.js').write_text(app)
    inputs=(public/'inputs.js').read_text().replace('Reviewer name saved on server.','Reviewer name saved in this browser.')
    (public/'inputs.js').write_text(inputs)
    analysis=(public/'analysis.js').read_text().replace('Calculating from all member spikes in the verified checkpoint…','Loading precomputed statistics for this verified checkpoint…')
    (public/'analysis.js').write_text(analysis)
    full_waveform_images=copy_full_waveform_images(public)
    pca_images=copy_pca_images(public)
    analyses=AnalysisService(manager);states={};count_pairs=0;correlation_cache=CorrelogramCache()
    summaries=[]
    for identity,bundle in manager.bundles.items():
        dataset=bundle['dataset_id']
        if dataset not in ALLOWED:raise ValueError('Unapproved dataset')
        root=manager.paths[identity]
        exported=json.loads(json.dumps(bundle));exported['id']=identity
        for name,checksum in bundle['assets'].items():
            if name!=checksum+'.png':raise ValueError('Unexpected image naming')
            copy_verified(root/'assets'/name,public/'share/assets'/name,checksum)
        for row in bundle['evidence_files'].values():
            if Path(row['file']).name!=row['file']:raise ValueError('Invalid evidence path')
            copy_verified(root/'evidence'/row['file'],public/'share/evidence'/row['file'],row['sha256'])
        for original,c in zip(bundle['cases'],exported['cases']):
            for image in c.get('images',[]):image['url']='/share/assets/'+image['sha256']+'.png'
            evidence=bundle['evidence_files'][c['evidence_id']]
            c['evidence_url']='/share/evidence/'+evidence['file']
            c['static_analysis_url']='/share/analysis/'+dataset+'--'+c['state_sha256']+'.json'
            state_key=(dataset,c['state_sha256'])
            if state_key not in states:
                inputs=analyses._inputs(original,bundle)
                assigns=analyses._assignments(original,bundle,inputs)
                fs,times,waves=inputs[:3]
                populations={};member_times={}
                for unit in c['units']:
                    cid=unit['cluster_id'];ids=np.flatnonzero(assigns==cid)
                    if len(ids)!=unit['spikes'] or not len(ids):raise ValueError('Cluster membership mismatch')
                    populations[cid]=population_statistics(waves[ids],times[ids],fs,cid)
                    member_times[cid]=times[ids]
                states[state_key]=dict(data=dict(dataset_id=dataset,state_sha256=c['state_sha256'],sampling_rate_hz=fs,
                    units=populations,pairs={},expert_fields_accessed=False,new_model_calls=0),times=member_times,url=c['static_analysis_url'],mat_sha256=c['mat_sha256'],correlogram_pairs=set())
            state=states[state_key];source=next(u['cluster_id'] for u in c['units'] if u['id']==c['source'])
            for unit in c['units']:
                target=unit['cluster_id']
                if target==source:continue
                state['correlogram_pairs'].add((source,target))
                pair_key=','.join(map(str,sorted([source,target])))
                if pair_key not in state['data']['pairs']:
                    state['data']['pairs'][pair_key]=dict(pair=compare_populations(state['data']['units'][source],state['data']['units'][target]),
                        merged_isi=interval_statistics(np.concatenate([state['times'][source],state['times'][target]])))
                    count_pairs+=1
        url='/share/bundles/'+identity+'.json';write(public/url.lstrip('/'),exported)
        summaries.append(dict(id=identity,dataset_id=dataset,source_kind=bundle['source_kind'],run_status=bundle['run_status'],
            study_id=bundle.get('study_id'),study_agent_id=bundle.get('study_agent_id'),optional_reference=bundle.get('optional_reference',False),
            cases=len(bundle['cases']),n_spikes=bundle['n_spikes'],sampling_rate_hz=bundle['sampling_rate_hz'],url=url))
        print(json.dumps(dict(bundle=identity,cases=len(bundle['cases']),status='prepared')),flush=True)
    for (dataset,state_sha),state in states.items():
        write(public/state['url'].lstrip('/'),state['data'])
        write(public/'share/correlograms'/(dataset+'--'+state_sha+'.json'),correlogram_snapshot(dataset,state_sha,state['mat_sha256'],state['times'],state['correlogram_pairs'],correlation_cache))
    write(public/'share/reasons.json',seed)
    from working_data import WorkingInputs
    working_inputs=WorkingInputs(ROOT/'output/curation_review_local')
    references={dataset:working_inputs.references(dataset) for dataset in ALLOWED}
    write(public/'share/jacob-references.json',references)
    write(public/'share/manifest.json',dict(schema='spikeagent-free-static-v1',project_id=PROJECT,
        created_at=datetime.now(timezone.utc).isoformat(),bundles=summaries,storage_mode='browser',raw_inputs_published=False))
    write(destination/'firebase.json',dict(hosting=dict(site=PROJECT,public='public',ignore=['firebase.json','**/.*','**/node_modules/**'],
        headers=[dict(source='**',headers=[dict(key='X-Content-Type-Options',value='nosniff'),dict(key='Referrer-Policy',value='same-origin')]),
                 dict(source='/share/**',headers=[dict(key='Cache-Control',value='public, max-age=3600')]),
                 dict(source='**/*.@(html|js|css)',headers=[dict(key='Cache-Control',value='public, max-age=300')])])))
    files=[p for p in public.rglob('*') if p.is_file()]
    if any(p.suffix.casefold() not in {'.html','.js','.css','.json','.png'} for p in files):raise ValueError('Unexpected public file type')
    summary=dict(project_id=PROJECT,mode='Spark Hosting only',cases=len(manager.all_cases()),bundles=len(manager.bundles),jacob_reasons=len(seed['reasons']),
        anonymous_agents=['Agent_1','Agent_2'] if study else [],optional_live_agent='Agent_3',live_gateway_configured=False,
        states=len(states),precomputed_candidate_pairs=count_pairs,files=len(files),bytes=sum(p.stat().st_size for p in files),
        raw_mat_npz_published=False,feedback_published=False,cloud_services_deployed=False,
        primary_review_pca=pca_images,primary_review_full_waveforms=full_waveform_images,clusters_navigation=False,browser_working_curation=True,working_packages_published=False,workflow='context-v2',agent_expanded_by_default=True,
        workspace_layout='existing-review-left-thumbnails',queue_thumbnail_columns=2,shift_click_comparison=True,primary_review_correlograms=True,generalized_review_reasons=True,private_working_in_primary_page=True,private_data_required_for_execution=True,snapshot_annotation_mode_preserved=True,redundant_gallery_tabs=False,full_member_waveform_support=True,
        operation_preview_confirmation=True,generalized_working_reasons=True,
        original_pair_colors_in_merge_preview=True,isolated_member_waveform_panels=True,shared_amplitude_axes=True,
        default_waveform_view='original-review-single-pair-full-overlay',full_single_cluster_waveforms=True,
        waveform_sample_toggle=True,sample_method='up to 24 chronologically spaced real members per cluster',remember_waveform_coverage=True,all_members_pageable=False,random_waveform_sampling=False,full_overlay_optional=False,
        all_spike_correlograms=True,activity_history_and_revisions=True,
        jacob_reference_edits=sum(map(len,references.values())),new_model_calls=0)
    write(destination/'BUILD_REPORT.json',summary)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    print(json.dumps(build(parser.parse_args().output)),flush=True)
