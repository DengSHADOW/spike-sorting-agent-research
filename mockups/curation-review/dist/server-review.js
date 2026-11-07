/* Server-computed curation: no waveform matrices, spike times or assignments downloaded. */
window.ServerReview=(()=>{
 'use strict';
 const endpoint=document.currentScript?.dataset.endpoint;
 if(!endpoint)return {configured:false};
 document.body.classList.add('server-starting');const connecting=document.createElement('section');connecting.id='server-connection-panel';connecting.innerHTML='<h2>Opening curation workspace…</h2><p id=server-connection-text>Connecting to Runpod</p><button type=button id=server-retry>Retry connection</button> <a href=data-sources.html>Data & uploads</a>';document.querySelector('#view-review').prepend(connecting);connecting.querySelector('button').onclick=()=>location.reload();
 const base=new URL(endpoint),esc=v=>escapeHtml(String(v??'')),uid=n=>'U-'+n;
 if(base.protocol!=='https:'&&base.hostname!=='127.0.0.1')throw Error('Secure compute endpoint required');
 let data=null,selection=null,virtual=null,preview=null,action=null,busy=false,editing=false,token='',owner='',catalog=[],agent='Agent_1',imageEpoch=0,waveKey='',metricKey='',queuedChoice=null,pendingRequest=null,revision=null,editOpening=false;
 let display='both',axes=1,opacity=[1,1],lastMetric='timing',datasetHandler=null;const blobs=new Map();let thumbObserver=null,imageQueue=Promise.resolve();
 const active=()=>!!data,storageKey=()=> 'spikeagent-server-curation-v1:'+reviewerId;
 function stored(){try{return JSON.parse(localStorage.getItem(storageKey())||'{}');}catch{return {};}}
 function remember(patch){localStorage.setItem(storageKey(),JSON.stringify({...stored(),...patch}));}
 async function apiCall(op,body={},auth=true){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),115000);
  try{const r=await fetch(new URL('api/compute/'+op,base),{method:'POST',credentials:'omit',headers:{'Content-Type':'application/json',...(auth?{Authorization:'Bearer '+token}:{})},body:JSON.stringify(body),signal:controller.signal});
   if(!r.ok){let detail;try{detail=await r.json();}catch{}throw Object.assign(Error(detail?.error||'Server calculation unavailable ('+r.status+')'),{status:r.status});}
   return op==='figure'?await r.blob():await r.json();
  }catch(e){if(e.name==='AbortError')throw Error('Calculation took too long. Reload to check the saved session.');throw e;}finally{clearTimeout(timer);}
 }
 async function connect(){
  if(owner===reviewerId&&token)return;
  owner=reviewerId;const old=stored();token=old.token||'';
  if(!token){const result=await apiCall('session',{},false);token=result.token;remember({token});}
  try{catalog=(await apiCall('datasets')).datasets;}catch(e){if(e.status!==401)throw e;remember({token:''});token='';owner='';throw Error('Your saved server session expired. Click Data & sessions to start again.');}
 }
 const request=extra=>({workspace_id:data.state.workspace_id,version:data.state.version,source:selection?.source??null,target:selection?.target??null,...(revision?{revision_id:revision.id}:{}),...extra});
 function status(text){if($('#server-connection-text'))$('#server-connection-text').textContent=text;$('#inline-working-status').hidden=false;$('#inline-working-status').textContent=text;const host=$('#inline-working-message');if(host)host.textContent=text;}
 function resetForm(){action=null;preview=null;editing=false;draft={agree:null,action:null,target:selection?.target!=null?uid(selection.target):'',scope:selection?.target==null?'single':'pair',reason:'',note:'',positive:'',confidence:null,sufficiency:'unspecified',missing:[],missing_note:'',disagreement_types:[]};isDirty=false;tabsViewed=new Set();for(const id of ['reason-select','review-note','positive-comment'])$('#'+id).value='';$('#form-error').textContent='';hideReasonMatches();}
 function makeCase(){
  if(!data)return;const ai=data.suggestions.find(r=>r.agent_id===agent);
  virtual={id:'server:'+data.state.workspace_id+':'+data.state.version+':'+(selection?.source??'empty'),working:true,server:true,synthetic:false,review_mode:ai?'working_agent':'manual',dataset_id:data.state.dataset_id,channel:data.state.dataset_id.startsWith('upload-')?data.state.recording_id:data.state.dataset_id.split('_').at(-1),state_sha256:data.state_sha256,source:uid(selection?.source),pair:selection?.target!=null?[uid(selection.source),uid(selection.target)]:null,phase_id:selection?.target==null?'phase1':'phase2',units:data.units.map((u,i)=>({id:uid(u.id),cluster_id:u.id,title:'Cluster '+u.id,spikes:u.n,split:u.split,color:FULL_WAVEFORM_COLORS[i%2],kind:'real'})),ai:ai?.action||'KEEP',rationale:ai?.rationale||'',study_agent_id:agent,evidenceLoaded:true};
  selectedUnitId=virtual.source;comparisonChoices.set(virtual.id,selection?.target!=null?uid(selection.target):'');
 }
 function accept(result){document.body.classList.remove('server-starting');data=result;selection=result.selected;revision=result.revision||null;preview=result.preview?{...result.preview,action:result.preview.action}:null;makeCase();remember({workspace_id:data.state.workspace_id,dataset_id:data.state.dataset_id,source:revision?revision.original_selected.source:selection?.source,target:revision?revision.original_selected.target:selection?.target,agent});}
 async function run(fn){
  if(busy)return;busy=true;if(active())renderActions();$('#reviewer-menu').disabled=true;
  try{return await fn();}catch(e){$('#form-error').textContent=e.message;status(e.message);toast(e.message);return null;}
  finally{busy=false;$('#reviewer-menu').disabled=false;if(active()){renderActions();renderChoice();}const next=queuedChoice;queuedChoice=null;if(next)choose(...next);}
 }
 function cacheDraft(){if(!active())return;if(revision){remember({draft:null});return;}remember({draft:action?{version:data.state.version,source:selection?.source,target:selection?.target,action,preview_id:preview?.id,agree:draft.agree,reason:$('#reason-select').value,note:$('#review-note').value,positive:$('#positive-comment').value}:null});}
 async function open(dataset,workspace=null,context=null){
  revision=null;await connect();if(!catalog.some(r=>r.dataset_id===dataset))throw Error('This dataset is unavailable in the current private session.');
  status('Opening '+dataset.split('_').at(-1)+' on Runpod…');
  const old=stored(),args={dataset_id:dataset};if(workspace)args.workspace_id=workspace;
  if(context?.checkpoint)args.checkpoint=context.checkpoint;
  if(Number.isInteger(context?.source))args.source=context.source;else if(workspace&&Number.isInteger(old.source))args.source=old.source;
  if(Number.isInteger(context?.target))args.target=context.target;else if(workspace&&Number.isInteger(old.target))args.target=old.target;
  resetForm();display='both';accept(await apiCall('open',args));waveKey=metricKey='';render();$('#inline-working-dialog')?.close();status('Connected · data stays on Runpod');
  const url=new URL(location.href);url.searchParams.delete('case');history.replaceState(null,'',url.pathname+url.search+url.hash);
  const saved=old.draft;if(saved&&saved.version===data.state.version&&saved.source===selection?.source&&(saved.target??null)===(selection?.target??null)){
   $('#reason-select').value=saved.reason||'';$('#review-note').value=saved.note||'';$('#positive-comment').value=saved.positive||'';
   if(saved.preview_id){try{accept(await apiCall('view',request({preview_id:saved.preview_id})));action=draft.action=saved.action;draft.agree=saved.agree;isDirty=true;editing=true;waveKey=metricKey='';render();}catch{queuedChoice=[saved.action,saved.agree];}}
   else queuedChoice=[saved.action,saved.agree];renderReasons();
  }
 }
 function render(){if(!active())return;makeCase();renderQueue();renderCase();}
 function renderAgentSelection(){if(!active())return;$('#agent-picker').hidden=false;$('#agent-choice').innerHTML='<option value="Agent_1">Agent 1</option><option value="Agent_2">Agent 2</option>';$('#agent-choice').value=agent;$('#agent-card-title').textContent='Agent';$('#agent-choice').onchange=()=>{agent=$('#agent-choice').value;remember({agent});makeCase();renderCase();};}
 function renderCase(){
  makeCase();renderAgentSelection();const suggestion=data.suggestions.find(r=>r.agent_id===agent);
  $('#case-title').textContent=selection?'Cluster '+selection.source+(selection.target!=null?' vs. Cluster '+selection.target:''):'No active clusters';$('#case-position').textContent=virtual.channel+' · v'+data.state.version;
  $('#suggestion').innerHTML=suggestion?'<div class="suggestion-card"><div class="suggestion-action"><strong>'+esc(suggestion.action)+'</strong><span>Cluster '+suggestion.source+(suggestion.target!=null?' vs. Cluster '+suggestion.target:'')+'</span></div><div class="rationale-label">Rationale</div><p class="rationale">'+esc(suggestion.rationale||'No rationale was retained.')+'</p></div>':'<div class="suggestion-card"><p>No saved Agent suggestion for this grouping and selection.</p></div>';
  const groups=preview?.action==='SPLIT'?data.groups:[selection?.source,selection?.target].filter(n=>n!=null).map(id=>data.units.find(u=>u.id===id));
  $('#metrics').innerHTML='<div class="metrics-table"><div class="metric-table-header">'+['Cluster','Spikes','Detail','Active','Evidence'].map(v=>'<span>'+v+'</span>').join('')+'</div>'+groups.map((g,i)=>'<div class="metric-table-row"><strong style="color:'+FULL_WAVEFORM_COLORS[i]+'">Cluster '+g.id+'</strong><span>'+g.n.toLocaleString()+'</span><span>'+(waveformMemberMode==='sample24'?Math.min(24,g.n)+' sampled':'All waveforms')+'</span><span>'+(data.units.length+(preview?.action==='SPLIT'?1:0))+'</span><span>Server data</span></div>').join('')+'</div>';
  $('#packet-button').disabled=true;$('#inline-start-working').textContent='Data & uploads';$('#inline-return-snapshots').hidden=true;$('#export-button').textContent='Export operation log';$('#source-badge').textContent='SERVER COMPUTATION';$('#inline-working-status').hidden=false;
  renderActions();renderEvidence();renderChoice();
 }
 function latestChoice(choice,operation){return operation&&(!choice||operation.version>choice.version)?operation:choice;}
 function renderActions(){
  if(!active())return;const suggestion=data.suggestions.find(r=>r.agent_id===agent),saved=data.choice,source=data.units.find(u=>u.id===selection?.source);
  $('#review-editor').hidden=!selection||!!saved&&!editing&&!action&&!revision;$('.action-fieldset legend').textContent='Your action';$('.action-fieldset>p').textContent='';$('.agreement-fieldset').hidden=!suggestion;$('#correction-panel').hidden=false;$('#correction-panel .reason-section').hidden=!action||draft.agree===true;$('.disagreement-details').hidden=true;$('#merge-controls').hidden=true;$('#validity-warning').innerHTML='';
  const ids=!selection?[]:selection.target==null?['KEEP','SPLIT','DISCARD']:['MERGE','NOT_MERGE','DISCARD'];
  $('#action-grid').innerHTML=ids.map(id=>'<button type="button" class="action-option '+(action===id?'selected':'')+'" data-action="'+id+'" '+(busy||id==='SPLIT'&&source.n<2?'disabled':'')+'><span class="action-icon">'+ACTIONS[id].icon+'</span><span>'+ACTIONS[id].label+'</span></button>').join('');
  for(const b of $('#action-grid').querySelectorAll('[data-action]'))b.onclick=()=>choose(b.dataset.action,suggestion?false:null);
  $('#action-context').innerHTML=action==='SPLIT'&&!preview?'<p class="action-hint">Draw around part of the source cluster in PCA, then preview.</p>':preview?'':'';
  $('.reason-section>label[for="general-reason-choice"] span').textContent=draft.agree===true||action==='KEEP'&&draft.agree!==false?'optional':'reason or note required';
  $('#save-button').disabled=busy||!action||!selection||['SPLIT','MERGE'].includes(action)&&!preview?.ready;
  $('#save-button').textContent=busy?'Computing…':preview&&!preview.ready?'Rendering preview…':action==='SPLIT'&&!preview?'Select spikes in PCA':action?'Confirm '+ACTIONS[action].label.toLowerCase():'Confirm choice';
  $('#inline-working-undo').disabled=busy||!data.state.undo.length;$('#positive-panel').hidden=true;$('#decision-preview').textContent='';window.ReviewStudio?.syncActions();
 }
 async function editSavedChoice(row){
  if(busy||editOpening)return;editOpening=true;
  try{
   const operation=data.units.find(u=>u.id===selection?.source)?.operation;
   if(operation){
    const plan=await apiCall('revision-plan',request({event_id:operation.id})),answer=await ReviewStudio.confirmGroupingEdit(plan);
    if(answer==='cancel')return;
    if(answer==='replace')return await run(async()=>{const result=await apiCall('revision-start',request({event_id:operation.id}));resetForm();accept(result);editing=true;remember({draft:null});waveKey=metricKey='';render();status('Restored grouping preview · confirm a replacement action to save');});
   }
   if(['KEEP','NOT_MERGE'].includes(row.choice.action)){editing=true;$('#reason-select').value=row.choice.reason||'';$('#review-note').value=row.choice.note||'';await choose(row.choice.action,row.choice.agree);}
   else{resetForm();editing=true;renderReasons();renderActions();renderChoice();}
  }catch(e){status(e.message);$('#form-error').textContent=e.message;}
  finally{editOpening=false;}
 }
 function renderChoice(){
  if(!active())return;const row=latestChoice(data.choice,data.units.find(u=>u.id===selection?.source)?.operation),other=latestChoice(data.comparison_choice,data.units.find(u=>u.id===selection?.target)?.operation),otherPanel=$('#comparison-choice-card'),refs=$('#jacob-reference-panel');
  $('#user-choice-card').innerHTML=action?choiceMarkup({action,reason:$('#reason-select').value,note:$('#review-note').value,positive:$('#positive-comment').value},{status:preview?'Preview':'Draft',source:'Cluster '+selection.source,title:'User choice',viewing:true}):row?choiceMarkup(row.choice,{status:'Saved',source:'Cluster '+selection.source,title:'User choice',viewing:true})+(!editing&&!revision?'<button class="button small" id="server-edit-choice">Edit choice</button>':''):'<div class="choice-card-heading"><h3>User choice</h3></div><p>No saved choice for this grouping and selection.</p>';
  if(otherPanel){otherPanel.hidden=selection?.target==null;otherPanel.innerHTML=other?choiceMarkup(other.choice,{status:'Saved',source:'Cluster '+selection.target,title:'Comparison cluster choice'}):'<p>Cluster '+selection?.target+' has not been reviewed in this grouping.</p>';$('#user-choice-card').append(otherPanel);}
  if(refs){refs.querySelector('#jacob-reference-content').textContent=data.references.length?data.references.map(r=>r.action+' · '+(r.reason||'No reason recorded')).join('\n'):'No exact reference matches this grouping and selection.';$('#user-choice-card').append(refs);}
  $('#server-edit-choice')?.addEventListener('click',()=>editSavedChoice(row));
 }
 function renderQueue(){
  if(!active())return;const q=$('#case-search').value.trim().toLowerCase(),units=data.units.filter(u=>(!q||('cluster '+u.id+' '+ActivityCore.origin(u.operation)).toLowerCase().includes(q))&&(filter==='all'||(filter==='reviewed')===u.reviewed));
  units.sort((a,b)=>(queueSort==='isi'?(a.isi_rate??Infinity)-(b.isi_rate??Infinity):b.n-a.n)||a.id-b.id);
  $('#queue-mode').disabled=true;$('#snapshot-options').hidden=true;$('#phase-filter').closest('label').hidden=true;$('#data-source').innerHTML=catalog.map(r=>'<option value="'+esc(r.dataset_id)+'">'+esc(r.recording_id||r.dataset_id)+'</option>').join('');$('#data-source').value=data.state.dataset_id;$('#data-source').disabled=busy;$('#data-source').onchange=()=>run(()=>{cacheDraft();return open($('#data-source').value);});
  $('#queue-sort').value=queueSort;$('#queue-sort').disabled=false;$('#queue-sort').onchange=()=>{queueSort=$('#queue-sort').value;localStorage.setItem('spike-review-queue-sort',queueSort);renderQueue();};
  const progress=data.units.length-data.pending;
  $('#case-list').innerHTML='<details class="queue-file-group" open><summary><strong>'+esc(data.state.dataset_id.startsWith('upload-')?data.state.recording_id:virtual.channel+'_spikes.mat')+'</strong><div class="file-progress"><div class="file-progress-track"><span style="width:'+(data.units.length?progress/data.units.length*100:0)+'%"></span></div><small>'+progress+' / '+data.units.length+' reviewed</small></div></summary><div class="queue-cluster-grid">'+units.map(u=>{const saved=latestChoice(u.choice,u.operation),pending=u.id===selection?.source&&action;return '<button class="case-item queue-thumb-card '+(u.id===selection?.source?'active':'')+' '+(u.id===selection?.target?'comparison-target':'')+'" data-server-cluster="'+u.id+'"><span class="queue-thumb-title">Cluster '+u.id+'</span><span class="queue-wave-preview"><canvas width="360" height="160" aria-label="Cluster '+u.id+' waveform thumbnail"></canvas></span><span class="queue-thumb-count">'+u.n.toLocaleString()+' spikes</span><span class="queue-thumb-quality">ISI '+(u.isi_rate==null?'—':(u.isi_rate*100).toFixed(2)+'%')+'</span><span class="queue-item-meta">'+(pending?'<span class="queue-action-tag draft-tag">'+esc(action)+(preview?' · Preview':' · Draft')+'</span>':saved?'<span class="queue-action-tag">'+esc(saved.choice.action)+(u.operation?' · Saved':'')+'</span>':'')+(u.reviewed?'<span class="queue-review-label">✓ Reviewed</span>':'')+'</span></button>';}).join('')+'</div></details>';
  thumbObserver?.disconnect();thumbObserver=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){thumbObserver.unobserve(entry.target);drawThumbnail(entry.target,Number(entry.target.closest('button').dataset.serverCluster));}},{root:$('#queue-panel'),rootMargin:'40px'});
  for(const button of document.querySelectorAll('[data-server-cluster]')){button.onclick=e=>pick(e.shiftKey&&selection&&selection.source!==Number(button.dataset.serverCluster)?selection.source:Number(button.dataset.serverCluster),e.shiftKey&&selection&&selection.source!==Number(button.dataset.serverCluster)?Number(button.dataset.serverCluster):null);thumbObserver.observe(button.querySelector('canvas'));}
  $('.queue-heading .count').textContent=data.units.length;$('.filters [data-filter=all] span').textContent=data.units.length;$('#pending-count').textContent=data.pending;$('#reviewed-count').textContent=progress;$('#queue-result-summary').textContent=data.pending+' clusters pending';$('#queue-sort-status').textContent='Shift-click a second cluster to compare.';$('#clear-queue-filters').hidden=true;
 }
 function pick(source,target=null){if(busy)return;if(revision&&source!==revision.task.source){status('Confirm or cancel this grouping edit before selecting another source cluster');return;}cacheDraft();selection={source,target,key:target==null?'cluster:'+source:'pair:'+source+':'+target,phase:target==null?'phase1':'phase2'};resetForm();editing=!!revision;display='both';makeCase();remember({draft:null});return run(async()=>{accept(await apiCall('view',request()));render();});}
 function selectComparison(target){return pick(selection.source,target?Number(String(target).replace(/^U-/,'')):null);}
 function cancelChoice(){if(busy)return;if(revision)return run(async()=>{const result=await apiCall('revision-cancel',request());resetForm();accept(result);remember({draft:null});waveKey=metricKey='';render();status('Grouping edit canceled; saved assignments unchanged');});if(!active()){clearLocalDraft(selectedId);selectCase(selectedId,true);resetForm();const target=comparisonTarget(currentCase());draft.scope=target?'pair':'single';draft.target=target||'';editingSaved=false;window.renderActions();window.renderEvidence();renderChoiceSummaries();window.renderQueue();return;}resetForm();remember({draft:null});waveKey=metricKey='';run(async()=>{accept(await apiCall('view',request()));render();});}
 async function choose(value,agree=null){
  if(!value){resetForm();editing=!!revision;remember({draft:null});waveKey=metricKey='';render();return;}
  if(busy){queuedChoice=[value,agree];return;}
  action=draft.action=value;draft.agree=agree;isDirty=true;editing=true;preview=null;display='both';
  return run(async()=>{
   if(['SPLIT','MERGE'].includes(value)){status('Computing '+value.toLowerCase()+' preview…');const result=await apiCall('preview',request({action:value}));accept(result);if(result.manual_split_required)selectedTab=lastMetric='pca';else selectedTab='detail';}
   waveKey=metricKey='';cacheDraft();render();status('Connected · data stays on Runpod');
  });
 }
 function agreement(agree){const ai=data.suggestions.find(r=>r.agent_id===agent);if(agree&&ai)return choose(ai.action,true);draft.agree=false;editing=true;action=draft.action=null;preview=null;isDirty=true;renderActions();renderChoice();}
 async function confirm(){return run(async()=>{if(!action)throw Error('Choose an action');if(['SPLIT','MERGE'].includes(action)&&!preview?.ready)throw Error('Inspect the current preview first');const choice={action,agree:draft.agree,agent_id:agent,reason:$('#reason-select').value,note:$('#review-note').value,positive:$('#positive-comment').value};const expected=data.state.version;
   pendingRequest=pendingRequest?.version===expected&&JSON.stringify(pendingRequest.choice)===JSON.stringify(choice)?pendingRequest:{version:expected,choice,id:crypto.randomUUID()};
   const result=await apiCall('confirm',request({preview_id:preview?.id,choice,request_id:pendingRequest.id}));resetForm();remember({draft:null});pendingRequest=null;accept(result);waveKey=metricKey='';render();status('Saved · v'+data.state.version);});}
 async function undo(){if(revision)return cancelChoice();return run(async()=>{const result=await apiCall('undo',request({request_id:crypto.randomUUID()}));resetForm();remember({draft:null});accept(result);waveKey=metricKey='';render();status('Undone · v'+data.state.version);});}
 async function blobFigure(args){const key=JSON.stringify([owner,args]);if(blobs.has(key))return blobs.get(key);let result;for(let n=0;n<3;n++){try{result=await apiCall('figure',args);break;}catch(e){if(e.status!==429||n===2)throw e;await new Promise(resolve=>setTimeout(resolve,500*(n+1)));}}const url=URL.createObjectURL(result);blobs.set(key,url);while(blobs.size>48){const [k,v]=blobs.entries().next().value;URL.revokeObjectURL(v);blobs.delete(k);}return url;}
 function drawThumbnail(canvas,cid){if(!active())return;const params=request({figure:'thumbnail',cluster_id:cid}),workspace=data.state.workspace_id,version=data.state.version;imageQueue=imageQueue.catch(()=>{}).then(async()=>{if(!canvas.isConnected||!active()||workspace!==data.state.workspace_id||version!==data.state.version)return;try{const url=await blobFigure(params),image=new Image();image.src=url;await image.decode();if(canvas.isConnected){canvas.getContext('2d').drawImage(image,0,0,canvas.width,canvas.height);canvas.dataset.complete='true';}}catch{canvas.title='Thumbnail unavailable; select the cluster to retry.';}});}
 async function paintImage(host,figure,key,extra={}){
  const epoch=imageEpoch,params=request({preview_id:preview?.id,figure,width:Math.round(host.clientWidth*2),...extra}),img=host.querySelector('img'),info=host.querySelector('[role=status]');
  try{const url=await blobFigure(params);if(!host.isConnected||epoch!==imageEpoch)return;img.src=url;await img.decode();if(!host.isConnected||epoch!==imageEpoch)return;img.dataset.complete='true';if(info)info.textContent='';
   if(figure==='waves'&&preview&&params.preview_id===preview.id&&display==='both'){preview.ready=true;renderActions();}
  }catch(e){if(host.isConnected&&epoch===imageEpoch){if(info)info.textContent=e.message;$('#form-error').textContent=e.message;}}
 }
 const imageFrame=(kind,caption='')=>'<div class="plot-card server-plot" data-server-figure="'+kind+'">'+(caption?'<div class="plot-key">'+esc(caption)+'</div>':'')+'<img class="server-evidence-image" alt="'+esc(kind)+' from recorded member spikes" draggable="false"><p role="status">Rendering…</p></div>';
 function renderEvidence(){
  if(!active())return;const metric=window.ReviewStudio?.metricTab()||lastMetric;lastMetric=metric;const wave=$('#studio-wave-content'),metrics=$('#evidence-content');if(!wave)return;
  for(const b of document.querySelectorAll('[data-tab]'))b.hidden=['detail','tree','amplitude'].includes(b.dataset.tab);
  $('#evidence-context').hidden=!selection;$('#evidence-context').innerHTML=selection?'<label>Compare<select id="compare-with"><option value="">None selected</option>'+data.units.filter(u=>u.id!==selection.source).map(u=>'<option value="'+uid(u.id)+'" '+(u.id===selection.target?'selected':'')+'>Cluster '+u.id+'</option>').join('')+'</select></label>':'';if($('#compare-with'))$('#compare-with').onchange=()=>selectComparison($('#compare-with').value);
  if(!selection){wave.innerHTML='<p>No active clusters. Use Undo to restore a grouping.</p>';metrics.innerHTML='';return;}
  const groups=preview?.action==='MERGE'?[selection.source,selection.target].map((id,i)=>({...data.units.find(u=>u.id===id),color:FULL_WAVEFORM_COLORS[i]})):data.groups;
  const key=[data.state.workspace_id,data.state.version,selection.source,selection.target,preview?.id,waveformMemberMode,display,opacity.join(',')].join(':');
  if(waveKey!==key){waveKey=key;imageEpoch++;const settings=$('.studio-wave-options');settings.innerHTML='<details class="studio-wave-settings"><summary>Waveform display</summary><div class="full-wave-display-controls"><label>Waveforms<select id="server-wave-mode"><option value="all">All waveforms</option><option value="sample24">24 sampled / cluster</option></select></label>'+(groups.length===2?'<label>Display<select id="server-wave-display"><option value="both">Both clusters</option>'+groups.map(g=>'<option value="'+g.id+'">Cluster '+g.id+' only</option>').join('')+'</select></label>':'')+groups.map((g,i)=>'<label style="color:'+g.color+'">Cluster '+g.id+' opacity<input type="range" min="10" max="100" value="'+opacity[i]*100+'" data-server-opacity="'+i+'"></label>').join('')+'</div></details>';
   wave.innerHTML='<div class="plot-key">'+groups.map(g=>'<span style="color:'+g.color+'">Cluster '+g.id+' · '+g.n.toLocaleString()+'</span>').join('')+'</div>'+imageFrame('waves',preview?ACTIONS[preview.action].label+' preview':'')+'<p class="plot-note">'+groups.map(g=>'Cluster '+g.id+': '+(display!=='both'&&Number(display)!==g.id?'hidden':(waveformMemberMode==='sample24'?Math.min(24,g.n):g.n).toLocaleString()+' / '+g.n.toLocaleString()+' drawn')).join(' · ')+'</p>';
   $('#server-wave-mode').value=waveformMemberMode;$('#server-wave-mode').onchange=()=>{waveformMemberMode=$('#server-wave-mode').value;localStorage.setItem('spike-review-waveform-members',waveformMemberMode);renderCase();};if($('#server-wave-display')){$('#server-wave-display').value=display;$('#server-wave-display').onchange=()=>{display=$('#server-wave-display').value;renderEvidence();};}for(const slider of document.querySelectorAll('[data-server-opacity]'))slider.onchange=()=>{opacity[Number(slider.dataset.serverOpacity)]=Number(slider.value)/100;renderEvidence();};
   paintImage(wave.querySelector('[data-server-figure]'),'waves',key,{mode:waveformMemberMode,isolate:display==='both'?null:Number(display),opacity});attachPlotZoom(wave);
  }
  const nextMetric=key+':'+metric+':'+axes;if(metricKey===nextMetric)return;metricKey=nextMetric;
  if(metric==='timing'){metrics.innerHTML=data.groups.map(g=>'<section class="studio-child-metrics"><header><strong style="color:'+g.color+'">Cluster '+g.id+'</strong><span>'+g.n.toLocaleString()+' spikes</span></header>'+plotCard('ISI','',candidateISI({merged_isi:g.quality.isi}))+'</section>').join('')+(data.pooled_isi?plotCard('If merged · pooled ISI','',candidateISI({merged_isi:data.pooled_isi})):'');}
  else if(metric==='quality'){metrics.innerHTML='<table class="quality-table"><thead><tr><th>Metric</th>'+data.groups.map(g=>'<th style="color:'+g.color+'">Cluster '+g.id+'</th>').join('')+'</tr></thead><tbody>'+[['Spikes',q=>q.n.toLocaleString()],['ISI violations <2 ms',q=>metricPercent(q.isi.violation_rate)],['Amplitude mean ± SD',q=>metricNumber(q.mean_amplitude)+' ± '+metricNumber(q.amplitude_sd)],['Amplitude CV',q=>metricNumber(q.amplitude_cv)],['Amplitude change',q=>q.drift_percent==null?'—':q.drift_percent.toFixed(2)+'%']].map(([name,format])=>'<tr><th>'+name+'</th>'+data.groups.map(g=>'<td>'+format(g.quality)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';}
  else if(metric==='correlation'){metrics.innerHTML='<p role="status">Calculating ACG / CCG…</p>';apiCall('correlation',request({preview_id:preview?.id})).then(result=>{if(metricKey!==nextMetric)return;metrics.innerHTML=result.acg.map((g,i)=>plotCard('Cluster '+g.id+' · ACG','All spikes · self-pairs excluded',correlogramPlot(g,FULL_WAVEFORM_COLORS[i]))).join('')+(result.ccg?plotCard('CCG','Target − source',correlogramPlot(result.ccg,'#5c3d9d')):'');attachPlotZoom(metrics);syncPlotTypography();}).catch(e=>{if(metricKey===nextMetric)metrics.textContent=e.message;});}
  else{const pca=metric==='pca';metrics.innerHTML=(pca?'<label>PCA axes<select id="server-pca-axes"><option value="1">PC1 × PC2</option><option value="2">PC1 × PC3</option></select></label>':'')+imageFrame(pca?'pca':'stability')+(pca&&!preview&&selection.target==null?'<div class="server-pca-actions"><label><input id="server-lasso" type="checkbox" '+(action==='SPLIT'?'checked':'')+'> Select source spikes</label><button class="button small" id="server-pca-preview" disabled>Preview selected split</button><span id="server-selection-count"></span></div>':'');
   const host=metrics.querySelector('[data-server-figure]');paintImage(host,pca?'pca':'stability',nextMetric,{axes});if(pca){$('#server-pca-axes').value=String(axes);$('#server-pca-axes').onchange=()=>{axes=Number($('#server-pca-axes').value);renderEvidence();};if($('#server-lasso'))attachLasso(host);}
  }
  if(metric!=='pca'||!$('#server-lasso'))attachPlotZoom(metrics);syncPlotTypography();window.ReviewStudio?.syncActions();
 }
 function attachLasso(host){
  const img=host.querySelector('img'),canvas=document.createElement('canvas');canvas.className='server-pca-overlay';canvas.width=1200;canvas.height=650;host.append(canvas);let polygon=[],drawing=false;
  const enable=$('#server-lasso'),button=$('#server-pca-preview'),status=$('#server-selection-count');
  const update=()=>{canvas.hidden=!enable.checked;button.disabled=true;polygon=[];canvas.getContext('2d').clearRect(0,0,1200,650);};enable.onchange=update;update();
  const point=e=>{const rect=canvas.getBoundingClientRect();return [Math.max(0,Math.min(1,((e.clientX-rect.left)/rect.width-.17)/.80)),Math.max(0,Math.min(1,((e.clientY-rect.top)/rect.height-.05)/.79))];};
  function draw(){const ctx=canvas.getContext('2d');ctx.clearRect(0,0,1200,650);ctx.beginPath();polygon.forEach((p,i)=>ctx[i?'lineTo':'moveTo']((.17+p[0]*.80)*1200,(.05+p[1]*.79)*650));ctx.closePath();ctx.fillStyle='#00847b22';ctx.strokeStyle='#00685f';ctx.lineWidth=2;ctx.fill();ctx.stroke();}
  canvas.onpointerdown=e=>{if(!enable.checked||img.dataset.complete!=='true')return;e.preventDefault();drawing=true;polygon=[point(e)];button.disabled=true;canvas.setPointerCapture(e.pointerId);};canvas.onpointermove=e=>{if(drawing&&polygon.length<510){polygon.push(point(e));draw();}};canvas.onpointerup=e=>{if(!drawing)return;drawing=false;polygon.push(point(e));draw();button.disabled=polygon.length<3;status.textContent='Selection will be calculated on Runpod.';};
  button.onclick=()=>run(async()=>{const result=await apiCall('pca-selection',request({axes,polygon}));accept(result);action=draft.action='SPLIT';draft.agree=null;editing=true;isDirty=true;selectedTab='detail';waveKey=metricKey='';cacheDraft();render();});
 }
 async function openData(){location.href='data-sources.html';}

 async function openWorkspace(id,source=null,target=null){if(!busy)return run(()=>openWorkspace(id,source,target));await connect();const copies=(await apiCall('workspaces')).workspaces,row=copies.find(s=>s.workspace_id===id);if(!row){const local=(await ActivityUI.readWorkspaces(reviewerId)).find(s=>s.workspace_id===id);if(local){location.href='curation.html?workspace='+encodeURIComponent(id);return;}throw Error('This session belongs to another reviewer or is no longer available');}return open(row.dataset_id,id,{source,target});}
 async function exportResult(){if(!active())return;const value={...await apiCall('export',request()),reviewer_profile_id:reviewerId,reviewer_name:profileName||''},url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=data.state.dataset_id+'.operation-log.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 async function exit(){cacheDraft();data=null;selection=null;virtual=null;preview=null;action=null;waveKey=metricKey='';imageEpoch++;thumbObserver?.disconnect();$('#inline-return-snapshots').hidden=true;$('#inline-start-working').textContent='Open server session';$('#export-button').textContent='Export feedback';$('#packet-button').disabled=false;$('#snapshot-options').hidden=false;$('#queue-mode').disabled=false;$('#data-source').onchange=datasetHandler;selectedUnitId=CASES.find(c=>c.id===selectedId)?.source;isDirty=false;window.renderQueue();window.renderCase();window.renderEvidence();}
 async function openOperation(value){const c=currentCase(),context={source:Number(c.source.slice(2)),target:comparisonTarget(c)?Number(comparisonTarget(c).slice(2)):null};if(c.review_mode!=='manual')context.checkpoint=c.state_sha256;return run(async()=>{await open(c.dataset_id,null,context);queuedChoice=[value,draft.agree];});}
 async function activity(){await connect();return apiCall('history');}
 async function mount(){
  datasetHandler=$('#data-source').onchange;$('#manage-inputs').hidden=true;
  $('#inline-start-working').onclick=()=>openData().catch(e=>toast(e.message));$('#inline-return-snapshots').onclick=exit;$('#inline-working-undo').onclick=undo;
  const originalActivate=activateReviewer;activateReviewer=async function(...args){const result=await originalActivate(...args);await run(async()=>{await connect();const old=stored();if(old.workspace_id)await openWorkspace(old.workspace_id);else await open(catalog[0].dataset_id);});return result;};
  const oldLoad=loadSavedActivity;loadSavedActivity=async function(){const previous=await oldLoad(),remote=await activity();for(const row of [...remote.history,...remote.revisions])row.actor=reviewerId;return {history:[...remote.history,...previous.history],revisions:[...remote.revisions,...previous.revisions],workspaces:[...remote.workspaces,...previous.workspaces],warnings:previous.warnings};};
  const originalHelp=$('#tour-button').onclick;$('#tour-button').onclick=()=>active()?modal('Server curation','<p>Choose an action, inspect the preview, then Confirm. Undo restores the preceding grouping. Export operation log saves your decisions; original numerical data stays on Runpod.</p>'):originalHelp?.();
  let polling=false;setInterval(async()=>{const id=stored().agent_job,identity=reviewerId;if(!id||polling||busy||!active())return;polling=true;try{const job=await apiCall('agent-status',{job_id:id});if(identity!==reviewerId)return;status(job.agent_id.replace('_',' ')+' · '+job.completed+' / '+job.total+' · '+job.status);if(job.workspace_id===data.state.workspace_id){const value=await apiCall('view',request({preview_id:preview?.id}));if(identity===reviewerId&&value.state.version===data.state.version){data.suggestions=value.suggestions;renderCase();}}if(!['queued','running'].includes(job.status))remember({agent_job:null});}catch{}finally{polling=false;}},3000);
  let lastWidth=0,resizeTimer;const resize=new ResizeObserver(()=>{const width=Math.round($('#studio-wave-content').clientWidth);if(width!==lastWidth){lastWidth=width;clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(active()){waveKey=metricKey='';renderEvidence();}},250);}});resize.observe($('#studio-wave-content'));
  if(new URLSearchParams(location.search).get('source')==='demo'){document.body.classList.remove('server-starting');return;}
  await run(async()=>{await connect();agent=stored().agent||'Agent_1';if(!['Agent_1','Agent_2'].includes(agent))agent='Agent_1';const old=stored(),c=currentCase(),requested=new URLSearchParams(location.search).has('case');if(old.workspace_id&&!requested){try{await openWorkspace(old.workspace_id);return;}catch(e){if(!/not found|no longer available|active clusters/i.test(e.message))throw e;remember({workspace_id:null});}}
   const dataset=catalog.some(r=>r.dataset_id===c?.dataset_id)?c.dataset_id:catalog[0].dataset_id;await open(dataset,null,requested?{checkpoint:c.review_mode!=='manual'?c.state_sha256:null,source:Number(c.source.slice(2)),target:comparisonTarget(c)?Number(comparisonTarget(c).slice(2)):null}:null);});
 }
 return {configured:true,server:true,get active(){return active();},get busy(){return busy;},get state(){return data?.state;},get case(){return virtual;},get input(){return null;},get selected(){return selection;},get hash(){return data?.state_sha256;},get preview(){return preview;},get revision(){return revision;},operationForCluster:cid=>data?.units.find(u=>u.id===cid)?.operation||null,mount,render,renderCase,renderQueue,renderEvidence,renderActions,renderChoice,renderAgentSelection,selectComparison,choose,agreement,confirm,undo,cancelChoice,cacheDraft,openData,openOperation,openWorkspace,exit,exportResult,drawThumbnail};
})();
