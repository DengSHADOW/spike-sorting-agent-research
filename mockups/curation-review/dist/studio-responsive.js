/* Fit the existing Studio to a scaled desktop without changing curation state. */
(()=>{
 'use strict';
 const compact=matchMedia('(min-width:761px) and (max-width:1100px), (min-width:761px) and (max-width:1440px) and (max-height:620px)');
 const body=document.body,panel=document.querySelector('#decision-panel'),content=document.querySelector('#studio-wave-content');
 const review=document.createElement('button');review.id='studio-review-toggle';review.type='button';review.className='button primary';review.textContent='Review & actions';review.setAttribute('aria-controls','decision-panel');review.setAttribute('aria-expanded','false');document.querySelector('.top-actions').prepend(review);
 const close=document.createElement('button');close.id='studio-review-close';close.type='button';close.className='button small';close.textContent='Back to charts';document.querySelector('.decision-heading').prepend(close);
 const focus=document.createElement('button');focus.id='studio-charts-focus';focus.type='button';focus.className='button small';focus.setAttribute('aria-controls','case-list');document.querySelector('.queue-heading').append(focus);
 function toggleReview(open,restoreFocus=false){body.classList.toggle('studio-review-open',open);review.setAttribute('aria-expanded',String(open));review.textContent=open?'Back to charts':'Review & actions';if(open){panel.scrollTop=0;close.focus();}else if(restoreFocus)review.focus();}
 review.onclick=()=>toggleReview(!body.classList.contains('studio-review-open'));
 close.onclick=()=>toggleReview(false,true);
 panel.addEventListener('keydown',e=>{if(e.key==='Escape'&&compact.matches&&!document.querySelector('dialog[open]')){e.preventDefault();toggleReview(false,true);}});
 // A focus view keeps cluster selection available while giving both plots more height.
 let focusChosen=false;
 function setFocus(value,chosen=false){if(chosen)focusChosen=true;body.classList.toggle('studio-charts-focus',value);focus.textContent=value?'Show clusters':'Focus charts';focus.setAttribute('aria-expanded',String(!value));schedule();}
 focus.onclick=()=>setFocus(!body.classList.contains('studio-charts-focus'),true);
 function layout(){body.classList.toggle('studio-compact-screen',compact.matches);if(!compact.matches)toggleReview(false);if(innerWidth<=760)setFocus(false);else if(!focusChosen)setFocus(compact.matches&&innerHeight<=580);schedule();}
 let frame=0;
 function schedule(){cancelAnimationFrame(frame);frame=requestAnimationFrame(fit);}
 function fit(){
  if(!content.clientHeight)return;
  for(const canvas of content.querySelectorAll('.inline-working-wave canvas')){
   if(compact.matches||innerHeight<=800){
    const above=canvas.getBoundingClientRect().top-content.getBoundingClientRect().top+content.scrollTop;
    const height=Math.round(Math.max(150,Math.min(420,content.clientHeight-above-29)));
    canvas.dataset.fitHeight=String(height);canvas.style.height=height+'px';
   }else{delete canvas.dataset.fitHeight;canvas.style.height='';}
  }
  WorkingReview.resizeWaveforms?.();
 }
 new ResizeObserver(schedule).observe(content);
 new MutationObserver(schedule).observe(content,{childList:true,subtree:true});
 document.addEventListener('click',e=>{
  if(!compact.matches)return;
  if(e.target.closest('#case-list [data-working-cluster]')&&innerHeight<=580){requestAnimationFrame(()=>setFocus(true));return;}
  if(!e.target.closest('#action-grid [data-action],#studio-pair-picker [data-studio-target]'))return;
  requestAnimationFrame(()=>{if(WorkingReview.preview){setFocus(true);toggleReview(false);}else if(window.ReviewStudio?.pendingMerge){setFocus(false,true);toggleReview(false);}});
 },true);
 new MutationObserver(()=>{if(compact.matches&&!body.classList.contains('studio-review-open'))review.textContent=WorkingReview.preview?'Review & confirm':'Review & actions';}).observe(document.querySelector('#save-button'),{childList:true});
 compact.addEventListener('change',layout);window.addEventListener('resize',layout);
 // Keep the panel from covering another view when navigating away from Studio.
 new MutationObserver(()=>{if(!document.querySelector('#view-review.active'))toggleReview(false);}).observe(document.querySelector('#view-review'),{attributes:true,attributeFilter:['class']});
 setFocus(false);layout();
})();
