/* Private RPC worker. The HTTP layer must only expose the explicit view/export DTOs. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),zlib=require('node:zlib'),crypto=require('node:crypto'),readline=require('node:readline');
const C=require('../dist/curation-core.js'),A=require('../dist/activity-core.js');
const config=JSON.parse(fs.readFileSync(process.argv[2])),root=config.storage;
fs.mkdirSync(root,{recursive:true,mode:0o700});
const cache=new Map(),previews=new Map(),revisions=new Map();
const fail=(message,status=400)=>{throw Object.assign(Error(message),{status});};
const id=v=>typeof v==='string'&&/^[a-f0-9-]{32,36}$/.test(v);
const digest=b=>crypto.createHash('sha256').update(b).digest('hex');
function rowsFor(owner){return [...config.packages,...(config.upload_registry&&fs.existsSync(config.upload_registry)?JSON.parse(fs.readFileSync(config.upload_registry)).filter(r=>r.owner===owner):[])];}
function save(s){const p=path.join(root,s.workspace_id+'.json'),tmp=p+'.tmp';fs.writeFileSync(tmp,JSON.stringify(s),{mode:0o600});fs.renameSync(tmp,p);}
function load(q){if(!id(q.workspace_id))fail('Invalid session',404);let s;try{s=JSON.parse(fs.readFileSync(path.join(root,q.workspace_id+'.json')));}catch{fail('Session not found',404);}if(s.reviewer!==q.owner)fail('Session not found',404);return s;}
function version(q,s){if(!Number.isInteger(q.version)||q.version!==s.version)fail('Session changed in another tab. Reload before continuing.',409);}
async function input(dataset,owner){
 const row=rowsFor(owner).find(r=>r.dataset_id===dataset);if(!row)fail('This dataset is not available for this session',404);
 if(cache.has(dataset)){const p=cache.get(dataset);cache.delete(dataset);cache.set(dataset,p);return p;}
 const packed=fs.readFileSync(row.path);if(packed.length!==row.bytes||digest(packed)!==row.sha256)fail('Server input integrity check failed',503);
 const data=JSON.parse(zlib.gunzipSync(packed)),p=C.prepare(data),wf=data.arrays.waveforms;
 if(!wf)fail('Full recorded waveforms required',503);
 const bytes=Buffer.from(wf.data,'base64');if(digest(bytes)!==wf.sha256)fail('Recorded waveform integrity check failed',503);
 if(await C.stateHash(p,C.initial(p,'check'))!==data.initial_state_sha256)fail('Initial grouping integrity check failed',503);
 const base=path.join(root,digest(Buffer.from(dataset)).slice(0,24));
 const write=(suffix,buffer)=>{const name=base+suffix;if(!fs.existsSync(name)||fs.statSync(name).size!==buffer.length||digest(fs.readFileSync(name))!==digest(buffer)){fs.writeFileSync(name+'.tmp',buffer,{mode:0o600});fs.renameSync(name+'.tmp',name);}return name;};
 p.privateFiles={wave_path:write('.wave.bin',bytes),time_path:write('.time.bin',Buffer.from(data.arrays.times.data,'base64')),amp_path:write('.amp.bin',Buffer.from(data.arrays.amplitudes.data,'base64')),dtype:wf.dtype,n_spikes:data.n_spikes,n_samples:data.n_samples,fs:data.sampling_rate_hz};
 const initial=C.assignments(p,C.initial(p,'check'));p.privateFiles.initial_path=write('.initial.bin',Buffer.from(initial.buffer));
 cache.set(dataset,p);while(cache.size>3)cache.delete(cache.keys().next().value);return p;
}
const safeChoice=e=>e?{id:e.id,version:e.version,task:e.task,choice:Object.fromEntries(['action','reason','note','positive','agree','agent_id'].filter(k=>e.choice?.[k]!=null).map(k=>[k,e.choice[k]])),timestamp:e.timestamp}:null;
const withSuggestions=(p,s)=>({...p,data:{...p.data,agent_suggestions:[...(p.data.agent_suggestions||[]),...(s.agent_suggestions||[])]}});
function choiceFor(s,cid,target=null){return [...C.retainedOperations(s.events)].reverse().find(e=>e.type==='choice_applied'&&e.task?.source===cid&&(e.task.target??null)===target&&e.object_before?.[cid]===C.signature(s,cid)&&e.object_after?.[cid]===C.signature(s,cid));}
function selected(q,s,p){const ids=C.active(p,s);if(!ids.length)return null;const source=q.source??ids[0],target=q.target??null;if(!Number.isInteger(source)||!ids.includes(source)||target!=null&&(!Number.isInteger(target)||target===source||!ids.includes(target)))fail('Choose active clusters');return C.task(source,target);}
function revisionPath(key){return path.join(root,key+'.revision.json');}
function revisionFor(q,s){
 if(!q.revision_id)return null;if(!id(q.revision_id))fail('Invalid grouping edit',404);let v=revisions.get(q.revision_id);
 if(!v){try{v=JSON.parse(fs.readFileSync(revisionPath(q.revision_id)));revisions.set(q.revision_id,v);}catch{}}
 if(!v||v.workspace!==s.workspace_id||v.owner!==q.owner||Date.now()-v.created>1800000)fail('Grouping edit expired. Reopen the saved choice.',409);
 if(v.version!==s.version||v.original_hash!==s.state_sha256)fail('Session changed in another tab. Cancel or reload this grouping edit.',409);return v;
}
function removeRevision(key){revisions.delete(key);try{fs.unlinkSync(revisionPath(key));}catch{}for(const [id,v] of previews)if(v.revision_id===key){previews.delete(id);try{fs.unlinkSync(previewPath(id));}catch{}}}
function previewPath(key){return path.join(root,key+'.preview.json');}
function previewFor(q,s){if(!q.preview_id)return null;if(!id(q.preview_id))fail('Invalid preview',404);let v=previews.get(q.preview_id);if(!v){try{v=JSON.parse(fs.readFileSync(previewPath(q.preview_id)));previews.set(q.preview_id,v);}catch{}}if(!v||v.workspace!==s.workspace_id||v.version!==s.version||(v.revision_id||null)!==(q.revision_id||null)||Date.now()-v.created>1800000)fail('Preview expired. Select the action again.',409);return v;}
function cleanEvent(e){const out=C.clone(e);delete out.object_before;delete out.object_after;if(out.choice)delete out.choice.manual_rows;return out;}
function publicState(s){return {schema:'spikeagent-server-session-v1',workspace_id:s.workspace_id,dataset_id:s.dataset_id,recording_id:s.recording_id,reviewer:s.reviewer,version:s.version,complete:s.complete,phase:s.phase,updated_at:s.updated_at,undo:s.undo.map(()=>true),events:s.events.map(cleanEvent)};}
function view(p,s,q={}){
 const edit=revisionFor(q,s),base=edit?.state||s,t=selected(q,base,p),v=previewFor(q,s),projected=v?.state||base,metrics=C.unitMetrics(p,base),us=C.units(p,base);
 const units=us.map(u=>({id:u.id,n:u.n,split:u.split,isi_rate:metrics.get(u.id)?.rate??null,reviewed:base.kept[u.id]===C.signature(base,u.id),choice:safeChoice(choiceFor(base,u.id)),operation:safeChoice(C.clusterOperation(base,u.id))}));
 const ids=!t?[]:v?(v.action==='SPLIT'?v.child_ids:[t.target]):[t.source,t.target].filter(n=>n!=null);
 const populations=C.units(p,projected),groups=ids.map((id,i)=>{const u=populations.find(u=>u.id===id),quality=C.quality(p,projected,id);delete quality.scatter;return {id,n:u.n,color:['#0072ff','#ef007a'][i],quality};});
 const suggestions=t?['Agent_1','Agent_2'].map(agent=>C.suggestion(withSuggestions(p,base),base.state_sha256,t,agent)).filter(Boolean).map(r=>({agent_id:r.agent_id,source:r.source,target:r.target??null,action:r.action,rationale:r.rationale||''})):[];
 return {schema:'spikeagent-server-view-v1',state:publicState(s),state_sha256:base.state_sha256,revision:edit?{...edit.plan,id:q.revision_id,original_selected:edit.original_selected}:null,selected:t,units,groups,preview:v?{id:q.preview_id,action:v.action,version:v.version,ready:v.viewed,child_ids:v.child_ids,selection_count:v.manual_rows?.length??null}:null,suggestions,choice:t?safeChoice(choiceFor(base,t.source,t.target)||C.clusterOperation(base,t.source)):null,comparison_choice:t?.target!=null?safeChoice(choiceFor(base,t.target)||C.clusterOperation(base,t.target)):null,pooled_isi:t?.target!=null&&!v?C.pairISI(p,base,t.source,t.target):null,pending:units.filter(u=>!u.reviewed).length,pairs:C.pairTasks(p,base).map(t=>({source:t.source,target:t.target,correlation:t.correlation})),references:t?C.reference(p,base.state_sha256,t).map(r=>({action:r.action,reason:r.reason||''})):[]};
}
function prune(){for(const [k,v] of revisions)if(Date.now()-v.created>1800000)removeRevision(k);while(revisions.size>128)removeRevision(revisions.keys().next().value);for(const [k,v] of previews)if(Date.now()-v.created>1800000)previews.delete(k);while(previews.size>128)previews.delete(previews.keys().next().value);}
function makePreview(p,s,t,action,rows,q={}){prune();const base=revisionFor(q,s)?.state||s,state=C.preview(p,base,t,action,rows?{manual_rows:rows}:{}),key=crypto.randomUUID(),child_ids=action==='SPLIT'?[t.source,rows?C.manualPlan(p,base,t.source,rows).breakaway:C.splitPlan(p,base,t.source).breakaway]:[t.target],value={workspace:s.workspace_id,version:s.version,revision_id:q.revision_id||null,created:Date.now(),state,task:t,action,manual_rows:rows||null,child_ids,viewed:false};previews.set(key,value);fs.writeFileSync(previewPath(key),JSON.stringify(value),{mode:0o600});return key;}
function safeOptions(q){return {mode:q.mode==='sample24'?'sample24':'all',axes:q.axes===2?2:1,width:Number.isInteger(q.width)?Math.max(600,Math.min(2400,q.width)):1200,isolate:Number.isInteger(q.isolate)?q.isolate:null,opacity:Array.isArray(q.opacity)&&q.opacity.length===2?q.opacity.map(n=>typeof n==='number'&&Number.isFinite(n)?Math.max(.1,Math.min(1,n)):1):[1,1]};}
async function dispatch(q){
 if(!id(q.owner))fail('Session identity missing',401);
 if(q.op==='datasets')return {datasets:rowsFor(q.owner).map(r=>({dataset_id:r.dataset_id,n_spikes:r.n_spikes,recording_id:r.recording_id||r.dataset_id,source_kind:r.source_kind||'runpod_library'})),raw_data_downloads:false,model_calls_enabled:false};
 if(q.op==='workspaces'||q.op==='history'){
  const states=fs.readdirSync(root).filter(n=>/^[a-f0-9-]{36}\.json$/.test(n)).map(n=>JSON.parse(fs.readFileSync(path.join(root,n)))).filter(s=>s.reviewer===q.owner);
  if(q.op==='workspaces')return {workspaces:states.map(s=>({workspace_id:s.workspace_id,dataset_id:s.dataset_id,version:s.version,updated_at:s.updated_at}))};
  const activity=A.working(states);for(const r of [...activity.history,...activity.revisions]){r.raw=cleanEvent(r.raw);delete r.object_key;}return activity;
 }
 if(q.op==='open'){
  const p=await input(q.dataset_id,q.owner);let s;
  if(q.workspace_id){s=load(q);if(s.dataset_id!==q.dataset_id)fail('Wrong channel');}
  else{
   const checkpoint=q.checkpoint? p.data.review_snapshots?.find(r=>r.archive_state_sha256===q.checkpoint):null;
   if(q.checkpoint&&!checkpoint&&q.checkpoint!==p.data.initial_state_sha256)fail('Saved grouping not found',404);
   // Resume an existing independent session for this recording/checkpoint; never reset it.
   const existing=fs.readdirSync(root).filter(n=>/^[a-f0-9-]{36}\.json$/.test(n)).map(n=>JSON.parse(fs.readFileSync(path.join(root,n)))).filter(s=>s.reviewer===q.owner&&s.dataset_id===q.dataset_id&&!s.complete&&(s.parent_snapshot?.state_sha256||null)===(q.checkpoint||null)).sort((a,b)=>b.updated_at.localeCompare(a.updated_at))[0];
   s=existing||C.initial(p,q.owner);if(!existing&&checkpoint){s.labels=checkpoint.labels.slice();s.tree=C.clone(checkpoint.tree);s.parent_snapshot={state_sha256:q.checkpoint};}
   if(!existing){s.state_sha256=await C.stateHash(p,s);if(checkpoint&&s.state_sha256!==checkpoint.state_sha256)fail('Saved grouping integrity check failed',503);s.updated_at=new Date().toISOString();save(s);}
  }
  return view(p,s,q);
 }
 const s=load(q),p=await input(s.dataset_id,q.owner);if(q.op==='view')return view(p,s,q);
 if(['confirm','undo'].includes(q.op)&&id(q.request_id)&&s.requests?.[q.request_id])return view(p,s,{...q,source:C.active(p,s).includes(q.source)?q.source:undefined,target:null,preview_id:null,revision_id:null});
 version(q,s);
 if(q.op==='revision-plan')return C.revisionPlan(p,s,q.event_id);
 if(q.op==='revision-start'){
  if(q.revision_id)fail('Finish or cancel the current grouping edit');prune();const out=await C.revisionBase(p,s,q.event_id,s.version),key=crypto.randomUUID();out.state.state_sha256=out.plan.base_hash;
  const value={workspace:s.workspace_id,owner:q.owner,version:s.version,original_hash:s.state_sha256,created:Date.now(),state:out.state,plan:out.plan,original_selected:{source:q.source??null,target:q.target??null}};revisions.set(key,value);fs.writeFileSync(revisionPath(key),JSON.stringify(value),{mode:0o600});
  return view(p,s,{...q,source:out.plan.task.source,target:out.plan.task.target,preview_id:null,revision_id:key});
 }
 if(q.op==='revision-cancel'){
  const edit=revisionFor(q,s),ids=C.active(p,s),source=ids.includes(edit?.original_selected.source)?edit.original_selected.source:ids[0],target=ids.includes(edit?.original_selected.target)&&edit.original_selected.target!==source?edit.original_selected.target:null;if(q.revision_id)removeRevision(q.revision_id);
  return view(p,s,{...q,source,target,preview_id:null,revision_id:null});
 }
 const edit=revisionFor(q,s),base=edit?.state||s,t=selected(q,base,p);
 if(edit&&t?.source!==edit.plan.task.source)fail('Confirm or cancel this grouping edit before selecting another source cluster',409);
 if(q.op==='preview'){
  if(!t)fail('Select a cluster');if(!['SPLIT','MERGE'].includes(q.action))fail('Invalid preview action');
  if(q.action==='SPLIT'&&!q.manual_rows&&!C.splitPlan(p,base,t.source))return {...view(p,s,q),manual_split_required:true};
  const key=makePreview(p,s,t,q.action,q.manual_rows,q);return view(p,s,{...q,preview_id:key});
 }
 if(q.op==='confirm'||q.op==='undo'){
  if(!id(q.request_id))fail('Operation request ID required');
  if(s.events.length>=2000)fail('Session operation limit reached. Export the log and start another session.',429);
  let next;
  if(q.op==='undo'){if(edit)fail('Cancel the grouping edit before undoing a saved operation',409);next=await C.undo(p,s,q.owner);}
  else{
   if(!t)fail('Select a cluster');const value=q.choice||{},allowed=['KEEP','SPLIT','MERGE','NOT_MERGE','DISCARD'];if(!allowed.includes(value.action))fail('Invalid action');
   const choice={action:value.action,agent_id:['Agent_1','Agent_2'].includes(value.agent_id)?value.agent_id:'Agent_1',agree:typeof value.agree==='boolean'?value.agree:null};
   for(const [k,max] of [['reason',2000],['note',10000],['positive',10000]]){if(value[k]!=null&&typeof value[k]!=='string'||String(value[k]||'').length>max)fail('Invalid choice text');choice[k]=String(value[k]||'').trim();}
   if(['SPLIT','MERGE'].includes(choice.action)){const v=previewFor(q,s);if(!v||!v.viewed||v.action!==choice.action||v.task.source!==t.source||v.task.target!==t.target)fail('Inspect the current preview before confirming',409);if(v.manual_rows)choice.manual_rows=v.manual_rows;}
   if(edit){choice.revision_of=edit.plan.event_id;choice.revision_removed=edit.plan.removed.map(e=>e.id);choice.revision_preserved=edit.plan.preserved.map(e=>e.id);}next=await C.apply(withSuggestions(p,base),base,t,choice,base.version);const v=previewFor(q,s);if(v&&await C.stateHash(p,next)!==await C.stateHash(p,v.state))fail('Preview no longer matches the confirmed grouping',409);
  }
  next.requests={...(s.requests||{}),[q.request_id]:true};for(const key of Object.keys(next.requests).slice(0,-100))delete next.requests[key];next.updated_at=new Date().toISOString();next.state_sha256=await C.stateHash(p,next);save(next);if(q.revision_id)removeRevision(q.revision_id);
  for(const [k,v] of previews)if(v.workspace===s.workspace_id)previews.delete(k);
  const ids=C.active(p,next),source=ids.includes(q.source)?q.source:ids.includes(q.target)?q.target:ids[0];
  return view(p,next,{...q,source,target:ids.includes(q.target)&&q.target!==source?q.target:null,preview_id:null,revision_id:null});
 }
 if(q.op==='export'&&edit)fail('Confirm or cancel the grouping edit before exporting',409);
 if(q.op==='export')return {schema:'spikeagent-operation-log-v1',exported_at:new Date().toISOString(),dataset_id:s.dataset_id,workspace_id:s.workspace_id,version:s.version,reviewer:s.reviewer,state_sha256:s.state_sha256,operations:s.events.map(cleanEvent),clusters:C.units(p,s).map(u=>({cluster_id:u.id,n_spikes:u.n})),raw_data_included:false};
 if(q.op==='correlation'){
  const v=previewFor(q,s),state=v?.state||base,ids=v?v.child_ids:[t?.source,t?.target].filter(n=>n!=null);
  return {acg:ids.map(id=>({id,...C.correlogram(p,state,id)})),ccg:ids.length===2?C.correlogram(p,state,ids[0],ids[1]):null};
 }
 if(q.op==='render-spec'){
  const v=previewFor(q,s),options=safeOptions(q);let state=v?.state||base,ids=v?v.child_ids:[t?.source,t?.target].filter(n=>n!=null);
  if(q.figure==='thumbnail'){if(!C.active(p,base).includes(q.cluster_id))fail('Cluster not found');ids=[q.cluster_id];state=base;}
  else if(!['waves','pca','stability'].includes(q.figure))fail('Figure not found',404);
  if(q.figure==='waves'&&v?.action==='MERGE'){state=base;ids=[v.task.source,v.task.target];}
  if(!ids.length)fail('No selected population');const labels=C.assignments(p,state),key=digest(Buffer.from(s.workspace_id+':'+s.version+':'+(q.preview_id||'')+':'+(q.revision_id||'')+':'+(state===s?'original':'projected'))),labels_path=path.join(root,key+'.labels.bin');if(!fs.existsSync(labels_path))fs.writeFileSync(labels_path,Buffer.from(labels.buffer),{mode:0o600});
  const us=C.units(p,state);return {...p.privateFiles,labels_path,groups:ids.map((id,i)=>({id,n:us.find(u=>u.id===id).n,color:['#0072ff','#ef007a'][i]})),figure:q.figure,options,workspace_id:s.workspace_id,version:s.version,preview_id:q.preview_id||null};
 }
 if(q.op==='preview-seen'){const v=previewFor(q,s);if(v){v.viewed=true;fs.writeFileSync(previewPath(q.preview_id),JSON.stringify(v),{mode:0o600});}return {seen:true};}
 fail('Operation not available',404);
}
let pending=Promise.resolve();readline.createInterface({input:process.stdin,crlfDelay:Infinity}).on('line',line=>{pending=pending.then(async()=>{let request;try{request=JSON.parse(line);const result=await dispatch(request);process.stdout.write(JSON.stringify({ok:true,result})+'\n');}catch(e){process.stdout.write(JSON.stringify({ok:false,error:e.message,status:e.status||400})+'\n');}});});
