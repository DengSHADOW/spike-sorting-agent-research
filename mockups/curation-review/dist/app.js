'use strict';

// Demo cases remain separate from the server's verified real-data bundle.
// Neither mode invokes a model or changes spike assignments.
const DEMO_CASES = [
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
// Each case owns a separate synthetic recording. Never compare clusters across channels.
for(const c of DEMO_CASES){
  c.source=c.pair?c.pair[0]:c.id;
  c.dataset_id=`synthetic-session04-${c.channel}`;
  c.phase_id=c.pair?'phase2':'phase1';
  const number=Number(c.source.slice(2));
  const ids=c.pair?[c.source,c.pair[1],'U-034']:[c.source,`U-${String(number+1).padStart(3,'0')}`,`U-${String(number+2).padStart(3,'0')}`];
  c.units=ids.map((id,i)=>({id,cluster_id:Number(id.slice(2)),title:`Cluster ${id.slice(2)}`,
    channel:c.channel,seed:Number(id.slice(2)),kind:i===0?c.kind:i===1?'clean':'split',
    spikes:c.pair?[3030,1860,1020][i]:i===0?c.spikes:i===1?1247:25,
    split:i===0?(c.pair?true:c.split):true,color:['#287e70','#b46d4c','#6665aa'][i]}));
}
const Core=ReviewCore;
let CASES=DEMO_CASES,mode='demo',backend=null,realCatalog=null,revisions={},saving=false;
let bundleCatalog=[],activeBundle=null,sourceChoice='demo',profileName='';
const queueCatalogs=new Map(),expandedQueueFiles=new Set(),expandedQueueViews=new Set(),expandedQueueClusters=new Set();
let allReviewRecords={},queueFileSelection='all',queueMode='clusters';
let activeAgentId='Agent_1',referenceAgentEnabled=false;
try{referenceAgentEnabled=localStorage.getItem('spikeagent-reference-agent-v1')==='true';const previous=localStorage.getItem('spikeagent-study-agent-v1');if(['Agent_1','Agent_2'].includes(previous))activeAgentId=previous;}catch{}
const REAL_STORAGE_KEY='spikeagent-human-review-real-v3';
const realStorageKey=()=>`${REAL_STORAGE_KEY}:${reviewerId}:${activeBundle}`;
let reviewerId;
try{reviewerId=localStorage.getItem('spikeagent-reviewer-id');if(!reviewerId){reviewerId=crypto.randomUUID();localStorage.setItem('spikeagent-reviewer-id',reviewerId);}}
catch{reviewerId='temporary-'+Date.now()+'-'+Math.random().toString(36).slice(2);}
try{if(localStorage.getItem('spikeagent-queue-mode-v1:'+reviewerId)==='history')queueMode='history';}catch{}
const STORAGE_KEY = 'spikeagent-human-review-demo-v2';
const demoStorageKey=()=>STORAGE_KEY+':'+reviewerId;
let state = {reviews:{},events:[],reasons:[],annotator:''};
let storageAvailable = true;
let legacyFeedback=null;
try {
  const legacy=localStorage.getItem('spikeagent-human-review-demo-v1');
  if(legacy){try{legacyFeedback=JSON.parse(legacy);}catch{legacyFeedback={unparsed_storage:legacy};}}
  let owner=localStorage.getItem('spikeagent-legacy-demo-owner-v1');if(!owner){owner=reviewerId;localStorage.setItem('spikeagent-legacy-demo-owner-v1',owner);}
  const saved = JSON.parse(localStorage.getItem(demoStorageKey())||(owner===reviewerId?localStorage.getItem(STORAGE_KEY):null)||'null');
  if(saved && saved.reviews && typeof saved.reviews==='object' && Array.isArray(saved.events)){
    state.reviews=Object.fromEntries(Object.entries(saved.reviews).filter(([id,r])=>{
      const c=CASES.find(c=>c.id===id);return c&&Core.validStoredReview(c,r);
    }));
    state.events=saved.events;
    state.reasons=Array.isArray(saved.reasons)?saved.reasons.filter(r=>r&&typeof r.text==='string'):[];
    state.annotator=typeof saved.annotator==='string'?saved.annotator:'';
  }
} catch {storageAvailable=false;}
let demoState=state;
let selectedId=CASES[0].id, selectedUnitId=CASES[0].source, selectedTab='detail', filter='all', currentView='review';
let editingSaved=false;
let draft={},tabsViewed=new Set(),unitsViewed=new Set(),rationaleViewed=false,referenceViewed=false,toastTimeout=null,isDirty=false;
const jacobReferenceCache=new Map();
const $=s=>document.querySelector(s);
const $$=s=>Array.from(document.querySelectorAll(s));
const escapeHtml=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const isWorkingReview=()=>typeof WorkingReview!=='undefined'&&WorkingReview.active;
const currentCase=()=>isWorkingReview()?WorkingReview.case:CASES.find(c=>c.id===selectedId);
const allowed=c=>Core.allowed(c,c.id===selectedId?draft:{});
const currentUnit=()=>currentCase().units.find(u=>u.id===selectedUnitId);
const sourceUnit=c=>c.units.find(u=>u.id===c.source);
const unitLabel=(c,id)=>c.units.find(u=>u.id===id)?.title||'Choose a cluster';
const dateText=t=>new Date(t).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
let reasonMatches=[],reasonIndex=-1,reasonSequence=0,reasonTimer=null;

async function api(path,body){
  if(window.StaticReview)return window.StaticReview.api(path,body,reviewerId);
  const headers={'X-Reviewer-ID':reviewerId};
  if(body!==undefined){headers['Content-Type']='application/json';headers['X-CSRF-Token']=backend.csrf_token;}
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||`Server error ${response.status}`);
  return result;
}
function connectionWarning(message){$('#backend-warning').textContent=message;$('#backend-warning').hidden=!message;}
function mergeReasons(reasons){
  const map=new Map(state.reasons.map(r=>[Core.reasonKey(r.text),r]));
  for(const r of reasons)map.set(Core.reasonKey(r.text),r);
  state.reasons=[...map.values()];
}
function agentBundles(){return typeof AgentStudy==='undefined'?bundleCatalog:AgentStudy.visible(bundleCatalog,activeAgentId,referenceAgentEnabled);}
function queueBundles(){return AgentStudy.queue(bundleCatalog,activeAgentId,queueMode);}
function rememberQueueMode(){try{localStorage.setItem('spikeagent-queue-mode-v1:'+reviewerId,queueMode);}catch{}}
async function changeQueueMode(next){
  if(saving||!['clusters','history'].includes(next))return;
  const perform=async()=>{
    const previous=queueMode;queueMode=next;
    const choices=queueBundles(),target=choices.find(b=>b.dataset_id===realCatalog?.dataset_id)||choices[0];
    if(!target){queueMode=previous;renderQueue();toast('No imported data is available for this view.');return;}
    const oldFile=queueFileSelection;
    if(queueFileSelection!=='all')queueFileSelection=target.dataset_id;
    if(!await switchSource(choiceForBundle(target.id),true)){queueMode=previous;queueFileSelection=oldFile;}
    else{filter='all';$('#phase-filter').value='all';$('#case-search').value='';$$('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter==='all'));rememberQueueMode();renderQueue();}
  };
  $('#queue-mode').value=queueMode;
  if(isDirty)showUnsaved(null,perform);else await perform();
}
function agentName(c=currentCase()){return typeof AgentStudy==='undefined'?'Historical agent':AgentStudy.label(c.study_agent_id||c.agent_id);}
function renderAgentMenu(){
  if(typeof WorkingReview!=='undefined'&&WorkingReview.active){WorkingReview.renderAgentSelection();return;}
  const available=AgentStudy.available(bundleCatalog,referenceAgentEnabled);
  $('#agent-picker').hidden=!available.length;
  if(!['Agent_1','Agent_2'].includes(activeAgentId))activeAgentId='Agent_1';
  $('#agent-choice').innerHTML=available.map(id=>`<option value="${id}">${AgentStudy.label(id)}${id==='Agent_3'?' · live model':''}</option>`).join('');
  $('#agent-choice').value=activeAgentId;
  $('#study-options').hidden=!available.length;
  $('#enable-reference-agent').checked=referenceAgentEnabled;
  $('#reference-agent-status').textContent='Agent 3 opens a live-model window. Choose OpenAI-compatible or Anthropic there and connect a configured inference gateway.';
  $('#enable-reference-agent').disabled=false;
  $('#open-closed-agent').hidden=!referenceAgentEnabled;
}
async function changeAgent(next,completed=null,hideReference=false){
  if(next==='Agent_3'){$('#agent-choice').value=activeAgentId;ClosedAgent.open();return;}
  if(saving||next===activeAgentId&&mode==='real'&&currentCase().study_agent_id===next)return;
  const previous=activeAgentId,c=mode==='real'?currentCase():null,previousComparison=c?comparisonTarget(c):'';
  $('#agent-choice').value=previous;
  const perform=async()=>{
    const target=AgentStudy.choose(bundleCatalog,next,c?.dataset_id);
    if(!target){toast('No historical responses are available for this agent.');return;}
    const previousReference=referenceAgentEnabled;
    if(hideReference)referenceAgentEnabled=false;
    activeAgentId=next;renderDatasetOptions();$('#agent-choice').disabled=true;
    const success=await switchSource(choiceForBundle(target.id),true);
    if(success){
      const match=AgentStudy.matched(CASES,c?.comparison_case_id);
      if(match)selectCase(match.id,true);
      if(match&&previousComparison&&Core.candidates(match).some(u=>u.id===previousComparison))selectComparison(previousComparison,false);
      try{localStorage.setItem('spikeagent-study-agent-v1',next);}catch{}
      if(completed)completed();
    }else{activeAgentId=previous;referenceAgentEnabled=previousReference;renderDatasetOptions();}
    $('#agent-choice').disabled=false;
  };
  if(isDirty)showUnsaved(null,perform);else await perform();
}
async function connectBackend(){
  const requestedView=location.hash.slice(1);
  try{
    const cloudIdentity=window.SpikeCloud?await window.SpikeCloud.ready():null;
    if(cloudIdentity)reviewerId=cloudIdentity.uid;
    backend=await api('/api/status');
    if(cloudIdentity)window.SpikeCloud.mount(cloudIdentity,backend.csrf_token);
    await refreshDatasetMenu();$('#manage-inputs').disabled=false;
    const profile=await api('/api/profile');profileName=profile.annotator;
    if(!profile.updated_at){
      let previous=null;try{previous=JSON.parse(localStorage.getItem(REAL_STORAGE_KEY)||'null');}catch{}
      profileName=previous?.annotator||state.annotator||'';
      if(profileName)await api('/api/profile',{annotator:profileName});
    }
    state.annotator=profileName;$('#reviewer-name').value=profileName;
    renderReviewerBadge();
    if(state.reasons.length)await api('/api/reasons/import',{reasons:state.reasons.slice(0,500).map(r=>r.text)});
    mergeReasons((await api('/api/reasons')).reasons);renderReasons();
    if(new URLSearchParams(location.search).get('source')!=='demo'&&bundleCatalog.length){
      let previous,lastSource;try{previous=localStorage.getItem('spikeagent-active-bundle');lastSource=localStorage.getItem('spikeagent-last-source-v1:'+reviewerId);}catch{}
      if(lastSource==='demo'){await switchSource('demo',true);return;}
      const preferred=queueBundles().find(b=>b.id===previous)||queueBundles()[0]||agentBundles()[0];
      if(preferred)await switchSource(choiceForBundle(preferred.id));
    }else if(backend){const saved=await api('/api/reviews');allReviewRecords=Object.fromEntries(Object.entries(saved.reviews).map(([id,row])=>[id,row.review]));renderQueue();}
    const requestedCase=new URLSearchParams(location.search).get('case');if(requestedCase)await openActivityRecord({kind:'snapshot',case_id:requestedCase});else if(['history','revisions'].includes(requestedView))showView(requestedView);

  }catch(error){
    connectionWarning(`Real-data backend unavailable: ${error.message}. The synthetic demo is still available.`);
    $('#data-source').value=queueFileSelection;
  }
}
async function switchSource(next,force=false,keepCache=true){
  if(isWorkingReview())await WorkingReview.exit();
  const targetBundle=next==='demo'?null:next==='real'?agentBundles()[0]?.id:next.startsWith('bundle:')?next.slice(7):null;
  if(saving)return false;
  if(!force&&next===sourceChoice)return true;
  $('#data-source').disabled=true;
  try{
    if(next!=='demo'){
      if(!targetBundle)throw new Error('Choose an imported dataset first.');
      const catalog=await api('/api/cases?bundle_id='+encodeURIComponent(targetBundle));
      const saved=await api('/api/reviews');
      allReviewRecords=Object.fromEntries(Object.entries(saved.reviews).map(([id,row])=>[id,row.review]));
      queueCatalogs.set(targetBundle,catalog);
      const savedReasons=(await api('/api/reasons')).reasons;
      if(keepCache)persist();if(mode==='demo')demoState=state;
      realCatalog=catalog;activeBundle=targetBundle;
      let cachedReal=null;try{cachedReal=JSON.parse(localStorage.getItem(realStorageKey())||'null');}catch{}
      mode='real';queueMode=realCatalog.source_kind==='initial'?'clusters':'history';rememberQueueMode();CASES=realCatalog.cases;const ids=new Set(CASES.map(c=>c.id));
      revisions=Object.fromEntries(Object.entries(saved.revisions).filter(([id])=>ids.has(id)));
      state={reviews:Object.fromEntries(Object.entries(saved.reviews).filter(([id])=>ids.has(id)).map(([id,r])=>[id,r.review])),events:Array.isArray(cachedReal?.events)?cachedReal.events:[],reasons:demoState.reasons.slice(),annotator:profileName};
      mergeReasons(savedReasons);
    }else{persist();mode='demo';CASES=DEMO_CASES;state=demoState;state.annotator=profileName;revisions={};}
    if(queueFileSelection!=='all')queueFileSelection=next==='demo'?'demo':realCatalog.dataset_id;
    resetGeneralReasonLibrary();
    sourceChoice=next;try{localStorage.setItem('spikeagent-last-source-v1:'+reviewerId,next);}catch{}
    $('#data-source').value=queueFileSelection;
    $('#data-source').title=mode==='real'?`${realCatalog.dataset_id} · ${realCatalog.source_kind}`:'Synthetic demo';
    const manual=mode==='real'&&realCatalog.source_kind==='initial';
    $('#source-badge').textContent=mode==='real'?'RECORDING SNAPSHOT':'INTERACTIVE MOCKUP';$('#source-badge').title='Choose an action to continue directly; Confirm saves your choice or applies the preview.';
    $('#reason-help').textContent=backend&&!backend.static_share?'New reasons are saved to this server.':'New reasons stay in this browser. Export to transfer.';
    $('.queue-heading .count').textContent=CASES.length;
    $('.filters [data-filter="all"] span').textContent=CASES.length;
    $('.evidence-footer>span:last-child').textContent=mode==='real'?'Real recording · saved pre-decision state':'Synthetic preview · not recorded spikes';
    $('[data-tab="timing"]').textContent=mode==='real'?'ISI':'Timing';
    $('[data-tab="stability"]').textContent=mode==='real'?'Amplitude over time':'Drift & location';
    $('#reviewer-name').value=state.annotator;
    populateQueueFiles();renderQueue();
    isDirty=false;selectCase(lastSelectedCase()||CASES[0].id,true);showView('review');
    $$('.workspace>*').forEach(panel=>panel.scrollTop=0);connectionWarning('');
    if(mode==='real')try{localStorage.setItem('spikeagent-active-bundle',activeBundle);}catch{}
    return true;
  }catch(error){connectionWarning(`Could not load the selected source: ${error.message}`);$('#data-source').value=queueFileSelection;return false;}
  finally{$('#data-source').disabled=false;}
}

function persist(){
  try{localStorage.setItem(mode==='real'?realStorageKey():demoStorageKey(),JSON.stringify(state));}
  catch{storageAvailable=false;toast('Browser storage unavailable. Export feedback before closing.');}
  $('#storage-warning').hidden=storageAvailable;
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
  $('#page-name').textContent={review:'Expert review',history:'Feedback history',revisions:'Review revisions'}[view];
  history.replaceState(null,'',`#${view}`);
  if(view==='history')renderHistory();
  if(view==='revisions')renderRevisions();
  if(view==='review'){if(isWorkingReview())WorkingReview.render();syncPlotTypography();}
}
function queueEntries(){
  const entries=[];
  for(const bundle of queueBundles()){
    const catalog=queueCatalogs.get(bundle.id);if(!catalog)continue;
    for(const c of catalog.cases)entries.push({c,bundle_id:bundle.id,file:bundle.dataset_id,catalog});
  }
  if(mode==='demo'||queueFileSelection==='demo')for(const c of DEMO_CASES)entries.push({c,bundle_id:'demo',file:'demo',catalog:null});
  return entries;
}
function queueFile(c){
  const entry=queueEntries().find(row=>row.c.id===c.id),path=c.mat_path||entry?.catalog?.provenance?.mat_path;
  return {key:c.synthetic===false?c.dataset_id:'demo',name:c.synthetic===false?(path?path.split(/[\\/]/).pop():c.dataset_id.split('_').pop()+'_spikes.mat'):'Synthetic demo',dataset:c.dataset_id,path};
}
function populateQueueFiles(){if(typeof renderDatasetOptions==='function')renderDatasetOptions();else $('#data-source').innerHTML='<option value="all">All files</option><option value="demo">Synthetic demo</option>';}
function queueReview(c){return c.synthetic===false?allReviewRecords[c.id]||null:demoState.reviews[c.id]||null;}
function queueAction(c){
  const saved=queueReview(c);
  if(c.id===selectedId&&isDirty&&(draft.agree!==null||c.review_mode==='manual')&&draft.action)return {action:draft.action,draft:true};
  return saved?{action:saved.action,draft:false}:null;
}
function queueScope(){
  const query=$('#case-search').value.toLowerCase().trim(),phase=$('#phase-filter').value;
  return queueEntries().filter(({c,file,catalog})=>(queueFileSelection==='all'||file===queueFileSelection)&&(phase==='all'||c.phase_id===phase)&&
    `${c.id} ${c.channel} ${c.title} ${c.brief} ${file} ${catalog?.provenance?.mat_path||''}`.toLowerCase().includes(query)).map(row=>row.c);
}
function resetQueueFilters(){
  filter='all';queueFileSelection='all';$('#data-source').value='all';$('#case-search').value='';$('#phase-filter').value='all';
  $$('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter==='all'));renderQueue();
}
async function openQueueCase(id,bundle){
  if(isWorkingReview())await WorkingReview.exit();
  if(saving)return;
  if((bundle==='demo'&&mode==='demo')||(mode==='real'&&bundle===activeBundle)){selectCase(id);return;}
  const perform=async()=>{if(await switchSource(bundle==='demo'?'demo':choiceForBundle(bundle)))selectCase(id,true);};
  if(isDirty)showUnsaved(null,perform);else await perform();
}
function renderQueue(){
  if(isWorkingReview())return WorkingReview.renderQueue();
  $('#queue-mode').value=queueMode;
  $('#queue-mode').disabled=saving||!backend;
  $('#queue-mode-help').textContent=mode==='demo'?'Synthetic demo · separate from real-data progress.':queueMode==='clusters'?'Clusters at recording start.':'Saved Agent decisions · original grouping.';
  const scope=queueScope(),ids=new Set(scope.map(c=>c.id)),entries=queueEntries().filter(row=>ids.has(row.c.id));
  const visible=c=>filter==='all'||(filter==='reviewed'?!!queueReview(c):!queueReview(c));
  const groups=new Map();
  for(const entry of entries){if(!groups.has(entry.file))groups.set(entry.file,[]);groups.get(entry.file).push(entry);}
  function caseButton({c,bundle_id},grouped=false){
    const saved=queueReview(c),action=queueAction(c);
    return `<button class="case-item queue-thumb-card ${c.id===selectedId?'active':''}" data-case="${escapeHtml(c.id)}" data-bundle="${escapeHtml(bundle_id)}" title="${escapeHtml(sourceUnit(c).title)} · ${c.state_sha256?.slice(0,10)||'demo'} · Click to inspect; Shift-click to compare within the same state" aria-pressed="${c.id===selectedId}">${queueThumbnailMarkup(c,grouped)}<span class="queue-item-meta"><span class="queue-action-tag ${action?.draft?'draft-tag':''}" data-choice-for="${escapeHtml(c.id)}" ${action?'':'hidden'}>${action?escapeHtml(action.action)+(action.draft?' · Draft':''):''}</span>${saved?`<span class="queue-review-label ${saved.action==='DEFER'?'deferred':''}">${saved.action==='DEFER'?'Deferred':'✓ Reviewed'}</span>`:''}</span></button>`;
  }
  function clusterEntries(items){
    const clusters=new Map();for(const row of items){const key=sourceUnit(row.c).cluster_id;if(!clusters.has(key))clusters.set(key,[]);clusters.get(key).push(row);}
    return queueSortEntries([...clusters.values()].map(rows=>rows[0])).map(first=>{const rows=queueSortEntries(clusters.get(sourceUnit(first.c).cluster_id));if(rows.length===1)return caseButton(rows[0]);const key=rows[0].bundle_id+':'+sourceUnit(rows[0].c).cluster_id;
      return `<details class="queue-cluster-group" data-queue-cluster="${escapeHtml(key)}" ${expandedQueueClusters.has(key)?'open':''}><summary><strong>${escapeHtml(sourceUnit(rows[0].c).title)}</strong><small>${rows.length} states</small></summary><div class="queue-cluster-grid">${rows.map(row=>caseButton(row,true)).join('')}</div></details>`;
    }).join('');
  }
  const activeQueueFile=queueFile(currentCase()||CASES[0]).key;
  $('#case-list').innerHTML=[...groups].sort(([a],[b])=>Number(b===activeQueueFile)-Number(a===activeQueueFile)).map(([file,rows])=>{
    const shown=rows.filter(row=>visible(row.c));if(!shown.length)return '';
    const reviewed=rows.filter(row=>queueReview(row.c)).length,views=new Map();
    for(const row of shown){if(!views.has(row.bundle_id))views.set(row.bundle_id,[]);views.get(row.bundle_id).push(row);}
    const percentage=Math.round(reviewed/(rows.length||1)*100);
    const counts=`<div class="file-progress" role="progressbar" aria-label="${escapeHtml(queueFile(rows[0].c).name)} ${queueMode==='clusters'?'cluster':'historical case'} review progress" aria-valuemin="0" aria-valuemax="${rows.length}" aria-valuenow="${reviewed}" title="${reviewed} of ${rows.length} ${queueMode==='clusters'?'clusters':'historical cases'} saved"><div class="file-progress-track"><span style="width:${percentage}%"></span></div><small>${reviewed} / ${rows.length} · ${percentage}%</small></div>`;
    const content=views.size===1&&rows.every(row=>row.bundle_id===rows[0].bundle_id)?'<div class="queue-cluster-grid">'+clusterEntries(shown)+'</div>':[...views].map(([id,items])=>`<details class="queue-view-group" data-queue-view="${escapeHtml(id)}" ${expandedQueueViews.has(id)?'open':''}><summary>${items[0].catalog?.source_kind==='initial'?'Recording start · snapshot':agentName(items[0].c)+' decisions'}</summary><div class="queue-cluster-grid">${clusterEntries(items)}</div></details>`).join('');
    return `<details class="queue-file-group" data-queue-file="${escapeHtml(file)}" ${expandedQueueFiles.has(file)?'open':''}><summary title="${escapeHtml(file)}"><strong>${escapeHtml(queueFile(rows[0].c).name)}</strong>${counts}</summary>${content}</details>`;
  }).join('')||'<p class="no-results">No clusters match these filters.</p>';
  attachQueueGallery();
  for(const [attribute,storage] of [['queueFile',expandedQueueFiles],['queueView',expandedQueueViews],['queueCluster',expandedQueueClusters]])$$('[data-'+attribute.replace(/[A-Z]/g,v=>'-'+v.toLowerCase())+']').forEach(details=>details.ontoggle=()=>{if(details.open){storage.add(details.dataset[attribute]);if(attribute==='queueFile')loadExpandedQueueQuality(details.dataset[attribute]);}else storage.delete(details.dataset[attribute]);});
  const reviewed=scope.filter(c=>queueReview(c)).length,total=queueEntries().length;
  $('.queue-heading .count').textContent=scope.filter(visible).length;$('.filters [data-filter="all"] span').textContent=scope.length;
  $('#reviewed-count').textContent=reviewed;$('#pending-count').textContent=scope.length-reviewed;
  $('#queue-result-summary').textContent=`${scope.filter(visible).length} / ${total} ${queueMode==='clusters'?'clusters':'historical cases'}`;
  $('#clear-queue-filters').hidden=!(queueFileSelection!=='all'||$('#phase-filter').value!=='all'||$('#case-search').value||filter!=='all');

}
const DRAFT_PREFIX='spikeagent-review-drafts-v1:';
function selectionKey(){return 'spikeagent-selected-case-v1:'+reviewerId+':'+(mode==='real'?activeBundle:'demo');}
function lastSelectedCase(){try{const id=localStorage.getItem(selectionKey());return CASES.some(c=>c.id===id)?id:null;}catch{return null;}}
function readDrafts(){try{const rows=JSON.parse(localStorage.getItem(DRAFT_PREFIX+reviewerId)||'{}');return rows&&typeof rows==='object'&&!Array.isArray(rows)?rows:{};}catch{return {};}}
function clearLocalDraft(id){try{const rows=readDrafts();delete rows[id];localStorage.setItem(DRAFT_PREFIX+reviewerId,JSON.stringify(rows));}catch{}}
function cacheLocalDraft(){
  if(typeof WorkingReview!=='undefined'&&WorkingReview.active)return WorkingReview.cacheDraft();
  if(!isDirty)return;
  try{
    const c=currentCase(),rows=readDrafts();
    rows[c.id]={case_id:c.id,dataset_id:c.dataset_id,state_sha256:c.state_sha256||null,run_id:c.run_id||null,agent_id:c.agent_id||null,
      base_revision:revisions[c.id]||0,base_timestamp:state.reviews[c.id]?.timestamp||null,
      draft:{...draft,reason:$('#reason-select').value,note:$('#review-note').value,positive:$('#positive-comment').value},comparison_target:comparisonTarget(c),updated_at:new Date().toISOString()};
    localStorage.setItem(DRAFT_PREFIX+reviewerId,JSON.stringify(rows));
  }catch{storageAvailable=false;$('#storage-warning').hidden=false;}
}
function restoreLocalDraft(c){
  const saved=readDrafts()[c.id],d=saved?.draft;if(!saved||!d)return false;
  if(saved.dataset_id!==c.dataset_id||saved.state_sha256!==(c.state_sha256||null)||saved.run_id!==(c.run_id||null)||saved.agent_id!==(c.agent_id||null)||saved.base_revision!==(revisions[c.id]||0)||saved.base_timestamp!==(state.reviews[c.id]?.timestamp||null))return false;
  if(![true,false,null].includes(d.agree)||d.action!==null&&!ACTIONS[d.action]||typeof d.target!=='string'||d.target&&!Core.candidates(c).some(u=>u.id===d.target))return false;
  if(!['reason','note','positive','missing_note'].every(key=>typeof d[key]==='string'&&d[key].length<=10000)||!Array.isArray(d.disagreement_types)||d.disagreement_types.some(t=>!['rationale','evidence','action','target'].includes(t)))return false;
  if(![null,'high','medium','low'].includes(d.confidence)||!['unspecified','sufficient','insufficient'].includes(d.sufficiency)||!Array.isArray(d.missing)||d.missing.some(v=>!Core.MISSING_EVIDENCE.includes(v)))return false;
  draft={...d};isDirty=true;
  if(saved.comparison_target===''||Core.candidates(c).some(u=>u.id===saved.comparison_target))comparisonChoices.set(c.id,saved.comparison_target);
  return true;
}
function selectCase(id,force=false){
  if(saving)return;
  if(!force&&isDirty&&id!==selectedId){showUnsaved(id);return;}
  if(!CASES.some(c=>c.id===id))return;
  const openedCase=CASES.find(item=>item.id===id);expandedQueueFiles.add(openedCase.synthetic===false?openedCase.dataset_id:'demo');expandedQueueViews.add(mode==='real'?activeBundle:'demo');
  selectedId=id;selectedTab='detail';tabsViewed=new Set();unitsViewed=new Set();rationaleViewed=false;isDirty=false;editingSaved=false;
  referenceViewed=false;$('#jacob-reference-panel').open=false;
  resetAnalysisExposure(id);
  const c=currentCase(),review=state.reviews[id];
  selectedUnitId=c.source;
  draft={agree:review?review.expert_agree:null,action:review?review.action:c.review_mode==='manual'?'KEEP':Core.allowed(c).includes(c.ai)?c.ai:null,
    target:review&&review.expert_merge_target!==null?c.units.find(u=>u.cluster_id===review.expert_merge_target)?.id:c.pair?.[1]||'',
    reason:review?.expert_reason||'',note:review?.expert_note||'',positive:review?.expert_positive_comment||'',
    confidence:review?.expert_confidence??null,sufficiency:review?.evidence_sufficiency||'unspecified',
    missing:review?.missing_evidence||[],missing_note:review?.missing_evidence_note||'',
    disagreement_types:(review?.disagreement_types||[]).filter(t=>['rationale','evidence'].includes(t))};
  if(review){const targetId=c.units.find(u=>u.cluster_id===(review.judgment_pair?.[1]??review.expert_merge_target))?.id;comparisonChoices.set(c.id,review.judgment_context==='single'?'':targetId||c.pair?.[1]||'');}
  draft.scope=review?.judgment_context||(comparisonTarget(c)?'pair':'single');draft.target=comparisonTarget(c);
  if(restoreLocalDraft(c))editingSaved=Boolean(review);
  if(draft.scope===undefined)draft.scope=comparisonTarget(c)?'pair':'single';
  expandedQueueClusters.add((mode==='real'?activeBundle:'demo')+':'+sourceUnit(c).cluster_id);try{localStorage.setItem(selectionKey(),c.id);}catch{}
  $('#reason-select').value=draft.reason;$('#review-note').value=draft.note;$('#positive-comment').value=draft.positive;
  $('#disagree-rationale').checked=draft.disagreement_types.includes('rationale');
  $('#disagree-evidence').checked=draft.disagreement_types.includes('evidence');
  $('#form-error').textContent='';
  hideReasonMatches();
  renderReasons();renderQueue();recordEvidence();renderCase();
}
function renderCase(){
  if(isWorkingReview())return WorkingReview.renderCase();
  const c=currentCase(),u=currentUnit(),target=comparisonTarget(c),other=c.units.find(unit=>unit.id===target);
  const savedWave=savedWaveformFor(c,u);
  const traceCount=c.synthetic===false?(savedWave?Math.min(u.spikes,500):u.waveform?.traces.length??Math.min(u.spikes,24)):24;
  $('#case-channel').textContent=c.synthetic===false?`${c.dataset_id} / saved state ${c.state_sha256.slice(0,10)}`:`${c.channel} / DEMO CASE`;
  $('#case-title').textContent=comparisonName(c);
  $('#case-badges').innerHTML=`<span class="pill ${target?'teal':''}" title="${target?'This is a reviewer comparison, not an executed merge or a change to the recorded agent pair.':c.pair?'The recorded agent decision concerns two clusters; select Compare with to inspect a candidate pair.':'This decision concerns one cluster, which can contain multiple neurons or noise.'}">${target?'Comparing two clusters':c.pair?'Recorded two-cluster decision':'One-cluster decision'}</span>${state.reviews[c.id]?`<span class="pill teal" title="Saved judgment for this case. The comparison preview is not a new saved review.">${state.reviews[c.id].action==='DEFER'?'Deferred':'Case reviewed'}</span>`:!c.study_id?`<span class="pill ${c.risk==='High impact'?'amber':'teal'}">${escapeHtml(c.risk)}</span>`:''}`;
  const position=CASES.findIndex(item=>item.id===c.id)+1;
  $('#case-position').textContent=c.channel+' · '+position+' / '+CASES.length;
  $('#case-position').setAttribute('aria-label',`${c.channel}, case ${position} of ${CASES.length}`);
  const stats=[u,...(other?[other]:[])];
  $('#metrics').innerHTML='<div class="metrics-table" role="table" aria-label="Cluster statistics"><div class="metric-table-header" role="row">'+['Cluster','Spikes','Detail','Active','Evidence'].map(text=>`<span role="columnheader">${text}</span>`).join('')+'</div>'+stats.map(unit=>`<div class="metric-table-row" role="row" data-stat-cluster="${escapeHtml(unit.id)}"><strong role="cell">${escapeHtml(unit.title)}</strong><span role="cell">${unit.spikes.toLocaleString()}</span><span role="cell" title="${c.synthetic===false?'All individual member waveforms in Cluster detail':'Number of illustrative detail traces'}">${c.synthetic===false?waveformCoverageLabel(unit):target?'Mean':traceCount+' traces'}</span><span role="cell" title="Active clusters in this recording and checkpoint">${c.units.length}</span><span role="cell">${c.synthetic===false?'MAT':'Demo'}</span></div>`).join('')+'</div>';
  $('#agent-card-title').textContent=c.review_mode==='manual'?'Agent':agentName(c);
  $('#suggestion').innerHTML=c.review_mode==='manual'?'<div class="suggestion-card"><div class="suggestion-label">MANUAL REVIEW</div><p>No saved Agent suggestion matches this recording-start state. Review this cluster using its waveform and timing evidence.</p></div>':`<div class="suggestion-card"><div class="suggestion-label">${c.synthetic===false?escapeHtml(agentName(c))+' · SAVED RESPONSE':'Agent 1 · SCRIPTED DEMO'}</div><div class="suggestion-action" data-agent-action="${c.ai}"><strong>${escapeHtml(ACTIONS[c.ai].label.toUpperCase())}</strong><span class="agent-action-target">${escapeHtml(sourceUnit(c).title)} ${viewingTag(c.source)}${c.pair?' + '+escapeHtml(unitLabel(c,c.pair[1]))+' '+viewingTag(c.pair[1]):''}</span></div>${c.rationale_available===false?'<p class="missing-rationale">No rationale was retained. Review the action and evidence; no explanation is generated here.</p>':`<p class="rationale" id="rationale-text">${escapeHtml(c.rationale)}</p>`}</div>`;
  recordVisibleRationale();
  $('#validity-warning').innerHTML=!sourceUnit(c).split&&c.ai==='SPLIT'?(c.synthetic===false?'<div class="warning">No saved tree split is available. Use Split to select spikes in PCA.</div>':'<div class="warning"><strong>SPLIT is unavailable.</strong><br>This is a leaf node. Select a valid action or defer the case.</div>'):'';
  renderActions();renderEvidence();
}
function recordVisibleRationale(){
  const c=currentCase();
  if(rationaleViewed||!$('#agent-card').open||!$('#rationale-text')||!c.rationale||c.rationale_available===false||c.review_mode==='manual')return;
  rationaleViewed=true;state.events.push({type:'ai_rationale_viewed',case_id:c.id,timestamp:new Date().toISOString(),synthetic:c.synthetic!==false});persist();
}
function renderJacobReference(){
  if(isWorkingReview())return;
  const c=currentCase(),panel=$('#jacob-reference-panel'),content=$('#jacob-reference-content');panel.hidden=c.synthetic!==false;
  if(panel.hidden)return;
  const dataset=c.dataset_id;
  if(!jacobReferenceCache.has(dataset)){
    const entry={rows:null,error:null};jacobReferenceCache.set(dataset,entry);
    api('/api/curation/reference?dataset_id='+encodeURIComponent(dataset)).then(result=>{entry.rows=result.references;if(currentCase().dataset_id===dataset)renderJacobReference();}).catch(error=>{entry.error=error.message;if(currentCase().dataset_id===dataset)renderJacobReference();});
  }
  const entry=jacobReferenceCache.get(dataset),source=sourceUnit(c).cluster_id,target=c.pair?c.units.find(u=>u.id===c.pair[1]).cluster_id:null;
  const matched=(entry.rows||[]).filter(r=>r.review_state_sha256===c.state_sha256&&r.phase===c.phase_id&&r.source===source&&(r.target??null)===target);
  content.innerHTML=entry.error?'<p>Reference unavailable: '+escapeHtml(entry.error)+'</p>':!entry.rows?'<p>Loading verified historical references…</p>':matched.length?matched.map(r=>`<p><strong>${escapeHtml(r.action)}</strong> · Cluster ${r.source}${r.target!=null?' + Cluster '+r.target:''} · ${escapeHtml(r.reference_id)}</p><p>${escapeHtml(r.reason||'No text reason was recorded.')}</p><small>Jacob reference for this exact state and object. This is not the Agent suggestion or your saved judgment.</small>`).join(''):'<p>No exact historical annotation matches this state/object. Missing KEEP and NOT_MERGE labels are not inferred.</p>';
  const expose=()=>{if(panel.open&&matched.length&&!referenceViewed){referenceViewed=true;state.events.push({type:'jacob_reference_viewed',case_id:c.id,timestamp:new Date().toISOString()});persist();}};
  panel.ontoggle=expose;expose();
}
function canEditCurrentReview(){return !state.reviews[selectedId]||editingSaved;}
function renderActions(){
  if(isWorkingReview())return WorkingReview.renderActions();
  const c=currentCase(),actions=Core.PHASE_ACTIONS[draft.scope==='pair'?'phase2':'phase1']||[];
  const manual=c.review_mode==='manual';
  $('#agent-choice').disabled=saving;
  $('.agreement-fieldset legend').textContent=c.rationale_available===false?'Do you agree with this action?':'Do you agree with the action and rationale?';
  $('#disagree-rationale').closest('label').hidden=c.rationale_available===false;
  $('.agreement-fieldset').hidden=manual;$('.disagreement-details').hidden=manual;
  $('#agree-button').disabled=!allowed(c).includes(c.ai)||!Core.agreementMatches(c,draft);
  for(const [id,value] of [['agree-button',true],['disagree-button',false]]){
    $('#'+id).classList.toggle('selected',draft.agree===value);$('#'+id).setAttribute('aria-pressed',String(draft.agree===value));
  }
  $('#correction-panel .reason-section').hidden=false;$('#correction-panel').hidden=!manual&&draft.agree!==false;$('#positive-panel').hidden=manual?draft.action!=='KEEP':draft.agree!==true;
  $('#action-grid').innerHTML=actions.map(a=>`<button type="button" class="action-option ${draft.action===a?'selected':''}" data-action="${a}" aria-pressed="${draft.action===a}" ${!allowed(c).includes(a)&&!(c.synthetic===false&&a==='SPLIT')?'disabled title="No child clusters are available"':''}><span class="action-icon" aria-hidden="true">${ACTIONS[a].icon}</span><span>${ACTIONS[a].label}</span><span class="radio" aria-hidden="true"></span></button>`).join('');
  $$('[data-action]').forEach(b=>b.onclick=()=>{draft.action=b.dataset.action;isDirty=true;$('#form-error').textContent='';if(c.synthetic===false&&['SPLIT','MERGE','DISCARD'].includes(draft.action)){if(draft.action==='MERGE'){draft.target=comparisonTarget(c)||draft.target||'';if(!draft.target){$('#form-error').textContent='Select a comparison cluster first.';return;}}renderActions();cacheLocalDraft();WorkingReview.openOperation(draft.action).catch(error=>{$('#form-error').textContent=error.message;});return;}if(draft.action==='DEFER')draft.sufficiency='insufficient';if(draft.action==='NOT_MERGE'){draft.target=comparisonTarget(c);renderActions();renderEvidence();}else if(draft.action==='MERGE'){const target=comparisonTarget(c)||draft.target||'';draft.target=target;selectComparison(target);}else{renderActions();renderEvidence();}});
  $('#merge-controls').hidden=draft.action!=='MERGE';
  $('#merge-source').textContent=`Source: ${sourceUnit(c).title} · n = ${sourceUnit(c).spikes.toLocaleString()}`;
  $('#merge-target').innerHTML='<option value="">Choose a target cluster…</option>'+Core.candidates(c).map(u=>`<option value="${u.id}">${u.title}</option>`).join('');
  $('#merge-target').value=draft.target||'';
  const operationSelected=c.synthetic===false&&['SPLIT','MERGE','DISCARD'].includes(draft.action),realHints={KEEP:'Confirm to keep this cluster.',NOT_MERGE:'Confirm to keep these clusters separate.',DEFER:'Confirm to leave this judgment pending.',SPLIT:'Preview the two resulting clusters before confirming.',MERGE:'Preview the combined cluster before confirming.',DISCARD:'Confirm before removing the selected source.'};
  $('#action-context').innerHTML=(!operationSelected&&!allowed(c).includes(draft.action)&&draft.action?'<p class="phase-action-warning">Choose an action for the current single-cluster or pair view. Clear Compare with to return to single-cluster actions.</p>':'')+`<p class="action-hint">${draft.action?(c.synthetic===false&&realHints[draft.action]||ACTIONS[draft.action].hint):''}</p>`;

  const pair=draft.action==='MERGE'?` → ${escapeHtml(unitLabel(c,draft.target))}`:'';
  $('#decision-preview').innerHTML=manual?`Manual proposal: ${ACTIONS[draft.action]?.label||'Choose an action'}${pair}. No spike assignments change.`:draft.agree===null?'Choose Agree or Disagree to record your judgment.':`<strong>${draft.agree?'Agree':'Disagree'}:</strong> ${draft.action?ACTIONS[draft.action].label:'Choose an action'}${pair}. ${draft.action==='DEFER'?'Deferred is a review status, not a backend action.':'Expert proposal only; no spike assignments change.'}`;
  if(operationSelected)$('#decision-preview').textContent='Load spike data to preview; Confirm applies the change.';$('#save-button').disabled=operationSelected||saving||(!manual&&draft.agree===null)||!allowed(c).includes(draft.action);
  $('.reason-section>label[for="general-reason-choice"] span').textContent=manual&&draft.action==='KEEP'?'optional for KEEP':'reason or note required';
  $('#save-button').textContent=operationSelected?'Preview pending':c.synthetic===false&&draft.action?'Confirm '+ACTIONS[draft.action].label.toLowerCase():state.reviews[c.id]?'Update review':'Save review';const cancellable=c.synthetic===false&&canEditCurrentReview()&&!operationSelected&&!!draft.action&&(manual||draft.agree!==null);$('#inline-cancel-preview').hidden=!cancellable;$('#inline-cancel-preview').textContent='Cancel choice';
  renderJudgmentDetails();renderChoiceSummaries();
}
function choiceMarkup(choice,{status,source,title='User choice',note='',viewing=false,targetViewing=false}={}){
  const labels=[];
  if(choice.agree===true)labels.push('Agree');if(choice.agree===false)labels.push('Disagree');
  if(choice.confidence)labels.push(choice.confidence[0].toUpperCase()+choice.confidence.slice(1)+' confidence');
  if(choice.sufficiency==='sufficient')labels.push('Sufficient evidence');
  if(choice.sufficiency==='insufficient')labels.push('More evidence needed');
  for(const value of choice.disagreement_types||[])if(['rationale','evidence'].includes(value))labels.push(value==='rationale'?'Rationale correction':'Evidence interpretation');
  for(const value of choice.missing||[])labels.push('Missing: '+value.replaceAll('_',' '));
  return `<div class="choice-card-heading"><h3>${escapeHtml(title)}</h3><span class="choice-state ${status==='Draft'?'draft-state':''}">${escapeHtml(status||'Saved')}</span></div>${source?`<p class="choice-source">${escapeHtml(source)} ${viewing?'<span class="cluster-tag viewing-tag">Viewing</span>':''}</p>`:''}${choice.action?`<div class="choice-action"><strong>${escapeHtml(ACTIONS[choice.action]?.label.toUpperCase()||choice.action)}</strong>${choice.target!=null?`<span>Target: Cluster ${escapeHtml(choice.target)} ${targetViewing?'<span class="cluster-tag viewing-tag">Viewing</span>':''}</span>`:''}</div>`:'<p class="choice-empty">No action selected yet.</p>'}${choice.reason?`<p class="choice-text"><span>Reason</span>${escapeHtml(choice.reason)}</p>`:''}${choice.note?`<p class="choice-text"><span>Note</span>${escapeHtml(choice.note)}</p>`:''}${choice.positive?`<p class="choice-text"><span>Comment</span>${escapeHtml(choice.positive)}</p>`:''}<div class="choice-tags">${labels.map(label=>`<span>${escapeHtml(label)}</span>`).join('')}</div>${choice.missing_note?`<p class="choice-text"><span>Evidence needed</span>${escapeHtml(choice.missing_note)}</p>`:''}${note?`<p class="choice-scope-note">${escapeHtml(note)}</p>`:''}`;
}
function savedChoice(review){return {action:review.action,target:review.judgment_pair?.[1]??review.expert_merge_target,agree:review.expert_agree,reason:review.expert_reason,note:review.expert_note,positive:review.expert_positive_comment,confidence:review.expert_confidence,sufficiency:review.evidence_sufficiency,missing:review.missing_evidence,missing_note:review.missing_evidence_note,disagreement_types:review.disagreement_types};}
function renderChoiceSummaries(){
  if(isWorkingReview())return WorkingReview.renderChoice();
  cacheLocalDraft();
  const c=currentCase(),saved=state.reviews[c.id],manual=c.review_mode==='manual',comparisonPanel=$('#comparison-choice-card'),referencePanel=$('#jacob-reference-panel');
  const selected=draft.agree!==null||manual&&isDirty;
  const hasDraft=isDirty&&(selected||draft.confidence||$('#reason-select').value||$('#review-note').value||draft.sufficiency!=='unspecified');
  if(saved&&!isDirty)$('#user-choice-card').innerHTML=choiceMarkup(savedChoice(saved),{status:'Saved',source:sourceUnit(c).title,note:c.synthetic===false?'Judgment recorded; cluster assignments unchanged.':'',viewing:viewingIds(c).includes(c.source),targetViewing:viewingIds(c).some(id=>c.units.find(u=>u.id===id)?.cluster_id===saved.expert_merge_target)});
  else if(selected||hasDraft){
    const choice={...draft,action:selected?draft.action:null,target:selected&&draft.scope==='pair'?c.units.find(u=>u.id===draft.target)?.cluster_id:null,reason:draft.agree===true?'':$('#reason-select').value,note:draft.agree===true?'':$('#review-note').value,positive:draft.agree===true||manual?$('#positive-comment').value:'',missing_note:draft.missing_note};
    $('#user-choice-card').innerHTML=choiceMarkup(choice,{status:'Draft',source:sourceUnit(c).title,viewing:viewingIds(c).includes(c.source),targetViewing:viewingIds(c).includes(draft.target)});
  }else $('#user-choice-card').innerHTML=`<div class="choice-card-heading"><h3>User choice</h3></div><p class="choice-source">${escapeHtml(sourceUnit(c).title)} ${viewingTag(c.source)}</p><p class="choice-empty">No choice saved yet.</p>`;
  const target=comparisonTarget(c),panel=comparisonPanel;
  $('#user-choice-card').append(panel);panel.hidden=!target;
  $('#user-choice-card').append(referencePanel);renderJacobReference();
  if(target){
    const match=Core.findClusterReview(Object.values(mode==='real'?allReviewRecords:state.reviews),c,target);
    const name=unitLabel(c,target);
    if(match){panel.innerHTML=choiceMarkup(savedChoice(match.review),{title:name+' · user choice',status:match.exact_state?'Saved':'Other state',source:name,viewing:true,note:match.exact_state?'':'Different checkpoint: this is not a judgment for the current state.'});panel.dataset.reviewCase=match.review.case_id;}
    else{panel.innerHTML=`<p class="choice-empty">${escapeHtml(name)} ${viewingTag(target)}: no saved choice for this reviewer and agent.</p>`;delete panel.dataset.reviewCase;}
  }
  updateReviewEditorMode();
  const chip=$$('[data-choice-for]').find(node=>node.dataset.choiceFor===c.id),action=queueAction(c);
  if(chip){chip.hidden=!action;chip.textContent=action?action.action+(action.draft?' · Draft':''):'';chip.classList.toggle('draft-tag',Boolean(action?.draft));}
}
function updateReviewEditorMode(){
  const saved=state.reviews[selectedId],editable=canEditCurrentReview();
  $('#review-editor').hidden=!editable;
  if(saved){
    const heading=$('#user-choice-card .choice-card-heading');
    const button=document.createElement('button');button.type='button';button.className='button small edit-choice-button';
    button.id=editable?'cancel-edit-choice':'edit-user-choice';button.textContent=editable?'Cancel edit':'Edit';
    button.onclick=()=>{if(editable){clearLocalDraft(selectedId);isDirty=false;editingSaved=false;selectCase(selectedId,true);}else{editingSaved=true;const c=currentCase();selectComparison(draft.scope==='pair'?draft.target:'',false);}};
    heading.append(button);
  }
}
function renderJudgmentDetails(){
  $('#judgment-confidence').value=draft.confidence||'';
  $('#evidence-sufficiency').value=draft.sufficiency||'unspecified';
  $('#missing-evidence-fields').hidden=draft.sufficiency!=='insufficient';
  $$('.missing-evidence-options input').forEach(input=>input.checked=(draft.missing||[]).includes(input.value));
  $('#missing-evidence-note').value=draft.missing_note||'';
}
function reasonSourceLabel(reason){
  return reason.sources?.some(s=>s.kind==='jacob')?'Jacob original':reason.sources?.some(s=>s.kind==='reviewer')?'Reviewer-added':reason.sources?.length?'Imported reason':'Saved reason';
}
function reasonClusterMarkup(reason){
  const origins=Core.reasonClusters(reason,mode==='real'?Object.values(allReviewRecords):Object.values(state.reviews));
  const labels=origins.map(row=>`${row.dataset_id?.match(/CH\d+$/)?.[0]||row.dataset_id||'Source'}: Cluster ${row.cluster_ids.join(' + ')}`);
  return `<span class="reason-cluster-origin" title="${escapeHtml(labels.join('; ')||'This reason has no recorded source cluster.')}">${escapeHtml(labels.slice(0,3).join(' · ')||'Cluster not recorded')}${labels.length>3?' · +'+(labels.length-3):''}</span>`;
}
function reviewJudgmentMeta(review){
  const parts=[];
  if(review.expert_confidence)parts.push(review.expert_confidence+' confidence');
  if(review.evidence_sufficiency&&review.evidence_sufficiency!=='unspecified')parts.push(review.evidence_sufficiency+' evidence');
  if(review.missing_evidence?.length)parts.push('Missing: '+review.missing_evidence.map(item=>item.replaceAll('_',' ')).join(', '));
  if(review.missing_evidence_note)parts.push(review.missing_evidence_note);
  return parts.length?`<small class="judgment-meta">${escapeHtml(parts.join(' · '))}</small>`:'';
}
function setAgreement(agree){
  if(isWorkingReview())return WorkingReview.agreement(agree);
  const c=currentCase();draft.agree=c.review_mode==='manual'?null:agree;isDirty=true;$('#form-error').textContent='';
  if(c.review_mode==='manual'){renderActions();renderEvidence();return;}
  if(agree){draft.action=c.ai;draft.target=c.pair?.[1]||'';draft.disagreement_types=[];$('#disagree-rationale').checked=false;$('#disagree-evidence').checked=false;}
  else if(!allowed(c).includes(draft.action))draft.action=null;
  if(!agree&&draft.action==='MERGE'&&comparisonTarget(c))draft.target=comparisonTarget(c);
  if(agree){draft.scope=c.pair?'pair':'single';selectComparison(c.pair?.[1]||'',false);if(c.synthetic===false&&['SPLIT','MERGE','DISCARD'].includes(draft.action)){cacheLocalDraft();WorkingReview.openOperation(draft.action).catch(error=>{$('#form-error').textContent=error.message;});}return;}
  renderActions();renderEvidence();
}
function renderReasons(){
  renderGeneralReasons();
  $('#reason-options').innerHTML=generalReasonRows.map(r=>`<option value="${escapeHtml(r.text)}"></option>`).join('');
}
function hideReasonMatches(){reasonSequence++;clearTimeout(reasonTimer);$('#reason-suggestions').hidden=true;$('#reason-select').setAttribute('aria-expanded','false');$('#reason-select').removeAttribute('aria-activedescendant');reasonIndex=-1;}
function showReasonMatches(){
  const input=$('#reason-select'),prefix=input.value;
  reasonMatches=Core.matchReasons(generalReasonRows,prefix).slice(0,20);reasonIndex=-1;
  $('#reason-suggestions').innerHTML=reasonMatches.map((r,i)=>`<button type="button" role="option" aria-selected="false" tabindex="-1" id="reason-option-${i}" data-reason-index="${i}"><span>${escapeHtml(r.text)}</span><small><span>${r.uses?`Used ${r.uses}×`:''}</span></small></button>`).join('');
  const open=reasonMatches.length>0&&!$('#correction-panel').hidden&&document.activeElement===input;
  $('#reason-suggestions').hidden=!open;input.setAttribute('aria-expanded',String(open));input.removeAttribute('aria-activedescendant');
  $$('[data-reason-index]').forEach(b=>{b.onpointerdown=e=>e.preventDefault();b.onclick=()=>chooseReason(Number(b.dataset.reasonIndex));});
}
function chooseReason(index){
  if(!reasonMatches[index])return;
  $('#reason-select').value=reasonMatches[index].text;isDirty=true;$('#form-error').textContent='';hideReasonMatches();$('#reason-select').focus();renderChoiceSummaries();
}
function queryReasons(){
  const sequence=++reasonSequence,prefix=$('#reason-select').value;
  showReasonMatches();clearTimeout(reasonTimer);
  if(backend)reasonTimer=setTimeout(async()=>{
    try{const result=await api('/api/reasons?prefix='+encodeURIComponent(prefix));if(sequence!==reasonSequence)return;mergeReasons(result.reasons);renderReasons();showReasonMatches();}
    catch{ /* The input and remembered local matches remain usable offline. */ }
  },120);
}
async function saveReview(){
  if(isWorkingReview())return WorkingReview.confirm();
  if(saving||!canEditCurrentReview())return;
  const c=currentCase();
  draft.reason=$('#reason-select').value;draft.note=$('#review-note').value;draft.positive=$('#positive-comment').value;if(draft.action==='DEFER'&&!draft.reason&&!draft.note&&draft.missing_note)draft.note=draft.missing_note;
  const error=Core.validate(c,draft);
  if(error){$('#form-error').textContent=error;return;}
  const context={viewed_cluster_ids:[...unitsViewed],visible_cluster_ids:visibleUnits(),viewed_tabs:[...tabsViewed],rationale_viewed:rationaleViewed,jacob_reference_viewed:referenceViewed,annotator:state.annotator,pair_comparisons:comparedPairs(c),quality_evidence:ReviewQuality.load(c)};
  let review;
  if(mode==='real'){
    saving=true;$('.decision-panel').inert=true;renderActions();$('#save-button').textContent=backend?.static_share?'Saving in browser…':'Saving to server…';
    try{const result=await api('/api/reviews',{case_id:c.id,draft:{...draft},context,expected_revision:revisions[c.id]||0,request_id:crypto.randomUUID()});review=result.review;revisions[c.id]=result.revision;connectionWarning('');}
    catch(error){$('#form-error').textContent=`Not saved: ${error.message}`;saving=false;$('.decision-panel').inert=false;renderActions();return;}
    saving=false;$('.decision-panel').inert=false;
  }else review=Core.buildReview(c,draft,context);
  state.reviews[c.id]=review;if(mode==='real')allReviewRecords[c.id]=review;
  state.events.push({type:'review_saved',...review});
  if(!draft.agree)state.reasons=Core.rememberReason(state.reasons,review.expert_reason,{case_id:c.id,timestamp:review.timestamp,provenance:mode==='real'?'reviewer-entered-real-review':'reviewer-entered-in-synthetic-demo'});
  if(backend&&mode==='demo'&&review.expert_reason)api('/api/reasons/import',{reasons:[review.expert_reason]}).catch(()=>connectionWarning('Reason is saved in this browser; server sync failed.'));
  persist();clearLocalDraft(c.id);isDirty=false;editingSaved=false;
  renderReasons();renderQueue();renderCase();
  toast(`${sourceUnit(c).title}: ${ACTIONS[review.action].label} saved.`,c.id);
}
async function undoReview(id,force=false){
  if(saving)return;
  if(isDirty&&!force){showUnsaved(id,()=>undoReview(id,true));return;}
  if(!state.reviews[id])return;
  if(mode==='real'){
    saving=true;$('.decision-panel').inert=true;
    try{const result=await api('/api/reviews/undo',{case_id:id,expected_revision:revisions[id]||0,request_id:crypto.randomUUID()});revisions[id]=result.revision;}
    catch(error){toast(`Undo failed: ${error.message}`);saving=false;$('.decision-panel').inert=false;return;}
    saving=false;$('.decision-panel').inert=false;
  }
  state.events.push({type:'review_undone',case_id:id,previous_review:state.reviews[id],timestamp:new Date().toISOString(),synthetic:mode!=='real'});
  delete state.reviews[id];if(mode==='real')delete allReviewRecords[id];clearLocalDraft(id);persist();
  selectCase(id,true);renderHistory();showView('review');toast('Review undone. The case is back in the queue.');
}
const activityViews=new Map();
async function loadSavedActivity(){
 const identity=reviewerId,warnings=[];let payload,workspaces=[];
 payload=mode==='real'?await api('/api/export'):{reviews:Object.values(state.reviews),events:state.events,reviewer_id:reviewerId};
 if(mode==='real')try{workspaces=await ActivityUI.readWorkspaces(identity);}catch(e){warnings.push('Working history unavailable: '+e.message);}
 return {...ActivityCore.combine(workspaces,payload),warnings};
}
function activityName(id){return ReviewerProfiles.list(localStorage).find(r=>r.id===id)?.name||(id?String(id).slice(0,8):profileName||'Reviewer');}
async function openActivityRecord(row,undo=false){
 const perform=async()=>{
  if(row.kind==='working'){await WorkingReview.openWorkspace(row.workspace_id,row.source,row.target);showView('review');return;}
  if(!CASES.some(c=>c.id===row.case_id)){
   let match=null;for(const bundle of bundleCatalog){let catalog=queueCatalogs.get(bundle.id);if(!catalog){catalog=await api('/api/cases?bundle_id='+encodeURIComponent(bundle.id));queueCatalogs.set(bundle.id,catalog);}if(catalog.cases.some(c=>c.id===row.case_id)){match=bundle;break;}}
   if(!match)throw Error('The original snapshot is not in the active catalog. Its saved record is retained in the export.');
   if(match.study_agent_id)activeAgentId=match.study_agent_id;
   if(!await switchSource(choiceForBundle(match.id),true))throw Error('Could not open this saved snapshot');
  }
  if(mode==='real'){const saved=await api('/api/reviews'),ids=new Set(CASES.map(c=>c.id));allReviewRecords=Object.fromEntries(Object.entries(saved.reviews).map(([id,r])=>[id,r.review]));state.reviews=Object.fromEntries(Object.entries(allReviewRecords).filter(([id])=>ids.has(id)));revisions=Object.fromEntries(Object.entries(saved.revisions).filter(([id])=>ids.has(id)));}
  showView('review');selectCase(row.case_id,true);if(undo)await undoReview(row.case_id,true);
 };
 if(isDirty){showUnsaved(null,()=>perform().catch(e=>toast(e.message)));return;}await perform();
}
async function renderActivity(view){const root=view==='history'?$('#history-list'):$('#revision-list');if(!activityViews.has(view))activityViews.set(view,ActivityUI.mount(root,{view,owner:()=>reviewerId,name:activityName,load:loadSavedActivity,onOpen:openActivityRecord,onUndo:r=>openActivityRecord(r,true),onView:showView}));$('#revision-status').textContent='';await activityViews.get(view).refresh(view);}
function renderHistory(){renderActivity('history');$('#view-history .section-title>.pill').textContent=mode==='real'&&!backend?.static_share?'Runpod annotations + browser workspaces':'Browser-local';}
function renderRevisions(){return renderActivity('revisions');}
$('#revisions-refresh').onclick=renderRevisions;
async function exportFeedback(){
  if(isWorkingReview()&&currentView==='review')return WorkingReview.exportResult();
  let payload={schema:Core.SCHEMA,synthetic:true,notice:'Illustrative feedback only. No model or spike curation operations were performed. Null hashes mean no real experiment provenance exists.',exported_at:new Date().toISOString(),reviews:Object.values(state.reviews),events:state.events,reason_library:state.reasons,
    legacy_v1:legacyFeedback,legacy_notice:legacyFeedback?'Preserved original v1 data; not reinterpreted as v2 annotations.':null};
  if(mode==='real'){
    try{payload=await api('/api/export');payload.browser_exposure_events=state.events;payload.active_dataset_provenance=realCatalog.provenance;}
    catch(error){toast(`Server export failed: ${error.message}`);return;}
  }
  const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=mode==='real'?'spikeagent-feedback-v3.json':'spikeagent-demo-feedback-v2.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
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
function templateValue(u,x){
  const shift=(u.seed%7-3)*.007,scale=u.kind==='noise'?.28:.68+(u.seed%5)*.07;
  return scale*(-125*Math.exp(-Math.pow((x-.43-shift)/.05,2))+45*Math.exp(-Math.pow((x-.54-shift)/.085,2)));
}
function tracePoints(u,box,offset=()=>0){
  return Array.from({length:120},(_,i)=>{
    const x=i/119,y=templateValue(u,x)+offset(x);
    return `${(box.l+x*box.iw).toFixed(1)},${(box.t+(60-y)/220*box.ih).toFixed(1)}`;
  }).join(' ');
}
function savedWaveformFor(c,u){
  if(c.synthetic!==false||c.review_mode==='manual')return null;
  const name=c.pair?(u.id===c.source?'small_waveform':u.id===c.pair[1]?'large_waveform':null):u.id===c.source?'waveform':null;
  return c.images.find(image=>image.name===name)||null;
}
function waveformLegend(u){
  const real=currentCase().synthetic===false;
  return `<div class="plot-key" aria-label="Waveform legend"><span><i style="--key-color:${u.color}"></i>${escapeHtml(u.title)} mean</span><span><i class="trace-key" style="--key-color:${u.color}"></i>${real?'up to 24 individual spikes':'24 illustrative traces'}</span></div><p class="plot-note">${real?'Mean uses all member spikes; individual traces are a chronological sample.':'Synthetic example; the mean and traces are illustrative.'} Total cluster spikes and displayed traces are different counts.</p>`;
}
function waveform(u,thumbnail=false,limits=null){
  if(currentCase().synthetic===false)return realWaveform([u],thumbnail,limits);
  const w=610,h=218,box=axes(w,h,'Time (ms)','Source units',['−0.5','0','0.5','1.0','1.5'],['60','5','−50','−105','−160']);
  const random=rng(u.seed);let lines='';
  if(!thumbnail)for(let k=0;k<24;k++){
    const spread=['mixed','split'].includes(u.kind)?12:3;
    lines+=`<polyline points="${tracePoints(u,box,()=>((random()-.5)*spread))}" fill="none" stroke="${u.color}" stroke-opacity=".15" stroke-width="1"/>`;
  }
  lines+=`<polyline points="${tracePoints(u,box)}" fill="none" stroke="${u.color}" stroke-width="2"/>`;
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Synthetic waveform for ${u.title}">${box.markup}${lines}</svg>`;
}
function visibleUnits(){
  const c=currentCase();
  if(comparisonTarget(c)&&['detail','quality','timing','stability','correlation','pca'].includes(selectedTab))return [c.source,comparisonTarget(c)];
  if(['overview','gallery'].includes(selectedTab))return c.units.map(u=>u.id);
  if(selectedTab==='timing'&&c.pair&&(selectedUnitId===c.source||(c.synthetic===false&&c.pair.includes(selectedUnitId))))return c.pair;
  return [selectedUnitId];
}
function recordEvidence(){
  if(isWorkingReview()){tabsViewed.add(selectedTab);return;}
  tabsViewed.add(selectedTab);visibleUnits().forEach(id=>unitsViewed.add(id));
  if(comparisonTarget(currentCase())&&['detail','quality','timing','stability','correlation','pca'].includes(selectedTab))tabsViewed.add('pair');
  state.events.push({type:'evidence_viewed',case_id:selectedId,tab:selectedTab,
    cluster_ids:visibleUnits().map(id=>currentCase().units.find(u=>u.id===id).cluster_id),
    timestamp:new Date().toISOString(),synthetic:currentCase().synthetic!==false});persist();
}
function inspectUnit(id){
  if(!currentCase().units.some(u=>u.id===id))return;
  selectComparison(id===currentCase().source?'':id);
}
function viewingIds(c=currentCase()){return comparisonTarget(c)?[c.source,comparisonTarget(c)]:[selectedUnitId];}
function viewingTag(id,c=currentCase()){return viewingIds(c).includes(id)?'<span class="cluster-tag viewing-tag" title="Currently selected for evidence viewing; this is not a completed-review marker.">Viewing</span>':'';}
function clusterTags(u){return viewingTag(u.id);}
function allWaveforms(){
  if(currentCase().synthetic===false){
    const legend=currentCase().units.map(u=>`<button class="cluster-legend" data-inspect="${u.id}"><i style="background:${u.color}"></i><strong>${u.title}</strong><span>n = ${u.spikes.toLocaleString()}</span>${clusterTags(u)}</button>`).join('');
    return plotCard('All active clusters · real checkpoint','Means of all member spikes · shared axes',comparisonControls()+realWaveform(currentCase().units,true)+comparisonGuide()+`<div class="cluster-legends">${legend}</div>`);
  }
  const c=currentCase(),w=610,h=250,box=axes(w,h,'Time (ms)','Source units',['−0.5','0','0.5','1.0','1.5'],['60','5','−50','−105','−160']);
  const lines=c.units.map(u=>`<polyline data-waveform-cluster="${u.id}" points="${tracePoints(u,box)}" fill="none" stroke="${u.color}" stroke-width="${u.id===selectedUnitId||u.id===draft.target?3:1.8}"><title>${escapeHtml(u.title)} mean · ${u.spikes.toLocaleString()} spikes</title></polyline>`).join('');
  const legend=c.units.map(u=>`<button class="cluster-legend" data-inspect="${u.id}"><i style="background:${u.color}"></i><strong>${u.title}</strong><span>n = ${u.spikes.toLocaleString()}</span>${clusterTags(u)}</button>`).join('');
  return plotCard('All clusters · same synthetic recording','Shared axes · illustrative means',`${comparisonControls()}<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="All cluster waveforms on shared axes">${box.markup}${lines}</svg>${comparisonGuide()}<div class="cluster-legends">${legend}</div>`);
}
function comparisonControls(){
  return `<div class="comparison-controls"><label for="highlight-cluster">Highlight curve<select id="highlight-cluster"><option value="">All cluster means</option>${currentCase().units.map(u=>`<option value="${u.id}">${escapeHtml(u.title)}</option>`).join('')}</select></label><span id="highlight-label" role="status">Each line is one cluster mean. Axes are shared.</span></div>`;
}
function comparisonGuide(){
  return '<p class="plot-note comparison-guide">One line = one cluster mean, on shared axes. Colors can repeat: hover or focus a cluster below to isolate its line; click to open detail. Viewing marks the cluster or pair currently selected for evidence.</p>';
}
function gallery(){
  const limits=currentCase().synthetic===false?waveformLimits(currentCase().units,true):null;
  return `<div class="cluster-gallery">${currentCase().units.map(u=>`<div class="thumbnail-card"><div class="thumbnail-heading"><strong>${u.title}</strong><span>n = ${u.spikes.toLocaleString()}</span></div><div class="thumbnail-viewport"><button class="cluster-thumbnail ${u.id===selectedUnitId?'inspected':''}" data-inspect="${u.id}" aria-label="Inspect ${u.title}">${waveform(u,true,limits)}</button><button class="thumbnail-expand" data-expand-unit="${u.id}" aria-label="Expand ${u.title} waveform" title="Expand waveform">⤢</button></div><div class="thumbnail-meta"><div class="cluster-tags">${clusterTags(u)}</div><small>${currentCase().synthetic===false?'Real mean · shared scale':'Illustrative mean · shared scale'} · click for detail</small></div></div>`).join('')}</div>`;
}
function tickLabel(value){return Math.abs(value)>=1000?value.toFixed(0):Number(value.toPrecision(3)).toString();}
function waveformLimits(units,meanOnly){
  let low=Infinity,high=-Infinity;
  for(const u of units.filter(unit=>unit.waveform))for(const values of meanOnly?[u.waveform.mean]:[u.waveform.mean,...u.waveform.traces])for(const value of values){low=Math.min(low,value);high=Math.max(high,value);}
  const pad=Math.max((high-low)*.08,1e-9);return {low:low-pad,high:high+pad};
}
function realWaveform(units,meanOnly=false,limits=null){
  const available=units.filter(u=>u.waveform);
  if(!available.length)return '<p class="tree-note">Waveform is loading.</p>';
  const w=610,h=250,xs=available[0].waveform.x_ms;
  const {low,high}=limits||waveformLimits(available,meanOnly);
  const xLow=xs[0],xHigh=xs[xs.length-1];
  const box=axes(w,h,'Time (ms)','Amplitude (source units)',Array.from({length:5},(_,i)=>tickLabel(xLow+(xHigh-xLow)*i/4)),Array.from({length:5},(_,i)=>tickLabel(high-(high-low)*i/4)));
  const points=values=>values.map((v,i)=>`${(box.l+(xs[i]-xLow)/(xHigh-xLow)*box.iw).toFixed(2)},${(box.t+(high-v)/(high-low)*box.ih).toFixed(2)}`).join(' ');
  const lines=available.map(u=>(meanOnly?'':u.waveform.traces.map(values=>`<polyline points="${points(values)}" fill="none" stroke="${u.color}" stroke-opacity=".18" stroke-width="1"/>`).join(''))+`<polyline data-waveform-cluster="${u.id}" points="${points(u.waveform.mean)}" fill="none" stroke="${u.color}" stroke-width="2"><title>${escapeHtml(u.title)} mean · ${u.spikes.toLocaleString()} spikes</title></polyline>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Real cluster waveform${available.length>1?'s on shared axes':''}">${box.markup}${lines}</svg>`;
}
function realAmplitude(u){
  const data=u.amplitude_time;
  const xLow=Math.min(...data.time_seconds),xHigh=Math.max(...data.time_seconds),high=Math.max(...data.peak_to_trough)*1.05||1;
  const w=610,h=250,box=axes(w,h,'Time (seconds)','Peak-to-trough (source units)',Array.from({length:5},(_,i)=>tickLabel(xLow+(xHigh-xLow)*i/4)),Array.from({length:5},(_,i)=>tickLabel(high*(1-i/4))));
  const dots=data.time_seconds.map((t,i)=>`<circle cx="${box.l+(t-xLow)/Math.max(xHigh-xLow,1e-9)*box.iw}" cy="${box.t+(1-data.peak_to_trough[i]/high)*box.ih}" r="2" fill="${u.color}" opacity=".65"/>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Real peak-to-trough amplitude over time">${box.markup}${dots}</svg>`;
}
function realISI(u){
  const data=u.isi,high=Math.max(...data.counts,1),w=610,h=250;
  const box=axes(w,h,'ISI (ms)','Count',['0','12.5','25','37.5','50'],Array.from({length:5},(_,i)=>tickLabel(high*(1-i/4))));
  const bars=data.counts.map((n,i)=>`<rect x="${box.l+i/data.counts.length*box.iw}" y="${box.t+(1-n/high)*box.ih}" width="${box.iw/data.counts.length}" height="${n/high*box.ih}" fill="${u.color}"/>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Real ISI histogram">${box.markup}${bars}</svg>`;
}
function savedImage(image){
  const titles={waveform:'Waveform · saved model input',isi:'ISI histogram · saved model input',amplitude:'Amplitude distribution · saved model input',tree:'Aggregation tree · saved model input',small_waveform:'Source waveform · saved model input',large_waveform:'Target waveform · saved model input',merged_isi:'Merged ISI · saved model input'};
  const notes={
    waveform:'Each faint blue line is one sampled spike waveform; the dashed black line is zero amplitude. This saved raw-waveform image has no mean overlay. “n” is the total spike count; “raw displayed” is the sampled count.',
    small_waveform:'Source cluster: each faint blue line is one sampled spike waveform; dashed black = zero amplitude. No mean overlay. Check axis ranges before comparing separate saved images; All waveforms uses shared axes.',
    large_waveform:'AI target cluster: each faint blue line is one sampled spike waveform; dashed black = zero amplitude. No mean overlay. Check axis ranges before comparing separate saved images; All waveforms uses shared axes.',
    isi:'Each bar counts intervals between adjacent spikes in this cluster. X = ISI in ms; Y = interval count. Only intervals up to 50 ms are shown. This is not an autocorrelogram.',
    merged_isi:'Each bar counts intervals after pooling the original AI pair’s spike times. This is a hypothetical merge, not an executed operation. Changing your proposed target does not recompute this saved image.',
    amplitude:'Each bar counts spikes in a peak-to-trough amplitude range, using all member spikes. X = amplitude in source units; Y = spike count. This distribution has no time axis.',
    tree:'Branches show the recorded aggregation topology, not spike amplitude or recording time. X identifies overclusters; Y is the displayed tree height. This image does not show a proposed split result.'
  };
  return plotCard(titles[image.name],`Verified SHA256 ${image.sha256.slice(0,12)}…`,`<p class="plot-note">${notes[image.name]}</p><img class="saved-evidence-image" src="${image.url}" alt="${titles[image.name]}" loading="lazy" decoding="async">`);
}
function realEvidence(c,u){
  if(!c.evidenceLoaded){
    if(!c.evidencePending&&!c.evidenceError){
      c.evidencePending=true;
      api(c.evidence_url).then(payload=>{
        for(const detail of payload.units)Object.assign(c.units.find(unit=>unit.cluster_id===detail.cluster_id),detail);
        c.evidenceLoaded=true;c.evidencePending=false;
        if(currentCase()===c)renderEvidence();
      }).catch(error=>{c.evidencePending=false;c.evidenceError=error.message;if(currentCase()===c)renderEvidence();});
    }
    return `<p class="tree-note">${c.evidenceError?'Real evidence could not be loaded: '+escapeHtml(c.evidenceError):'Loading verified real waveforms and saved images…'}</p>${c.evidenceError?'<button class="button" id="retry-evidence">Retry</button>':''}`;
  }
  if(selectedTab==='detail'&&comparisonTarget(c))return renderPairComparison(c);
  if(selectedTab==='detail'){const entry=reviewerAnalysis(c,u.id);if(!entry.data)return analysisPending(entry);return plotCard('Waveforms','',singleMemberWaveforms(entry.data,c,u))+(u.id!==c.source?`<p class="tree-note">Viewing ${escapeHtml(u.title)}; your review remains for ${escapeHtml(sourceUnit(c).title)}. Choose a different queue entry to change the review object.</p>`:'');}
  if(selectedTab==='correlation')return renderReviewCorrelograms(c);
  if(selectedTab==='quality')return renderQualityMetrics(c,u);
  if(selectedTab==='overview')return allWaveforms()+'<p class="tree-note">All active clusters in this exact pre-decision checkpoint. These extra mean waveforms were computed locally from MAT spike rows, not sent to the historical model.</p>';
  if(selectedTab==='gallery')return gallery()+'<p class="tree-note">Thumbnail means use the same time and amplitude scales across this checkpoint. Counts are total member spikes. Expanded detail uses its own labeled axes and adds up to 24 chronological traces.</p>';
  if(selectedTab==='stability')return [u,...(comparisonTarget(c)?[c.units.find(unit=>unit.id===comparisonTarget(c))]:[])].map(unit=>plotCard(`${unit.title} · real amplitude over time`,'Extra reviewer evidence · up to 500 chronological rows',realAmplitude(unit),'<p class="plot-note">One dot = one sampled spike. X = recording time in seconds; Y = peak-to-trough amplitude in source units. Each chart has its own labeled axes.</p>')).join('');
  if(selectedTab==='amplitude')return savedImage(c.images.find(image=>image.name==='amplitude'))+(comparisonTarget(c)?'<p class="tree-note">This saved amplitude image belongs to the original agent input, not a new measurement of the selected comparison pair. Use Cluster detail or Quality metrics for the selected candidate.</p>':'');
  const modelIds=c.review_mode==='manual'?[]:c.pair||[c.source],modelObject=modelIds.includes(u.id);
  if(selectedTab==='tree'){
    const image=c.images.find(i=>i.name==='tree');
    return image&&selectedUnitId===c.source?savedImage(image):`<p class="tree-note">No saved model tree image exists for this object in this step. Current checkpoint split availability: ${u.split?'available':'unavailable'}. No topology is invented.</p>`;
  }
  if(selectedTab==='timing'){
    if(comparisonTarget(c)){
      const entry=reviewerAnalysis(c,c.source,comparisonTarget(c));
      if(!entry.data)return analysisPending(entry);
      if(!comparisonExposure.has(c.id))resetAnalysisExposure(c.id);comparisonExposure.get(c.id).add(comparisonTarget(c));
      return plotCard(escapeHtml(comparisonName(c))+' · candidate pair ISI','Recomputed for selected candidate; no spikes reassigned',candidateISI(entry.data));
    }
    const image=c.images.find(i=>i.name===(c.phase_id==='phase1'?'isi':'merged_isi'));
    const savedISI=modelObject&&image;
    return (savedISI?savedImage(image):plotCard(`${u.title} · real ISI histogram`,'Extra reviewer evidence · all member spikes',realISI(u),'<p class="plot-note">Adjacent-spike intervals · 0–50 ms · raw counts.</p>'))+(savedISI&&c.pair?`<p class="tree-note">Saved AI pair: ${unitLabel(c,c.source)} + ${unitLabel(c,c.pair[1])}. Your proposed target: ${draft.action==='MERGE'?unitLabel(c,draft.target):'none'}. The displayed histogram always belongs to the saved AI pair.</p>`:'');
  }
  const savedWave=savedWaveformFor(c,u);
  if(savedWave)return savedImage(savedWave)+'<p class="tree-note">Cluster detail shows one waveform image. Select Compare with for shared-axis pair waveforms and candidate ISI. The other tabs show timing, amplitude and tree evidence.</p>';
  return plotCard(`${u.title} · real waveform`,'Extra reviewer evidence · mean + up to 24 traces',waveformLegend(u)+waveform(u))+
    `<p class="tree-note">${c.review_mode==='manual'?'Initial MAT hierarchy · manual review.':`Inspecting comparison ${u.title}; the review object remains ${sourceUnit(c).title}.`}</p>`;
}
function syncPlotTypography(){
  $$('#evidence-content svg, #modal-content svg').forEach(svg=>{
    if(svg.closest('.cluster-thumbnail'))return;
    const screenSize=Math.max(12,Math.min(16,(parseFloat(getComputedStyle(svg.closest('.plot-card')||svg).fontSize)||16)*.9));
    const width=svg.clientWidth,viewWidth=svg.viewBox.baseVal.width;
    if(width>0&&viewWidth>0)svg.style.setProperty('--chart-label-size',`${screenSize*viewWidth/width}px`);
  });
}
// View transforms are local to each graphic; data and review drafts are untouched.
function attachPlotZoom(root,expanded=false){
  root.querySelectorAll('.plot-card svg, .plot-card img, .plot-card canvas').forEach(visual=>{
    if(visual.closest('.plot-viewport'))return;
    const card=visual.closest('.plot-card');
    const title=card.querySelector('h3')?.textContent||visual.getAttribute('aria-label')||'Evidence plot';
    const toolbar=document.createElement('div');
    toolbar.className='plot-toolbar';toolbar.setAttribute('role','group');toolbar.setAttribute('aria-label',`${title} zoom controls`);
    toolbar.innerHTML='<button type="button" data-zoom="out" aria-label="Zoom out" title="Zoom out (−)">−</button><output class="zoom-level" aria-live="polite">100%</output><button type="button" data-zoom="in" aria-label="Zoom in" title="Zoom in (+)">+</button><button type="button" data-zoom="reset" aria-label="Fit plot" title="Reset zoom and position (0)">↺</button>'+(expanded?'':'<button type="button" data-zoom="expand" aria-label="Expand plot" title="Expand plot">⤢</button>');
    const viewport=document.createElement('div');viewport.className='plot-viewport';viewport.tabIndex=0;
    viewport.setAttribute('role','group');viewport.setAttribute('aria-label',`${title}. Plus or minus to zoom, arrow keys to pan, 0 to reset.`);
    const surface=document.createElement('div');surface.className='plot-surface';
    visual.before(viewport);surface.append(visual);viewport.append(surface,toolbar);
    if(visual.tagName.toLowerCase()==='img')visual.draggable=false;
    let scale=1,x=0,y=0,drag=null;
    const paint=()=>{
      x=Math.max(viewport.clientWidth*(1-scale),Math.min(0,x));
      y=Math.max(viewport.clientHeight*(1-scale),Math.min(0,y));
      surface.style.transform=`translate(${x}px, ${y}px) scale(${scale})`;
      viewport.dataset.scale=scale.toFixed(3);viewport.classList.toggle('is-zoomed',scale>1);
      toolbar.querySelector('output').value=`${Math.round(scale*100)}%`;
      toolbar.querySelector('[data-zoom="out"]').disabled=scale<=1;
      toolbar.querySelector('[data-zoom="in"]').disabled=scale>=8;
    };
    const zoom=(next,px=viewport.clientWidth/2,py=viewport.clientHeight/2)=>{
      next=Math.max(1,Math.min(8,next));const ratio=next/scale;
      x=px-(px-x)*ratio;y=py-(py-y)*ratio;scale=next;paint();
    };
    toolbar.onclick=e=>{
      const button=e.target.closest('[data-zoom]');if(!button)return;
      const action=button.dataset.zoom;
      if(action==='in')zoom(scale*1.25);
      if(action==='out')zoom(scale/1.25);
      if(action==='reset')zoom(1);
      if(action==='expand'){const copy=visual.cloneNode(true);if(visual.tagName.toLowerCase()==='canvas')copy.getContext('2d').drawImage(visual,0,0);openExpandedPlot(title,copy);}
    };
    viewport.addEventListener('wheel',e=>{
      if(!(e.ctrlKey||e.metaKey))return;
      e.preventDefault();const r=viewport.getBoundingClientRect();
      zoom(scale*Math.exp(-Math.max(-100,Math.min(100,e.deltaY))*.005),e.clientX-r.left,e.clientY-r.top);
    },{passive:false});
    viewport.addEventListener('dblclick',e=>{
      if(e.target.closest('.plot-toolbar'))return;
      e.preventDefault();const r=viewport.getBoundingClientRect();zoom(scale>1?1:2,e.clientX-r.left,e.clientY-r.top);
    });
    viewport.addEventListener('pointerdown',e=>{
      if(scale<=1||!e.isPrimary||e.button!==0||e.target.closest('.plot-toolbar'))return;
      e.preventDefault();viewport.focus({preventScroll:true});viewport.setPointerCapture(e.pointerId);
      drag={id:e.pointerId,x:e.clientX,y:e.clientY,startX:x,startY:y};viewport.classList.add('is-dragging');
    });
    viewport.addEventListener('pointermove',e=>{
      if(!drag||drag.id!==e.pointerId)return;
      x=drag.startX+e.clientX-drag.x;y=drag.startY+e.clientY-drag.y;paint();
    });
    const stopDrag=()=>{drag=null;viewport.classList.remove('is-dragging');};
    viewport.addEventListener('lostpointercapture',stopDrag);viewport.addEventListener('pointercancel',stopDrag);
    viewport.addEventListener('pointerup',e=>{if(viewport.hasPointerCapture(e.pointerId))viewport.releasePointerCapture(e.pointerId);stopDrag();});
    viewport.addEventListener('keydown',e=>{
      if(e.ctrlKey||e.metaKey||e.altKey||e.target.closest('.plot-toolbar'))return;
      if(['+','=','-','0','Home','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){
        if(e.key.startsWith('Arrow')&&scale===1)return;
        e.preventDefault();e.stopPropagation();
        if(e.key==='+'||e.key==='=')zoom(scale*1.25);
        else if(e.key==='-')zoom(scale/1.25);
        else if(e.key==='0'||e.key==='Home')zoom(1);
        else{x+=e.key==='ArrowLeft'?40:e.key==='ArrowRight'?-40:0;y+=e.key==='ArrowUp'?40:e.key==='ArrowDown'?-40:0;paint();}
      }
    });
    // A resize preserves the current scale while keeping the graphic inside its bounds.
    viewport.addEventListener('plotresize',paint);paint();
  });
}
function openExpandedPlot(title,visual){
  modal(title,'<div class="plot-card expanded-plot"><div class="plot-heading"></div></div>');
  const card=$('#modal-content .plot-card'),heading=document.createElement('h3');
  heading.textContent=title;card.querySelector('.plot-heading').append(heading);card.append(visual);
  $('#info-modal').classList.add('plot-modal');attachPlotZoom($('#modal-content'),true);syncPlotTypography();
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
  const w=large?610:296,h=large?235:177,{markup,l,t,iw,ih}=axes(w,h,'Time (min)','Source units',['0','5','10','15','20'],['160','120','80','40','0']),random=rng(c.seed+200);
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
  if(isWorkingReview())return WorkingReview.renderEvidence();
  const c=currentCase();
  if(['pair','overview','gallery'].includes(selectedTab))selectedTab='detail';
  const u=currentUnit(),pairTiming=!!c.pair&&selectedUnitId===c.source;
  $('[data-tab="quality"]').hidden=c.synthetic!==false;
  const treeTab=$('[data-tab="tree"]');treeTab.hidden=Boolean(comparisonTarget(c))||c.synthetic===false&&!c.images.some(image=>image.name==='tree');if(selectedTab==='tree'&&treeTab.hidden)selectedTab='detail';
  const amplitudeTab=$('[data-tab="amplitude"]');
  amplitudeTab.hidden=!(c.synthetic===false&&u.id===c.source&&c.images.some(image=>image.name==='amplitude'));
  if(selectedTab==='amplitude'&&amplitudeTab.hidden)selectedTab='detail';
  $$('[data-tab]').forEach(b=>{const active=b.dataset.tab===selectedTab;b.classList.toggle('active',active);b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;b.id='evidence-tab-'+b.dataset.tab;b.setAttribute('aria-controls','evidence-content');});
  $('#evidence-content').setAttribute('aria-labelledby','evidence-tab-'+selectedTab);
  const target=comparisonTarget(c),other=c.units.find(unit=>unit.id===target);
  $('#evidence-context').hidden=false;
  const contextMarkup=`<label for="compare-with">Compare with<select id="compare-with"><option value="">None selected</option>${Core.candidates(c).map(unit=>`<option value="${unit.id}" ${unit.id===target?'selected':''}>${escapeHtml(unit.title)}</option>`).join('')}</select></label><div class="comparison-context"><div class="comparison-role"><strong>${escapeHtml(sourceUnit(c).title)}</strong><span class="cluster-tags">${clusterTags(sourceUnit(c))}</span></div>${other?`<div class="comparison-role"><strong>${escapeHtml(other.title)}</strong><span class="cluster-tags">${clusterTags(other)}</span></div>`:''}<details class="comparison-help"><summary>What am I comparing?</summary><p>Compare two clusters from the same recording and saved state. Inspect waveforms and timing before proposing a merge. Selecting a candidate ${canEditCurrentReview()&&draft.action==='MERGE'&&(draft.agree===false||c.review_mode==='manual')?'also updates your merge proposal target':'updates the available pair actions'}; assignments and the saved Agent answer stay unchanged.</p></details></div>`;
  const contextElement=$('#evidence-context');
  if(contextElement.renderedMarkup!==contextMarkup){contextElement.innerHTML=contextMarkup;contextElement.renderedMarkup=contextMarkup;}
  let body='';
  if(c.synthetic===false){
    body=selectedTab==='pca'?ReviewPCA.readonly(c):realEvidence(c,u);
  }else if(selectedTab==='correlation'){
    body=renderReviewCorrelograms(c);
  }else if(selectedTab==='overview'){
    body=allWaveforms()+'<p class="tree-note">All clusters belong to this case’s synthetic recording. Extra comparison evidence is recorded in your feedback.</p>';
  }else if(selectedTab==='gallery'){
    body=gallery()+'<p class="tree-note">Colors supplement stable cluster IDs. Counts describe the synthetic population, not the number of drawn traces.</p>';
  }else if(selectedTab==='detail'){
    body=target?renderPairComparison(c):plotCard(`${u.title} · waveform detail`,'24 illustrative traces + mean',waveformLegend(u)+waveform(u))+
      `<p class="tree-note">Inspecting ${u.title}; n = ${u.spikes.toLocaleString()}. Your review still concerns ${sourceUnit(c).title}. These demo views do not reproduce the frozen main-pipeline PNGs.</p>`;
  }else if(selectedTab==='timing'){
    body=target?'<p class="analysis-message">The synthetic demo has no measured candidate-pair ISI. Choose a real-data recording for those measurements.</p>':plotCard(pairTiming?'AI pair · cross-correlogram':`${u.title} · autocorrelogram`,'Lag window: ±25 ms',correlogram(u,pairTiming,true),'<p class="plot-note">Each bar counts illustrative spike pairs at a time lag. X = lag in ms; Y = count. The shaded band marks lags near zero. A correlogram is not an ISI histogram.</p>')+
      '<div class="annotation"><span>◈</span><p>Interpret timing evidence in context, including spike count and recording stability.</p></div>';
  }else if(selectedTab==='stability'){
    body=target?[u,other].map(unit=>plotCard(`${unit.title} · illustrative amplitude over time`,'Synthetic demo · independent labeled axes',amplitude(unit,true))).join(''):plotCard(`${u.title} · amplitude over time`,'20 min',amplitude(u,true),'<p class="plot-note">One dot = one illustrative spike. X = recording time in minutes; Y = amplitude in source units. Teal and coral indicate illustrative families, not verified subclusters.</p>')+
      `<div class="plot-row">${plotCard('Event positions','Synthetic locations',locationPlot(u),'<p class="plot-note">One dot = one invented event position. X = lateral position; Y = depth, both in µm. Colors indicate illustrative families.</p>')}<div class="plot-card"><div class="plot-heading"><h3>Evidence provenance</h3></div><div class="tree-note"><span class="pill amber">Additional reviewer evidence</span><p style="margin-top:12px">Locations are invented for this demo and are not in the main pipeline’s observation fields. Viewing this tab is recorded separately.</p></div></div></div>`;
  }else{
    body=plotCard(`${u.title} · split tree`,'Synthetic topology · read-only',treePlot(u))+
      `<div class="annotation"><span>◈</span><p>${u.split?'Two illustrative children are available. This prototype records proposals without splitting any data.':'This node is a leaf. A further tree split is unavailable, even if the model recommends it.'}</p></div>`;
  }
  $('#evidence-content').innerHTML=body;ReviewQuality.bind($('#evidence-content'),c);if(selectedTab==='pca')ReviewPCA.attachReadonly($('#evidence-content'),c);
  attachPlotZoom($('#evidence-content'));
  syncPlotTypography();
  $$('[data-inspect]').forEach(button=>button.onclick=()=>inspectUnit(button.dataset.inspect));
  const highlightSelect=$('#highlight-cluster');
  if(highlightSelect){
    const card=highlightSelect.closest('.plot-card');
    const highlight=id=>{
      const unit=c.units.find(item=>item.id===id);card.classList.toggle('comparison-highlight',!!unit);
      card.querySelectorAll('[data-waveform-cluster]').forEach(line=>line.classList.toggle('highlighted-curve',line.dataset.waveformCluster===id));
      card.querySelectorAll('.cluster-legend').forEach(item=>item.classList.toggle('highlighted-legend',item.dataset.inspect===id));
      $('#highlight-label').textContent=unit?`${unit.title} mean · n = ${unit.spikes.toLocaleString()}. Other means are faded.`:'Each line is one cluster mean. Axes are shared.';
    };
    highlightSelect.onchange=()=>highlight(highlightSelect.value);
    $$('.cluster-legend').forEach(button=>{
      const show=()=>highlight(button.dataset.inspect),restore=()=>highlight(highlightSelect.value);
      button.onpointerenter=show;button.onpointerleave=restore;button.onfocus=show;button.onblur=restore;
    });
  }
  $$('[data-expand-unit]').forEach(button=>button.onclick=()=>{
    const unit=c.units.find(u=>u.id===button.dataset.expandUnit),holder=document.createElement('div');
    holder.innerHTML=waveform(unit);openExpandedPlot(`${unit.title} · waveform detail`,holder.firstElementChild);
    $('#modal-content .plot-heading').insertAdjacentHTML('afterend',waveformLegend(unit));
  });
  if($('#retry-evidence'))$('#retry-evidence').onclick=()=>{c.evidenceError=null;renderEvidence();};
  attachAnalysisControls();
  if($('#retry-correlograms'))$('#retry-correlograms').onclick=()=>{reviewCorrelogramRequests.delete(c.id+':'+comparisonTarget(c));renderEvidence();};
  const extra=Boolean(target)||c.review_mode==='manual'||Boolean(c.study_id)||['overview','gallery','stability','quality'].includes(selectedTab)||!(c.pair||[c.source]).includes(selectedUnitId);
  $('.evidence-footer>span:first-child').innerHTML=`<span class="teal-dot"></span>${c.study_id?'Reconstructed reviewer evidence':extra?'Additional reviewer evidence':c.synthetic===false?'Verified saved model input':'Scripted demo evidence'}`;
}
function modal(title,body){$('#info-modal').classList.remove('plot-modal');$('#modal-title').textContent=title;$('#modal-content').innerHTML=body;if(!$('#info-modal').open)$('#info-modal').showModal();}
function showPacket(){
  const c=currentCase();
  if(c.study_id){
    modal('Anonymous historical case',`<div class="modal-prose"><p><strong>${escapeHtml(agentName(c))} · matched case ${c.step_number}</strong></p><p>${escapeHtml(c.comparison_protocol_note)}</p><p>${c.rationale_available?'The retained rationale is shown verbatim.':'This archive retained an action only; no rationale has been generated.'} Human reference actions were used offline to reconstruct the shared pre-action state. Reference answers and expert terminal labels are not included in this case.</p></div><div class="packet-code">${escapeHtml(JSON.stringify({study_id:c.study_id,agent_id:c.study_agent_id,comparison_case_id:c.comparison_case_id,state_sha256:c.state_sha256,mat_sha256:c.mat_sha256,response_sha256:c.response_sha256,request_hash_status:c.request_hash_status},null,2))}</div>`);return;
  }
  if(c.synthetic===false){
    modal(c.review_mode==='manual'?'MAT input · manual review':'Saved model input · real data',`<div class="modal-prose"><p><strong>${escapeHtml(c.dataset_id)} · ${escapeHtml(c.step_id)}</strong></p><p>${c.review_mode==='manual'?'These initial MAT clusters have no model request or decision.':'The historical model received these numeric observations and the saved images.'} Expert terminal labels are excluded. The Jacob reason library is separate human-reference vocabulary and was not part of the model input. No new model request is running.</p></div><div class="packet-code">${escapeHtml(JSON.stringify({phase:c.phase_id,metrics:c.metrics,state_sha256:c.state_sha256,mat_sha256:c.mat_sha256,image_sha256:c.image_sha256,request_sha256:c.request_sha256,request_hash_status:c.request_hash_status},null,2))}</div>`);return;
  }
  modal('Scripted input · not a live request',`<div class="modal-prose"><p>No model has analyzed these signals. This is a <strong>synthetic case description</strong>, not a reproduction of a frozen main-pipeline request.</p></div><table class="packet-table"><tr><th>Recording</th><td class="mono">${c.dataset_id}</td></tr><tr><th>Snapshot</th><td class="mono">synthetic-${c.id}-v2</td></tr><tr><th>Agent</th><td>Agent 1 · scripted demo</td></tr><tr><th>AI objects</th><td>${(c.pair||[c.source]).map(id=>unitLabel(c,id)).join(' + ')}</td></tr><tr><th>Scripted evidence</th><td>Waveforms, timing, amplitude and tree availability for the AI objects</td></tr><tr><th>Extra reviewer views</th><td>All-cluster comparison, gallery, location, and other clusters</td></tr><tr><th>Recorded experiment actions</th><td>${Core.PHASE_ACTIONS[c.phase_id].join(' · ')}</td></tr><tr><th>Request/image hashes</th><td>Unavailable · no real request exists</td></tr></table><div class="packet-code">AI action: ${c.ai}\nAvailable for expert review: ${allowed(c).includes(c.ai)?'yes':'no — invalid leaf split'}</div><p class="modal-prose" style="margin-top:16px">Expert MERGE corrections may propose a different target. DEFER is saved as a review status. Neither is sent to the controller.</p>`);
}
function showUnsaved(nextId,callback=null){
  modal('Leave this unsaved review?',`<div class="modal-prose"><p>Your action or note has not been saved. You can keep editing or discard this draft and open the other case.</p></div><div style="display:flex;gap:10px;margin-top:22px"><button class="button" id="keep-editing">Keep editing</button><button class="button primary" id="discard-draft">Discard draft & switch</button></div>`);
  $('#keep-editing').onclick=()=>$('#info-modal').close();
  $('#discard-draft').onclick=()=>{clearLocalDraft(selectedId);isDirty=false;$('#info-modal').close();if(callback)callback();else selectCase(nextId,true);};
}
function showAbout(){
  if(isWorkingReview()){modal('Curation session','<p>Choose an action, inspect any projected waveforms and timing, then Confirm. Undo restores the previous grouping. Export sorting result saves per-spike assignments; Export session also saves data and history.</p>');return;}
  if(mode==='real'){
    modal('Jacob real-data review',`<div class="modal-prose"><p><strong>${escapeHtml(realCatalog.dataset_id)}</strong> · ${CASES.length} ${realCatalog.source_kind==='initial'?'initial clusters for manual review':'historical agent responses'}. Actions open directly. Confirm saves the judgment or applies the inspected grouping change; original saved states remain available.</p><p>Saved input PNGs, when present, are original model evidence. Waveform comparison, quality metrics, ISI, ACG/CCG and amplitude-over-time are reviewer evidence derived from the MAT inputs. Select Split or Merge to preview, then Confirm to apply the change. Anonymous comparison cases share a reconstructed reference state; historical model input protocols differ.</p><p>Use Data & inputs to upload/import approved MAT files, select views, manage reason files or restore feedback. Reviews, revisions, reasons and reviewer names are saved on this server.</p><p>No live model, automatic experiment or training is running.</p></div>`);return;
  }
  modal('A workspace for expert judgment',`<div class="modal-prose"><p><strong>Try it.</strong> Agree with a KEEP without a comment. Disagree with only a rationale. For the merge case, replace target 021 with 034. Explore all waveforms and the gallery.</p><p><strong>Feedback.</strong> Disagreement needs a reason or note; agreement can include an optional positive comment. New reasons can be reused in this browser.</p><p><strong>Persistence.</strong> Save, reload, undo and export preserve review events. Older v1 feedback stays untouched and is exported separately.</p><p><strong>Scope.</strong> All recordings, counts, plots and AI suggestions are synthetic. No models, MAT files, backend actions or learning jobs are connected.</p></div>`);
}

$$('[data-view]').forEach(b=>b.onclick=()=>showView(b.dataset.view));
$('.brand').onclick=e=>{e.preventDefault();showView('review');};
$$('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;$$('[data-filter]').forEach(x=>x.classList.toggle('active',x===b));renderQueue();});
$$('[data-tab]').forEach(b=>{
  b.onclick=()=>{selectedTab=b.dataset.tab;recordEvidence();renderEvidence();};
  b.onkeydown=e=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
    e.preventDefault();const tabs=$$('[data-tab]').filter(tab=>!tab.hidden),index=tabs.indexOf(b);
    const next=e.key==='Home'?tabs[0]:e.key==='End'?tabs[tabs.length-1]:tabs[(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length];
    next.click();next.focus();
  };
});
const queueFilterDisclosure=$('#queue-filters');if(queueFilterDisclosure){try{queueFilterDisclosure.open=localStorage.getItem('spike-review-filters-hidden')!=='true';}catch{}queueFilterDisclosure.addEventListener('toggle',()=>{try{localStorage.setItem('spike-review-filters-hidden',String(!queueFilterDisclosure.open));}catch{}});}
$('#case-search').addEventListener('input',renderQueue);

$('#phase-filter').addEventListener('change',renderQueue);
$('#clear-queue-filters').onclick=resetQueueFilters;
$('#reason-select').addEventListener('input',()=>{isDirty=true;$('#form-error').textContent='';queryReasons();});
$('#reason-select').addEventListener('focus',queryReasons);
$('#reason-select').addEventListener('blur',hideReasonMatches);
$('#reason-select').addEventListener('keydown',e=>{
  if(e.key==='Escape'){e.preventDefault();hideReasonMatches();return;}
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){
    e.preventDefault();if($('#reason-suggestions').hidden)showReasonMatches();if(!reasonMatches.length)return;
    reasonIndex=(reasonIndex+(e.key==='ArrowDown'?1:-1)+reasonMatches.length)%reasonMatches.length;
    $$('[data-reason-index]').forEach((b,i)=>b.setAttribute('aria-selected',String(i===reasonIndex)));
    $('#reason-select').setAttribute('aria-activedescendant',`reason-option-${reasonIndex}`);
    document.getElementById(`reason-option-${reasonIndex}`).scrollIntoView({block:'nearest'});
  }else if(e.key==='Enter'&&!$('#reason-suggestions').hidden&&reasonIndex>=0){e.preventDefault();chooseReason(reasonIndex);}
});
$('#data-source').onchange=()=>{
  const next=$('#data-source').value;$('#data-source').value=queueFileSelection;
  if(saving)return;
  const perform=async()=>{
    const previous=queueFileSelection;queueFileSelection=next;
    const target=next==='all'?null:next==='demo'?'demo':queueBundles().find(b=>b.dataset_id===next);
    if(target==='demo'){if(!await switchSource('demo'))queueFileSelection=previous;}
    else if(target&&!(mode==='real'&&realCatalog.dataset_id===next)){if(!await switchSource(choiceForBundle(target.id)))queueFileSelection=previous;}
    if(next!=='all')expandedQueueFiles.add(next);$('#data-source').value=queueFileSelection;renderQueue();
  };
  if(isDirty)showUnsaved(null,perform);else perform();
};
$('#review-note').addEventListener('input',()=>{isDirty=true;$('#form-error').textContent='';});
$('#positive-comment').addEventListener('input',()=>{isDirty=true;});
$('#reviewer-name').value=state.annotator;
$('#agree-button').onclick=()=>setAgreement(true);
$('#disagree-button').onclick=()=>setAgreement(false);
$('#merge-target').onchange=()=>{draft.target=$('#merge-target').value;isDirty=true;$('#form-error').textContent='';selectComparison(draft.target);};
for(const type of ['rationale','evidence'])$('#disagree-'+type).onchange=()=>{
  draft.disagreement_types=['rationale','evidence'].filter(t=>$('#disagree-'+t).checked);isDirty=true;$('#form-error').textContent='';renderActions();
};
$('#save-button').onclick=saveReview;
$('#queue-mode').onchange=()=>changeQueueMode($('#queue-mode').value);
$('#defer-button').onclick=()=>{if(isWorkingReview())return WorkingReview.choose('DEFER');setAgreement(false);draft.action='DEFER';draft.sufficiency='insufficient';$('#evidence-sufficiency').value='insufficient';renderActions();renderEvidence();$('#missing-evidence-note').focus();toast('Describe the missing evidence, then confirm.');};
$('#judgment-confidence').onchange=()=>{draft.confidence=$('#judgment-confidence').value||null;isDirty=true;$('#form-error').textContent='';};
$('#evidence-sufficiency').onchange=()=>{draft.sufficiency=$('#evidence-sufficiency').value;if(draft.sufficiency!=='insufficient'){draft.missing=[];draft.missing_note='';}isDirty=true;$('#form-error').textContent='';renderJudgmentDetails();};
$$('.missing-evidence-options input').forEach(input=>input.onchange=()=>{draft.missing=$$('.missing-evidence-options input:checked').map(item=>item.value);isDirty=true;$('#form-error').textContent='';});
$('#missing-evidence-note').oninput=()=>{draft.missing_note=$('#missing-evidence-note').value;isDirty=true;$('#form-error').textContent='';};
for(const field of ['review-note','positive-comment','reason-select','missing-evidence-note'])$('#'+field).addEventListener('input',renderChoiceSummaries);
for(const field of ['judgment-confidence','evidence-sufficiency','disagree-rationale','disagree-evidence'])$('#'+field).addEventListener('change',renderChoiceSummaries);
$$('.missing-evidence-options input').forEach(input=>input.addEventListener('change',renderChoiceSummaries));
$('#export-button').onclick=exportFeedback;$('#history-export').onclick=exportFeedback;
$('#history-link').onclick=()=>showView('history');
$('#packet-button').onclick=showPacket;
$('#tour-button').onclick=showAbout;
$('#close-modal').onclick=()=>$('#info-modal').close();
$('#info-modal').addEventListener('click',e=>{if(e.target===$('#info-modal')){const r=$('#info-modal').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('#info-modal').close();}});
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key==='Enter'&&currentView==='review'&&!document.querySelector('dialog[open]')){e.preventDefault();saveReview();}
});
window.addEventListener('beforeunload',e=>{cacheLocalDraft();if(isDirty){e.preventDefault();e.returnValue='';}});
window.addEventListener('resize',()=>{syncPlotTypography();$$('.plot-viewport').forEach(v=>v.dispatchEvent(new Event('plotresize')));});
populateQueueFiles();
selectCase(lastSelectedCase()||selectedId,true);
showView(location.hash.replace('#','')||'review');
$('#storage-warning').hidden=storageAvailable;
if(!storageAvailable)toast('Browser storage unavailable. Export feedback before closing.');
window.addEventListener('DOMContentLoaded',()=>connectBackend().finally(()=>WorkingReview.mount()),{once:true});
