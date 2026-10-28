'use strict';

let datasetInputs=[],jobPoll=null,uploadRequest=null,pendingBackup=null,profileTimer=null,profileQueue=Promise.resolve();
let trackedImportJob=null;
let browsedReasons=[];
async function refreshAgentSummary(){
  try{
    const result=await api('/api/reviews');
    $('#agent-review-summary').innerHTML='<p class="input-help">Your saved judgments only; agreement is not model accuracy. Agent 3 live previews are separate and are not counted here.</p><table class="agent-summary-table"><thead><tr><th>Agent</th><th>Reviewed</th><th>Agree</th><th>Disagree</th><th>Defer</th></tr></thead><tbody>'+AgentStudy.summary(result.reviews).filter(r=>r.id!=='Agent_3').map(r=>`<tr><td>${r.label}</td><td>${r.reviewed}</td><td>${r.agreed}</td><td>${r.disagreed}</td><td>${r.deferred}</td></tr>`).join('')+'</tbody></table>';
  }catch(error){$('#agent-review-summary').textContent=error.message;}
}
function setReferenceAgent(enabled){
  const commit=()=>{referenceAgentEnabled=enabled;try{localStorage.setItem('spikeagent-reference-agent-v1',String(enabled));}catch{}renderDatasetOptions();refreshAgentSummary();showDatasetList();};
  if(!enabled&&activeAgentId==='Agent_3'){if(isDirty)$('#enable-reference-agent').checked=true;changeAgent('Agent_1',commit,true);}else commit();
}
const choiceForBundle=id=>agentBundles()[0]?.id===id?'real':'bundle:'+id;

function renderDatasetOptions(){
  renderAgentMenu();
  const files=new Map();for(const bundle of agentBundles())if(!files.has(bundle.dataset_id))files.set(bundle.dataset_id,bundle);
  $('#data-source').innerHTML='<option value="all">All files</option>'+[...files].map(([id,b])=>`<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`).join('')+'<option value="demo">Synthetic demo</option>';
  $('#data-source').value=queueFileSelection;$('#data-source').title='Choose a file, or browse all files below';
}
async function refreshQueueCatalogs(){
  await Promise.all(agentBundles().filter(bundle=>!queueCatalogs.has(bundle.id)).map(async bundle=>{const catalog=await api('/api/cases?bundle_id='+encodeURIComponent(bundle.id));queueCatalogs.set(bundle.id,catalog);}));
}

