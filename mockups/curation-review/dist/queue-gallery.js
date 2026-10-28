'use strict';
let queueSort='spikes',queueGalleryObserver=null;
try{if(['isi','quality'].includes(localStorage.getItem('spike-review-queue-sort')))queueSort=localStorage.getItem('spike-review-queue-sort');}catch{}
const queueQuality=new Map(),queueEvidenceRequests=new Map();
const queueQualityKey=(c,u=sourceUnit(c))=>[c.dataset_id,c.state_sha256,u.cluster_id].join(':');
function queueThumbnailMarkup(c,grouped=false){
 const u=sourceUnit(c),quality=queueQuality.get(queueQualityKey(c)),target=c.id===selectedId?comparisonTarget(currentCase()):'';
 return `<span class="queue-thumb-title">${escapeHtml(u.title)}</span><span class="queue-wave-preview"><canvas width="360" height="160" data-queue-wave="${escapeHtml(c.id)}" role="img" aria-label="${escapeHtml(u.title)} all member waveform thumbnail"></canvas><span class="queue-wave-loading">${c.synthetic===false?'Loading…':'Synthetic demo'}</span></span><span class="queue-thumb-count">${u.spikes.toLocaleString()} spikes</span><span class="queue-thumb-quality">${queueSort==='quality'?'Quality '+escapeHtml(ReviewQuality.cell(c,u.cluster_id)):quality==null?'ISI —':`ISI ${(quality*100).toFixed(2)}%`}</span>${target?`<span class="queue-comparing">vs. ${escapeHtml(unitLabel(c,target))}</span>`:''}${grouped?`<span class="queue-state">State ${escapeHtml(c.state_sha256?.slice(0,8)||'demo')}</span>`:''}`;
}
function queueSortEntries(rows){
 return [...rows].sort((a,b)=>{const ua=sourceUnit(a.c),ub=sourceUnit(b.c),qa=queueQuality.get(queueQualityKey(a.c))??Infinity,qb=queueQuality.get(queueQualityKey(b.c))??Infinity;const quality=typeof ReviewQuality!=='undefined'?ReviewQuality.rank(a.c,ua.cluster_id)-ReviewQuality.rank(b.c,ub.cluster_id):0;return (queueSort==='quality'?quality||ub.spikes-ua.spikes:queueSort==='isi'?qa-qb||ub.spikes-ua.spikes:ub.spikes-ua.spikes||quality||qa-qb)||ua.cluster_id-ub.cluster_id||a.c.id.localeCompare(b.c.id);});
}
async function ensureQueueQuality(c){
 if(c.synthetic!==false)return;
 const key=c.dataset_id+':'+c.state_sha256;
 if(!queueEvidenceRequests.has(key))queueEvidenceRequests.set(key,api(c.evidence_url).then(payload=>{for(const u of payload.units){const rate=u.isi?.violation_rate;queueQuality.set([c.dataset_id,c.state_sha256,u.cluster_id].join(':'),Number.isFinite(rate)?rate:null);}return payload;}).catch(error=>{queueEvidenceRequests.delete(key);throw error;}));
 return queueEvidenceRequests.get(key);
}
async function paintQueueThumbnail(canvas,c){
 if(canvas.dataset.started||c.synthetic!==false)return;canvas.dataset.started='true';
 const button=canvas.closest('.case-item'),status=canvas.nextElementSibling,u=sourceUnit(c);
 ensureQueueQuality(c).then(()=>{if(button?.isConnected){const rate=queueQuality.get(queueQualityKey(c));button.querySelector('.queue-thumb-quality').textContent=queueSort==='quality'?'Quality '+ReviewQuality.cell(c,u.cluster_id):rate==null?'ISI not defined':`ISI ${(rate*100).toFixed(2)}%`;}}).catch(()=>{if(button?.isConnected)button.querySelector('.queue-thumb-quality').textContent='ISI unavailable';});
 try{
  const state=await pairMemberState(c);if(!canvas.isConnected)return;
  const population=state.units[String(u.cluster_id)],atoms=population?.atoms.map(id=>state.atoms[id]);
  if(!population||population.n_spikes!==u.spikes||atoms.some(a=>!a)||atoms.reduce((n,a)=>n+a.n_waveforms,0)!==u.spikes)throw Error('Membership mismatch');
  const all=Object.values(state.atoms),bottom=Math.min(...all.map(a=>a.raw_low)),top=Math.max(...all.map(a=>a.raw_high)),pad=Math.max((top-bottom)*.08,1e-6),low=bottom-pad,high=top+pad,ctx=canvas.getContext('2d'),tint=document.createElement('canvas');tint.width=360;tint.height=160;const tx=tint.getContext('2d');
  const active=currentCase(),same=c.dataset_id===active.dataset_id&&c.state_sha256===active.state_sha256,other=active.units.find(unit=>unit.id===comparisonTarget(active)),candidate=same&&other?.cluster_id===u.cluster_id,color=candidate?FULL_WAVEFORM_COLORS[1]:FULL_WAVEFORM_COLORS[0];
  canvas.closest('.case-item')?.classList.toggle('comparison-target',candidate);
  ctx.clearRect(0,0,360,160);ctx.strokeStyle='#e0e8ef';ctx.lineWidth=1;for(const y of [40,80,120]){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(360,y);ctx.stroke();}
  for(const atom of atoms){const bitmap=await createImageBitmap(await pairMemberMask(atom));if(!canvas.isConnected){bitmap.close();return;}tx.globalCompositeOperation='source-over';tx.clearRect(0,0,360,160);tx.drawImage(bitmap,0,0,360,160);bitmap.close();tx.globalCompositeOperation='source-in';tx.fillStyle=color;tx.fillRect(0,0,360,160);const pixels=tx.getImageData(0,0,360,160);for(let i=3;i<pixels.data.length;i+=4)pixels.data[i]=Math.min(255,pixels.data[i]*3);tx.putImageData(pixels,0,0);ctx.drawImage(tint,0,(high-atom.image_high)/(high-low)*160,360,(atom.image_high-atom.image_low)/(high-low)*160);}
  canvas.dataset.ready='true';canvas.dataset.members=String(u.spikes);canvas.dataset.stateSha=c.state_sha256;status.hidden=true;
 }catch(error){if(canvas.isConnected){status.textContent='Waveforms unavailable';canvas.setAttribute('aria-label',u.title+' waveform unavailable: '+error.message);canvas.dataset.error=error.message;}}
}
function attachQueueGallery(){
 queueGalleryObserver?.disconnect();const rows=new Map(queueEntries().map(row=>[row.c.id,row]));
 queueGalleryObserver=new IntersectionObserver(items=>{for(const item of items)if(item.isIntersecting){queueGalleryObserver.unobserve(item.target);const row=rows.get(item.target.dataset.queueWave);if(row)paintQueueThumbnail(item.target,row.c);}},{root:$('#queue-panel'),rootMargin:'120px'});
 for(const canvas of document.querySelectorAll('[data-queue-wave]'))queueGalleryObserver.observe(canvas);
 for(const button of document.querySelectorAll('#case-list [data-case]'))button.onclick=event=>{
  const row=rows.get(button.dataset.case);if(!row)return;
  if(!event.shiftKey){openQueueCase(button.dataset.case,button.dataset.bundle);return;}
  const active=currentCase(),u=sourceUnit(row.c);
  if(active.dataset_id!==row.c.dataset_id||active.state_sha256!==row.c.state_sha256||!active.units.some(unit=>unit.cluster_id===u.cluster_id)){toast('Choose two clusters from the same recording and saved state. Use Compare with for candidates in the current state.');return;}
  selectComparison(u.id===active.source?'':u.id);
 };
 const active=currentCase();if(active?.synthetic===false&&active.review_mode!=='manual')for(const button of document.querySelectorAll('#case-list [data-case]')){const row=rows.get(button.dataset.case);if(!row)continue;const cluster=sourceUnit(row.c);const affects=row.c.dataset_id===active.dataset_id&&row.c.state_sha256===active.state_sha256&&[active.source,...(active.pair||[])].some(id=>active.units.find(u=>u.id===id)?.cluster_id===cluster.cluster_id);button.classList.toggle('agent-object',affects);if(affects){const tag=document.createElement('span');tag.className='agent-object-label';tag.textContent='AI · '+active.ai;tag.title='Saved Agent recommendation affects this cluster in this exact state';button.append(tag);}}
 const sort=$('#queue-sort');sort.value=queueSort;sort.disabled=false;
 $('#queue-sort-status').textContent=queueSort==='quality'?'Imported scores · source direction · missing last.':queueSort==='isi'?'ISI <2 ms · lower first · missing last.':'Shift-click a second cluster to compare.';
 sort.onchange=()=>{queueSort=sort.value;try{localStorage.setItem('spike-review-queue-sort',queueSort);}catch{}renderQueue();};
 if(queueSort==='isi'){
  const needed=queueScope().filter(c=>c.synthetic===false&&expandedQueueFiles.has(queueFile(c).key)&&!queueQuality.has(queueQualityKey(c))),unique=new Map(needed.map(c=>[c.dataset_id+':'+c.state_sha256,c]));
  if(unique.size){
   sort.disabled=true;$('#queue-sort-status').textContent='Loading ISI statistics…';const work=[...unique.values()];let cursor=0;
   Promise.all(Array.from({length:Math.min(4,work.length)},async()=>{while(cursor<work.length){const c=work[cursor++];try{await ensureQueueQuality(c);}catch{for(const u of c.units)queueQuality.set(queueQualityKey(c,u),null);}}})).then(()=>{if(queueSort==='isi')renderQueue();});
  }
 }
}

async function loadExpandedQueueQuality(file){
 if(queueSort!=='isi')return;
 const rows=queueEntries().filter(row=>row.file===file&&row.c.synthetic===false&&!queueQuality.has(queueQualityKey(row.c))),work=[...new Map(rows.map(row=>[row.c.state_sha256,row.c])).values()];
 if(!work.length)return;
 let cursor=0;await Promise.all(Array.from({length:Math.min(4,work.length)},async()=>{while(cursor<work.length){const c=work[cursor++];try{await ensureQueueQuality(c);}catch{for(const u of c.units)queueQuality.set(queueQualityKey(c,u),null);}}}));if(queueSort==='isi'&&expandedQueueFiles.has(file))renderQueue();
}
