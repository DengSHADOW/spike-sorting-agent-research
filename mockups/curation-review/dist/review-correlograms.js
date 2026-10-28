'use strict';
const reviewCorrelogramRequests=new Map();
function correlogramPlot(data,color,ymax){
 const w=610,h=250,maximum=ymax||Math.max(1,...data.counts),box=axes(w,h,'Lag (ms)','Spike-pair count',['−50','−25','0','25','50'],Array.from({length:5},(_,i)=>maximum*(1-i/4)>=1000?Number((maximum*(1-i/4)/1000).toPrecision(3))+'k':tickLabel(maximum*(1-i/4))));
 const bars=data.counts.map((n,i)=>`<rect x="${box.l+i/100*box.iw}" y="${box.t+(1-n/maximum)*box.ih}" width="${box.iw/100}" height="${n/maximum*box.ih}" fill="${color}"/>`).join('');
 return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${data.self_pairs_excluded?'Autocorrelogram':'Cross-correlogram'} ${data.source}${data.self_pairs_excluded?'':' to '+data.target}">${box.markup}${bars}<path d="M${box.l+box.iw/2} ${box.t}v${box.ih}" stroke="#243a4c" stroke-width="1" stroke-dasharray="3 3"/></svg>`;
}
function renderReviewCorrelograms(c){
 if(c.synthetic!==false)return '<p class="tree-note">ACG/CCG unavailable in the synthetic demo.</p>';
 const target=comparisonTarget(c),query=new URLSearchParams({cluster:c.source});if(target)query.set('target',target);
 const key=c.id+':'+target;
 if(!reviewCorrelogramRequests.has(key)){
  const entry={data:null,error:null};reviewCorrelogramRequests.set(key,entry);while(reviewCorrelogramRequests.size>64)reviewCorrelogramRequests.delete(reviewCorrelogramRequests.keys().next().value);
  api('/api/correlograms/'+encodeURIComponent(c.id)+'?'+query).then(data=>{
   const source=sourceUnit(c),other=c.units.find(u=>u.id===target);
   if(data.schema!=='review-correlograms-v1'||data.case_id!==c.id||data.dataset_id!==c.dataset_id||data.state_sha256!==c.state_sha256||data.mat_sha256!==c.mat_sha256||data.source_cluster!==source.cluster_id||data.target_cluster!==(other?.cluster_id??null)||data.acg?.length!==(other?2:1))throw Error('Correlogram checkpoint/object mismatch');
   const us=[source,...(other?[other]:[])],valid=d=>d&&d.counts?.length===100&&d.counts.every(n=>Number.isSafeInteger(n)&&n>=0)&&d.min_ms===-50&&d.max_ms===50&&d.bin_ms===1;
   data.acg.forEach((d,i)=>{if(!valid(d)||!d.self_pairs_excluded||d.source!==us[i].cluster_id||d.target!==us[i].cluster_id||d.n_source!==us[i].spikes||d.n_target!==us[i].spikes)throw Error('ACG member-count mismatch');});
   if(other&&(!valid(data.ccg)||data.ccg.self_pairs_excluded||data.ccg.source!==source.cluster_id||data.ccg.target!==other.cluster_id||data.ccg.n_source!==source.spikes||data.ccg.n_target!==other.spikes))throw Error('CCG member-count mismatch');
   entry.data=data;if(currentCase()===c&&selectedTab==='correlation'&&comparisonTarget(c)===target)renderEvidence();
  }).catch(error=>{entry.error=error.message;if(currentCase()===c&&selectedTab==='correlation')renderEvidence();});
 }
 const entry=reviewCorrelogramRequests.get(key);
 if(!entry.data)return `<div class="analysis-message" role="status">${entry.error?'Could not load ACG/CCG: '+escapeHtml(entry.error):'Loading exact all-spike ACG/CCG counts…'}${entry.error?'<button class="button small" id="retry-correlograms">Retry</button>':''}</div>`;
 const data=entry.data;
 return `<div class="correlogram-evidence" data-ready="true" data-state-sha="${escapeHtml(c.state_sha256)}" data-source="${data.source_cluster}" data-target="${data.target_cluster??''}"><p class="tree-note">All spike pairs · −50 ≤ lag &lt; 50 ms · 1 ms bins · raw counts.</p><div class="${data.acg.length===2?'plot-row':''}">${data.acg.map((d,i)=>plotCard(`Cluster ${d.source} · ACG`,`${d.n_source.toLocaleString()} spikes · own count scale`,correlogramPlot(d,FULL_WAVEFORM_COLORS[i]))).join('')}</div>${data.ccg?plotCard(`Cluster ${data.ccg.source} → ${data.ccg.target} · CCG`,'Positive lag: comparison-cluster spike occurs later',correlogramPlot(data.ccg,'#5c3d9d')):'<p class="tree-note">Select a second cluster to view CCG (Compare with or Shift-click).</p>'}<details class="evidence-details"><summary>ACG / CCG definitions</summary><p>ACG compares spike times within one cluster; CCG compares two clusters. Counts use every spike pair in this saved state, without sampling or time rounding. ACG excludes each event paired with itself and retains distinct simultaneous events. ISI uses adjacent spikes only. Timing alone does not establish cell identity or approve a merge.</p></details></div>`;
}