async function refreshDatasetMenu(){
  const catalog=await api('/api/datasets');
  bundleCatalog=catalog.bundles;datasetInputs=catalog.inputs;
  await refreshQueueCatalogs();renderDatasetOptions();renderQueue();
  return catalog;
}
function inputMessage(id,message,error=false){const el=$('#'+id);el.textContent=message;el.classList.toggle('input-error',error);}
function downloadInputJSON(payload,name){
  const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function changeSourceFromManager(next){
  $('#data-modal').close();
  if(isDirty)showUnsaved(null,()=>switchSource(next));else switchSource(next);
}
function showDatasetList(){
  const visible=agentBundles();
  $('#dataset-list').innerHTML=visible.length?visible.map(b=>`<div class="input-record"><strong>${escapeHtml(b.dataset_id)}</strong><span>${b.source_kind==='initial'?'Recording start · snapshot':b.study_id?AgentStudy.label(b.study_agent_id)+' · matched historical cases':b.run_status==='completed'?'Saved model run · completed':'Saved model run · incomplete'} · ${b.cases} cases · ${b.n_spikes.toLocaleString()} spikes</span><button class="text-button" data-open-bundle="${escapeHtml(b.id)}">Open</button></div>`).join(''):'<p class="input-help">No datasets have been imported yet.</p>';
  $$('[data-open-bundle]').forEach(b=>b.onclick=()=>changeSourceFromManager(choiceForBundle(b.dataset.openBundle)));
}
function updateImportSelection(){
  const row=datasetInputs.find(r=>r.dataset_id===$('#import-dataset').value);
  if(!row)return;
  $('#import-file-info').textContent=`${row.filename} · ${(row.bytes/1024/1024).toFixed(1)} MiB · ${row.n_spikes.toLocaleString()} spikes`;
  $('#import-file-info').title=row.mat_path+' · SHA256 '+row.sha256;
  $('#import-source').innerHTML=row.sources.map(s=>`<option value="${s}">${s==='initial'?'Recording start · snapshot':'Past Agent decisions'}</option>`).join('');
  $('#import-source').value=row.sources.includes('saved')?'saved':'initial';
  $('#start-import').disabled=$('#import-origin').value==='server'&&!row.server_available;
}
function updateUploadMode(){
  $('#dataset-upload-field').hidden=$('#import-origin').value!=='upload';
  const row=datasetInputs.find(r=>r.dataset_id===$('#import-dataset').value);
  $('#start-import').disabled=$('#import-origin').value==='server'&&!row?.server_available;
}
async function refreshImportJobs(){
  clearTimeout(jobPoll);
  try{
    const {jobs}=await api('/api/imports');
    const tracked=jobs.find(j=>j.id===trackedImportJob);
    if(tracked?.status==='completed')inputMessage('import-message','Import complete. Open the dataset from the list or the page selector.');
    if(tracked?.status==='failed')inputMessage('import-message',tracked.error||'Import failed.',true);
    if(tracked?.status==='cancelled')inputMessage('import-message','Import cancelled. The original dataset is unchanged.');
    const known=new Set(bundleCatalog.map(b=>b.id));
    if(jobs.some(j=>j.status==='completed'&&j.bundle_id&&!known.has(j.bundle_id))){await refreshDatasetMenu();showDatasetList();}
    $('#import-jobs').innerHTML=jobs.length?jobs.slice(0,20).map(j=>`<div class="input-record"><strong>${escapeHtml(j.dataset_id)} · ${escapeHtml(j.source)}</strong><span>${escapeHtml(j.status)} · ${j.progress}%</span>${j.status==='queued'||j.status==='running'?`<progress max="100" value="${j.progress}"></progress><button class="text-button" data-cancel-job="${j.id}">Cancel import</button>`:''}${j.error?`<p class="input-message input-error">${escapeHtml(j.error)}</p>`:''}</div>`).join(''):'<p class="input-help">No import jobs.</p>';
    $$('[data-cancel-job]').forEach(b=>b.onclick=async()=>{try{await api('/api/imports/cancel',{job_id:b.dataset.cancelJob});await refreshImportJobs();}catch(e){inputMessage('import-message',e.message,true);}});
    if($('#data-modal').open&&jobs.some(j=>['queued','running'].includes(j.status)))jobPoll=setTimeout(refreshImportJobs,1000);
  }catch(e){inputMessage('import-message',e.message,true);}
}
async function openInputManager(){
  $('#reviewer-key').value=reviewerId;
  $('#data-modal').showModal();
  if(backend?.static_share){
    $('#start-import').closest('.input-section').hidden=true;
    const identity=$('#reviewer-key').closest('.input-section');
    identity.querySelector('.input-help').textContent='Your name, reviews and new reasons stay in this browser. Export feedback JSON to share or transfer your history. Clearing browser storage removes local records.';
    identity.querySelector('.input-row').hidden=true;
    $('#import-jobs').previousElementSibling.textContent='Static sharing';
  }
  try{
    await refreshDatasetMenu();showDatasetList();
    const previous=$('#import-dataset').value;
    $('#import-dataset').innerHTML=datasetInputs.map(r=>`<option value="${r.dataset_id}">${r.dataset_id}</option>`).join('');
    if(datasetInputs.some(r=>r.dataset_id===previous))$('#import-dataset').value=previous;
    updateImportSelection();updateUploadMode();await refreshImportJobs();await refreshReasonStatus();
    if(backend?.static_share)$('#import-jobs').textContent='These views are published snapshots. New MAT imports are available only on the Runpod version.';
    if(!$('#study-options').hidden)await refreshAgentSummary();
  }catch(e){inputMessage('import-message',e.message,true);}
}
async function uploadMAT(file,dataset){
  if(backend?.upload_mode==='resumable'){
    const session=await api('/api/uploads/session',{dataset_id:dataset,filename:file.name,bytes:file.size});
    await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();uploadRequest=xhr;xhr.open('PUT',session.url);
      xhr.setRequestHeader('Content-Type','application/octet-stream');
      xhr.upload.onprogress=e=>{if(e.lengthComputable){$('#upload-progress').value=e.loaded/e.total*100;inputMessage('import-message',`Uploading ${Math.round(e.loaded/e.total*100)}%…`);}};
      xhr.onload=()=>xhr.status>=200&&xhr.status<300?resolve():reject(new Error('Cloud upload failed. Retry the upload.'));
      xhr.onerror=()=>reject(new Error('Cloud upload connection failed.'));xhr.onabort=()=>reject(new Error('Upload cancelled.'));
      xhr.send(file);
    });
    inputMessage('import-message','Checking the uploaded file SHA256…');
    return api('/api/uploads/complete',{id:session.id});
  }
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();uploadRequest=xhr;
    xhr.open('POST','/api/uploads?dataset_id='+encodeURIComponent(dataset)+'&filename='+encodeURIComponent(file.name));
    xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.setRequestHeader('X-CSRF-Token',backend.csrf_token);
    xhr.upload.onprogress=e=>{if(e.lengthComputable){$('#upload-progress').value=e.loaded/e.total*100;inputMessage('import-message',`Uploading ${Math.round(e.loaded/e.total*100)}%…`);}};
    xhr.onload=()=>{try{const result=JSON.parse(xhr.responseText);if(xhr.status>=400)reject(new Error(result.error||'Upload failed'));else resolve(result);}catch{reject(new Error('Invalid upload response'));}};
    xhr.onerror=()=>reject(new Error('Upload connection failed. The original file is unchanged.'));
    xhr.onabort=()=>reject(new Error('Upload cancelled.'));
    xhr.send(file);
  });
}
async function beginImport(){
  trackedImportJob=null;
  $('#start-import').disabled=true;
  try{
    const dataset=$('#import-dataset').value,source=$('#import-source').value;
    let upload=null;
    if($('#import-origin').value==='upload'){
      const file=$('#dataset-upload').files[0],row=datasetInputs.find(r=>r.dataset_id===dataset);
      if(!file)throw new Error('Choose a MAT file.');
      if(file.size!==row.bytes)throw new Error('This file size does not match the selected dataset.');
      $('#upload-progress').hidden=false;$('#upload-progress').value=0;$('#cancel-upload').hidden=false;
      upload=await uploadMAT(file,dataset);$('#cancel-upload').hidden=true;
    }
    const job=await api('/api/imports',{dataset_id:dataset,source,upload_id:upload?.id||null});
    trackedImportJob=job.id;
    inputMessage('import-message',job.reused?'Already available or importing; the existing input is reused.':'Import queued. You can close this panel and return to check progress.');
    if(job.status==='completed'){await refreshDatasetMenu();showDatasetList();}
    await refreshImportJobs();
  }catch(e){inputMessage('import-message',e.message,true);}
  finally{uploadRequest=null;$('#cancel-upload').hidden=true;updateUploadMode();}
}
async function readUTF8File(selector,maxBytes){
  const file=$(selector).files[0];if(!file)throw new Error('Choose a file first.');
  if(file.size>maxBytes)throw new Error(`File is too large (maximum ${maxBytes/1_000_000} MB).`);
  return {file,text:new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer()).replace(/^\uFEFF/,'')};
}
async function importReasonFile(){
  $('#import-reasons-file').disabled=true;
  try{
    const {file,text}=await readUTF8File('#reason-import-file',4_000_000);
    const ext=file.name.split('.').pop().toLowerCase(),format={json:'json',csv:'csv',txt:'text'}[ext];
    if(!format)throw new Error('Use a JSON, CSV or text file.');
    const result=await api('/api/reasons/import-file',{format,content:text});
    mergeReasons((await api('/api/reasons')).reasons);renderReasons();
    inputMessage('reason-import-message',`${result.added} reasons added; ${result.duplicates} duplicates skipped.`);
    await refreshReasonStatus();if(!$('#reason-library-browser').hidden)await browseReasonLibrary();
  }catch(e){inputMessage('reason-import-message',e.message,true);}
  finally{$('#import-reasons-file').disabled=false;}
}
async function refreshReasonStatus(){
  const status=await api('/api/reasons/status');
  if(backend?.static_share){$('#reason-library-status').textContent=`${status.total} reasons in this browser, including ${status.jacob.unique_reasons} Jacob defaults. Newly entered or imported reasons are not synchronized to other visitors.`;return;}
  $('#reason-library-status').textContent=status.jacob?`${status.total} shared reasons. Jacob defaults: ${status.jacob.unique_reasons} unique reasons from ${status.jacob.n_source_rows} original rows (CH3, CH31, CH30). CH20's inconsistent old sheet is excluded.`:`${status.total} shared reasons. The Jacob default seed has not been initialized on this server.`;
}
function renderReasonLibrary(){
  const prefix=$('#reason-library-prefix').value,source=$('#reason-library-source').value;
  const matches=Core.matchReasons(browsedReasons,prefix).filter(r=>source==='all'||(source==='jacob'?r.sources?.some(s=>s.kind==='jacob'):r.sources?.some(s=>s.kind!=='jacob')));
  $('#reason-library-list').innerHTML=matches.length?matches.map(r=>{
    const original=r.sources?.find(s=>s.kind==='jacob');
    return `<div class="input-record"><strong>${escapeHtml(r.text)}</strong><div class="reason-card-footer"><span>${reasonSourceLabel(r)} · used ${r.uses||0} times</span>${reasonClusterMarkup(r)}</div>${original?`<details><summary>Original source · ${original.references.length} row reference${original.references.length===1?'':'s'}</summary>${original.references.map(ref=>`<p class="input-help">${escapeHtml(ref.sheet)} · row ${ref.row}<br>Workbook SHA256: ${escapeHtml(ref.sha256)}<br>Original text: ${escapeHtml(ref.original_text)}</p>`).join('')}</details>`:''}</div>`;
  }).join(''):'<p class="input-help">No reasons match this prefix/source.</p>';
}
async function browseReasonLibrary(){
  $('#reason-library-browser').hidden=false;
  try{browsedReasons=(await api('/api/reasons?limit=5000')).reasons;renderReasonLibrary();}
  catch(e){inputMessage('reason-import-message',e.message,true);}
}
async function checkBackup(){
  $('#restore-feedback-file').disabled=true;pendingBackup=null;
  try{
    const {text}=await readUTF8File('#feedback-import-file',7_000_000),payload=JSON.parse(text);
    const result=await api('/api/reviews/import',{payload,preview:true});pendingBackup=payload;
    inputMessage('feedback-import-message',`${result.valid} valid reviews; ${result.available_to_import} missing; ${result.skipped_existing} existing reviews will be skipped.`);
    $('#restore-feedback-file').disabled=!result.available_to_import;
  }catch(e){inputMessage('feedback-import-message',e.message,true);}
}
async function restoreBackup(){
  if(isDirty){inputMessage('feedback-import-message','Save or discard your current draft before restoring feedback.',true);return;}
  $('#restore-feedback-file').disabled=true;
  try{
    if(!pendingBackup)throw new Error('Check the backup first.');
    const result=await api('/api/reviews/import',{payload:pendingBackup,preview:false});
    inputMessage('feedback-import-message',`${result.added} reviews restored; ${result.skipped_existing} existing reviews preserved.`);
    if(mode==='real')await switchSource(sourceChoice,true);else{const saved=await api('/api/reviews');allReviewRecords=Object.fromEntries(Object.entries(saved.reviews).map(([id,row])=>[id,row.review]));renderQueue();}
  }catch(e){inputMessage('feedback-import-message',e.message,true);}
}
function scheduleProfileSave(){
  clearTimeout(profileTimer);
  if(!backend)return;
  const identity=reviewerId,name=state.annotator;
  profileTimer=setTimeout(()=>{
    profileQueue=profileQueue.catch(()=>{}).then(async()=>{
      if(identity!==reviewerId)return;
      try{await api('/api/profile',{annotator:name});inputMessage('reviewer-profile-message','Reviewer name saved on server.');}
      catch(e){connectionWarning(`Reviewer name is saved in this browser; server sync failed: ${e.message}`);}
    });
  },400);
}
function renderReviewerBadge(){
  const name=profileName||state.annotator||'';
  $('#reviewer-initials').textContent=ReviewerProfiles.initials(name);
  $('#reviewer-badge-name').textContent=name||'Reviewer';
  $('#reviewer-menu').title=name?'Reviewer: '+name+' · click to choose':'Choose or create a reviewer';
  try{ReviewerProfiles.remember(localStorage,reviewerId,name);}catch{}
}
function openReviewerDialog(){
  renderReviewerBadge();
  const profiles=ReviewerProfiles.list(localStorage);
  $('#reviewer-profile-select').innerHTML=profiles.map((r,i)=>`<option value="${escapeHtml(r.id)}">${escapeHtml(r.name||'Unnamed reviewer '+(i+1))}</option>`).join('');
  $('#reviewer-profile-select').value=reviewerId;$('#reviewer-name').value=profileName||state.annotator||'';
  $('#reviewer-dialog-status').textContent='';$('#reviewer-dialog').showModal();
}
async function activateReviewer(key,name=null){
  if(typeof WorkingReview!=='undefined'&&WorkingReview.active){if(WorkingReview.busy)throw Error('Wait for the current operation to finish before changing reviewer.');await WorkingReview.exit();}
  if(!/^[A-Za-z0-9_-]{8,100}$/.test(key))throw new Error('Invalid reviewer identity.');
  if(backend?.cloud&&key!==reviewerId)throw new Error('Cloud history belongs to the signed-in account.');
  const previous={id:reviewerId,name:profileName,state,demo:demoState,reviews:allReviewRecords};
  try{
    persist();clearTimeout(profileTimer);await profileQueue.catch(()=>{});
    reviewerId=key;allReviewRecords={};localStorage.setItem('spikeagent-reviewer-id',key);
    let profile=backend?await api('/api/profile'):{annotator:'',updated_at:null};
    if(name!==null){
      if(typeof name!=='string'||name.length>100)throw new Error('Use a name of up to 100 characters.');
      profile={...profile,annotator:name.trim()};
      if(backend)await api('/api/profile',{annotator:profile.annotator});
    }
    profileName=profile.annotator;
    let saved=null;try{saved=JSON.parse(localStorage.getItem(demoStorageKey())||'null');}catch{}
    demoState=saved&&saved.reviews&&Array.isArray(saved.events)?saved:{reviews:{},events:[],reasons:[],annotator:profileName};
    demoState.annotator=profileName;
    if(mode==='real'){if(!await switchSource(sourceChoice,true,false))throw new Error('Could not load this reviewer history.');}
    else{if(backend){const saved=await api('/api/reviews');allReviewRecords=Object.fromEntries(Object.entries(saved.reviews).map(([id,row])=>[id,row.review]));}state=demoState;$('#reviewer-name').value=profileName;persist();renderQueue();renderCase();}
    $('#reviewer-key').value=key;renderReviewerBadge();return true;
  }catch(error){
    reviewerId=previous.id;profileName=previous.name;state=previous.state;demoState=previous.demo;allReviewRecords=previous.reviews;
    try{localStorage.setItem('spikeagent-reviewer-id',reviewerId);}catch{}
    $('#reviewer-name').value=profileName;renderReviewerBadge();throw error;
  }
}
function applyReviewerProfile(create=false){
  const key=create?crypto.randomUUID():$('#reviewer-profile-select').value,name=$('#reviewer-name').value.trim();
  if(create&&!name){$('#reviewer-dialog-status').textContent='Enter a name or study ID for the new reviewer.';return;}
  const perform=async()=>{try{await activateReviewer(key,name);$('#reviewer-dialog').close();toast('Reviewer selected: '+(name||'Unnamed reviewer'));}catch(error){$('#reviewer-dialog-status').textContent=error.message;toast(error.message);}};
  if(isDirty){$('#reviewer-dialog').close();showUnsaved(null,perform);}else perform();
}
async function useReviewerKey(){
  if(backend?.static_share){inputMessage('reviewer-profile-message','Use the lower-left Reviewer button for browser-local profiles. Transfer records using JSON export/restore.',true);return;}
  if(isDirty){inputMessage('reviewer-profile-message','Save or discard your current draft first.',true);return;}
  try{await activateReviewer($('#reviewer-key').value.trim());inputMessage('reviewer-profile-message','Reviewer history loaded.');}
  catch(error){inputMessage('reviewer-profile-message',error.message,true);}
}

