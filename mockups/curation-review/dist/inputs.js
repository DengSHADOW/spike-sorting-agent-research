'use strict';

let datasetInputs=[],jobPoll=null,uploadRequest=null,pendingBackup=null,profileTimer=null,profileQueue=Promise.resolve();
let trackedImportJob=null;
let browsedReasons=[];
const choiceForBundle=id=>bundleCatalog[0]?.id===id?'real':'bundle:'+id;

async function refreshDatasetMenu(){
  const catalog=await api('/api/datasets');
  bundleCatalog=catalog.bundles;datasetInputs=catalog.inputs;
  $('#data-source').innerHTML=bundleCatalog.map((b,i)=>`<option value="${escapeHtml(i===0?'real':'bundle:'+b.id)}">${escapeHtml(b.dataset_id)} · ${b.source_kind==='initial'?'manual':'saved'}</option>`).join('')+'<option value="demo">Synthetic demo</option>';
  $('#data-source').value=sourceChoice;
  if(!$('#data-source').value)$('#data-source').value='demo';
  $('#data-source').title=mode==='real'?`${realCatalog?.dataset_id||''} · ${realCatalog?.source_kind||''}`:'Synthetic demo';
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
  $('#dataset-list').innerHTML=bundleCatalog.length?bundleCatalog.map(b=>`<div class="input-record"><strong>${escapeHtml(b.dataset_id)}</strong><span>${b.source_kind==='initial'?'Initial clusters · manual':b.run_status==='completed'?'Saved model run · completed':'Saved model run · incomplete'} · ${b.cases} cases · ${b.n_spikes.toLocaleString()} spikes</span><button class="text-button" data-open-bundle="${escapeHtml(b.id)}">Open</button></div>`).join(''):'<p class="input-help">No datasets have been imported yet.</p>';
  $$('[data-open-bundle]').forEach(b=>b.onclick=()=>changeSourceFromManager(choiceForBundle(b.dataset.openBundle)));
}
function updateImportSelection(){
  const row=datasetInputs.find(r=>r.dataset_id===$('#import-dataset').value);
  if(!row)return;
  $('#import-file-info').textContent=`${row.filename} · ${(row.bytes/1024/1024).toFixed(1)} MiB · ${row.n_spikes.toLocaleString()} spikes`;
  $('#import-file-info').title=row.mat_path+' · SHA256 '+row.sha256;
  $('#import-source').innerHTML=row.sources.map(s=>`<option value="${s}">${s==='initial'?'Initial clusters · manual':'Saved Qwen9B decisions'}</option>`).join('');
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
  try{
    await refreshDatasetMenu();showDatasetList();
    const previous=$('#import-dataset').value;
    $('#import-dataset').innerHTML=datasetInputs.map(r=>`<option value="${r.dataset_id}">${r.dataset_id}</option>`).join('');
    if(datasetInputs.some(r=>r.dataset_id===previous))$('#import-dataset').value=previous;
    updateImportSelection();updateUploadMode();await refreshImportJobs();await refreshReasonStatus();
  }catch(e){inputMessage('import-message',e.message,true);}
}
function uploadMAT(file,dataset){
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
  $('#reason-library-status').textContent=status.jacob?`${status.total} shared reasons. Jacob defaults: ${status.jacob.unique_reasons} unique reasons from ${status.jacob.n_source_rows} original rows (CH3, CH31, CH30). CH20's inconsistent old sheet is excluded.`:`${status.total} shared reasons. The Jacob default seed has not been initialized on this server.`;
}
function renderReasonLibrary(){
  const prefix=$('#reason-library-prefix').value,source=$('#reason-library-source').value;
  const matches=Core.matchReasons(browsedReasons,prefix).filter(r=>source==='all'||(source==='jacob'?r.sources?.some(s=>s.kind==='jacob'):r.sources?.some(s=>s.kind!=='jacob')));
  $('#reason-library-list').innerHTML=matches.length?matches.map(r=>{
    const original=r.sources?.find(s=>s.kind==='jacob');
    return `<div class="input-record"><strong>${escapeHtml(r.text)}</strong><span>${reasonSourceLabel(r)} · used ${r.uses||0} times</span>${original?`<details><summary>Original source · ${original.references.length} row reference${original.references.length===1?'':'s'}</summary>${original.references.map(ref=>`<p class="input-help">${escapeHtml(ref.sheet)} · row ${ref.row}<br>Workbook SHA256: ${escapeHtml(ref.sha256)}<br>Original text: ${escapeHtml(ref.original_text)}</p>`).join('')}</details>`:''}</div>`;
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
    if(mode==='real')await switchSource(sourceChoice,true);
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
async function useReviewerKey(){
  const key=$('#reviewer-key').value.trim();
  if(!/^[A-Za-z0-9_-]{8,100}$/.test(key)){inputMessage('reviewer-profile-message','Use a reviewer key of 8–100 letters, digits, underscores or hyphens.',true);return;}
  if(isDirty){inputMessage('reviewer-profile-message','Save or discard the current draft before switching reviewer keys.',true);return;}
  const previousIdentity=reviewerId,previousName=profileName,previousState=state;
  try{
    persist();clearTimeout(profileTimer);await profileQueue.catch(()=>{});
    localStorage.setItem('spikeagent-reviewer-id',key);reviewerId=key;
    const profile=await api('/api/profile');profileName=profile.annotator;
    if(mode==='real'){if(!await switchSource(sourceChoice,true,false))throw new Error('Could not load this reviewer history.');}
    else{state.annotator=profileName;$('#reviewer-name').value=profileName;persist();}
    inputMessage('reviewer-profile-message','Reviewer identity loaded. Saved reviews remain linked to their original keys.');
  }catch(e){
    reviewerId=previousIdentity;profileName=previousName;state=previousState;
    try{localStorage.setItem('spikeagent-reviewer-id',previousIdentity);}catch{}
    $('#reviewer-name').value=previousState.annotator;$('#reviewer-key').value=previousIdentity;
    renderQueue();renderCase();inputMessage('reviewer-profile-message',e.message,true);
  }
}

$('#manage-inputs').onclick=openInputManager;
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
