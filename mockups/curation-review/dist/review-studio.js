/* Alternate layout over the existing review and curation engines. No inference. */
'use strict';
window.ReviewStudio=(()=>{
 const C=CurationCore, wave=$('#evidence-content'), nativeEvidence=renderEvidence, nativeActions=renderActions, nativeQueue=renderQueue;
 let metric='timing',pendingMerge=null,workingContext=null,waveKey='',positiveKey='',targetObserver=null,workDisplay='both',workPopulationKey='';
 const workOpacities=new Map();
 const projections=new WeakMap();let projectionId=0;
 const nativeAgentMenu=renderAgentMenu;
 function actionOf(text){const value=String(text||'').trim().replace(/^AI\s*[·:]\s*/i,'').toUpperCase();for(const id of ['NOT_MERGE','DISCARD','SPLIT','MERGE','KEEP','DEFER'])for(const label of [id,ACTIONS[id]?.label?.toUpperCase()])if(label&&(value===label||value.startsWith(label+' ')||value.startsWith(label+' ·')))return id;return '';}
 function decorateActions(){
  for(const tag of document.querySelectorAll('#case-list .queue-review-label'))if(tag.textContent.trim()==='Pending')tag.hidden=true;
  for(const node of document.querySelectorAll('.queue-action-tag,.agent-object-label,.suggestion-action,.choice-action,#action-grid [data-action]')){
   const action=node.dataset.action||node.dataset.agentAction||actionOf(node.querySelector('strong')?.textContent||node.textContent);
   if(action&&node.dataset.reviewAction!==action)node.dataset.reviewAction=action;
  }
  for(const option of [...$('#agent-choice').options])if(!['Agent_1','Agent_2'].includes(option.value))option.remove();
 }
 renderAgentMenu=function(){const value=nativeAgentMenu();decorateActions();return value;};
 let decorationQueued=false;
 new MutationObserver(()=>{if(decorationQueued)return;decorationQueued=true;requestAnimationFrame(()=>{decorationQueued=false;decorateActions();});}).observe($('.workspace'),{childList:true,subtree:true});

 const nativeGeneralReasons=renderGeneralReasons,nativeSaveReview=saveReview,reasonDrafts=new Map();let lastReason=null,reasonRenderKey='';
 const reasonAction=()=>pendingMerge?'MERGE':WorkingReview.preview?.action||draft.action||'';
 function reasonLinks(){
  const rows=[...Object.values(allReviewRecords||{}),...Object.values(state.reviews||{})].filter(Boolean).map(r=>({text:r.expert_reason||'',action:r.action}));
  if(isWorkingReview())for(const e of WorkingReview.state.events||[])if(e.choice?.reason)rows.push({text:e.choice.reason,action:e.choice.action});
  return [...rows,...ActionReasons.read(localStorage)];
 }
 renderGeneralReasons=function(){
  const c=currentCase(),action=reasonAction(),scope=stateKey()+':'+(c?comparisonTarget(c):''),key=scope+':'+action,select=$('#general-reason-choice');
  let forceCustom=select.value==='__new__';
  if(lastReason&&lastReason.scope===scope&&lastReason.action&&lastReason.action!==action){
   reasonDrafts.set(lastReason.key,{reason:$('#reason-select').value,note:$('#review-note').value,custom:forceCustom});
   const saved=reasonDrafts.get(key);$('#reason-select').value=saved?.reason||'';$('#review-note').value=saved?.note||'';draft.reason=$('#reason-select').value;draft.note=$('#review-note').value;forceCustom=!!saved?.custom;
  }
  lastReason={scope,action,key};
  nativeGeneralReasons();
  generalReasonRows=ActionReasons.forAction(ActionReasons.catalog(generalReasonRows,state.reasons,reasonLinks()),action);
  const original=$('#reason-select').value,note=$('#review-note').value,found=generalReasonRows.findIndex(r=>ActionReasons.key(r.text)===ActionReasons.key(original));
  select.innerHTML='<option value="" disabled>'+ (action?'Choose your reason':'Choose an action first')+'</option><option value="__new__">Type new reason…</option>'+generalReasonRows.map((r,i)=>'<option value="'+i+'">'+escapeHtml(r.text)+'</option>').join('');
  select.disabled=!ActionReasons.ACTIONS.includes(action);select.value=select.disabled?'':forceCustom||note?'__new__':found>=0?String(found):original?'__new__':'';
  $('#custom-reason-input').hidden=select.value!=='__new__';$('#reason-options').innerHTML=generalReasonRows.map(r=>'<option value="'+escapeHtml(r.text)+'"></option>').join('');
  $('#general-reason-status').textContent=action?ACTIONS[action].label+' · '+generalReasonRows.length+' reasons':'Choose an action to see its reasons.';
 };
 saveReview=async function(){
  const working=isWorkingReview(),id=currentCase().id,version=working?WorkingReview.state.version:revisions[id]||0;
  await nativeSaveReview();
  let text='',action='';
  if(working&&isWorkingReview()&&WorkingReview.state.version>version){const choice=WorkingReview.state.events.at(-1)?.choice;text=choice?.reason||'';action=choice?.action;}
  else if(!working&&(revisions[id]||0)>version){const row=state.reviews[id];text=row?.expert_reason||'';action=row?.action;}
  if(text){try{ActionReasons.remember(localStorage,text,action);}catch{toast('Choice saved. Reason category could not be stored in this browser.');}renderReasons();}
 };
 $('#save-button').onclick=saveReview;

 const nativeTypography=syncPlotTypography;
 syncPlotTypography=function(){
  nativeTypography();
  for(const svg of document.querySelectorAll('#studio-wave-content svg,#evidence-content svg,#modal-content svg')){
   const width=svg.clientWidth;if(!width||!svg.viewBox.baseVal.width)continue;
   if(!svg.dataset.studioViewBox)svg.dataset.studioViewBox=svg.getAttribute('viewBox');
   const original=svg.dataset.studioViewBox.trim().split(/[ ,]+/).map(Number);if(original.length!==4||!original.every(Number.isFinite))continue;
   const pad=Math.max(0,(440-width)*.26),[x,y,w,h]=original;
   svg.setAttribute('viewBox',[x-pad,y-pad*.2,w+pad*1.6,h+pad*.5].join(' '));
   const size=Math.max(11,Math.min(14,(parseFloat(getComputedStyle(svg.closest('.plot-card')||svg).fontSize)||14)*.9));
   svg.style.setProperty('--chart-label-size',size*(w+pad*1.6)/width+'px');
  }
 };

 const stateKey=()=>{const c=currentCase();return c?[c.id,c.state_sha256].join(':'):'';};
 const contextKey=()=>{const c=currentCase();return c?[c.dataset_id,c.state_sha256,c.source].join(':'):'';};
 const columns=document.createElement('div');columns.className='studio-evidence-columns';
 columns.innerHTML='<section class="studio-wave-panel" aria-label="Cluster detail"><div class="studio-wave-toolbar"><div class="studio-wave-options"></div><div class="studio-compact-compare"></div></div><div id="studio-wave-content" role="region" aria-label="Cluster waveform detail"></div></section><section class="studio-metric-panel" aria-label="Cluster measurements"></section>';
 const evidenceResize=document.createElement('div');evidenceResize.id='studio-evidence-resize';evidenceResize.className='studio-evidence-resizer';evidenceResize.tabIndex=0;evidenceResize.setAttribute('role','separator');evidenceResize.setAttribute('aria-orientation','vertical');evidenceResize.setAttribute('aria-label','Resize waveform and metrics views');evidenceResize.setAttribute('aria-controls','studio-wave-content evidence-content');columns.insertBefore(evidenceResize,columns.lastElementChild);
 const center=document.createElement('section');center.className='studio-center';center.setAttribute('aria-label','Clusters and evidence');
 const galleryResize=document.createElement('div');galleryResize.className='studio-gallery-resizer';galleryResize.id='studio-gallery-resize';galleryResize.tabIndex=0;galleryResize.setAttribute('role','separator');galleryResize.setAttribute('aria-orientation','horizontal');galleryResize.setAttribute('aria-label','Resize cluster gallery height');
 const queue=$('#queue-panel'),evidence=$('#evidence-panel'),workspace=$('.workspace');workspace.prepend(center);center.append(queue,galleryResize,evidence);
 evidence.insertBefore(columns,wave);columns.lastElementChild.append($('.evidence-tabs'),wave);
 const detail=$('#studio-wave-content');
 const waveOptions=columns.querySelector('.studio-wave-options');
 columns.querySelector('.studio-compact-compare').append($('#evidence-context'));
 evidence.insertBefore($('#metrics'),columns);$('#metrics').hidden=false;
 function compactHeader(){
  const table=$('#metrics .metrics-table');if(!table)return;
  const rows=[...table.querySelectorAll('.metric-table-row')],pair=rows.length===2,header=table.querySelector('.metric-table-header:not([data-studio-repeat])'),repeat=table.querySelector('[data-studio-repeat]');
  table.classList.toggle('studio-pair-statistics',pair);
  for(const [i,row] of rows.entries()){row.querySelector('strong').style.color=FULL_WAVEFORM_COLORS[i%2];row.dataset.studioColumn=String(i+1);}
  if(pair&&header&&!repeat){const copy=header.cloneNode(true);copy.dataset.studioRepeat='true';table.append(copy);}else if(!pair&&repeat)repeat.remove();
 }
 new MutationObserver(compactHeader).observe($('#metrics'),{childList:true,subtree:true});

 const picker=document.createElement('section');picker.id='studio-pair-picker';picker.hidden=true;queue.insertBefore(picker,$('#case-list'));
 const actionField=$('.action-fieldset');$('#review-editor').insertBefore(actionField,$('#correction-panel'));
 const undo=document.createElement('button');undo.id='studio-undo';undo.type='button';undo.title='Undo';undo.setAttribute('aria-label','Undo');undo.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5 3 11l6 6M3 11h11a6 6 0 0 1 0 12" transform="translate(0 -3)"/></svg>';$('.decision-heading').append(undo);
 $('#queue-filters').open=false;
 const navLink=$('.studio-link');
 const originalShow=showView;
 showView=function(name){const result=originalShow(name);navLink.classList.toggle('active',name==='review');$('.nav-item[data-view=review]').classList.remove('active');syncUndo();requestAnimationFrame(()=>window.dispatchEvent(new Event('resize')));return result;};
 $('.nav-item[data-view=review]').onclick=()=>{
  const url=new URL('index.html',location.href);url.search=location.search;location.href=url.pathname+url.search+'#review';
 };
 navLink.onclick=event=>{event.preventDefault();if(currentView!=='review')showView('review');};

 function compactQueue(){
  if(isWorkingReview())return;
  for(const group of document.querySelectorAll('#case-list .queue-cluster-group')){
   const buttons=[...group.querySelectorAll('button[data-case]')];if(!buttons.length)continue;
   const chosen=buttons.find(b=>b.dataset.case===selectedId)||buttons[0],tile=document.createElement('div');tile.className='studio-history-cluster';
   const states=document.createElement('select');states.className='studio-history-state';states.setAttribute('aria-label',chosen.querySelector('.queue-thumb-title').textContent+' historical state');
   buttons.forEach((button,i)=>{const row=queueEntries().find(row=>row.c.id===button.dataset.case),option=document.createElement('option');option.value=button.dataset.case;option.textContent='State '+(row?.c.state_sha256?.slice(0,8)||String(i+1));states.append(option);});
   states.value=chosen.dataset.case;states.onchange=()=>{const row=buttons.find(b=>b.dataset.case===states.value);if(row)openQueueCase(row.dataset.case,row.dataset.bundle);};tile.append(chosen,states);group.replaceWith(tile);
  }
 }
 function compactWaveTools(){
  for(const controls of detail.querySelectorAll('.full-wave-display-controls')){
   if(controls.dataset.studioDisclosure)continue;controls.dataset.studioDisclosure='true';const options=document.createElement('details');options.className='studio-wave-settings';const summary=document.createElement('summary');summary.textContent='Waveform display';options.append(summary,controls);waveOptions.replaceChildren(options);
  }
 }
 new MutationObserver(compactWaveTools).observe(detail,{childList:true,subtree:true});
 renderQueue=function(){const value=nativeQueue();compactQueue();decorateActions();return value;};
 function metricTab(){
  if(!['detail','overview','gallery','pair'].includes(selectedTab))metric=selectedTab;
  if(!['timing','quality','correlation','pca','stability','tree','amplitude'].includes(metric))metric='timing';
  if((metric==='tree'||metric==='amplitude')&&$('[data-tab="'+metric+'"]').hidden)metric='timing';
  selectedTab=metric;
  for(const tab of document.querySelectorAll('[data-tab]')){const active=tab.dataset.tab===metric;tab.classList.toggle('active',active);tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;}
  wave.setAttribute('aria-labelledby','evidence-tab-'+metric);tabsViewed.add('detail');tabsViewed.add(metric);return metric;
 }
 function syncUndo(){
  const working=isWorkingReview(),waiting=!!pendingMerge||isDirty;
  undo.disabled=saving||!!WorkingReview.busy||!(waiting||(working?WorkingReview.state?.undo?.length:state.reviews[currentCase()?.id]));
  undo.title=waiting?'Undo unsaved choice':working?'Undo last grouping operation':'Undo this saved review';undo.setAttribute('aria-label',undo.title);
 }
 undo.onclick=async()=>{
  if(undo.disabled)return;
  if(isDirty||pendingMerge)clearSelectedAction();
  else if(isWorkingReview())await WorkingReview.undo();
  else if(state.reviews[currentCase().id])await undoReview(currentCase().id,true);
  syncUndo();
 };
 function clearSelectedAction(){
  pendingMerge=null;showTargets();
  const prefix=stateKey()+':';for(const key of reasonDrafts.keys())if(key.startsWith(prefix))reasonDrafts.delete(key);
  lastReason=null;reasonRenderKey='';WorkingReview.cancelChoice();
 }
 function syncActions(){
  const c=currentCase();if(!c)return;
  const compareLabel=$('#evidence-context>label');if(compareLabel?.firstChild?.nodeType===Node.TEXT_NODE)compareLabel.firstChild.textContent='Compare';
  decorateActions();compactHeader();
  if(pendingMerge&&pendingMerge!==contextKey()){pendingMerge=null;showTargets();}
  const grid=$('#action-grid');grid.querySelector('[data-action=DEFER]')?.remove();
  let merge=grid.querySelector('[data-action=MERGE]');
  if(!merge){merge=document.createElement('button');merge.type='button';merge.className='action-option';merge.dataset.action='MERGE';merge.innerHTML='<span class="action-icon" aria-hidden="true">⋈</span><span>Merge</span><span class="radio" aria-hidden="true"></span>';grid.append(merge);}
  merge.disabled=saving||!!WorkingReview.busy||!Core.candidates(c).length;
  let split=grid.querySelector('[data-action=SPLIT]');
  if(!split){split=document.createElement('button');split.type='button';split.className='action-option';split.dataset.action='SPLIT';split.innerHTML='<span class="action-icon" aria-hidden="true">⑂</span><span>Split</span><span class="radio" aria-hidden="true"></span>';grid.append(split);}
  const source=sourceUnit(c),splittable=!!source&&source.spikes>1&&(c.synthetic===false||source.split);
  split.disabled=saving||!!WorkingReview.busy||!splittable;split.title=!splittable?'Split requires at least two spikes and a valid source':comparisonTarget(c)?'Split '+sourceUnit(c).title+'; end the comparison':'Split '+sourceUnit(c).title;
  if(!split.dataset.studioBound){const original=split.onclick;split.dataset.studioBound='true';split.onclick=()=>{if(comparisonTarget(currentCase())){const carry=captureText();selectComparison('');restoreText(carry);$('#action-grid [data-action=SPLIT]').click();}else original?.();};}
  const chosen=pendingMerge?'MERGE':draft.action;
  for(const button of grid.querySelectorAll('[data-action]')){const active=button.dataset.action===chosen;button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));}
  if(!merge.dataset.studioBound){const original=merge.onclick;merge.dataset.studioBound='true';merge.onclick=()=>{if(comparisonTarget(c)){if(original)original();else applyMerge(comparisonTarget(c));}else beginMerge();};}
  const agreed=draft.agree===true&&!$('.agreement-fieldset').hidden,key=stateKey()+':'+agreed;
  $('#positive-panel').hidden=!agreed;
  if(key!==positiveKey){$('#positive-panel').open=false;positiveKey=key;}
  if(pendingMerge){
   const comparison=$('#compare-with');if(comparison)comparison.onchange=()=>applyMerge(comparison.value);
   $('#merge-controls').hidden=false;$('#merge-source').textContent='Source: '+sourceUnit(c).title;$('#merge-target').innerHTML='<option value="">Choose above or select a target…</option>'+Core.candidates(c).map(u=>'<option value="'+escapeHtml(u.id)+'">'+escapeHtml(u.title)+'</option>').join('');$('#merge-target').value='';$('#merge-target').onchange=()=>applyMerge($('#merge-target').value);
   $('#action-context').innerHTML='<p class="action-hint">Choose the other cluster in the gallery above.</p>';$('#save-button').disabled=true;$('#save-button').textContent='Choose merge target';
  }else if(isWorkingReview()&&WorkingReview.preview){$('#action-context').innerHTML='<p class="action-hint">Inspect both panels, then confirm.</p>';}
  const reasonKey=stateKey()+':'+comparisonTarget(c)+':'+reasonAction();if(reasonKey!==reasonRenderKey){reasonRenderKey=reasonKey;renderReasons();}
  syncUndo();
 }
 gridCapture();
 function gridCapture(){
  $('#action-grid').addEventListener('click',event=>{
   const button=event.target.closest('[data-action]');if(!button||button.disabled)return;
   if(isDirty&&button.classList.contains('selected')){event.preventDefault();event.stopImmediatePropagation();clearSelectedAction();return;}
   if(button.dataset.action!=='MERGE'){pendingMerge=null;showTargets();}
   if(!isWorkingReview()&&currentCase().review_mode!=='manual'&&(draft.agree===null||draft.agree===true&&button.dataset.action!==currentCase().ai))draft.agree=false;
  },true);
 }
 function beginMerge(){
  const c=currentCase();if(!Core.candidates(c).length)return;
  // Clear the previous native operation as well as its visual selection.
  // A pending target must never retain a Keep/Split/Discard confirmation.
  if(isWorkingReview())WorkingReview.choose(null);
  pendingMerge=contextKey();draft.action='MERGE';draft.target='';if(c.review_mode!=='manual')draft.agree=false;isDirty=true;
  renderActions();renderReasons();showTargets();syncUndo();
  queue.scrollTop=0;
 }
 function captureText(){return {reason:$('#reason-select').value,note:$('#review-note').value,positive:$('#positive-comment').value};}
 function restoreText(value){$('#reason-select').value=value.reason;$('#review-note').value=value.note;$('#positive-comment').value=value.positive;renderReasons();}
 function applyMerge(target){
  if(!target)return;const c=currentCase();if(!Core.candidates(c).some(u=>u.id===target))return;
  const carry=captureText();pendingMerge=null;showTargets();
  selectComparison(target);restoreText(carry);
  if(isWorkingReview()){WorkingReview.choose('MERGE',currentCase().review_mode==='manual'?null:false);restoreText(carry);WorkingReview.renderActions();}
  else{draft.action='MERGE';draft.target=target;draft.scope='pair';if(c.review_mode!=='manual')draft.agree=false;renderActions();cacheLocalDraft();WorkingReview.openOperation('MERGE').catch(e=>{$('#form-error').textContent=e.message;});}
 }
 function showTargets(){
  picker.hidden=!pendingMerge;$('#case-list').hidden=!!pendingMerge;targetObserver?.disconnect();
  if(!pendingMerge){picker.replaceChildren();return;}
  const c=currentCase(),units=[sourceUnit(c),...Core.candidates(c)];
  picker.innerHTML='<p class="studio-target-caption">'+escapeHtml(sourceUnit(c).title)+' · choose a merge target</p><div class="studio-target-grid">'+units.map(u=>'<button type="button" class="case-item queue-thumb-card '+(u.id===c.source?'studio-target-source':'')+'" data-studio-target="'+escapeHtml(u.id)+'" '+(u.id===c.source?'disabled':'')+'><span class="queue-thumb-title">'+escapeHtml(u.title)+'</span><span class="queue-wave-preview"><canvas width="360" height="160" role="img" aria-label="'+escapeHtml(u.title)+' real waveform thumbnail"></canvas><span class="queue-wave-loading">Loading…</span></span><span class="queue-thumb-count">'+u.spikes.toLocaleString()+' spikes</span><span class="queue-thumb-quality">'+(u.id===c.source?'Source':'Merge target')+'</span></button>').join('')+'</div>';
  for(const b of picker.querySelectorAll('[data-studio-target]'))b.onclick=()=>applyMerge(b.dataset.studioTarget);
  targetObserver=new IntersectionObserver(rows=>{for(const row of rows)if(row.isIntersecting){targetObserver.unobserve(row.target);const canvas=row.target,u=c.units.find(u=>u.id===canvas.closest('button').dataset.studioTarget);if(isWorkingReview()&&workingContext){workingContext.view.draw(canvas,canvas.nextElementSibling,WorkingReview.state.labels,[{id:u.cluster_id,color:u.id===c.source?FULL_WAVEFORM_COLORS[0]:FULL_WAVEFORM_COLORS[1]}],true,1,()=>{canvas.nextElementSibling.hidden=true;},null,null,'all',WorkingReview.state.row_labels||{});}else if(c.synthetic===false)paintQueueThumbnail(canvas,{...c,source:u.id});else{canvas.nextElementSibling.textContent='Synthetic demo';}}},{root:queue,rootMargin:'70px'});
  for(const canvas of picker.querySelectorAll('canvas'))targetObserver.observe(canvas);
 }
 function renderSnapshot(){
  const c=currentCase();if(!c)return;
  wave.classList.remove('studio-split-active');detail.classList.remove('studio-split-preview');detail.dataset.mode='snapshot';waveKey='';workingContext=null;metricTab();nativeEvidence();metricTab();
  const u=currentUnit(),target=comparisonTarget(c),key=[c.id,c.state_sha256,u.id,target,c.evidenceLoaded].join(':');
  let body;
  if(c.synthetic===false&&c.evidenceLoaded){
   const entry=reviewerAnalysis(c,target?c.source:u.id,target||null),readyKey=key+':'+!!entry.data+':'+(entry.error||'');
   if(detail.dataset.key!==readyKey){body=entry.data?plotCard('Waveforms','',target?pairWaveforms(entry.data,c):singleMemberWaveforms(entry.data,c,u)):analysisPending(entry);waveOptions.replaceChildren();detail.innerHTML=body;detail.dataset.key=readyKey;attachAnalysisControls(detail);attachPlotZoom(detail);}
  }else if(detail.dataset.key!==key){
   const keep=selectedTab;selectedTab='detail';body=c.synthetic===false?realEvidence(c,u):target?renderPairComparison(c):plotCard(u.title+' · waveform','Synthetic demo',waveformLegend(u)+waveform(u));selectedTab=keep;waveOptions.replaceChildren();detail.innerHTML=body;detail.dataset.key=key;attachAnalysisControls(detail);attachPlotZoom(detail);
  }
  if(pendingMerge){const select=$('#compare-with');if(select)select.onchange=()=>applyMerge(select.value);}
  syncActions();syncPlotTypography();
 }
 function isi(values,title){return plotCard(title,'',candidateISI({merged_isi:values}));}
 function childQuality(ctx,g){
  const q=C.quality(ctx.input,ctx.state,g.id),rows=[['ISI violations <2 ms',metricPercent(q.isi.violation_rate)],['Violating / all intervals',q.isi.violation_count.toLocaleString()+' / '+q.isi.denominator.toLocaleString()],['Amplitude mean ± SD',metricNumber(q.mean_amplitude)+' ± '+metricNumber(q.amplitude_sd)],['Amplitude CV',metricNumber(q.amplitude_cv)],['Amplitude change',q.drift_percent==null?'Not defined':q.drift_percent.toFixed(2)+'%'],['Imported quality','Not provided for this new grouping']];
  return '<table class="quality-table" aria-label="Quality metrics for Cluster '+g.id+'"><tbody>'+rows.map(([k,v])=>'<tr><th>'+k+'</th><td>'+v+'</td></tr>').join('')+'</tbody></table>';
 }
 function renderSplitMetrics(ctx){
  wave.classList.add('studio-split-active');const {groups,input,state:projected,selected,view}=ctx;
  const content=g=>metric==='quality'?childQuality(ctx,g):metric==='pca'?ReviewPCA.working():metric==='correlation'?'<p class="analysis-message">Calculating ACG…</p>':metric==='stability'?(()=>{const points=C.quality(input,projected,g.id).scatter;return plotCard('Amplitude over time','Up to 500 chronological spikes',realAmplitude({amplitude_time:{time_seconds:points.map(p=>p[0]),peak_to_trough:points.map(p=>p[1])}}));})():isi(C.quality(input,projected,g.id).isi,'ISI');
  wave.innerHTML='<div class="studio-split-measurements">'+groups.map(g=>'<section class="studio-child-metrics" data-child-cluster="'+g.id+'" data-child-spikes="'+g.n+'" style="--child-color:'+g.color+'"><header><strong>Cluster '+g.id+'</strong><span>'+g.n.toLocaleString()+' spikes</span></header><div class="studio-child-metric-body">'+content(g)+'</div></section>').join('')+'</div>'+(metric==='correlation'?'<details class="studio-child-crosscorr"><summary>Between these child clusters · CCG</summary><div data-child-ccg>Calculating CCG…</div></details>':'');
  for(const card of wave.querySelectorAll('.studio-child-metric-body .plot-card')){const heading=card.querySelector('.plot-heading'),notes=[...card.querySelectorAll(':scope > .plot-note')];if(heading||notes.length){const help=document.createElement('details');help.className='studio-child-plot-details';help.innerHTML='<summary>Plot details</summary>';if(heading)help.append(heading);for(const note of notes)help.append(note);card.append(help);}}
  if(metric==='pca')for(const g of groups){const host=wave.querySelector('[data-child-cluster="'+g.id+'"] .studio-child-metric-body');ReviewPCA.attachWorking(host,{view,input,state:projected,groups:[g],axisGroups:groups,source:selected.source,canSplit:false,onSplit:ctx.onSplit});host.querySelector('.pca-selection-actions').hidden=true;}
  if(metric==='correlation')setTimeout(()=>{if(!ctx.isCurrent())return;try{for(const g of groups){const body=wave.querySelector('[data-child-cluster="'+g.id+'"] .studio-child-metric-body');body.innerHTML=plotCard('ACG','All spikes · self-pairs excluded',correlogramPlot(ctx.correlation(g.id),g.color));}wave.querySelector('[data-child-ccg]').innerHTML=plotCard('Cluster '+groups[0].id+' → '+groups[1].id,'Target − source',correlogramPlot(ctx.correlation(groups[0].id,groups[1].id),'#5c3d9d'));attachPlotZoom(wave);syncPlotTypography();}catch(e){for(const body of wave.querySelectorAll('.studio-child-metric-body'))body.textContent=e.message;}},20);
  attachPlotZoom(wave);syncPlotTypography();$('.evidence-footer>span:first-child').textContent='Split preview';$('.evidence-footer>span:last-child').textContent='Not saved · confirm to apply';syncActions();
 }
 function renderWorking(ctx){
  detail.dataset.mode='working';workingContext=ctx;delete detail.dataset.key;const {input,ws,selected,projection,action,view,virtual,hash,groups,state:stateValue}=ctx;
  metricTab();detail.classList.toggle('studio-split-preview',projection?.action==='SPLIT');
  if(!selected){waveOptions.replaceChildren();detail.innerHTML='<p>No active clusters. Use Undo to restore a grouping.</p>';wave.innerHTML='';waveKey='';syncActions();return;}
  if(projection?.action==='SPLIT'&&groups.length===2){const count=C.units(input,stateValue).length;$('#metrics').innerHTML='<div class="metrics-table" role="table" aria-label="Split preview child statistics"><div class="metric-table-header" role="row">'+['Cluster','Spikes','Detail','Active','Evidence'].map(text=>'<span role="columnheader">'+text+'</span>').join('')+'</div>'+groups.map(g=>'<div class="metric-table-row" role="row" data-preview-child="'+g.id+'"><strong role="cell" style="color:'+g.color+'">Cluster '+g.id+'</strong><span role="cell">'+g.n.toLocaleString()+'</span><span role="cell">'+(waveformMemberMode==='sample24'?Math.min(24,g.n)+' sampled':'All waveforms')+'</span><span role="cell">'+count+'</span><span role="cell">MAT</span></div>').join('')+'</div>';}
  const mergePreview=projection?.action==='MERGE',originalGroups=[selected.source,selected.target].filter(v=>v!=null).map((id,i)=>({id,n:C.units(input,ws).find(u=>u.id===id).n,color:FULL_WAVEFORM_COLORS[i],opacity:workOpacities.get(id)??1}));
  for(const g of groups)g.opacity=workOpacities.get(g.id)??g.opacity;
  const displayGroups=mergePreview?originalGroups:groups,tableGroups=groups;
  if(projection&&!projections.has(projection))projections.set(projection,++projectionId);
  const populationKey=[ws.workspace_id,ws.version,selected.source,selected.target,projection?projections.get(projection):''].join(':');
  if(workPopulationKey!==populationKey){workDisplay='both';workPopulationKey=populationKey;}
  if(workDisplay!=='both'&&!displayGroups.some(g=>String(g.id)===workDisplay))workDisplay='both';
  const visibleGroups=displayGroups.filter(g=>workDisplay==='both'||String(g.id)===workDisplay);
  const nextKey=[ws.workspace_id,ws.version,hash,selected.source,selected.target,projection?projections.get(projection):'',waveformMemberMode,workDisplay,displayGroups.map(g=>g.opacity).join(',')].join(':');
  if(waveKey!==nextKey){
   waveKey=nextKey;waveOptions.replaceChildren();
   detail.innerHTML=(projection&&!mergePreview?'<div class="inline-preview-summary"><strong>'+ACTIONS[action].label+' preview · not saved</strong><p>'+groups.map(g=>'Cluster '+g.id+' · '+g.n.toLocaleString()).join(' / ')+'</p></div>':'')+'<div class="full-wave-display-controls"><label>Waveforms<select id="studio-wave-mode"><option value="all">All waveforms</option><option value="sample24">24 sampled / cluster</option></select></label>'+ (displayGroups.length===2?'<label>Display<select id="studio-wave-display"><option value="both">Both clusters</option>'+displayGroups.map(g=>'<option value="'+g.id+'">Cluster '+g.id+' only</option>').join('')+'</select></label>':'')+displayGroups.map(g=>'<label style="color:'+g.color+'">Cluster '+g.id+' opacity<input type="range" min="10" max="100" step="5" value="'+Math.round(g.opacity*100)+'" data-studio-opacity="'+g.id+'"><output>'+Math.round(g.opacity*100)+'%</output></label>').join('')+'</div><div class="plot-key">'+displayGroups.map(g=>'<span style="color:'+g.color+'">Cluster '+g.id+' · '+g.n.toLocaleString()+'</span>').join('')+'</div>'+plotCard(mergePreview?'Merge preview':projection?'Split preview · both child clusters':'','', '<div class="inline-working-wave" data-main-wave'+(mergePreview?' data-merge-comparison':'')+'><canvas></canvas><p class="inline-wave-status"></p></div>');
   const isCurrentWave=()=>isWorkingReview()&&WorkingReview.state===ws&&WorkingReview.preview===projection&&waveKey===nextKey;
   if(projection){projection.ready=false;projection.mainReady=false;projection.originalReady=action!=='MERGE';ctx.refreshReady();}
   const ready=()=>{if(!projection||!isCurrentWave())return;projection.ready=!!projection.mainReady&&!!projection.originalReady;ctx.refreshReady();syncUndo();};
   const draw=(host,populations,state,done,axisGroups=displayGroups)=>view.draw(host.querySelector('canvas'),host.querySelector('.inline-wave-status'),state.labels,populations,false,1,done,axisGroups,null,waveformMemberMode,state.row_labels||{});
   draw(detail.querySelector('[data-main-wave]'),visibleGroups,mergePreview?ws:stateValue,()=>{if(projection&&isCurrentWave()){projection.seenGroups=projection.seenGroups||new Set();for(const g of visibleGroups)projection.seenGroups.add(g.id);projection.mainReady=displayGroups.every(g=>projection.seenGroups.has(g.id));if(mergePreview)projection.originalReady=projection.mainReady;ready();}});
   $('#studio-wave-mode').value=waveformMemberMode;$('#studio-wave-mode').onchange=()=>{waveformMemberMode=$('#studio-wave-mode').value;localStorage.setItem('spike-review-waveform-members',waveformMemberMode);WorkingReview.renderCase();};if($('#studio-wave-display')){$('#studio-wave-display').value=workDisplay;$('#studio-wave-display').onchange=()=>{workDisplay=$('#studio-wave-display').value;ctx.onRender();};}for(const slider of detail.querySelectorAll('[data-studio-opacity]')){slider.oninput=()=>{workOpacities.set(Number(slider.dataset.studioOpacity),Number(slider.value)/100);slider.nextElementSibling.value=slider.value+'%';};slider.onchange=()=>ctx.onRender();}attachPlotZoom(detail);
  }
  if(projection?.action==='SPLIT'&&groups.length===2){renderSplitMetrics(ctx);return;}
  wave.classList.remove('studio-split-active');
  let html='';
  if(metric==='timing'){
   html=tableGroups.map(g=>isi(C.quality(input,stateValue,g.id).isi,'Cluster '+g.id+' · '+(projection?'projected ':'')+'ISI')).join('');
   if(!projection&&groups.length===2)html+=isi(C.pairISI(input,ws,selected.source,selected.target),'If merged · pooled ISI');
  }else if(metric==='quality'){
   html=(projection?'<p class="plot-note">Imported scores belong to the saved grouping.</p>':ReviewQuality.controls())+'<div class="quality-table-wrap"><table class="quality-table"><thead><tr><th>Metric</th>'+groups.map(g=>'<th>Cluster '+g.id+'</th>').join('')+'</tr></thead><tbody>'+[
    ['Spikes',q=>q.n.toLocaleString()],['ISI violations <2 ms',q=>metricPercent(q.isi.violation_rate)],['Amplitude mean ± SD',q=>metricNumber(q.mean_amplitude)+' ± '+metricNumber(q.amplitude_sd)],['Amplitude CV',q=>metricNumber(q.amplitude_cv)],['Amplitude change',q=>q.drift_percent==null?'Not defined':q.drift_percent.toFixed(2)+'%'],['Input / imported quality',q=>projection?'Unavailable for preview':escapeHtml(ReviewQuality.cell(virtual,q.id))]
   ].map(([name,format])=>'<tr><th>'+name+'</th>'+groups.map(g=>'<td>'+format(C.quality(input,stateValue,g.id))+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
  }else if(metric==='pca')html=ReviewPCA.working();
  else if(metric==='correlation')html='<div id="studio-correlation-loading" role="status">Calculating ACG / CCG…</div>';
  else if(metric==='stability')html=groups.map(g=>{const points=C.quality(input,stateValue,g.id).scatter;return plotCard('Cluster '+g.id+' · amplitude over time','Up to 500 chronological spikes',realAmplitude({amplitude_time:{time_seconds:points.map(p=>p[0]),peak_to_trough:points.map(p=>p[1])}}));}).join('');
  wave.innerHTML=html;ReviewQuality.bind(wave,virtual);
  if(metric==='pca')ReviewPCA.attachWorking(wave,{view,input,state:stateValue,groups,source:selected.source,canSplit:!projection&&selected.target==null&&groups[0]?.n>1,selectOnLoad:action==='SPLIT'&&!projection,onSplit:ctx.onSplit});
  if(metric==='correlation')setTimeout(()=>{if(!ctx.isCurrent())return;try{let html=groups.map(g=>plotCard('Cluster '+g.id+' · ACG','All spikes · self-pairs excluded',correlogramPlot(ctx.correlation(g.id),g.color))).join('');if(groups.length===2)html+=plotCard('Cluster '+groups[0].id+' → '+groups[1].id+' · CCG','Target − source',correlogramPlot(ctx.correlation(groups[0].id,groups[1].id),'#5c3d9d'));wave.innerHTML=html;attachPlotZoom(wave);syncPlotTypography();}catch(e){const loading=$('#studio-correlation-loading');if(loading)loading.textContent=e.message;}},20);
  attachPlotZoom(wave);syncPlotTypography();$('.evidence-footer>span:first-child').textContent='Current grouping';$('.evidence-footer>span:last-child').textContent=projection?'Preview · confirm to apply':'Saved v'+ws.version;
  if(pendingMerge){const select=$('#compare-with');if(select)select.onchange=()=>applyMerge(select.value);}
  syncActions();
 }
 renderEvidence=function(){return isWorkingReview()?nativeEvidence():renderSnapshot();};
 renderActions=function(){const value=nativeActions();if(!isWorkingReview())syncActions();return value;};
 for(const id of ['agree-button','disagree-button'])$('#'+id).addEventListener('click',()=>{pendingMerge=null;showTargets();syncActions();});
 for(const input of document.querySelectorAll('#review-editor input,#review-editor textarea,#review-editor select'))input.addEventListener('input',syncUndo);
 function resizer(handle,axis,read,apply,limits,key,defaultSize=null){
  let drag=null,requested=null;
  const initial=()=>defaultSize?defaultSize():axis==='x'?310:210;
  try{const saved=Number(localStorage.getItem(key));if(saved){requested=saved;apply(Math.max(limits()[0],Math.min(limits()[1],saved)));}}catch{}
  const change=value=>{const [min,max]=limits(),next=Math.max(min,Math.min(max,value));apply(next);handle.setAttribute('aria-valuenow',String(Math.round(next)));handle.setAttribute('aria-valuemin',String(min));handle.setAttribute('aria-valuemax',String(max));};
  handle.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();drag={start:axis==='x'?e.clientX:e.clientY,size:read()};handle.setPointerCapture(e.pointerId);};
  handle.onpointermove=e=>{if(drag)change(drag.size+(axis==='x'?drag.start-e.clientX:e.clientY-drag.start));};
  const finish=()=>{if(!drag)return;drag=null;requested=read();try{localStorage.setItem(key,String(requested));}catch{}syncPlotTypography();};
  handle.onpointerup=handle.onpointercancel=handle.onlostpointercapture=finish;
  handle.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home'].includes(e.key))return;e.preventDefault();change(e.key==='Home'?initial():read()+(e.key==='ArrowLeft'||e.key==='ArrowDown'?18:-18));requested=read();try{localStorage.setItem(key,String(requested));}catch{}syncPlotTypography();};
  handle.ondblclick=()=>{requested=null;try{localStorage.removeItem(key);}catch{}change(initial());syncPlotTypography();};
  window.addEventListener('resize',()=>{if(handle.offsetParent)change(requested??(defaultSize?initial():axis==='x'?Math.min(370,Math.max(250,innerWidth*.24)):Math.min(285,Math.max(180,innerHeight*.27))));});
  return ()=>change(requested??read());
 }
 resizer($('.panel-resizer[data-resize=decision]'),'x',()=>$('#decision-panel').getBoundingClientRect().width,n=>workspace.style.setProperty('--studio-decision-width',n+'px'),()=>[240,Math.min(580,workspace.clientWidth*(innerWidth<=1100?.29:.46))],'spikeagent-studio-decision-width');
 resizer(galleryResize,'y',()=>queue.getBoundingClientRect().height,n=>center.style.setProperty('--studio-gallery-height',n+'px'),()=>[130,Math.max(160,innerHeight-600)],'spikeagent-studio-gallery-height');
 const fitEvidence=resizer(evidenceResize,'x',()=>columns.lastElementChild.getBoundingClientRect().width,n=>columns.style.setProperty('--studio-metric-width',n+'px'),()=>{const available=columns.clientWidth-8,min=Math.min(220,available*.32);return [min,available-min];},'spikeagent-studio-metric-width',()=>(columns.clientWidth-8)/2);
 new ResizeObserver(()=>{if(evidenceResize.offsetParent)fitEvidence();}).observe(columns);
 return {active:true,agentSlots:['Agent_1','Agent_2'],syncActions,renderWorking,get pendingMerge(){return !!pendingMerge;},get metric(){return metric;}};
})();
