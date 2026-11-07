/* Default fast path: two public channels, local execution and IndexedDB persistence. */
(()=>{
 'use strict';if(!window.PublicData?.configured)return;
 const panel=document.createElement('section');panel.id='public-load-panel';panel.setAttribute('role','status');panel.innerHTML='<span id="public-load-status">Loading data…</span><progress id="public-load-progress" aria-label="Loading data" max="100" value="0"></progress><span id="public-load-recovery" hidden><button id="public-load-retry">Retry</button> <a href="data-sources.html">Other data & uploads</a></span>';$('#view-review').prepend(panel);$('#public-load-retry').onclick=()=>location.reload();
 const workspace=$('#view-review .workspace');let loadingTimer;
 function begin(){clearTimeout(loadingTimer);document.body.classList.add('public-loading');document.body.classList.remove('public-load-visible','public-load-error');panel.setAttribute('role','status');$('#public-load-recovery').hidden=true;workspace.inert=true;workspace.setAttribute('aria-busy','true');loadingTimer=setTimeout(()=>document.body.classList.add('public-load-visible'),650);}
 function finish(){clearTimeout(loadingTimer);$('#public-load-progress').value=100;document.body.classList.remove('public-starting','public-loading','public-load-visible','public-load-error');document.querySelector('#primary-navigation')?.removeAttribute('inert');workspace.inert=false;workspace.removeAttribute('aria-busy');}
 function fail(error){clearTimeout(loadingTimer);$('#public-load-status').textContent=error.message;$('#public-load-progress').removeAttribute('value');$('#public-load-recovery').hidden=false;panel.setAttribute('role','alert');document.body.classList.add('public-load-visible','public-load-error');if(WorkingReview.active){workspace.inert=false;workspace.removeAttribute('aria-busy');}}
 window.PublicLoading={begin,finish,fail};begin();
 const mount=WorkingReview.mount;
 WorkingReview.mount=async function(){
  try{await PublicData.ready();await mount();$('#inline-start-working').onclick=()=>location.href='data-sources.html';$('#manage-inputs').hidden=true;
   const original=activateReviewer;activateReviewer=async function(...args){const previous=WorkingReview.state?.dataset_id,result=await original(...args);await WorkingReview.openPublic(PublicData.has(previous)?previous:PublicData.rows[0].dataset_id);return result;};
   const params=new URLSearchParams(location.search),wanted=params.get('public');
   if(wanted&&!PublicData.has(wanted))throw Error('This public demo contains CH3 and CH31 only');
   if(!WorkingReview.active||wanted&&WorkingReview.state.dataset_id!==wanted){let previous;try{previous=localStorage.getItem('spikeagent-public-dataset:'+reviewerId);}catch{}await WorkingReview.openPublic(wanted||(PublicData.has(previous)?previous:PublicData.rows[0].dataset_id));}
   if(!WorkingReview.active)throw Error('Dataset could not be opened. Retry the download.');
   const requestedView=location.hash.slice(1);if(['history','revisions'].includes(requestedView))showView(requestedView);
   finish();
   // Warm the other published channel without activating it or touching reviewer state.
   const preload=async()=>{document.body.dataset.publicPreload='loading';try{for(const row of PublicData.rows)if(row.dataset_id!==WorkingReview.state?.dataset_id)await WorkingReview.cachePublic(row.dataset_id);document.body.dataset.publicPreload='ready';}catch{document.body.dataset.publicPreload='failed';}};
   if(window.requestIdleCallback)requestIdleCallback(preload,{timeout:2500});else setTimeout(preload,1000);
  }catch(e){fail(e);}
 };
})();