$('#reviewer-menu').onclick=openReviewerDialog;
$('#close-reviewer-dialog').onclick=()=>$('#reviewer-dialog').close();
$('#reviewer-profile-select').onchange=()=>{$('#reviewer-name').value=ReviewerProfiles.list(localStorage).find(r=>r.id===$('#reviewer-profile-select').value)?.name||'';};
$('#apply-reviewer-profile').onclick=()=>applyReviewerProfile(false);
$('#create-reviewer-profile').onclick=()=>applyReviewerProfile(true);
$('#open-closed-agent').onclick=()=>ClosedAgent.open();
$('#manage-inputs').onclick=openInputManager;
$('#agent-choice').onchange=event=>changeAgent(event.target.value);
$('#enable-reference-agent').onchange=event=>setReferenceAgent(event.target.checked);
$('#refresh-agent-summary').onclick=refreshAgentSummary;
$('#close-data-modal').onclick=()=>$('#data-modal').close();
$('#data-modal').addEventListener('close',()=>clearTimeout(jobPoll));
$('#import-dataset').onchange=()=>{updateImportSelection();updateUploadMode();};
$('#import-origin').onchange=updateUploadMode;
$('#start-import').onclick=beginImport;
$('#cancel-upload').onclick=()=>uploadRequest?.abort();
$('#import-reasons-file').onclick=importReasonFile;
$('#browse-reasons').onclick=browseReasonLibrary;
$('#reason-library-prefix').oninput=renderReasonLibrary;
$('#reason-library-source').onchange=renderReasonLibrary;
$('#export-reasons-file').onclick=async()=>{try{downloadInputJSON(await api('/api/reasons/export'),'spikeagent-reasons.json');}catch(e){inputMessage('reason-import-message',e.message,true);}};
$('#feedback-import-file').onchange=()=>{pendingBackup=null;$('#restore-feedback-file').disabled=true;inputMessage('feedback-import-message','');};
$('#check-feedback-file').onclick=checkBackup;$('#restore-feedback-file').onclick=restoreBackup;
$('#use-reviewer-key').onclick=useReviewerKey;
$('#copy-reviewer-key').onclick=async()=>{try{await navigator.clipboard.writeText(reviewerId);inputMessage('reviewer-profile-message','Reviewer key copied.');}catch{$('#reviewer-key').select();inputMessage('reviewer-profile-message','Select and copy the reviewer key.');}};

