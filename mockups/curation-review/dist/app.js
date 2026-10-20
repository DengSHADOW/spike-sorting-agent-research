'use strict';

// Every signal and suggestion below is synthetic meeting material.
// No real model calls, recordings, or changes to spike assignments are made.
const CASES = [
  {id:'U-042',channel:'CH20',title:'Unit 042',kind:'mixed',spikes:6240,snr:'4.2',isi:'3.8',duration:'20',phase:'Cluster review',ai:'DISCARD',risk:'High impact',brief:'Mixed waveforms · check before discarding',summary:'Broad waveform variability suggests a noisy cluster.',rationale:'Illustrative model rationale: the waveform bundle contains several shapes. The proposed action discards the entire cluster without inspecting whether a useful subcluster can be separated.',split:true,seed:42},
  {id:'U-017',channel:'CH03',title:'Unit 017',kind:'split',spikes:3810,snr:'5.1',isi:'2.7',duration:'20',phase:'Cluster review',ai:'SPLIT',risk:'Needs review',brief:'Two waveform families · inspect children',summary:'Two waveform families may be separable in the current tree.',rationale:'Illustrative model rationale: a second waveform family persists across the recording. Inspecting the two available children may help distinguish a mixed cluster from amplitude variation.',split:true,seed:17},
  {id:'P-008',channel:'CH30',title:'Units 008 + 021',kind:'merge',spikes:4890,snr:'6.0',isi:'0.4',duration:'20',phase:'Pair comparison',ai:'MERGE',risk:'High impact',brief:'Similar templates · compare timing evidence',summary:'Similar waveforms suggest that the two clusters may share a source.',rationale:'Illustrative model rationale: the template shapes overlap. Timing and stability should be considered before deciding whether similarity represents one neuron or two distinct units.',split:false,pair:['U-008','U-021'],seed:8},
  {id:'U-063',channel:'CH17',title:'Unit 063',kind:'noise',spikes:960,snr:'1.3',isi:'8.6',duration:'20',phase:'Cluster review',ai:'KEEP',risk:'Needs review',brief:'Low signal-to-noise · verify unit quality',summary:'A repeating event shape was treated as evidence for keeping the cluster.',rationale:'Illustrative model rationale: events share a rough average shape. The proposal does not resolve the low signal-to-noise ratio or inconsistent timing evidence.',split:true,seed:63},
  {id:'U-005',channel:'CH01',title:'Unit 005',kind:'clean',spikes:7820,snr:'8.6',isi:'0.2',duration:'20',phase:'Cluster review',ai:'KEEP',risk:'Routine',brief:'Consistent waveform · stable amplitude',summary:'The displayed evidence is consistent with keeping this unit for further analysis.',rationale:'Illustrative model rationale: the waveform bundle is compact and the amplitude is stable. This is a low-friction review example, not a measured performance claim.',split:true,seed:5},
  {id:'U-031',channel:'CH31',title:'Unit 031',kind:'leaf',spikes:740,snr:'2.1',isi:'1.9',duration:'20',phase:'Cluster review',ai:'SPLIT',risk:'Action unavailable',brief:'Leaf cluster · SPLIT cannot be executed',summary:'The model proposes a split, but the current tree has no child nodes.',rationale:'Illustrative model rationale: the cluster looks heterogeneous. The suggestion overlooks the fact that no further tree split is available in this state.',split:false,seed:31}
];
const ACTIONS = {
  KEEP:{label:'Keep',icon:'✓',hint:'Keep this cluster in the working set. This does not assert a final “good” label.'},
  SPLIT:{label:'Split',icon:'⑂',hint:'Request inspection of the two child clusters. No spike assignments change in this demo.'},
  DISCARD:{label:'Discard',icon:'×',hint:'Propose removing this cluster from the working set. Record the evidence for this decision.'},
  MERGE:{label:'Merge',icon:'⋈',hint:'Propose joining the selected pair. This demo records a suggestion, not a real merge.'},
  NOT_MERGE:{label:'Keep separate',icon:'↔',hint:'Keep both clusters separate. This decision does not label either cluster as good or noise.'},
  DEFER:{label:'Defer',icon:'…',hint:'Keep the case unresolved and request additional evidence or expert review.'}
};
const STORAGE_KEY = 'spikeagent-human-review-demo-v1';
let state = {reviews:{},events:[]};
let storageAvailable = true;
try {
  const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
  if(saved && saved.reviews && typeof saved.reviews==='object' && Array.isArray(saved.events)){
    state.reviews=Object.fromEntries(Object.entries(saved.reviews).filter(([id,r])=>CASES.some(c=>c.id===id)&&r&&ACTIONS[r.action]&&typeof r.timestamp==='string'));
    state.events=saved.events;
  }
} catch {storageAvailable=false;}
let selectedId=CASES[0].id, selectedAction=CASES[0].ai, selectedTab='overview', filter='all', currentView='review';
let tabsViewed=new Set(['overview']), lastUndoId=null, toastTimeout=null, isDirty=false;
const $=s=>document.querySelector(s);
const $$=s=>Array.from(document.querySelectorAll(s));
const escapeHtml=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const currentCase=()=>CASES.find(c=>c.id===selectedId);
const allowed=c=>c.pair?['MERGE','NOT_MERGE','DEFER']:['KEEP',...(c.split?['SPLIT']:[]),'DISCARD','DEFER'];
const dateText=t=>new Date(t).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});

