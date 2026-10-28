'use strict';
let generalReasonRows=[],generalReasonLoadedFor='',generalReasonLoadingFor='',generalReasonEpoch=0;
function resetGeneralReasonLibrary(){generalReasonEpoch++;generalReasonLoadedFor='';generalReasonLoadingFor='';}
function renderGeneralReasons(){
 const select=$('#general-reason-choice');if(!select)return;
 const original=$('#reason-select').value,note=$('#review-note').value,c=currentCase(),context=[c?.id||'initializing',c?comparisonTarget(c)||'':''].join(':'),explicit=select.dataset.reasonContext===context&&isDirty?select.value:null,jacob=r=>(r.sources||[]).some(s=>s.kind==='jacob')||String(r.provenance||'').startsWith('jacob');
 generalReasonRows=CurationReasons.library(state.reasons.filter(jacob),state.reasons.filter(r=>!jacob(r)));
 select.innerHTML='<option value="" disabled>Choose your reason</option><option value="__new__">Type new reason…</option>'+generalReasonRows.map((r,i)=>`<option value="${i}">${escapeHtml(r.text)}</option>`).join('');
 const found=generalReasonRows.findIndex(r=>r.text===original);select.value=explicit==='__new__'?'__new__':explicit!==null?(found>=0?String(found):original?'__new__':''):note?'__new__':found>=0?String(found):original?'__new__':'';select.dataset.reasonContext=context;
 $('#custom-reason-input').hidden=select.value!=='__new__';
 select.onchange=()=>{
  const custom=select.value==='__new__',row=generalReasonRows[Number(select.value)];
  $('#custom-reason-input').hidden=!custom;
  if(!custom)$('#reason-select').value=select.value!==''&&row?row.text:'';
  else if(generalReasonRows.some(r=>r.text===$('#reason-select').value))$('#reason-select').value='';
  draft.reason=$('#reason-select').value;isDirty=true;$('#form-error').textContent='';hideReasonMatches();renderChoiceSummaries();cacheLocalDraft();if(custom)$('#reason-select').focus();
 };
 const owner=reviewerId+':'+mode;
 if(backend&&mode==='real'&&generalReasonLoadedFor!==owner&&generalReasonLoadingFor!==owner){
  generalReasonLoadingFor=owner;const epoch=generalReasonEpoch;
  api('/api/reasons/export').then(result=>{if(epoch!==generalReasonEpoch||owner!==reviewerId+':'+mode)return;mergeReasons(result.reasons);generalReasonLoadedFor=owner;generalReasonLoadingFor='';renderGeneralReasons();}).catch(()=>{if(epoch!==generalReasonEpoch)return;generalReasonLoadingFor='';$('#general-reason-status').textContent='Some saved reasons could not load. Existing input and notes remain available.';});
 }
}