// Desktop panel widths are local display preferences, independent of review data.
(function initNavigationRail(){
  const button=$('#rail-toggle'),key='spikeagent-rail-collapsed-v1';
  let collapsed=matchMedia('(max-width:1280px)').matches;
  try{const saved=localStorage.getItem(key);if(saved==='true'||saved==='false')collapsed=saved==='true';}catch{}
  function apply(){
    document.body.classList.toggle('rail-collapsed',collapsed);
    const label=collapsed?'Expand navigation':'Collapse navigation';
    button.setAttribute('aria-expanded',String(!collapsed));button.setAttribute('aria-label',label);button.title=label;button.querySelector('span').textContent=label;
    requestAnimationFrame(()=>window.dispatchEvent(new Event('resize')));
  }
  button.onclick=()=>{collapsed=!collapsed;try{localStorage.setItem(key,String(collapsed));}catch{}apply();};
  apply();
})();

(function initPanelResizing(){
  if(window.ReviewStudio?.active)return;
  const workspace=$('.workspace'),key='spikeagent-panel-widths-v1',handles=$$('.panel-resizer');
  const desktop=()=>matchMedia('(min-width:1000px)').matches;
  let requested=null,drag=null;
  try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved&&[saved.queue,saved.decision].every(v=>Number.isFinite(v)&&v>=100&&v<=800))requested=saved;}catch{}
  const current=()=>({queue:$('#queue-panel').getBoundingClientRect().width,decision:$('#decision-panel').getBoundingClientRect().width});
  function fit(widths){
    let queue=Math.min(450,Math.max(160,widths.queue)),decision=Math.min(520,Math.max(250,widths.decision));
    const available=workspace.clientWidth-16-260;
    if(queue+decision>available){const scale=Math.max(0,(available-410)/(queue+decision-410||1));queue=160+(queue-160)*scale;decision=250+(decision-250)*scale;}
    return {queue,decision};
  }
  function apply(widths){
    if(!desktop())return;
    const actual=fit(widths);workspace.style.setProperty('--queue-width',actual.queue+'px');workspace.style.setProperty('--decision-width',actual.decision+'px');
    handles.forEach(handle=>{const name=handle.dataset.resize;handle.setAttribute('aria-valuemin',name==='queue'?'160':'250');handle.setAttribute('aria-valuemax',name==='queue'?'450':'520');handle.setAttribute('aria-valuenow',Math.round(actual[name]));handle.setAttribute('aria-valuetext',Math.round(actual[name])+' pixels');});
    syncPlotTypography();$$('.plot-viewport').forEach(view=>view.dispatchEvent(new Event('plotresize')));
  }
  function persistWidths(){requested=current();try{localStorage.setItem(key,JSON.stringify(requested));}catch{}}
  function reset(){requested=null;workspace.style.removeProperty('--queue-width');workspace.style.removeProperty('--decision-width');try{localStorage.removeItem(key);}catch{};if(desktop())apply(current());}
  handles.forEach(handle=>{
    handle.onpointerdown=event=>{if(!desktop()||event.button!==0)return;event.preventDefault();drag={x:event.clientX,widths:current(),name:handle.dataset.resize};handle.setPointerCapture(event.pointerId);document.body.classList.add('resizing-panels');};
    handle.onpointermove=event=>{if(!drag)return;const delta=event.clientX-drag.x,widths={...drag.widths};widths[drag.name]+=drag.name==='queue'?delta:-delta;apply(widths);};
    const finish=()=>{if(!drag)return;drag=null;document.body.classList.remove('resizing-panels');persistWidths();};
    handle.onpointerup=finish;handle.onpointercancel=finish;handle.onlostpointercapture=finish;
    handle.ondblclick=reset;
    handle.onkeydown=event=>{if(!desktop()||!['ArrowLeft','ArrowRight','Home','Enter'].includes(event.key))return;event.preventDefault();if(event.key==='Home'||event.key==='Enter'){reset();return;}const widths=current(),name=handle.dataset.resize,delta=(event.shiftKey?40:16)*(event.key==='ArrowRight'?1:-1);widths[name]+=name==='queue'?delta:-delta;apply(widths);persistWidths();};
  });
  window.addEventListener('resize',()=>{if(desktop()){if(!requested){workspace.style.removeProperty('--queue-width');workspace.style.removeProperty('--decision-width');}apply(requested||current());}});
  requestAnimationFrame(()=>{if(desktop())apply(requested||current());});
})();

