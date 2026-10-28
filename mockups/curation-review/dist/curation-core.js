/* Human curation on private working copies. No model, expert-label or network calls. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.CurationCore=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 const SCHEMA='spikeagent-working-dataset-v1',clone=x=>JSON.parse(JSON.stringify(x));
 const cryptoAPI=()=>globalThis.crypto||require('node:crypto').webcrypto;
 async function digest(bytes){return [...new Uint8Array(await cryptoAPI().subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');}
 function decode(row,type,n){
  if(!row||row.dtype!==type||row.length!==n||typeof row.data!=='string')throw Error('Invalid working-data vector');
  const raw=typeof atob==='function'?atob(row.data):Buffer.from(row.data,'base64').toString('binary');
  const size=type==='<f8'?8:4;if(raw.length!==n*size)throw Error('Working-data vector length differs');
  const bytes=Uint8Array.from(raw,c=>c.charCodeAt(0)),view=new DataView(bytes.buffer),out=type==='<f8'?new Float64Array(n):new Int32Array(n);
  for(let i=0;i<n;i++){out[i]=size===8?view.getFloat64(i*size,true):view.getInt32(i*size,true);if(!Number.isFinite(out[i]))throw Error('Nonfinite working input');}return out;
 }
 function prepare(data){
  if(!data||data.schema!==SCHEMA||typeof data.dataset_id!=='string'||!/^[A-Za-z0-9_.-]{1,150}$/.test(data.dataset_id)||!/^[a-f0-9]{64}$/.test(data.source_sha256)||!Number.isInteger(data.n_spikes)||data.n_spikes<1||data.n_spikes>2_000_000||!Number.isInteger(data.n_samples)||data.n_samples<2||data.n_samples>1000||!Number.isFinite(data.sampling_rate_hz)||data.sampling_rate_hz<=0||!Array.isArray(data.atoms)||!data.atoms.length||data.atoms.length>10000)throw Error('Unsupported or invalid working dataset');
  if(!Array.isArray(data.tree)||data.tree.length!==4||!data.tree.every(r=>Array.isArray(r)&&r.length===data.tree[0].length&&r.every(Number.isFinite))||data.tree[0].length>100000)throw Error('Invalid hierarchy tree');
  const times=decode(data.arrays.times,'<f8',data.n_spikes),amps=decode(data.arrays.amplitudes,'<f8',data.n_spikes),index=decode(data.arrays.atom_index,'<i4',data.n_spikes),counts=new Uint32Array(data.atoms.length);
  for(let i=0;i<index.length;i++){if(index[i]<0||index[i]>=counts.length||amps[i]<0)throw Error('Invalid spike/component mapping');counts[index[i]]++;}
  data.atoms.forEach((a,i)=>{if(!Number.isInteger(a.initial_cluster)||a.initial_cluster<0||!Number.isInteger(a.overcluster)||a.overcluster<0||a.n!==counts[i]||!a.n||!['sum','sumsq'].every(k=>Array.isArray(a[k])&&a[k].length===data.n_samples&&a[k].every(Number.isFinite))||!Array.isArray(a.traces)||a.traces.length>24||a.traces.some(t=>!Number.isInteger(t.row)||t.row<0||t.row>=data.n_spikes||index[t.row]!==i||!Array.isArray(t.waveform)||t.waveform.length!==data.n_samples||!t.waveform.every(Number.isFinite)))throw Error('Invalid atomic waveform statistics');});
  const wf=data.arrays.waveforms;if(wf&&(!['<f4','<f8'].includes(wf.dtype)||wf.length!==data.n_spikes*data.n_samples||wf.length>50000000||!Array.isArray(wf.shape)||wf.shape[0]!==data.n_spikes||wf.shape[1]!==data.n_samples||typeof wf.data!=='string'||!/^[a-f0-9]{64}$/.test(wf.sha256)))throw Error('Invalid full waveform matrix');
  const order=Int32Array.from({length:data.n_spikes},(_,i)=>i);order.sort((a,b)=>times[a]-times[b]||a-b);
  return {data,times,amps,index,order};
 }
 function rowLabel(input,state,row){return state.row_labels?.[row]?.cluster??state.labels[input.index[row]];}
 function validateRowLabels(input,state){const rows=state.row_labels;if(rows==null)return;if(typeof rows!=='object'||Array.isArray(rows)||Object.keys(rows).length>input.data.n_spikes)throw Error('Invalid manual membership map');for(const [key,v] of Object.entries(rows)){const r=Number(key);if(!Number.isInteger(r)||String(r)!==key||r<0||r>=input.data.n_spikes||!v||v.atom!==input.index[r]||!Number.isInteger(v.cluster)||v.cluster<0||v.cluster>2147483647)throw Error('Invalid manual spike membership');}}
 function assignments(input,state){return Int32Array.from(input.index,(_,row)=>rowLabel(input,state,row));}
 async function stateHash(input,state){
  const a=assignments(input,state),n=state.tree[0].length,header=new TextEncoder().encode(`human-curation-state-v1\n${a.length}\n${n}\n`),bytes=new Uint8Array(header.length+a.length*4+n*32),view=new DataView(bytes.buffer);bytes.set(header);let offset=header.length;
  for(const v of a){view.setInt32(offset,v,true);offset+=4;}for(const row of state.tree)for(const v of row){view.setFloat64(offset,v,true);offset+=8;}return digest(bytes);
 }
 function initial(input,reviewer){return {schema:'spikeagent-curation-state-v1',workspace_id:cryptoAPI().randomUUID(),dataset_id:input.data.dataset_id,recording_id:input.data.metadata?.recording_id||input.data.dataset_id,source_sha256:input.data.source_sha256,reviewer,version:0,workflow:'context-v2',phase:'phase1',labels:input.data.atoms.map(a=>a.initial_cluster),tree:clone(input.data.tree),kept:{},separate:{},deferred:{},events:[],undo:[],complete:false};}
 function active(input,state){const counts=new Map();input.data.atoms.forEach((a,i)=>counts.set(state.labels[i],(counts.get(state.labels[i])||0)+a.n));for(const v of Object.values(state.row_labels||{})){const old=state.labels[v.atom];counts.set(old,(counts.get(old)||0)-1);counts.set(v.cluster,(counts.get(v.cluster)||0)+1);}return [...counts].filter(([id,n])=>id>0&&n>0).map(([id])=>id).sort((a,b)=>a-b);}
 function members(state,cid){return state.labels.flatMap((v,i)=>v===cid?[i]:[]);}
 function signature(state,cid){const base=members(state,cid).join(','),rows=Object.entries(state.row_labels||{}).filter(([,v])=>v.cluster===cid||state.labels[v.atom]===cid).sort((a,b)=>Number(a[0])-Number(b[0])).map(([row,v])=>row+'='+v.cluster);return base+(rows.length?'|rows:'+rows.join(','):'');}
 function splitPlan(input,state,cid){
  if(Object.values(state.row_labels||{}).some(v=>v.cluster===cid||state.labels[v.atom]===cid))return null;
  let last=-1;state.tree[0].forEach((v,i)=>{if(v===cid)last=i;});if(last<0)return null;
  const breakaway=state.tree[1][last],ocs=new Set([breakaway]);if(!Number.isInteger(breakaway)||breakaway<=0||breakaway===cid||state.labels.includes(breakaway))return null;
  for(let i=last-1;i>=0;i--)if(ocs.has(state.tree[0][i]))ocs.add(state.tree[1][i]);
  const ids=members(state,cid),out=ids.filter(i=>ocs.has(input.data.atoms[i].overcluster));
  return out.length&&out.length<ids.length?{last,breakaway,atoms:out}:null;
 }
 function waveValues(input){if(input.waveValues)return input.waveValues;const wf=input.data.arrays.waveforms;if(!wf)throw Error('Full waveforms required for a manual split');const size=wf.dtype==='<f4'?4:8,bytes=new Uint8Array(wf.length*size),chunk=4*262144;let offset=0;for(let at=0;at<wf.data.length;at+=chunk){const raw=typeof atob==='function'?atob(wf.data.slice(at,at+chunk)):Buffer.from(wf.data.slice(at,at+chunk),'base64').toString('binary');for(let j=0;j<raw.length;j++)bytes[offset++]=raw.charCodeAt(j);}if(offset!==bytes.length)throw Error('Full waveform byte count differs');input.waveValues=wf.dtype==='<f4'?new Float32Array(bytes.buffer):new Float64Array(bytes.buffer);return input.waveValues;}
 const populations=new WeakMap();
 function units(input,state){
  if(Object.keys(state.row_labels||{}).length){
   const old=populations.get(state);if(old&&old.labels===state.labels&&old.rows===state.row_labels&&old.treeN===state.tree[0].length)return old.units;
   const n=input.data.n_samples,waves=waveValues(input),map=new Map(active(input,state).map(id=>[id,{id,n:0,mean:Array(n).fill(0),squares:Array(n).fill(0),rows:[],split:false}]));
   for(const row of input.order){const u=map.get(rowLabel(input,state,row));if(!u)continue;u.n++;u.rows.push(row);for(let j=0;j<n;j++){const v=waves[row*n+j];u.mean[j]+=v;u.squares[j]+=v*v;}}
   const us=[...map.values()].map(u=>{u.mean=u.mean.map(v=>v/u.n);u.sd=u.squares.map((v,j)=>Math.sqrt(Math.max(0,v/u.n-u.mean[j]**2)));u.traces=sample(u.rows,24).map(row=>({row,waveform:Array.from(waves.subarray(row*n,(row+1)*n))}));u.split=!!splitPlan(input,state,u.id);delete u.rows;delete u.squares;return u;});populations.set(state,{labels:state.labels,rows:state.row_labels,treeN:state.tree[0].length,units:us});return us;
  }
  return active(input,state).map(cid=>{
  const ids=members(state,cid),n=ids.reduce((s,i)=>s+input.data.atoms[i].n,0),mean=Array(input.data.n_samples).fill(0),sq=mean.slice();
  for(const i of ids){const a=input.data.atoms[i];for(let j=0;j<mean.length;j++){mean[j]+=a.sum[j];sq[j]+=a.sumsq[j];}}
  mean.forEach((v,j)=>mean[j]=v/n);const sd=sq.map((v,j)=>Math.sqrt(Math.max(0,v/n-mean[j]**2)));
  const traces=ids.flatMap(i=>input.data.atoms[i].traces).sort((a,b)=>input.times[a.row]-input.times[b.row]);
  return {id:cid,n,mean,sd,traces:sample(traces,24),split:!!splitPlan(input,state,cid)};
 });}
 function sample(values,n){return values.length<=n?Array.from(values):Array.from({length:n},(_,i)=>values[Math.floor(i*(values.length-1)/(n-1))]);}
 function correlation(a,b){let ma=a.reduce((s,v)=>s+v,0)/a.length,mb=b.reduce((s,v)=>s+v,0)/b.length,xy=0,xx=0,yy=0;for(let i=0;i<a.length;i++){xy+=(a[i]-ma)*(b[i]-mb);xx+=(a[i]-ma)**2;yy+=(b[i]-mb)**2;}return xx&&yy?Math.max(-1,Math.min(1,xy/Math.sqrt(xx*yy))):null;}
 function pairKey(state,a,b){return [a,b].sort((x,y)=>x-y).map(cid=>cid+':'+signature(state,cid)).join('|');}
 function task(source,target=null){return {key:target==null?'cluster:'+source:`pair:${source}:${target}`,source,target,phase:target==null?'phase1':'phase2'};}
 function clusterTasks(input,state){return state.complete?[]:active(input,state).filter(cid=>state.kept[cid]!==signature(state,cid)).map(cid=>task(cid));}
 function pairTasks(input,state){
  if(state.complete)return [];const us=units(input,state),pairs=new Map();
  for(const u of us){let best=null,score=-Infinity;for(const other of us){if(u.id===other.id||state.separate[pairKey(state,u.id,other.id)])continue;const r=correlation(u.mean,other.mean)??-1;if(r>score){score=r;best=other;}}if(best){const ids=[u.id,best.id].sort((a,b)=>a-b);pairs.set(ids.join(':'),{...task(ids[0],ids[1]),correlation:score});}}
  return [...pairs.values()].sort((a,b)=>b.correlation-a.correlation||a.source-b.source||a.target-b.target);
 }
 function tasks(input,state){return [...clusterTasks(input,state),...pairTasks(input,state)];}
 // Retain the old phase field for exports. It describes pending work, never gates actions.
 function transition(input,state){state.workflow='context-v2';state.phase=clusterTasks(input,state).length?'phase1':'phase2';return state;}
 function interval(times){let counts=Array(100).fill(0),bad=0,out=0;for(let i=1;i<times.length;i++){const gap=times[i]-times[i-1],ms=gap*1000;if(gap<.002)bad++;if(ms>50)out++;else if(ms>=0)counts[Math.min(99,Math.floor(ms*2))]++;}const denominator=Math.max(0,times.length-1);return {counts,violation_count:bad,denominator,violation_rate:denominator?bad/denominator:null,outside_display_count:out};}
 function quality(input,state,cid){
  const rows=[];for(const i of input.order)if(rowLabel(input,state,i)===cid)rows.push(i);
  if(!rows.length)throw Error('Cluster no longer exists');let sum=0,sq=0;for(const i of rows){sum+=input.amps[i];sq+=input.amps[i]**2;}const mean=sum/rows.length,sd=Math.sqrt(Math.max(0,sq/rows.length-mean**2)),k=Math.max(1,Math.ceil(rows.length*.2));
  const early=rows.slice(0,k).reduce((s,i)=>s+input.amps[i],0)/k,late=rows.slice(-k).reduce((s,i)=>s+input.amps[i],0)/k;
  return {id:cid,n:rows.length,isi:interval(rows.map(i=>input.times[i])),mean_amplitude:mean,amplitude_sd:sd,amplitude_cv:mean?sd/mean:null,drift_percent:early&&rows.length>1?(late-early)/early*100:null,scatter:sample(rows,500).map(i=>[input.times[i],input.amps[i]])};
 }
 function pairISI(input,state,a,b){const times=[];for(const i of input.order)if([a,b].includes(rowLabel(input,state,i)))times.push(input.times[i]);return interval(times);}
 const metricCache=new WeakMap();
 function unitMetrics(input,state){
  if(metricCache.has(state.labels))return metricCache.get(state.labels);const out=new Map();for(const cid of active(input,state))out.set(cid,{n:0,violations:0,last:null});
  for(const i of input.order){const r=out.get(rowLabel(input,state,i));if(!r)continue;if(r.last!==null&&input.times[i]-r.last<.002)r.violations++;r.last=input.times[i];r.n++;}
  for(const r of out.values())r.rate=r.n>1?r.violations/(r.n-1):null;metricCache.set(state.labels,out);return out;
 }
 function correlogram(input,state,a,b=a){
  const first=[],second=[];for(const i of input.order){const cid=rowLabel(input,state,i);if(cid===a)first.push(input.times[i]);if(cid===b)second.push(input.times[i]);}
  const counts=Array(100).fill(0),bin=.001,half=.05;
  // Count every pair with sorted sliding bounds. Intervals are [-50,50) ms, B minus A.
  for(let k=0;k<100;k++){let lo=0,hi=0;const left=-half+k*bin,right=-half+(k+1)*bin;for(let i=0;i<first.length;i++){while(lo<second.length&&second[lo]-first[i]<left)lo++;if(hi<lo)hi=lo;while(hi<second.length&&second[hi]-first[i]<right)hi++;counts[k]+=hi-lo;if(a===b&&left<=0&&right>0)counts[k]--;}}
  return {counts,bin_ms:1,min_ms:-50,max_ms:50,source:a,target:b,n_source:first.length,n_target:second.length,self_pairs_excluded:a===b,normalization:'raw pair counts'};
 }
 function reference(input,hash,task){return (input.data.references||[]).filter(r=>r.state_sha256===hash&&r.phase===task.phase&&r.source===task.source&&(r.target??null)===(task.target??null));}
 function suggestion(input,hash,task,agent){return [...(input.data.agent_suggestions||[])].reverse().find(r=>r.state_sha256===hash&&r.phase===task.phase&&r.source===task.source&&(r.target??null)===(task.target??null)&&r.agent_id===agent)||null;}
 function retainedOperations(events){const stack=[];for(const e of events||[]){if(e.type==='choice_applied'||e.type==='finished')stack.push(e);else if(e.type==='undo')stack.pop();}return stack;}
 const statusMemberships=new WeakMap();
 function statusMembershipsFor(s){
  if(statusMemberships.has(s))return statusMemberships.get(s);
  const parts=new Map(),add=(id,value)=>{if(id>0){if(!parts.has(id))parts.set(id,[]);parts.get(id).push(value);}};
  s.labels.forEach((id,i)=>add(id,'a'+i));
  for(const [row,v] of Object.entries(s.row_labels||{})){const base=s.labels[v.atom];if(base!==v.cluster){add(base,'out'+row);add(v.cluster,'in'+row);}}
  const result=new Map([...parts].map(([id,values])=>[id,values.join(';')]));statusMemberships.set(s,result);return result;
 }
 // Find an applied grouping operation for this exact current population, including
 // children absent from legacy event.object_after. Undo snapshots retain both sides.
 function clusterOperation(s,cid){
  const operations=retainedOperations(s.events),current=statusMembershipsFor(s).get(cid);if(!current)return null;
  const aligned=s.undo?.length===operations.length;
  for(let i=operations.length-1;i>=0;i--){
   const e=operations[i];if(e.type!=='choice_applied'||!['SPLIT','MERGE'].includes(e.choice?.action))continue;
   if(aligned){const before=statusMembershipsFor(s.undo[i]).get(cid),after=statusMembershipsFor(i+1<operations.length?s.undo[i+1]:s).get(cid);if(after===current&&before!==after)return e;}
   else if(e.object_after?.[cid]===signature(s,cid)&&e.object_before?.[cid]!==e.object_after[cid])return e;
  }
  return null;
 }
 function summary(input,s){const us=units(input,s);return {active_clusters:us.length,active_spikes:us.reduce((n,u)=>n+u.n,0),accepted_clusters:us.filter(u=>s.kept[u.id]===signature(s,u.id)).length,complete:s.complete};}
 function objects(s,ids){return Object.fromEntries(ids.filter(id=>id!=null).map(id=>[id,(s.labels.includes(id)||Object.values(s.row_labels||{}).some(v=>v.cluster===id))?signature(s,id):null]));}
 function snapshot(s){return clone({labels:s.labels,row_labels:s.row_labels||{},tree:s.tree,kept:s.kept,separate:s.separate,deferred:s.deferred,phase:s.phase,complete:s.complete});}
 function manualPlan(input,state,cid,rows){
  if(!Array.isArray(rows)||!rows.length||rows.length>=input.data.n_spikes)throw Error('Select a nonempty subset of source spikes');const seen=new Set();for(const r of rows){if(!Number.isInteger(r)||r<0||r>=input.data.n_spikes||seen.has(r)||rowLabel(input,state,r)!==cid)throw Error('Manual selection contains duplicate or non-source rows');seen.add(r);}let total=0;for(let r=0;r<input.data.n_spikes;r++)if(rowLabel(input,state,r)===cid)total++;if(rows.length>=total)throw Error('Leave at least one source spike unselected');let next=0;for(const x of [...state.labels,...state.tree[0],...state.tree[1],...Object.values(state.row_labels||{}).map(v=>v.cluster)])next=Math.max(next,x);if(next>=2147483647)throw Error('No cluster ID available');return {breakaway:next+1,rows:rows.slice().sort((a,b)=>a-b),total};
 }
 function mutate(input,s,task,action,choice={}){
  if(action==='SPLIT'){
   let plan;if(choice.manual_rows){plan=manualPlan(input,s,task.source,choice.manual_rows);s.labels=s.labels.slice();s.row_labels={...s.row_labels};for(const row of plan.rows)s.row_labels[row]={atom:input.index[row],cluster:plan.breakaway};for(let i=s.tree[0].length-1;i>=0;i--)if(s.tree[0][i]===task.source||s.tree[1][i]===task.source)for(const row of s.tree)row.splice(i,1);}else{plan=splitPlan(input,s,task.source);if(!plan)throw Error('No valid two-child split exists');for(const i of plan.atoms)s.labels[i]=plan.breakaway;for(const row of s.tree)row.splice(plan.last,1);}delete s.kept[task.source];delete s.kept[plan.breakaway];
  }else if(action==='DISCARD'){
   s.labels=s.labels.map(c=>c===task.source?0:c);s.row_labels=Object.fromEntries(Object.entries(s.row_labels||{}).map(([r,v])=>[r,{...v,cluster:v.cluster===task.source?0:v.cluster}]));s.tree.forEach((row,i)=>row.push([0,task.source,0,0][i]));delete s.kept[task.source];
  }else if(action==='MERGE'){
   s.labels=s.labels.map(c=>c===task.source?task.target:c);s.row_labels=Object.fromEntries(Object.entries(s.row_labels||{}).map(([r,v])=>[r,{...v,cluster:v.cluster===task.source?task.target:v.cluster}]));s.tree.forEach((row,i)=>row.push([task.target,task.source,1,0][i]));delete s.kept[task.source];delete s.kept[task.target];s.phase='phase1';
  }else if(action==='KEEP')s.kept[task.source]=signature(s,task.source);
  else if(action==='NOT_MERGE')s.separate[pairKey(s,task.source,task.target)]=true;
  else {s.deferred[task.key]=String(choice.reason||choice.note);if(task.target==null)delete s.kept[task.source];else delete s.separate[pairKey(s,task.source,task.target)];}
  transition(input,s);return s;
 }
 function preview(input,original,task,action,choice={}){
  validateRowLabels(input,original);
  if(original.complete)throw Error('This workspace is finished');
  if(!['SPLIT','MERGE'].includes(action)||!active(input,original).includes(task.source))throw Error('Choose a valid split or merge');
  if(action==='SPLIT'&&task.target!=null||action==='MERGE'&&(task.target==null||task.target===task.source||!active(input,original).includes(task.target)))throw Error('Choose the appropriate cluster or pair');
  const projected={...original,labels:original.labels.slice(),row_labels:clone(original.row_labels||{}),tree:clone(original.tree),kept:{...original.kept},separate:{...original.separate},deferred:{...original.deferred}};
  return mutate(input,projected,task,action,choice);
 }
 async function apply(input,original,task,choice,expectedVersion){
  validateRowLabels(input,original);
  if(choice.manual_rows&&choice.agree===true)throw Error('A custom partition is not the saved Agent split');
  if(expectedVersion!==original.version)throw Error('Workspace changed; reload before applying');
  if(original.complete)throw Error('This workspace is finished; undo or start a new workspace');
  const s=clone(original),us=active(input,s),action=choice.action;
  if(!us.includes(task.source)||task.phase!==(task.target==null?'phase1':'phase2'))throw Error('This task has an invalid cluster or comparison context');
  const allowed=task.phase==='phase1'?['KEEP','SPLIT','DISCARD','DEFER']:['MERGE','NOT_MERGE','DISCARD','DEFER'];
  if(!allowed.includes(action))throw Error('Action is unavailable for this single-cluster or pair context');
  if(task.phase==='phase2'&&(!us.includes(task.target)||task.target===task.source))throw Error('Choose two different active clusters');
  const before=await stateHash(input,s),ai=suggestion(input,before,task,choice.agent_id);
  if(choice.agree!=null&&typeof choice.agree!=='boolean')throw Error('Invalid agreement');
  if(choice.agree===true&&(!ai||ai.action!==action))throw Error('Agreement must preserve the matched Agent action');
  if((choice.agree===false||action!=='KEEP'&&choice.agree!==true)&&!String(choice.reason||'').trim()&&!String(choice.note||'').trim())throw Error('Add a reason or note for this choice');
  if(choice.confidence&&!['high','medium','low'].includes(choice.confidence))throw Error('Invalid confidence');
  const old=snapshot(s),counts=units(input,s).map(u=>({id:u.id,n:u.n})),summaryBefore=summary(input,s),objectBefore=objects(s,[task.source,task.target]);
  delete s.deferred[task.key];
  mutate(input,s,task,action,choice);
  const after=await stateHash(input,s);s.version++;
  s.undo.push(old);s.events.push({id:cryptoAPI().randomUUID(),type:'choice_applied',version:s.version,reviewer:s.reviewer,dataset_id:s.dataset_id,workspace_id:s.workspace_id,object_before:objectBefore,object_after:objects(s,[task.source,task.target]),summary_before:summaryBefore,summary_after:summary(input,s),task:clone(task),judgment_context:task.target==null?'single':'pair',choice:clone(choice),timestamp:new Date().toISOString(),state_before:before,state_after:after,counts_before:counts,counts_after:units(input,s).map(u=>({id:u.id,n:u.n})),execution_status:action==='DEFER'?'deferred':before===after?'recorded_no_membership_change':'applied'});return s;
 }
 async function undo(input,original,reviewer){if(!original.undo.length)throw Error('No operation to undo');const s=clone(original),before=await stateHash(input,s),last=retainedOperations(s.events).at(-1),countsBefore=units(input,s).map(u=>({id:u.id,n:u.n})),summaryBefore=summary(input,s);Object.assign(s,snapshot(s.undo.pop()));s.version++;s.reviewer=reviewer;s.events.push({id:cryptoAPI().randomUUID(),type:'undo',version:s.version,reviewer,dataset_id:s.dataset_id,workspace_id:s.workspace_id,undo_of:last?.id||null,undone_version:last?.version??null,task:last?.task?clone(last.task):null,summary_before:summaryBefore,summary_after:summary(input,s),counts_before:countsBefore,counts_after:units(input,s).map(u=>({id:u.id,n:u.n})),timestamp:new Date().toISOString(),state_before:before,state_after:await stateHash(input,s)});return s;}
 async function finish(input,original){if(original.complete)throw Error('This workspace is already finished');const s=clone(original),pending=pairTasks(input,s).length,pendingClusters=clusterTasks(input,s).length,before=summary(input,s);s.undo.push(snapshot(s));s.complete=true;s.version++;s.events.push({id:cryptoAPI().randomUUID(),type:'finished',version:s.version,reviewer:s.reviewer,dataset_id:s.dataset_id,workspace_id:s.workspace_id,summary_before:before,summary_after:summary(input,s),timestamp:new Date().toISOString(),unreviewed_cluster_judgments:pendingClusters,unreviewed_suggested_pairs:pending,note:'Remaining groups preserved; no KEEP or NOT_MERGE labels inferred',state_before:await stateHash(input,s),state_after:await stateHash(input,s)});return s;}
 function result(input,state){return {schema:'spikeagent-sorting-result-v1',dataset_id:state.dataset_id,source_sha256:state.source_sha256,workspace_id:state.workspace_id,version:state.version,reviewer:state.reviewer,complete:state.complete,workflow:state.workflow||'context-v2',phase:state.phase,row_index_base:0,spike_count:input.data.n_spikes,assigns:Array.from(assignments(input,state)),hierarchy_tree:clone(state.tree),hierarchy_scope:Object.keys(state.row_labels||{}).length?'Partial: manual partitions are represented by per-spike assigns; no atom-level split tree is inferred.':'Input hierarchy with confirmed operations',clusters:units(input,state).map(u=>({cluster_id:u.id,n_spikes:u.n})),operations:clone(state.events),new_model_calls:(input.data.agent_suggestions||[]).filter(r=>r.origin==='explicit-current-state-request').length,input_provenance:clone(input.data.metadata||{})};}
 return {SCHEMA,prepare,initial,rowLabel,validateRowLabels,manualPlan,assignments,stateHash,active,signature,pairKey,splitPlan,units,task,clusterTasks,pairTasks,tasks,transition,retainedOperations,clusterOperation,preview,unitMetrics,correlogram,quality,pairISI,correlation,reference,suggestion,apply,undo,finish,result,clone,digest};
});