function persist(){
  try{localStorage.setItem(STORAGE_KEY,JSON.stringify(state));}
  catch{storageAvailable=false;toast('Browser storage unavailable. Export feedback before closing.');}
}
function toast(message,undoId=null){
  clearTimeout(toastTimeout);
  $('#toast').innerHTML=`<span>${escapeHtml(message)}</span>${undoId?'<button id="toast-undo">Undo</button>':''}`;
  $('#toast').classList.add('visible');
  if(undoId)$('#toast-undo').onclick=()=>undoReview(undoId);
  toastTimeout=setTimeout(()=>$('#toast').classList.remove('visible'),5500);
}
function showView(view){
  if(!['review','history','revisions'].includes(view))view='review';
  currentView=view;
  $$('.view').forEach(el=>el.classList.toggle('active',el.id===`view-${view}`));
  $$('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
  $('#page-name').textContent={review:'Expert review',history:'Feedback history',revisions:'Workflow revisions'}[view];
  history.replaceState(null,'',`#${view}`);
  if(view==='history')renderHistory();
}
function renderQueue(){
  const query=$('#case-search').value.toLowerCase().trim();
  const visible=CASES.filter(c=>(`${c.id} ${c.channel} ${c.title}`.toLowerCase().includes(query))&&(filter==='all'||(filter==='reviewed'?!!state.reviews[c.id]:!state.reviews[c.id])));
  $('#case-list').innerHTML=visible.length?visible.map(c=>`<button class="case-item ${c.id===selectedId?'active':''}" data-case="${c.id}" aria-pressed="${c.id===selectedId}"><div class="case-item-top"><strong>${escapeHtml(c.title)}</strong><span class="mini-channel">${c.channel}</span></div><p>${escapeHtml(c.brief)}</p><div class="case-item-bottom">${state.reviews[c.id]?`<span class="reviewed-check">✓ ${escapeHtml(ACTIONS[state.reviews[c.id].action].label)} recorded</span>`:`<span class="risk ${c.risk==='High impact'?'high':c.risk==='Routine'?'low':''}">${c.risk}</span>`}<span class="case-arrow">${c.id===selectedId?'→':'›'}</span></div></button>`).join(''):'<p class="no-results">No cases match this filter.</p>';
  $$('[data-case]').forEach(b=>b.onclick=()=>selectCase(b.dataset.case));
  const count=Object.keys(state.reviews).length;
  $('#reviewed-count').textContent=count;$('#pending-count').textContent=CASES.length-count;
  $('#progress-text').textContent=`${count} of ${CASES.length} reviewed`;
  $('#progress-percent').textContent=`${Math.round(count/CASES.length*100)}%`;
  $('#progress-bar').style.width=`${count/CASES.length*100}%`;
}
function selectCase(id,force=false){
  if(!force&&isDirty&&id!==selectedId){showUnsaved(id);return;}
  selectedId=id;selectedTab='overview';tabsViewed=new Set(['overview']);isDirty=false;
  const c=currentCase(),review=state.reviews[id];
  selectedAction=review?review.action:allowed(c).includes(c.ai)?c.ai:null;
  $('#reason-select').value=review?.reason||'';$('#review-note').value=review?.note||'';
  $('#form-error').textContent='';
  renderQueue();renderCase();
}
function renderCase(){
  const c=currentCase();
  $('#case-channel').textContent=`${c.channel} / ${c.phase.toUpperCase()} / DEMO CASE`;
  $('#case-title').textContent=c.title;
  $('#case-badges').innerHTML=`<span class="pill">${c.pair?'Pair review':'Single cluster'}</span><span class="pill ${c.risk==='High impact'?'amber':'teal'}">${state.reviews[c.id]?'Reviewed':c.risk}</span>`;
  $('#metrics').innerHTML=[
    ['Spike count',c.spikes.toLocaleString(),''],
    ['Signal / noise',c.snr,''],
    ['ISI violations',c.isi,'%',parseFloat(c.isi)>2],
    ['Recording',c.duration,'min']
  ].map(([label,value,unit,warn])=>`<div class="metric ${warn?'warn':''}"><span>${label}</span><strong>${value}<small>${unit}</small></strong></div>`).join('');
  $('#suggestion').innerHTML=`<div class="suggestion-card"><div class="suggestion-label">✧ AI suggestion <span style="margin-left:auto;font-size:7px">SCRIPTED DEMO</span></div><div class="suggestion-action"><strong>${ACTIONS[c.ai].label}${c.pair?' pair':''}</strong><span>Review requested</span></div><p>${c.summary}</p><button class="show-rationale" id="rationale-button" aria-expanded="false">Show reasoning</button><p class="rationale" id="rationale-text" hidden>${c.rationale}</p></div>`;
  $('#rationale-button').onclick=()=>{
    const el=$('#rationale-text');el.hidden=!el.hidden;
    $('#rationale-button').textContent=el.hidden?'Show reasoning':'Hide reasoning';
    $('#rationale-button').setAttribute('aria-expanded',String(!el.hidden));
  };
  $('#validity-warning').innerHTML=c.kind==='leaf'?'<div class="warning"><strong>SPLIT is unavailable.</strong><br>This is a leaf node. Select a valid action or defer the case.</div>':'';
  renderActions();renderEvidence();
}
function renderActions(){
  const c=currentCase(), actions=c.pair?['MERGE','NOT_MERGE','DEFER']:['KEEP','SPLIT','DISCARD','DEFER'];
  $('#action-grid').innerHTML=actions.map(a=>`<button type="button" class="action-option ${selectedAction===a?'selected':''}" data-action="${a}" aria-pressed="${selectedAction===a}" ${!allowed(c).includes(a)?'disabled title="No child clusters are available"':''}><span class="action-icon" aria-hidden="true">${ACTIONS[a].icon}</span><span>${ACTIONS[a].label}</span><span class="radio" aria-hidden="true"></span></button>`).join('');
  $$('[data-action]').forEach(b=>b.onclick=()=>{selectedAction=b.dataset.action;isDirty=true;$('#form-error').textContent='';renderActions();});
  $('#action-context').innerHTML=`<p class="action-hint">${selectedAction?ACTIONS[selectedAction].hint:'The proposed action is unavailable. Choose an action above.'}</p>`;
  const correction=selectedAction&&selectedAction!==c.ai;
  $('#required-indicator').textContent=correction?'reason or note required':'optional';
  $('#decision-preview').innerHTML=selectedAction?`${correction?`<strong>Correction:</strong> ${ACTIONS[c.ai].label} → ${ACTIONS[selectedAction].label}.`:`<strong>Accept suggestion:</strong> ${ACTIONS[selectedAction].label}.`} Saved with this evidence snapshot. No real data will be changed.`:'Choose a valid action to continue.';
  $('#save-button').disabled=!selectedAction;
  $('#save-button').innerHTML=`${state.reviews[c.id]?'Update review & next':'Save review & next'} <span aria-hidden="true">→</span>`;
}
function saveReview(){
  const c=currentCase();
  if(!selectedAction||!allowed(c).includes(selectedAction)){ $('#form-error').textContent='Choose an available action.';return; }
  const reason=$('#reason-select').value,note=$('#review-note').value.trim();
  if(selectedAction!==c.ai&&!reason&&!note){
    $('#form-error').textContent='Add a reason or note for your correction.';$('#reason-select').focus();return;
  }
  const review={case_id:c.id,channel:c.channel,ai_action:c.ai,action:selectedAction,reason,note,timestamp:new Date().toISOString(),evidence_id:`synthetic-${c.id}-v1`,viewed_tabs:[...tabsViewed],additional_evidence_viewed:tabsViewed.has('stability'),synthetic:true};
  state.reviews[c.id]=review;
  state.events.push({type:'review_saved',...review});
  persist();isDirty=false;lastUndoId=c.id;
  renderQueue();
  const index=CASES.findIndex(x=>x.id===c.id);
  const next=[...CASES.slice(index+1),...CASES.slice(0,index)].find(x=>!state.reviews[x.id]);
  if(next)selectCase(next.id,true);
  else {renderCase();showView('history');}
  toast(`${c.title}: ${ACTIONS[review.action].label} saved.`,c.id);
}
function undoReview(id){
  if(!state.reviews[id])return;
  state.events.push({type:'review_undone',case_id:id,previous_review:state.reviews[id],timestamp:new Date().toISOString(),synthetic:true});
  delete state.reviews[id];persist();lastUndoId=null;
  selectCase(id,true);renderHistory();showView('review');toast('Review undone. The case is back in the queue.');
}
function renderHistory(){
  const reviews=Object.values(state.reviews).sort((a,b)=>b.timestamp.localeCompare(a.timestamp));
  const corrected=reviews.filter(r=>r.action!==r.ai_action).length;
  $('#history-stats').innerHTML=[['Reviewed',reviews.length],['Suggestions accepted',reviews.length-corrected],['Suggestions corrected',corrected],['Deferred',reviews.filter(r=>r.action==='DEFER').length]].map(([name,n])=>`<div class="stat-card"><span>${name}</span><strong>${n.toString().padStart(2,'0')}</strong></div>`).join('');
  $('#history-list').innerHTML=reviews.length?`<table class="history-table"><thead><tr><th>Case</th><th>AI suggestion</th><th>Your action</th><th>Reason / note</th><th>Time</th><th></th></tr></thead><tbody>${reviews.map(r=>`<tr><td><strong>${escapeHtml(r.case_id)}</strong><small>${escapeHtml(r.channel)} · synthetic</small></td><td>${ACTIONS[r.ai_action]?.label||'Unknown'}</td><td><span class="pill ${r.action===r.ai_action?'teal':'amber'}">${ACTIONS[r.action].label}</span><small>${r.action===r.ai_action?'Accepted':'Corrected'}</small></td><td>${escapeHtml(r.reason||'No reason added')}<small>${escapeHtml(r.note)}</small></td><td class="mono">${dateText(r.timestamp)}</td><td><button class="text-button" data-undo="${escapeHtml(r.case_id)}">Undo</button></td></tr>`).join('')}</tbody></table>`:'<div class="empty-state"><div class="empty-icon">✓</div><h3>Your first review starts the record.</h3><p>Accept a suggestion or make a correction in the workspace.</p><button class="button" id="empty-review">Open review workspace →</button></div>';
  $$('[data-undo]').forEach(b=>b.onclick=()=>undoReview(b.dataset.undo));
  if($('#empty-review'))$('#empty-review').onclick=()=>showView('review');
}
function exportFeedback(){
  const payload={schema:'spikeagent-human-interface-mockup-v1',synthetic:true,notice:'Illustrative interface data. Not experimental results. No real model or spike curation operations were performed.',exported_at:new Date().toISOString(),reviews:Object.values(state.reviews),events:state.events};
  const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download='spikeagent-demo-feedback.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  toast(`Exported ${payload.reviews.length} review${payload.reviews.length===1?'':'s'} and the event history.`);
}

// Deterministic, code-generated SVGs keep synthetic evidence consistent across views.
function rng(seed){let n=seed;return()=>{n=(Math.imul(n,1664525)+1013904223)>>>0;return n/4294967296;};}
function axes(w,h,xLabel,yLabel,xLabels,yLabels){
  const l=40,r=14,t=18,b=32,iw=w-l-r,ih=h-t-b;
  let markup='';
  for(let i=0;i<5;i++){const y=t+ih*i/4;markup+=`<path d="M${l} ${y}H${w-r}" stroke="#edf1f4"/><text x="${l-9}" y="${y+3}" text-anchor="end">${yLabels[i]??''}</text>`;}
  for(let i=0;i<xLabels.length;i++){const x=l+iw*i/(xLabels.length-1);markup+=`<text x="${x}" y="${h-17}" text-anchor="middle">${xLabels[i]}</text>`;}
  markup+=`<text class="axis-label" x="${l+iw/2}" y="${h-2}" text-anchor="middle">${xLabel}</text><text class="axis-label" x="3" y="10">${yLabel}</text>`;
  return {markup,l,t,iw,ih};
}
function waveform(c){
  const w=610,h=218,{markup,l,t,iw,ih}=axes(w,h,'Time (ms)','µV',['−0.5','0','0.5','1.0','1.5'],['50','0','−50','−100','−150']);
  const random=rng(c.seed),mixed=['mixed','split','merge'].includes(c.kind),noise=c.kind==='noise',center=t+ih*.25;
  const f=(x,second=false)=>{let shift=second?.025:0;return -1.12*Math.exp(-Math.pow((x-.43-shift)/.048,2))+.42*Math.exp(-Math.pow((x-.53-shift)/.08,2))+.08*Math.exp(-Math.pow((x-.32)/.045,2));};
  let lines='';
  for(let k=0;k<48;k++){
    const second=mixed&&k>25,amp=(noise?.21:second?.52:.88)+(random()-.5)*(noise?.3:.28);
    const points=Array.from({length:120},(_,i)=>{
      const x=i/119,wiggle=(random()-.5)*(noise?19:3.8);
      return `${(l+x*iw).toFixed(1)},${(center-f(x,second)*amp*ih*.60+wiggle).toFixed(1)}`;
    }).join(' ');
    lines+=`<polyline points="${points}" fill="none" stroke="${second?'#c88972':'#62a99b'}" stroke-opacity="${noise?.22:.18}" stroke-width=".9"/>`;
  }
  const avg=second=>Array.from({length:140},(_,i)=>`${(l+i/139*iw).toFixed(1)},${(center-f(i/139,second)*(noise?.21:second?.52:.88)*ih*.60).toFixed(1)}`).join(' ');
  lines+=`<polyline points="${avg(false)}" fill="none" stroke="#33897c" stroke-width="1.8"/>`;
  if(mixed)lines+=`<polyline points="${avg(true)}" fill="none" stroke="#bf8b72" stroke-width="1.6"/>`;
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Synthetic waveform traces for ${c.title}">${markup}${lines}</svg>`;
}
function correlogram(c,cross=false,large=false){
  const w=large?610:296,h=large?250:177,{markup,l,t,iw,ih}=axes(w,h,'Lag (ms)','Count',['−25','0','25'],['80','60','40','20','0']),random=rng(c.seed+(cross?110:100));
  let bars='';const count=55;
  for(let i=0;i<count;i++){
    const d=Math.abs(i-(count-1)/2),dip=(['clean','merge'].includes(c.kind)?(d<2?.035:d<4?.25:1):(d<2?.5:1));
    const height=(.30+random()*.46)*dip*ih;
    bars+=`<rect x="${(l+i*iw/count).toFixed(2)}" y="${(t+ih-height).toFixed(2)}" width="${(iw/count-1.5).toFixed(2)}" height="${height.toFixed(2)}" fill="${cross?'#c08a73':'#70aaa0'}" opacity=".8" rx=".3"/>`;
  }
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Synthetic ${cross?'cross':'auto'}correlogram">${markup}<rect x="${l+iw*.46}" y="${t}" width="${iw*.08}" height="${ih}" fill="#fcf3e6" opacity=".8"/>${bars}</svg>`;
}
function amplitude(c,large=false){
  const w=large?610:296,h=large?235:177,{markup,l,t,iw,ih}=axes(w,h,'Time (min)','µV',['0','5','10','15','20'],['160','120','80','40','0']),random=rng(c.seed+200);
  let dots='';
  for(let i=0;i<270;i++){
    const x=random(),group=['mixed','split'].includes(c.kind)&&i>145;
    const trend=c.kind==='mixed'?.08*x:c.kind==='leaf'?.22*x:0;
    const base=c.kind==='noise'?.75:group?.64:.36;
    const y=Math.max(.03,Math.min(.95,base+trend+(random()-.5)*(c.kind==='noise'?.30:.14)));
    dots+=`<circle cx="${(l+x*iw).toFixed(1)}" cy="${(t+y*ih).toFixed(1)}" r="1.2" fill="${group?'#bf8f79':'#6a9e94'}" opacity=".55"/>`;
  }
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Synthetic spike amplitude over time">${markup}${dots}</svg>`;
}
function locationPlot(c){
  const w=296,h=177,{markup,l,t,iw,ih}=axes(w,h,'Lateral position (µm)','Depth (µm)',['−40','0','40'],['200','150','100','50','0']),random=rng(c.seed+300);
  let dots='';
  for(let i=0;i<180;i++){
    const second=['mixed','split'].includes(c.kind)&&i>90;
    const x=.5+(random()+random()-1)*.21+(second?.15:0),y=.4+(random()+random()-1)*.15+(second?.25:0);
    dots+=`<circle cx="${(l+x*iw).toFixed(1)}" cy="${(t+y*ih).toFixed(1)}" r="1.5" fill="${second?'#c08c75':'#50998b'}" opacity=".5"/>`;
  }
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Synthetic event positions">${markup}${dots}</svg>`;
}
function treePlot(c){
  if(c.pair)return `<svg class="tree-svg" viewBox="0 0 610 250" role="img" aria-label="Two separate clusters and a proposed merge"><path d="M160 75L305 165 450 75" fill="none" stroke="#c3d4d0" stroke-width="2" stroke-dasharray="5 5"/><rect x="95" y="30" width="130" height="55" rx="8" fill="#eef6f3" stroke="#a1c8bd"/><rect x="385" y="30" width="130" height="55" rx="8" fill="#fbf3ec" stroke="#d7b7a0"/><text x="160" y="61" text-anchor="middle">Unit 008</text><text x="450" y="61" text-anchor="middle">Unit 021</text><rect x="220" y="160" width="170" height="55" rx="8" fill="#f8fafb" stroke="#c8d3da" stroke-dasharray="5 5"/><text x="305" y="191" text-anchor="middle">Proposed merge only</text></svg>`;
  return `<svg class="tree-svg" viewBox="0 0 610 250" role="img" aria-label="${c.split?'Current cluster with two available children':'Leaf cluster with no available children'}"><path d="M305 75V110${c.split?'M160 156V110H450V156':''}" fill="none" stroke="#bfd1cc" stroke-width="2"/><rect x="230" y="20" width="150" height="60" rx="8" fill="#edf6f3" stroke="#87b6a8"/><text x="305" y="45" text-anchor="middle" style="fill:#3c796d">${c.title}</text><text x="305" y="64" text-anchor="middle">${c.spikes.toLocaleString()} events</text>${c.split?`<rect x="85" y="155" width="150" height="60" rx="8" fill="#f7faf9" stroke="#c9dcd6"/><rect x="375" y="155" width="150" height="60" rx="8" fill="#fcf7f2" stroke="#e0cfbd"/><text x="160" y="181" text-anchor="middle">Child A</text><text x="160" y="200" text-anchor="middle">${Math.round(c.spikes*.62).toLocaleString()} events</text><text x="450" y="181" text-anchor="middle">Child B</text><text x="450" y="200" text-anchor="middle">${(c.spikes-Math.round(c.spikes*.62)).toLocaleString()} events</text>`:'<rect x="215" y="140" width="180" height="48" rx="8" fill="#fbf4e9" stroke="#e1cba9"/><text x="305" y="169" text-anchor="middle">No child nodes available</text>'}</svg>`;
}
const plotCard=(title,meta,body,extra='')=>`<div class="plot-card"><div class="plot-heading"><h3>${title}</h3><span>${meta}</span></div>${body}${extra}</div>`;
function renderEvidence(){
  const c=currentCase();
  $$('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===selectedTab);b.setAttribute('aria-selected',String(b.dataset.tab===selectedTab));});
  const legend=`<div class="legend"><span><i></i>${c.pair?'Unit 008':'Waveform family A'}</span>${['mixed','split','merge'].includes(c.kind)?`<span><i class="coral"></i>${c.pair?'Unit 021':'Waveform family B'}</span>`:''}<span><i class="gray"></i>Illustrative sample</span></div>`;
  let body='';
  if(selectedTab==='overview'){
    body=plotCard(c.pair?'Overlaid waveforms':'Waveform evidence','48 illustrative traces',waveform(c),legend)+
      `<div class="plot-row">${plotCard(c.pair?'Cross-correlogram':'Autocorrelogram','1 ms bins',correlogram(c,!!c.pair))}${plotCard('Amplitude over time','20 min',amplitude(c))}</div>`+
      `<div class="annotation"><span>◈</span><p>${c.kind==='mixed'?'Two waveform families are shown. Consider whether to inspect the children before discarding the entire cluster.':c.kind==='leaf'?'The proposed SPLIT is not executable. The split tree has no available child nodes.':c.pair?'Template similarity identifies a candidate pair. It does not establish that both clusters come from the same neuron.':'Use the evidence together. A single plot or metric does not determine the correct action.'}</p></div>`;
  }else if(selectedTab==='timing'){
    body=plotCard(c.pair?'Cross-correlogram':'Autocorrelogram','Lag window: ±25 ms',correlogram(c,!!c.pair,true),'<p class="plot-note">The shaded window highlights short lags. Counts and violations are illustrative.</p>')+
      plotCard('Amplitude over time','Sampled events',amplitude(c,true))+
      '<div class="annotation"><span>◈</span><p>Interpret timing evidence in context, including spike count and recording stability.</p></div>';
  }else if(selectedTab==='stability'){
    body=plotCard('Amplitude over time','20 min',amplitude(c,true))+
      `<div class="plot-row">${plotCard('Event positions','Synthetic locations',locationPlot(c))}<div class="plot-card"><div class="plot-heading"><h3>Evidence provenance</h3></div><div class="tree-note"><span class="pill amber">Additional reviewer evidence</span><p style="margin-top:12px">The location view was not part of this case’s scripted AI input. The feedback record notes that you opened it.</p><p style="margin-top:12px">This distinction helps discuss whether a correction comes from better reasoning or access to more evidence.</p></div></div></div>`;
  }else{
    body=plotCard(c.pair?'Proposed operation':'Current split tree','Read-only preview',treePlot(c))+
      `<div class="annotation"><span>◈</span><p>${c.pair?'The two clusters remain separate until a merge is confirmed in the real system. This prototype only records your review.':c.split?'Two children are available. SPLIT means inspect those children next; it does not label either child as a good unit.':'This node is a leaf. A further tree split is unavailable, even if the model recommends it.'}</p></div>`;
  }
  $('#evidence-content').innerHTML=body;
  $('.evidence-footer>span:first-child').innerHTML=`<span class="teal-dot"></span>${selectedTab==='stability'?'Includes additional reviewer evidence':'Model-visible evidence'}`;
}
function modal(title,body){$('#modal-title').textContent=title;$('#modal-content').innerHTML=body;if(!$('#info-modal').open)$('#info-modal').showModal();}
function showPacket(){
  const c=currentCase();
  modal('What the model was shown',`<div class="modal-prose"><p>This packet is <strong>scripted for the mockup</strong>. No model has analyzed these synthetic signals.</p></div><table class="packet-table"><tr><th>Snapshot</th><td class="mono">synthetic-${c.id}-v1</td></tr><tr><th>Object</th><td>${escapeHtml(c.title)} · ${c.channel}</td></tr><tr><th>Included views</th><td>Waveforms, ${c.pair?'cross':'auto'}correlogram, amplitude over time</td></tr><tr><th>Included context</th><td>Spike count, signal-to-noise ratio, ISI violations, recording duration, ${c.pair?'candidate pair':'split-tree availability'}</td></tr><tr><th>Not included</th><td>Event-location view, raw voltage recording, expert labels</td></tr><tr><th>Valid actions</th><td>${allowed(c).join(' · ')}</td></tr></table><div class="packet-code">Proposed action: ${c.ai}\nExecutable in this state: ${allowed(c).includes(c.ai)?'yes':'no — blocked by the review interface'}</div><p class="modal-prose" style="margin-top:16px">The same packet identifier travels with your feedback. Extra evidence viewed by the reviewer is recorded separately.</p>`);
}
function showUnsaved(nextId){
  modal('Leave this unsaved review?',`<div class="modal-prose"><p>Your action or note has not been saved. You can keep editing or discard this draft and open the other case.</p></div><div style="display:flex;gap:10px;margin-top:22px"><button class="button" id="keep-editing">Keep editing</button><button class="button primary" id="discard-draft">Discard draft & switch</button></div>`);
  $('#keep-editing').onclick=()=>$('#info-modal').close();
  $('#discard-draft').onclick=()=>{$('#info-modal').close();selectCase(nextId,true);};
}
function showAbout(){
  modal('A workspace for expert judgment',`<div class="modal-prose"><p><strong>Purpose.</strong> Explore the human review experience before choosing a research workflow.</p><p><strong>Try three cases.</strong> Correct Unit 042 from Discard to Split; compare Units 008 + 021; then inspect the unavailable Split on Unit 031.</p><p><strong>What works.</strong> Case navigation, evidence views, action constraints, reviewer notes, browser-local saving, undo, feedback history, and JSON export.</p><p><strong>What is illustrative.</strong> Every trace, number, AI recommendation, and proposed workflow revision. No real recordings, model calls, spike edits, or learning jobs are connected.</p><p><strong>Meeting questions.</strong> What evidence should appear first? What should require a reason? Which actions need a preview or a second review?</p></div>`);
}

$$('[data-view]').forEach(b=>b.onclick=()=>showView(b.dataset.view));
$('.brand').onclick=e=>{e.preventDefault();showView('review');};
$$('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;$$('[data-filter]').forEach(x=>x.classList.toggle('active',x===b));renderQueue();});
$$('[data-tab]').forEach(b=>b.onclick=()=>{selectedTab=b.dataset.tab;tabsViewed.add(selectedTab);renderEvidence();});
$('#case-search').addEventListener('input',renderQueue);
$('#reason-select').addEventListener('change',()=>{isDirty=true;$('#form-error').textContent='';});
$('#review-note').addEventListener('input',()=>{isDirty=true;$('#form-error').textContent='';});
$('#save-button').onclick=saveReview;
$('#defer-button').onclick=()=>{selectedAction='DEFER';isDirty=true;$('#reason-select').value='Evidence is missing or ambiguous';renderActions();$('#review-note').focus();toast('Defer selected. Add a note if helpful, then save.');};
$('#export-button').onclick=exportFeedback;$('#history-export').onclick=exportFeedback;
$('#history-link').onclick=()=>showView('history');
$('#packet-button').onclick=showPacket;$('#input-button').onclick=showPacket;
$('#tour-button').onclick=showAbout;
$('#close-modal').onclick=()=>$('#info-modal').close();
$('#info-modal').addEventListener('click',e=>{if(e.target===$('#info-modal')){const r=$('#info-modal').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('#info-modal').close();}});
$('#reset-button').onclick=()=>{
  modal('Reset the meeting demo?',`<div class="modal-prose"><p>This clears the reviews and event history stored by this demo in this browser. Export your feedback first if you want to keep it.</p></div><div style="display:flex;gap:10px;margin-top:22px"><button class="button" id="cancel-reset">Keep reviews</button><button class="button primary" id="confirm-reset">Reset demo</button></div>`);
  $('#cancel-reset').onclick=()=>$('#info-modal').close();
  $('#confirm-reset').onclick=()=>{state={reviews:{},events:[]};persist();filter='all';$('#case-search').value='';$$('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter==='all'));$('#info-modal').close();selectCase(CASES[0].id,true);showView('review');toast('Demo reset. All six cases are ready to review.');};
};
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key==='Enter'&&currentView==='review'&&!$('#info-modal').open){e.preventDefault();saveReview();}
});
window.addEventListener('beforeunload',e=>{if(isDirty){e.preventDefault();e.returnValue='';}});
selectCase(selectedId,true);
showView(location.hash.replace('#','')||'review');
if(!storageAvailable)toast('Browser storage unavailable. Export feedback before closing.');