// Focus and scrollIntoView must account for the pinned controls' changing height.
(function initPinnedHeaders(){
  for(const [panel,header] of [['#queue-panel','.queue-sticky'],['#evidence-panel','.evidence-sticky'],['#decision-panel','.decision-sticky']]){
    const container=$(panel),sticky=container.querySelector(header);
    const update=()=>container.style.setProperty('--pinned-height',(getComputedStyle(sticky).position==='sticky'?Math.ceil(sticky.getBoundingClientRect().height+6):0)+'px');
    if(typeof ResizeObserver!=='undefined')new ResizeObserver(update).observe(sticky);
    window.addEventListener('resize',update);requestAnimationFrame(update);
  }
})();

(function initAgentDisclosure(){
  const box=$('#agent-card');box.open=true;
  const update=()=>{box.querySelector('.collapse-hint').textContent=box.open?'Hide':'Show';recordVisibleRationale();};
  box.addEventListener('toggle',update);update();
})();

(function initAgentHeight(){
  const key='spikeagent-agent-height-v1',panel=$('#decision-panel'),body=$('.agent-card-content'),handle=$('#agent-height-resizer');
  let preferred=280,drag=null,manualHeight=false;
  try{const value=Number(localStorage.getItem(key));if(Number.isFinite(value)&&value>=110&&value<=2000)preferred=value;}catch{}
  function maximum(){return Math.max(110,panel.clientHeight-$('.decision-heading').getBoundingClientRect().height-$('#agent-card>summary').getBoundingClientRect().height-145);}
  function apply(value){manualHeight=true;body.dataset.size='manual';const max=maximum(),height=Math.min(max,Math.max(110,value));body.style.setProperty('--agent-height',height+'px');body.style.setProperty('--agent-height-max',max+'px');handle.setAttribute('aria-valuemin','110');handle.setAttribute('aria-valuemax',Math.round(max));handle.setAttribute('aria-valuenow',Math.round(height));handle.setAttribute('aria-valuetext',Math.round(height)+' pixels');}
  function fitContent(){manualHeight=false;delete body.dataset.size;const height=Math.round(body.getBoundingClientRect().height);handle.setAttribute('aria-valuemin','0');handle.setAttribute('aria-valuemax',Math.max(height,Math.round(maximum())));handle.setAttribute('aria-valuenow',height);handle.setAttribute('aria-valuetext','Full content, '+height+' pixels');}
  function save(){try{localStorage.setItem(key,String(preferred));}catch{}}
  function finish(){if(!drag)return;drag=null;document.body.classList.remove('resizing-agent');preferred=body.getBoundingClientRect().height;save();}
  handle.onpointerdown=event=>{if(event.button!==0)return;event.preventDefault();drag={y:event.clientY,height:body.getBoundingClientRect().height};handle.setPointerCapture(event.pointerId);document.body.classList.add('resizing-agent');};
  handle.onpointermove=event=>{if(drag)apply(drag.height+event.clientY-drag.y);};
  handle.onpointerup=finish;handle.onpointercancel=finish;handle.onlostpointercapture=finish;
  handle.ondblclick=fitContent;
  handle.onkeydown=event=>{if(!['ArrowUp','ArrowDown','Home','Enter'].includes(event.key))return;event.preventDefault();if(event.key==='Home'||event.key==='Enter'){fitContent();return;}const delta=(event.shiftKey?60:20)*(event.key==='ArrowDown'?1:-1);preferred=body.getBoundingClientRect().height+delta;apply(preferred);preferred=body.getBoundingClientRect().height;save();};
  window.addEventListener('resize',()=>manualHeight?apply(preferred):fitContent());$('#agent-card').addEventListener('toggle',fitContent);requestAnimationFrame(fitContent);
})();
