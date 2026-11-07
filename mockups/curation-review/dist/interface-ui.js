/* Shared interface preferences and read-only cluster history. */
window.InterfaceUI=(()=>{
 'use strict';
 const key='spike-sorting-interface-settings-v1',esc=escapeHtml;
 let prefs;try{prefs=InterfaceCore.preferences(JSON.parse(localStorage.getItem(key)||'{}'));}catch{prefs=InterfaceCore.preferences();}
 const media=matchMedia('(prefers-color-scheme: dark)');
 function applyPreferences(){
  document.documentElement.dataset.theme=prefs.theme==='system'?(media.matches?'dark':'light'):prefs.theme;
  document.documentElement.style.colorScheme=document.documentElement.dataset.theme;
  document.body.dataset.density=prefs.density;
  document.body.classList.toggle('hide-session-banner',!prefs.showSession);
 }
 function save(){try{localStorage.setItem(key,JSON.stringify(prefs));}catch{toast('Settings could not be saved in this browser.');}applyPreferences();}
 media.addEventListener('change',applyPreferences);applyPreferences();
 const button=document.createElement('button');button.type='button';button.className='nav-item interface-settings-button';button.id='interface-settings';button.title='Settings';button.setAttribute('aria-label','Settings');
 button.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m9 3-1 3-3 1 1 3-2 2 2 2-1 3 3 1 1 3h6l1-3 3-1-1-3 2-2-2-2 1-3-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/></svg><span>Settings</span>';
 document.querySelector('.rail-nav').append(button);
 const dialog=document.createElement('dialog');dialog.id='interface-settings-dialog';dialog.className='interface-dialog';dialog.setAttribute('aria-labelledby','interface-settings-title');
 dialog.innerHTML=`<div class="modal-heading"><h2 id="interface-settings-title">Settings</h2><button class="icon-button" data-settings-close aria-label="Close settings">×</button></div>
 <section class="settings-section"><h3>Appearance</h3><div class="settings-fields"><label>Theme<select id="settings-theme"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label><label>Spacing<select id="settings-density"><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label></div></section>
 <section class="settings-section"><h3>Waveforms</h3><label>Waveform display<select id="settings-waveforms"><option value="all">All waveforms</option><option value="sample24">24 sampled per cluster</option></select></label><p class="field-help">Changes the display only. Sampled views may miss outliers; sorting operations always use the full membership.</p></section>
 <section class="settings-section"><h3>Current session</h3><div id="settings-session"></div><label class="settings-check"><input type="checkbox" id="settings-show-session"> Show session notifications above the workspace</label><div class="settings-actions"><button class="button small" id="settings-session-history">Operation history</button><button class="button small" id="settings-backup">Export session backup</button><button class="button small" id="settings-reviewer">Choose reviewer</button></div><p class="field-help">Browser sessions are saved on this device. Export a backup to move a session or keep a separate copy.</p></section>
 <details class="settings-section"><summary>Advanced · input provenance</summary><p class="field-help">Inspect the saved evidence and request metadata when available. This does not run a model. Current working states may have no matching model request.</p><button class="button small" id="settings-inspect">Inspect input details</button></details>
 <p class="field-help">Preferences are saved in this browser.</p>`;
 document.body.append(dialog);dialog.querySelector('[data-settings-close]').onclick=()=>dialog.close();
 function refreshSession(){
  const s=WorkingReview.state;
  $('#settings-session').innerHTML=s?`<strong>${esc(s.dataset_id)}</strong><p>Revision ${s.version} · ${CurationCore.active(WorkingReview.input,s).length} current clusters · ${rows().length} confirmed actions</p><p class="field-help">The revision increases after each saved choice or undo. It is not the cluster number or model version.</p>`:'<p>Viewing a recorded snapshot. No editable session is open.</p>';
  $('#settings-backup').disabled=!s||!!WorkingReview.revision||WorkingReview.busy;
 }
 button.onclick=()=>{refreshSession();$('#settings-theme').value=prefs.theme;$('#settings-density').value=prefs.density;$('#settings-show-session').checked=prefs.showSession;$('#settings-waveforms').value=waveformMemberMode;dialog.showModal();};
 $('#settings-theme').onchange=e=>{prefs.theme=e.target.value;save();};
 $('#settings-density').onchange=e=>{prefs.density=e.target.value;save();window.dispatchEvent(new Event('resize'));};
 $('#settings-show-session').onchange=e=>{prefs.showSession=e.target.checked;save();};
 $('#settings-waveforms').onchange=e=>{waveformMemberMode=e.target.value;try{localStorage.setItem('spike-review-waveform-members',waveformMemberMode);}catch{}if(WorkingReview.active)WorkingReview.renderEvidence();else renderEvidence();};
 $('#settings-reviewer').onclick=()=>{dialog.close();$('#reviewer-menu').click();};
 $('#settings-session-history').onclick=()=>{dialog.close();showView('history');};
 $('#settings-backup').onclick=async()=>{try{await WorkingReview.exportWorkspace();}catch(e){toast(e.message);}};
 $('#settings-inspect').onclick=()=>{
  dialog.close();if(!WorkingReview.active){showPacket();return;}
  const s=WorkingReview.state,pick=WorkingReview.selected,c=WorkingReview.case;
  const matches=pick?(WorkingReview.input.data.agent_suggestions||[]).filter(r=>r.state_sha256===WorkingReview.hash&&r.source===pick.source&&(r.target??null)===(pick.target??null)):[];
  modal('Current working input',`<p>${matches.length?'Matching saved suggestions exist for this exact grouping. Request bodies are not reconstructed here.':'No matching saved model request is available for this grouping and selection.'} No model request is sent by this inspector.</p><dl class="session-details"><dt>Dataset</dt><dd>${esc(s.dataset_id)}</dd><dt>Session revision</dt><dd>${s.version}</dd><dt>Selected clusters</dt><dd>${pick?[pick.source,pick.target].filter(x=>x!=null).join(' + '):'None'}</dd><dt>Matching suggestions</dt><dd>${matches.length}</dd><dt>State fingerprint</dt><dd>${esc(WorkingReview.hash)}</dd><dt>Input fingerprint</dt><dd>${esc(c?.mat_sha256||s.source_sha256)}</dd></dl>`);
 };
 function sessionNotice(text){
  const normal=/^(Session ·|Confirmed ·|Undone|Undo complete|Previous grouping restored;|Public .* · browser computation|Grouping edit canceled;|Selection cleared\.)/.test(text);
  $('#inline-working-status').classList.toggle('session-warning',!normal&&!!text);
  if(normal&&!text.startsWith('Session ·'))toast(text);
  if(dialog.open)refreshSession();
 }
 let cachedState=null,cachedEvents=null,cachedRows=[];
 function rows(){const s=WorkingReview.state;if(!s)return [];if(s!==cachedState||s.events!==cachedEvents){cachedState=s;cachedEvents=s.events;cachedRows=ActivityCore.working([s]).history;}return cachedRows;}
 const actionLabel=a=>({SPLIT:'Split',MERGE:'Merged',KEEP:'Kept',DISCARD:'Discarded',NOT_MERGE:'Kept separate',DEFER:'Deferred',UNDO:'Undo',FINISH:'Finished'})[a]||a;
 function openHistory(cluster,eventId=''){
  const s=WorkingReview.state;if(!s)return;
  const records=InterfaceCore.timeline(s,cluster,rows());
  modal('Cluster '+cluster+' · operation history',`<p class="field-help">${esc(s.dataset_id)} · session revision ${s.version}. Includes confirmed actions on this cluster and contributing clusters. Canceled actions are removed. Reading this log does not change the current grouping.</p><div class="cluster-timeline">${records.length?records.map(r=>`<article class="cluster-log-entry ${r.raw?.id===eventId?'highlighted':''}"><header><strong>${esc(actionLabel(r.action))} · v${r.version}</strong><span>${esc(r.status==='undone'?'Undone':r.status==='superseded'?'Superseded':r.status==='earlier_state'?'Earlier grouping':r.related?'Contributing cluster':r.status)}</span></header><p>Cluster ${r.source??cluster}${r.target!=null?' → Cluster '+r.target:''}${r.undo_version!=null?' · undoes v'+r.undo_version:''}</p>${r.reason?'<p>'+esc(r.reason)+'</p>':''}${r.note?'<p>'+esc(r.note)+'</p>':''}${r.count_changes.length?'<ul>'+r.count_changes.map(v=>'<li>Cluster '+v.cluster+': '+v.before.toLocaleString()+' → '+v.after.toLocaleString()+' spikes</li>').join('')+'</ul>':'<p class="field-help">No spike membership change.</p>'}<small>${esc(r.timestamp?new Date(r.timestamp).toLocaleString():'Time not recorded')} · ${esc(r.actor||'Reviewer')}</small><details><summary>State fingerprints</summary><code>${esc(r.state_before||'Not recorded')}</code><code>${esc(r.state_after||'Not recorded')}</code></details></article>`).join(''):'<p>No saved operations for this cluster yet.</p>'}</div><button class="button" id="cluster-full-history">Open all session history</button>`);
  $('#cluster-full-history').onclick=()=>{$('#info-modal').close();showView('history');};
 }
 function decorateHistory(){
  if(!WorkingReview.active)return;
  const s=WorkingReview.state,records=rows();
  for(const card of document.querySelectorAll('#case-list [data-working-cluster]')){
   const id=Number(card.dataset.workingCluster);
   if(WorkingReview.hidesSavedTags?.(id)){const wrapper=card.parentElement;if(wrapper.classList.contains('cluster-card-wrap'))wrapper.replaceWith(card);continue;}
   const op=CurationCore.isDiscarded(s,id)?[...CurationCore.retainedOperations(s.events)].reverse().find(e=>e.choice?.action==='DISCARD'&&e.task?.source===id):WorkingReview.operationForCluster?.(id)||CurationCore.clusterOperation(s,id);
   const history=InterfaceCore.timeline(s,id,records);if(!history.length){const wrapper=card.parentElement;if(wrapper.classList.contains('cluster-card-wrap'))wrapper.replaceWith(card);continue;}
   let wrapper=card.parentElement;if(!wrapper.classList.contains('cluster-card-wrap')){wrapper=document.createElement('div');wrapper.className='cluster-card-wrap';card.before(wrapper);wrapper.append(card);}
   let tag=wrapper.querySelector(':scope > .cluster-history-tag');const signature=[s.version,op?.id,history.length].join(':');if(tag?.dataset.signature===signature)continue;
   if(!tag){tag=document.createElement('button');tag.type='button';tag.className='cluster-history-tag';wrapper.append(tag);}
   tag.dataset.signature=signature;tag.dataset.historyCluster=String(id);tag.dataset.reviewAction=op?.choice?.action||'';
   tag.textContent=(op?actionLabel(op.choice.action)+' · ':'')+'History '+history.length;
   tag.title=op?ActivityCore.origin(op)+' · v'+op.version+' · open operation history':'Open saved operations for Cluster '+id;
   tag.onclick=()=>openHistory(id,op?.id);
  }
  for(const [host,id] of [[$('#user-choice-card'),WorkingReview.selected?.source],[$('#comparison-choice-card'),WorkingReview.selected?.target]]){
   if(!host||id==null)continue;let tag=host.querySelector(':scope > .cluster-detail-history');
   const signature=s.workspace_id+':'+s.version+':'+id;if(tag?.dataset.signature===signature)continue;
   if(!tag){tag=document.createElement('button');tag.type='button';tag.className='button small cluster-detail-history';host.append(tag);}
   tag.dataset.signature=signature;tag.textContent='Cluster '+id+' history';tag.onclick=()=>openHistory(id);
  }
  // A merged-away source no longer has an active tile, but its log remains accessible.
  const activeIds=new Set(CurationCore.active(WorkingReview.input,s));
  const retired=[...new Set(records.flatMap(r=>r.clusters))].filter(id=>!activeIds.has(id));
  let archive=$('#cluster-retired-history');const signature=s.workspace_id+':'+s.version;
  if(archive?.dataset.signature===signature)return;
  if(!retired.length){archive?.remove();return;}
  if(!archive){archive=document.createElement('details');archive.id='cluster-retired-history';$('#case-list').append(archive);}
  archive.dataset.signature=signature;archive.innerHTML='<summary>Earlier clusters · '+retired.length+'</summary><div class="retired-cluster-list"></div>';
  for(const id of retired){const b=document.createElement('button'),latest=[...records].reverse().find(r=>r.clusters.includes(id));b.type='button';b.className='button small';b.textContent='Cluster '+id+' · '+actionLabel(latest.action);b.onclick=()=>openHistory(id);archive.lastElementChild.append(b);}
 }
 syncQueueSortControls();
 return {openHistory,decorateHistory,sessionNotice,get preferences(){return {...prefs};}};
})();
