/* Free Hosting edition: immutable public evidence + browser-owned feedback.
 * This adapter makes no server writes and never uploads feedback or raw inputs.
 */
(function(){
  'use strict';
  const schema='spikeagent-human-review-v3',storagePrefix='spikeagent-static-feedback-v1:';
  const clone=x=>JSON.parse(JSON.stringify(x));
  const sorted=x=>Array.isArray(x)?x.map(sorted):x&&typeof x==='object'?Object.fromEntries(Object.keys(x).sort().map(k=>[k,sorted(x[k])])):x;
  const equal=(a,b)=>JSON.stringify(sorted(a))===JSON.stringify(sorted(b));
  let initialization;
  const cached=new Map();
  async function json(path){
    if(!cached.has(path))cached.set(path,(path==='/share/manifest.json'?fetch(path,{cache:'no-cache'}):fetch(path)).then(async response=>{if(!response.ok)throw new Error('Snapshot file unavailable. Reload or retry shortly.');return response.json();}).catch(error=>{cached.delete(path);throw error;}));
    return clone(await cached.get(path));
  }
  async function initialize(){
    if(!initialization)initialization=(async()=>{
      const manifest=await json('/share/manifest.json'),seed=await json('/share/reasons.json');
      const bundles=await Promise.all(manifest.bundles.map(b=>json(b.url)));
      const cases=new Map(bundles.flatMap(b=>b.cases.map(c=>[c.id,c])));
      return {manifest,seed,bundles,cases};
    })().catch(error=>{initialization=null;throw error;});
    return initialization;
  }
  function load(reviewer){
    const value=localStorage.getItem(storagePrefix+reviewer);
    if(value){const data=JSON.parse(value);if(data.version!==1)throw new Error('Unsupported local feedback version. Export your browser data before clearing it.');return data;}
    return {version:1,reviews:{},revisions:{},events:[],operations:{},reasons:[],annotator:'',profile_updated_at:null};
  }
  function store(reviewer,data){
    // A failed quota/private-mode write must throw before reporting success.
    localStorage.setItem(storagePrefix+reviewer,JSON.stringify(data));
  }
  function library(data,seed){
    const originals=seed.reasons.map(r=>({key:ReviewCore.reasonKey(r.text),text:r.text,uses:0,created_at:seed.created_at||'',last_used_at:'',provenance:'jacob-original-workbook',sources:[{kind:'jacob',version:seed.version,references:r.sources}]}));
    const rows=new Map(originals.map(r=>[r.key,r]));
    for(const r of data.reasons){
      const original=rows.get(r.key),sources=original?[...original.sources,...r.sources.filter(s=>s.kind!=='jacob')]:r.sources.filter(s=>s.kind!=='jacob');
      rows.set(r.key,{...r,sources});
    }
    return [...rows.values()].sort((a,b)=>(b.uses||0)-(a.uses||0)||a.key.localeCompare(b.key));
  }
  function remember(data,text,provenance='browser-review-history',increment=false){
    if(typeof text!=='string'||text.length>2000)throw new Error('Reason must be text of up to 2000 characters.');
    text=ReviewCore.normalizeReason(text);if(!text)return false;
    const key=ReviewCore.reasonKey(text),timestamp=new Date().toISOString();
    let row=data.reasons.find(r=>r.key===key),added=!row;
    if(!row){row={key,text,provenance,uses:0,created_at:timestamp,last_used_at:timestamp,sources:[]};data.reasons.push(row);}
    const source={kind:increment?'reviewer':'user_imported',provenance};
    if(!row.sources.some(s=>equal(s,source)))row.sources.push(source);
    if(increment){row.uses++;row.last_used_at=timestamp;}
    return added;
  }
  function canonical(c,draft,context,reviewer,data,seed,allowLegacy=false){
    const result=ReviewCore.buildReview(c,draft,context,allowLegacy);
    if(String(draft.note||'').length>10000||String(draft.reason||'').length>2000||String(draft.positive||'').length>10000)throw new Error('Review text is too long.');
    const reason=library(data,seed).find(r=>r.key===ReviewCore.reasonKey(result.expert_reason));
    return {...result,mat_sha256:c.mat_sha256,checkpoint_sha256:c.checkpoint_sha256,
      request_hash_status:c.request_hash_status,waveform_units:'source units',reviewer_id:reviewer,
      storage_mode:'browser',source_snapshot:'firebase-static-share-v1',
      reason_sources:reason?.sources||(result.expert_reason?[{kind:'reviewer',provenance:'reviewer-entered-real-review'}]:[])};
  }
  function csv(text){
    const rows=[];let row=[],cell='',quoted=false;
    for(let i=0;i<text.length;i++){
      const char=text[i];
      if(char==='"'){if(quoted&&text[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;}
      else if(!quoted&&(char===','||char==='\n')){row.push(cell);cell='';if(char==='\n'){rows.push(row);row=[];}}
      else if(char!=='\r'||quoted)cell+=char;
    }
    if(quoted)throw new Error('CSV contains an unclosed quoted field.');
    if(cell||row.length){row.push(cell);rows.push(row);}
    const headers=rows.shift()||[],index=headers.findIndex(h=>['text','reason'].includes(h.trim()));
    if(index<0)throw new Error('CSV needs a text or reason column.');
    return rows.filter(r=>r.some(Boolean)).map(r=>r[index]||'');
  }
  function restore(payload,reviewer,data,context,preview){
    if(!payload||payload.schema!==schema||payload.synthetic!==false||!Array.isArray(payload.reviews)||payload.reviews.length>5000)throw new Error('Choose a real-data v3 feedback export.');
    const prepared=[],seen=new Set();
    for(const r of payload.reviews){
      const c=context.cases.get(r.case_id);
      if(!c||seen.has(r.case_id)||r.schema!==schema||r.synthetic!==false)throw new Error('Unknown, duplicate or unsupported backup case.');
      seen.add(r.case_id);
      for(const name of ['dataset_id','run_id','step_id','state_sha256','mat_sha256','request_sha256','image_sha256','ai_rationale','ai_action']){
        if(!equal(r[name],c[{ai_rationale:'rationale',ai_action:'ai'}[name]||name]))throw new Error('Backup provenance mismatch: '+name);
      }
      const ids=new Map(c.units.map(u=>[u.cluster_id,u.id])),source=c.units.find(u=>u.id===c.source).cluster_id;
      if(r.source_cluster!==source||r.phase!==c.phase_id||!equal(r.ai_pair,c.pair?c.pair.map(id=>c.units.find(u=>u.id===id).cluster_id):null))throw new Error('Backup source, phase or AI pair differs.');
      const mapIds=(items)=>{if(!Array.isArray(items)||items.some(i=>!Number.isInteger(i)||!ids.has(i)))throw new Error('Invalid backup evidence exposure.');return items.map(i=>ids.get(i));};
      const pairs=r.pair_comparisons||[];
      if(!Array.isArray(pairs))throw new Error('Invalid backup pair comparisons.');
      const draft={agree:r.expert_agree,action:r.action,target:ids.get(r.expert_merge_target)||'',reason:r.expert_reason||'',note:r.expert_note||'',positive:r.expert_positive_comment||'',disagreement_types:r.disagreement_types||[],confidence:r.expert_confidence??null,sufficiency:r.evidence_sufficiency||'unspecified',missing:r.missing_evidence||[],missing_note:r.missing_evidence_note||''};
      if(r.judgment_context!==undefined){draft.scope=r.judgment_context;if(draft.scope==='pair'){if(!Array.isArray(r.judgment_pair)||r.judgment_pair.length!==2||r.judgment_pair[0]!==source||!ids.has(r.judgment_pair[1]))throw Error('Invalid backup judgment pair');draft.target=ids.get(r.judgment_pair[1]);}}
      const exposure={annotator:r.annotator||'',viewed_cluster_ids:mapIds(r.viewed_cluster_ids||[]),visible_cluster_ids:mapIds(r.visible_cluster_set||[]),viewed_tabs:r.viewed_tabs||['detail'],rationale_viewed:r.rationale_viewed||false,jacob_reference_viewed:r.jacob_reference_viewed||false,pair_comparisons:pairs.map(mapIds),quality_evidence:r.quality_evidence||null};
      const rebuilt=canonical(c,draft,exposure,reviewer,data,context.seed,true);
      rebuilt.imported_from={reviewer_id:payload.reviewer_id,timestamp:r.timestamp};prepared.push(rebuilt);
    }
    const reasons=payload.reason_library||[];
    if(!Array.isArray(reasons)||reasons.length>5000)throw new Error('Invalid backup reason library.');
    const texts=reasons.map(r=>typeof r==='string'?r:r.text);
    if(texts.some(t=>typeof t!=='string'||t.length>2000))throw new Error('Invalid backup reason text.');
    const pending=prepared.filter(r=>!(r.case_id in data.revisions));let reasonsAdded=0;
    if(!preview){
      for(const r of pending){
        data.reviews[r.case_id]=r;data.revisions[r.case_id]=1;
        data.events.push({type:'review_imported',request_id:'import-'+crypto.randomUUID(),timestamp:new Date().toISOString(),case_id:r.case_id,revision:1,review:r});
        if(r.expert_reason)remember(data,r.expert_reason,'feedback-backup');
      }
      const existing=new Set(library(data,context.seed).map(r=>r.key));
      for(const text of texts){const key=ReviewCore.reasonKey(text);if(text.trim()&&!existing.has(key)){remember(data,text,'feedback-backup');existing.add(key);reasonsAdded++;}}
    }
    return {preview,valid:prepared.length,available_to_import:pending.length,added:preview?0:pending.length,skipped_existing:prepared.length-pending.length,reasons_added:reasonsAdded};
  }
  async function route(path,body,reviewer,context){
    const {manifest,seed,bundles,cases}=context,url=new URL(path,location.origin),name=url.pathname,data=load(reviewer);
    const allReasons=()=>library(data,seed);
    if(body===undefined){
      if(name.startsWith('/share/'))return json(name);
      if(name==='/api/status')return {status:'ready',static_share:true,csrf_token:'browser-only',synthetic:false,cases:cases.size,model_calls_enabled:false};
      if(name==='/api/datasets')return {bundles:manifest.bundles,inputs:[],max_upload_bytes:0};
      if(name==='/api/cases'){const bundle=bundles.find(b=>b.id===url.searchParams.get('bundle_id'));if(!bundle)throw new Error('Unknown snapshot view.');return clone(bundle);}
      if(name==='/api/curation/reference'){const rows=await json('/share/jacob-references.json');return {references:rows[url.searchParams.get('dataset_id')]||[]};}
      if(name==='/api/imports')return {jobs:[]};
      if(name==='/api/profile')return {reviewer_id:reviewer,annotator:data.annotator,updated_at:data.profile_updated_at};
      if(name==='/api/reviews')return {reviews:Object.fromEntries(Object.entries(data.reviews).map(([id,review])=>[id,{review,revision:data.revisions[id]}])),revisions:data.revisions};
      if(name==='/api/reasons'){const prefix=url.searchParams.get('prefix')||'',limit=Number(url.searchParams.get('limit')||20);return {reasons:ReviewCore.matchReasons(allReasons(),prefix).slice(0,Math.max(1,Math.min(limit,5000)))};}
      if(name==='/api/reasons/status')return {total:allReasons().length,jacob:{unique_reasons:seed.reasons.length,n_source_rows:seed.n_source_rows}};
      if(name==='/api/reasons/export')return {schema:'spikeagent-reason-library-v1',reasons:allReasons(),storage_mode:'browser'};
      if(name==='/api/export')return {schema,synthetic:false,exported_at:new Date().toISOString(),reviewer_id:reviewer,storage_mode:'browser',execution_status:'not_executed',reviews:Object.values(data.reviews),events:data.events,reason_library:allReasons()};
      if(name.startsWith('/api/correlograms/')){
        const c=cases.get(decodeURIComponent(name.slice('/api/correlograms/'.length)));if(!c)throw Error('Unknown case.');
        const cluster=url.searchParams.get('cluster')||c.source,target=url.searchParams.get('target'),unit=id=>c.units.find(u=>u.id===id);
        if(!unit(cluster)||(target&&(!unit(target)||target===cluster||cluster!==c.source)))throw Error('Choose active clusters in this saved state.');
        const snapshot=await json('/share/correlograms/'+c.dataset_id+'--'+c.state_sha256+'.json'),sourceId=unit(cluster).cluster_id,targetId=target?unit(target).cluster_id:null;
        if(snapshot.schema!=='review-correlograms-snapshot-v1'||snapshot.dataset_id!==c.dataset_id||snapshot.state_sha256!==c.state_sha256||snapshot.mat_sha256!==c.mat_sha256)throw Error('Correlogram snapshot mismatch.');
        const acg=[snapshot.acg[sourceId],...(target?[snapshot.acg[targetId]]:[])],ccg=target?snapshot.ccg[sourceId+','+targetId]:null;if(acg.some(v=>!v)||(target&&!ccg))throw Error('Correlogram is unavailable for this pair.');
        return {schema:'review-correlograms-v1',case_id:c.id,dataset_id:c.dataset_id,state_sha256:c.state_sha256,mat_sha256:c.mat_sha256,source_cluster:sourceId,target_cluster:targetId,acg,ccg,new_model_calls:0,execution_status:'not_executed'};
      }
      if(name.startsWith('/api/analysis/')){
        const c=cases.get(decodeURIComponent(name.slice('/api/analysis/'.length)));if(!c)throw new Error('Unknown case.');
        const cluster=url.searchParams.get('cluster')||c.source,target=url.searchParams.get('target'),unit=id=>c.units.find(u=>u.id===id);
        if(!unit(cluster)||(target&&(!unit(target)||target===cluster||cluster!==c.source)))throw new Error('Choose a valid candidate in this case.');
        const snapshot=await json(c.static_analysis_url),sourceId=unit(cluster).cluster_id,targetId=target?unit(target).cluster_id:null;
        const result={schema:'reviewer-analysis-v1',case_id:c.id,dataset_id:c.dataset_id,state_sha256:c.state_sha256,mat_sha256:c.mat_sha256,source_cluster:sourceId,target_cluster:targetId,sampling_rate_hz:snapshot.sampling_rate_hz,units:[snapshot.units[sourceId]],waveform_units:'source units',expert_fields_accessed:false,new_model_calls:0,execution_status:'not_executed',calculation_mode:'precomputed-static-snapshot'};
        if(target){const pair=snapshot.pairs[[sourceId,targetId].sort((a,b)=>a-b).join(',')];if(!pair)throw new Error('This candidate pair is absent from the published snapshot.');result.units.push(snapshot.units[targetId]);Object.assign(result,pair);}
        const bytes=new TextEncoder().encode(JSON.stringify(sorted(result)));
        result.analysis_sha256=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
        return result;
      }
      throw new Error('This operation is unavailable in the static sharing edition.');
    }
    let result;
    if(name==='/api/profile'){
      if(typeof body.annotator!=='string'||body.annotator.length>100)throw new Error('Use a reviewer name of up to 100 characters.');
      data.annotator=body.annotator.trim();data.profile_updated_at=new Date().toISOString();result={reviewer_id:reviewer,annotator:data.annotator,updated_at:data.profile_updated_at};
    }else if(['/api/reasons/import','/api/reasons/import-file'].includes(name)){
      let texts=body.reasons;
      if(name.endsWith('import-file')){
        if(typeof body.content!=='string'||body.content.length>4_000_000)throw new Error('Invalid reason file.');
        if(body.format==='json'){const value=JSON.parse(body.content);texts=Array.isArray(value)?value:value.reasons||value.reason_library;texts=texts?.map(r=>typeof r==='string'?r:r.text);}
        else if(body.format==='text')texts=body.content.split(/\r?\n/);
        else if(body.format==='csv')texts=csv(body.content);
        else throw new Error('Choose JSON, CSV or text reasons.');
      }
      if(!Array.isArray(texts)||texts.length>5000||texts.some(t=>typeof t!=='string'||t.length>2000))throw new Error('Import up to 5000 text reasons.');
      let added=0;const existing=new Set(allReasons().map(r=>r.key));
      for(const t of texts){const k=ReviewCore.reasonKey(t);if(t.trim()&&!existing.has(k)){remember(data,t);existing.add(k);added++;}}
      result={added,duplicates:texts.filter(t=>t.trim()).length-added};
    }else if(name==='/api/reviews/import'){
      const preview=body.preview??true;if(typeof preview!=='boolean')throw new Error('Invalid preview flag.');
      result=restore(body.payload,reviewer,data,context,preview);if(preview)return result;
    }else if(['/api/reviews','/api/reviews/undo'].includes(name)){
      const c=cases.get(body.case_id),undo=name.endsWith('/undo');if(!c)throw new Error('Unknown case.');
      if(!/^[A-Za-z0-9_-]{8,100}$/.test(body.request_id)||!Number.isInteger(body.expected_revision)||body.expected_revision<0)throw new Error('A request ID and expected revision are required.');
      if(data.operations[body.request_id]){
        const old=data.operations[body.request_id];if(old.case_id!==c.id||old.undo!==undo)throw new Error('Request ID belongs to another operation.');return old.result;
      }
      const revision=data.revisions[c.id]||0;
      if(revision!==body.expected_revision)throw new Error('Review changed in another tab. Reload before editing it again.');
      if(undo&&!data.reviews[c.id])throw new Error('No saved review to undo.');
      const review=undo?null:canonical(c,body.draft,body.context||{},reviewer,data,seed);
      result={review,revision:revision+1,case_id:c.id};data.revisions[c.id]=result.revision;
      if(undo)delete data.reviews[c.id];else data.reviews[c.id]=review;
      data.events.push({type:undo?'review_undone':'review_saved',request_id:body.request_id,timestamp:new Date().toISOString(),...result});
      data.operations[body.request_id]={case_id:c.id,undo,result};
      if(review?.expert_reason)remember(data,review.expert_reason,'reviewer-entered-real-review',true);
    }else throw new Error('Uploads and server imports are unavailable in the free static edition.');
    store(reviewer,data);return clone(result);
  }
  window.StaticReview={async api(path,body,reviewer){
    const context=await initialize();
    if(body!==undefined&&navigator.locks)return navigator.locks.request(storagePrefix+reviewer,()=>route(path,body,reviewer,context));
    return route(path,body,reviewer,context);
  }};
})();
