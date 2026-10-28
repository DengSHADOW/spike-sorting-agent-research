/* Read-only activity projection. Stored events, working states and labels are never rewritten. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.ActivityCore=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 const FIELDS=['judgment_context','judgment_pair','expert_agree','action','expert_merge_target','disagreement_types','expert_reason','expert_note','expert_positive_comment','expert_confidence','evidence_sufficiency','missing_evidence','missing_evidence_note'];
 const copy=x=>x==null?null:JSON.parse(JSON.stringify(x)),same=(a,b)=>JSON.stringify(a??null)===JSON.stringify(b??null);
 const date=x=>typeof x==='string'&&Number.isFinite(Date.parse(x))?x:'';
 const changes=(a,b,fields)=>fields.filter(k=>!same(a?.[k],b?.[k])).map(k=>({field:k,before:copy(a?.[k]),after:copy(b?.[k])}));
 const countChanges=(before,after)=>{if(!Array.isArray(before)||!Array.isArray(after))return [];const a=new Map(before.map(u=>[u.id,u.n])),b=new Map(after.map(u=>[u.id,u.n]));return [...new Set([...a.keys(),...b.keys()])].sort((x,y)=>x-y).filter(id=>(a.get(id)||0)!==(b.get(id)||0)).map(id=>({cluster:id,before:a.get(id)||0,after:b.get(id)||0}));};
 const clusterIds=r=>[...new Set((r.judgment_pair||r.expert_pair||[r.source_cluster??r.current_cluster]).filter(Number.isSafeInteger))];
 function working(workspaces){
  const history=[],revisions=[],summaries=[];
  for(const s of workspaces){
   const stack=[],records=[],signatures=new Map();for(let i=0;i<s.labels.length;i++){const cid=s.labels[i];if(cid>0){if(!signatures.has(cid))signatures.set(cid,[]);signatures.get(cid).push(i);}}
   const pending=[...signatures].filter(([cid,ids])=>s.kept[cid]!==ids.join(',')).length;
   summaries.push({id:s.workspace_id,dataset_id:s.dataset_id,recording_id:s.recording_id||s.dataset_id,reviewer:s.reviewer,version:s.version,clusters:signatures.size,pending,complete:s.complete,parent:s.parent_workspace||null,source_sha256:s.source_sha256,updated_at:s.updated_at||''});
   for(const [i,e] of (s.events||[]).entries()){
    if(!e||!['choice_applied','undo','finished','continued_imported_workspace'].includes(e.type))continue;
    let undone=null;if(e.type==='undo'){undone=stack.pop()||null;if(undone)undone.status='undone';}
    const beforeCounts=e.counts_before||(undone?undone.raw.counts_after:null),afterCounts=e.counts_after||(undone?undone.raw.counts_before:null),task=e.task||undone?.raw.task||{},counts=countChanges(beforeCounts,afterCounts);
    const row={id:`working:${s.workspace_id}:${e.id||i}`,kind:'working',dataset_id:s.dataset_id,recording_id:s.recording_id||s.dataset_id,workspace_id:s.workspace_id,workspace_label:`Workspace ${s.workspace_id.slice(0,8)}`,origin_workspace_id:e.workspace_id||null,case_id:null,version:Number.isInteger(e.version)?e.version:null,actor:e.reviewer||s.reviewer,agent:e.choice?.agent_id||null,timestamp:date(e.timestamp),event:e.type,action:e.choice?.action||(e.type==='undo'?'UNDO':e.type==='finished'?'FINISH':'IMPORT'),affected_action:undone?.action||null,clusters:[...new Set([task.source,task.target,...counts.map(c=>c.cluster)].filter(Number.isSafeInteger))],source:task.source??null,target:task.target??null,reason:e.choice?.reason||'',note:e.choice?.note||e.note||'',confidence:e.choice?.confidence||null,status:e.type==='undo'?'undone':e.type==='finished'?'finished':e.type==='continued_imported_workspace'?'imported':e.choice?.action==='DEFER'?'deferred':e.execution_status==='applied'?'applied':'recorded',state_before:e.state_before||null,state_after:e.state_after||null,count_changes:counts,changes:[],inherited:!!(e.workspace_id&&e.workspace_id!==s.workspace_id||e.reviewer&&e.reviewer!==s.reviewer||s.parent_workspace&&Number.isInteger(e.version)&&e.version<=s.parent_workspace.version),undo_of:e.undo_of||undone?.raw.id||null,undo_version:e.undone_version??undone?.version??null,raw:e};
    if(e.type==='choice_applied'||e.type==='finished')stack.push(row);
    if(e.type==='undo'){const restored=undone?.object_key?[...stack].reverse().find(r=>r.object_key===undone.object_key):null;row.changes=changes(undone?.raw.choice,restored?.raw.choice,['action','reason','note','confidence','agree']);}
    else if(e.type==='choice_applied'){
     const key=e.object_before?JSON.stringify([task.source,task.target??null,e.object_before]):JSON.stringify([task.source,task.target??null,e.state_before]);
     row.object_key=key;const previous=[...stack.slice(0,-1)].reverse().find(r=>r.object_key===key);row.changes=changes(previous?.raw.choice,e.choice,['action','reason','note','confidence','agree']);
    }
    row.changes.push(...changes(e.summary_before,e.summary_after,['active_clusters','active_spikes','accepted_clusters','complete']));
    records.push(row);revisions.push(row);
   }
   const latest=new Map();for(const row of stack)if(row.object_key)latest.set(row.object_key,row);
   for(const row of records){
    if(row.event!=='choice_applied')continue;
    if(row.status!=='undone'&&latest.get(row.object_key)!==row)row.status='superseded';
    if(!['undone','superseded'].includes(row.status)&&row.raw.object_after&&['KEEP','NOT_MERGE','DEFER'].includes(row.action)){
     if(Object.entries(row.raw.object_after).some(([cid,sig])=>(signatures.get(Number(cid))?.join(',')??null)!==sig))row.status='earlier_state';
    }
    history.push(row);
   }
  }
  return {history,revisions,workspaces:summaries};
 }
 function snapshots(payload){
  const previous=new Map(),revisions=[],latestEvents=new Map();
  for(const [i,e] of (payload.events||[]).entries()){
   if(!e||!['review_saved','review_undone','review_imported'].includes(e.type))continue;
   const before=previous.get(e.case_id)||e.previous_review||null,after=e.type==='review_undone'?null:e.review||e,ref=after||before;if(!ref?.dataset_id)continue;
   const row={id:`snapshot:${e.request_id||e.case_id+':'+(e.revision??i)}`,kind:ref.synthetic?'demo':'snapshot',dataset_id:ref.dataset_id,recording_id:ref.recording_id||ref.dataset_id,workspace_id:null,workspace_label:'Snapshot annotations',case_id:e.case_id,version:Number.isInteger(e.revision)?e.revision:null,actor:ref.reviewer_id||payload.reviewer_id||null,agent:ref.study_agent_id||ref.agent_id||null,timestamp:date(e.timestamp||ref.timestamp),event:e.type==='review_saved'&&before?'review_edited':e.type,action:after?.action||'UNDO',affected_action:before?.action||null,clusters:clusterIds(ref),source:ref.source_cluster??ref.current_cluster??null,target:ref.judgment_pair?.[1]??ref.expert_merge_target??null,reason:ref.expert_reason||'',note:ref.expert_note||ref.expert_positive_comment||'',confidence:ref.expert_confidence||null,status:after?after.action==='DEFER'?'deferred':'recorded':'removed',state_before:ref.state_sha256||null,state_after:ref.state_sha256||null,count_changes:[],changes:changes(before,after,FIELDS),inherited:false,raw:e};
   revisions.push(row);latestEvents.set(e.case_id,row);previous.set(e.case_id,after);
  }
  const history=(payload.reviews||[]).map(r=>{const base=latestEvents.get(r.case_id);return {...base,id:`snapshot:latest:${r.case_id}`,kind:r.synthetic?'demo':'snapshot',dataset_id:r.dataset_id,recording_id:r.recording_id||r.dataset_id,workspace_id:null,workspace_label:'Snapshot annotations',case_id:r.case_id,version:base?.version??null,actor:r.reviewer_id||payload.reviewer_id||null,agent:r.study_agent_id||r.agent_id||null,timestamp:date(r.timestamp),event:'latest_review',action:r.action,affected_action:null,clusters:clusterIds(r),source:r.source_cluster??r.current_cluster??null,target:r.judgment_pair?.[1]??r.expert_merge_target??null,reason:r.expert_reason||'',note:r.expert_note||r.expert_positive_comment||'',confidence:r.expert_confidence||null,status:r.action==='DEFER'?'deferred':'recorded',state_before:r.state_sha256||null,state_after:r.state_sha256||null,count_changes:[],changes:base?.changes||[],inherited:false,raw:r};});
  return {history,revisions,workspaces:[]};
 }
 function combine(workspaces,payload){const a=working(workspaces),b=snapshots(payload);return {history:[...a.history,...b.history],revisions:[...a.revisions,...b.revisions],workspaces:a.workspaces};}
 function filter(rows,f={}){return rows.filter(r=>(!f.dataset||r.dataset_id===f.dataset)&&(!f.workspace||r.workspace_id===f.workspace)&&(!f.kind||r.kind===f.kind)&&(!f.action||r.action===f.action||r.affected_action===f.action)&&(!f.status||r.status===f.status)&&(!f.actor||r.actor===f.actor)&&(!f.agent||r.agent===f.agent)&&(!f.from||r.timestamp&&r.timestamp.slice(0,10)>=f.from)&&(!f.to||r.timestamp&&r.timestamp.slice(0,10)<=f.to)&&(!f.search||(/^\d+$/.test(f.search.trim())?r.clusters.includes(Number(f.search.trim())):[r.dataset_id,r.recording_id,r.case_id,r.workspace_id,r.reason,r.note,r.action].some(v=>String(v||'').toLocaleLowerCase().includes(f.search.trim().toLocaleLowerCase()))))).sort((a,b)=>Date.parse(b.timestamp||0)-Date.parse(a.timestamp||0)||String(a.workspace_id||'').localeCompare(String(b.workspace_id||''))||(b.version??0)-(a.version??0));}
 function csv(rows){const cols=['kind','dataset_id','recording_id','workspace_id','case_id','version','actor','agent','timestamp','event','action','affected_action','clusters','status','reason','note','state_before','state_after','confidence','inherited','origin_workspace_id','undo_of','undo_version','count_changes','changes'];const cell=v=>{v=Array.isArray(v)?v.some(x=>x&&typeof x==='object')?JSON.stringify(v):v.join(' + '):v&&typeof v==='object'?JSON.stringify(v):String(v??'');if(/^(?:\s*[=+\-@]|[\t\r])/.test(v))v="'"+v;return '"'+v.replace(/"/g,'""')+'"';};return [cols.join(','),...rows.map(r=>cols.map(c=>cell(r[c])).join(','))].join('\r\n');}
 return {working,snapshots,combine,filter,countChanges,csv};
});
