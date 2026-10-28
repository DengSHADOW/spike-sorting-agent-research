'use strict';

// Reviewer-only calculations. Nothing here executes a merge or calls a model.
const reviewerAnalyses=new Map(),comparisonChoices=new Map(),comparisonExposure=new Map();
function resetAnalysisExposure(id){comparisonExposure.set(id,new Set());}
function comparedPairs(c){return [...(comparisonExposure.get(c.id)||[])].map(target=>[c.source,target]);}
function comparisonTarget(c){
  const candidates=Core.candidates(c),valid=id=>candidates.some(u=>u.id===id);
  if(comparisonChoices.has(c.id))return valid(comparisonChoices.get(c.id))?comparisonChoices.get(c.id):'';
  return valid(c.pair?.[1])?c.pair[1]:'';
}
function comparisonName(c,target=comparisonTarget(c)){
  return sourceUnit(c).title+(target?' vs. '+unitLabel(c,target):'');
}
function selectComparison(target,linkDraft=true){
  if(typeof WorkingReview!=='undefined'&&WorkingReview.active)return WorkingReview.selectComparison(target);
  const c=currentCase(),valid=Core.candidates(c).some(u=>u.id===target);target=valid?target:'';
  comparisonChoices.set(c.id,target);selectedUnitId=c.source;if(!['detail','quality','timing','correlation','stability','pca'].includes(selectedTab))selectedTab='detail';
  if(linkDraft&&canEditCurrentReview()){
    const scope=target?'pair':'single',changed=draft.scope!==scope||draft.target!==target;
    draft.scope=scope;draft.target=target;
    if(changed){draft.agree=c.review_mode==='manual'?null:false;draft.action=null;isDirty=true;}
  }
  if(valid)tabsViewed.add('pair');
  recordEvidence();renderQueue();renderCase();
}
function reviewerAnalysis(c,cluster,target=null){
  const key=JSON.stringify([c.id,cluster,target]);
  if(!reviewerAnalyses.has(key)){
    const entry={data:null,error:null};reviewerAnalyses.set(key,entry);
    while(reviewerAnalyses.size>128)reviewerAnalyses.delete(reviewerAnalyses.keys().next().value);
    const query=new URLSearchParams({cluster});if(target)query.set('target',target);
    api('/api/analysis/'+encodeURIComponent(c.id)+'?'+query).then(data=>{
      if(data.case_id!==c.id||data.state_sha256!==c.state_sha256)throw new Error('Analysis belongs to a different checkpoint.');
      if(data.source_cluster!==c.units.find(u=>u.id===cluster)?.cluster_id||data.target_cluster!==(target?c.units.find(u=>u.id===target)?.cluster_id:null))throw new Error('Analysis belongs to a different candidate pair.');
      entry.data=data;if(currentCase()===c&&(window.ReviewStudio?.active||['detail','quality','timing'].includes(selectedTab)))renderEvidence();
    }).catch(error=>{entry.error=error.message;if(currentCase()===c&&(window.ReviewStudio?.active||['detail','quality','timing'].includes(selectedTab)))renderEvidence();});
  }
  return {key,...reviewerAnalyses.get(key)};
}
function analysisPending(entry){
  return `<div class="analysis-message" role="status">${entry.error?'Could not calculate this evidence: '+escapeHtml(entry.error):'Calculating from all member spikes in the verified checkpoint…'}${entry.error?`<button class="button small" data-retry-analysis="${escapeHtml(entry.key)}">Retry calculation</button>`:''}</div>`;
}
const metricNumber=value=>value==null?'Not defined':Number(value).toLocaleString(undefined,{maximumFractionDigits:3});
const metricPercent=value=>value==null?'Not defined':(value*100).toFixed(3)+'%';
function populationMetrics(units){
  const rows=[
    ['Input / imported quality','Source-supplied score for this exact recording and grouping; never inferred from ISI.',u=>escapeHtml(ReviewQuality.cell(currentCase(),u.cluster_id))],
    ['Member spikes','All spikes assigned to this cluster in the current checkpoint.',u=>u.n_spikes.toLocaleString()],
    ['ISI violations','Adjacent sorted intervals < 2 ms / all N−1 intervals. This is not an estimated contamination fraction.',u=>`${metricPercent(u.isi.violation_rate)} (${u.isi.violation_count.toLocaleString()} / ${u.isi.denominator.toLocaleString()})`],
    ['Peak-to-trough amplitude','Mean ± population SD of per-spike peak-to-trough amplitude; source units.',u=>`${metricNumber(u.amplitude.mean_peak_to_trough)} ± ${metricNumber(u.amplitude.std_peak_to_trough)}`],
    ['Amplitude CV','Population amplitude SD / mean, using all spikes. No automatic acceptance cutoff.',u=>metricNumber(u.amplitude.cv)],
    ['Amplitude change','Last 20% vs first 20% of spikes by time. Unequal-duration windows; descriptive drift measure.',u=>u.drift.delta_percent==null?'Not defined':`${u.drift.delta_percent>=0?'+':''}${u.drift.delta_percent.toFixed(2)}%`]
  ];
  const qualityFile=ReviewQuality.load(currentCase());return ReviewQuality.controls()+(qualityFile?'<p class="plot-note">'+escapeHtml(qualityFile.metric+' · '+qualityFile.provenance+' · '+qualityFile.direction+' is better')+'</p>':'<p class="plot-note">Native sorter score: Not provided by input.</p>')+`<div class="quality-table-wrap"><table class="quality-table"><thead><tr><th>Metric</th>${units.map(u=>`<th>Cluster ${u.cluster_id}</th>`).join('')}</tr></thead><tbody>${rows.map(([label,note,value])=>`<tr><th title="${escapeHtml(note)}">${label}</th>${units.map(u=>`<td>${value(u)}</td>`).join('')}</tr>`).join('')}</tbody></table></div><details class="evidence-details"><summary>Metric definitions</summary><dl>${rows.map(([label,note])=>`<dt>${label}</dt><dd>${note}</dd>`).join('')}</dl></details>`;
}
// Full-member raster layers are generated from each verified snapshot's raw
// waveforms. Only lossless images and membership/count metadata are public.
const pairMemberStates=new Map(),pairMemberMasks=new Map();
function pairMemberState(c){const key=c.dataset_id+'--'+c.state_sha256;if(!pairMemberStates.has(key)){const value=fetch('/review-waveforms/'+encodeURIComponent(key)+'.json',{cache:'no-cache'}).then(async r=>{if(!r.ok)throw Error('Full waveform evidence is unavailable ('+r.status+')');const v=await r.json();if(v.schema!=='review-all-waveform-layers-v1'||v.dataset_id!==c.dataset_id||v.state_sha256!==c.state_sha256||v.mat_sha256!==c.mat_sha256||!v.all_members||v.random_sampling||v.mean||v.sd_band)throw Error('Full waveform evidence does not match this snapshot');return v;});pairMemberStates.set(key,value);while(pairMemberStates.size>8)pairMemberStates.delete(pairMemberStates.keys().next().value);}return pairMemberStates.get(key);}
function pairMemberMask(atom){const key=atom.url;if(!pairMemberMasks.has(key)){if(!/^\/review-waveforms\/atoms\/[A-Za-z0-9_.-]+\.png$/.test(key)||!/^[a-f0-9]{64}$/.test(atom.sha256)||!atom.all_members||atom.mean||atom.sd_band||!Number.isInteger(atom.n_waveforms)||atom.n_waveforms<1)throw Error('Invalid full-waveform layer');const value=fetch(key).then(async r=>{if(!r.ok)throw Error('Could not load all member waveforms ('+r.status+')');const bytes=await r.arrayBuffer(),sha=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');if(sha!==atom.sha256)throw Error('Waveform image integrity mismatch');return new Blob([bytes],{type:'image/png'});});pairMemberMasks.set(key,value);while(pairMemberMasks.size>64)pairMemberMasks.delete(pairMemberMasks.keys().next().value);}return pairMemberMasks.get(key);}
const FULL_WAVEFORM_COLORS=['#0068c8','#cf0064'];
let waveformMemberMode='all';
try{if(localStorage.getItem('spike-review-waveform-members')==='sample24')waveformMemberMode='sample24';}catch{}
function waveformCoverageLabel(u){return waveformMemberMode==='sample24'?`${Math.min(24,u.spikes)} sampled`:'All waveforms';}
function syncWaveformCoverageLabels(c){for(const row of document.querySelectorAll('#metrics [data-stat-cluster]')){const u=c.units.find(unit=>unit.id===row.dataset.statCluster),cell=row.children[2];if(u&&cell){cell.textContent=waveformCoverageLabel(u);cell.title=waveformMemberMode==='sample24'?'Up to 24 actual spikes per cluster, chronologically spaced; not full coverage':'All individual member waveforms in Cluster detail';}}}

async function paintPairMembers(host,c,data){
 if(host.dataset.started==='true')return;host.dataset.started='true';
 try{
  const state=await pairMemberState(c);if(!host.isConnected)return;
  const groups=data.units.map((u,index)=>{const current=c.units.find(v=>v.cluster_id===u.cluster_id),population=state.units[String(u.cluster_id)];if(!current||!population||population.n_spikes!==u.n_spikes||population.n_spikes!==current.spikes)throw Error('Full waveform member count does not match this comparison');const atoms=population.atoms.map(id=>state.atoms[id]);if(atoms.some(a=>!a)||atoms.reduce((sum,a)=>sum+a.n_waveforms,0)!==population.n_spikes)throw Error('Incomplete full-waveform population');return {id:u.cluster_id,n:population.n_spikes,color:FULL_WAVEFORM_COLORS[index],atoms,sample:current.waveform};});
  if(!groups.length||groups.length>2)throw Error('Choose one cluster or a pair');
  const atoms=groups.flatMap(g=>g.atoms),lowValue=Math.min(...atoms.map(a=>a.raw_low)),highValue=Math.max(...atoms.map(a=>a.raw_high)),pad=Math.max((highValue-lowValue)*.08,1e-6),low=lowValue-pad,high=highValue+pad,total=groups.reduce((n,g)=>n+g.n,0),status=host.querySelector('.pair-member-status');let cursor=0,loaded=0;const blobs=new Map();
  await Promise.all(Array.from({length:Math.min(8,atoms.length)},async()=>{while(cursor<atoms.length&&host.isConnected){const a=atoms[cursor++];blobs.set(a.url,await pairMemberMask(a));loaded++;if(host.isConnected)status.textContent='Loading all member waveforms… '+loaded+' / '+atoms.length+' image layers';}}));if(!host.isConnected)return;
  const canvas=document.createElement('canvas');canvas.width=1600;canvas.height=900;const ctx=canvas.getContext('2d'),tint=document.createElement('canvas');tint.width=1600;tint.height=900;const tx=tint.getContext('2d');let verified=0;
  for(const group of groups){group.layer=document.createElement('canvas');group.layer.width=1600;group.layer.height=900;const layer=group.layer.getContext('2d');for(const atom of [...group.atoms].sort((a,b)=>b.n_waveforms-a.n_waveforms)){if(!host.isConnected)return;const bitmap=await createImageBitmap(blobs.get(atom.url));if(bitmap.width!==atom.width||bitmap.height!==atom.height){bitmap.close();throw Error('Waveform layer dimensions differ');}tx.globalCompositeOperation='source-over';tx.clearRect(0,0,1600,900);tx.drawImage(bitmap,0,0);bitmap.close();tx.globalCompositeOperation='source-in';tx.fillStyle=group.color;tx.fillRect(0,0,1600,900);
    // Boost display alpha only. Positions, amplitudes, times and memberships
    // remain unchanged; these are the same previously verified full-member PNGs.
    const pixels=tx.getImageData(0,0,1600,900);for(let i=3;i<pixels.data.length;i+=4)pixels.data[i]=Math.min(255,pixels.data[i]*3);tx.putImageData(pixels,0,0);
    const y=(high-atom.image_high)/(high-low)*900,h=(atom.image_high-atom.image_low)/(high-low)*900;layer.drawImage(tint,0,y,1600,h);verified+=atom.n_waveforms;}
  }
  if(verified!==total)throw Error('Not every member waveform was loaded');if(!host.isConnected)return;
  const maxN=Math.max(...groups.map(g=>g.n)),minN=Math.min(...groups.map(g=>g.n));for(const g of groups)g.opacity=groups.length===2&&maxN>4*minN?(g.n===maxN?.45:1):.9;
  const w=610,h=250,box=axes(w,h,'Time (ms)','Amplitude (source units)',Array.from({length:5},(_,i)=>tickLabel(state.x_min_ms+(state.x_max_ms-state.x_min_ms)*i/4)),Array.from({length:5},(_,i)=>tickLabel(high-(high-low)*i/4))),clip='pair-members-'+crypto.randomUUID();
  host.innerHTML='<div class="plot-key">'+groups.map(g=>`<span style="color:${g.color}"><i style="--key-color:${g.color};background:${g.color};border-color:${g.color}"></i>Cluster ${g.id}</span>`).join('')+'</div>'+`<div class="full-wave-display-controls"><label>Waveforms<select data-wave-members aria-label="Waveform coverage"><option value="all">All waveforms</option><option value="sample24">24 sampled / cluster</option></select></label>${groups.length===2?'<label>Display<select data-wave-display aria-label="Displayed waveform clusters"><option value="both">Both clusters</option>'+groups.map(g=>'<option value="'+g.id+'">Cluster '+g.id+' only</option>').join('')+'</select></label>':''}${groups.map(g=>`<label style="color:${g.color}">Cluster ${g.id} opacity<input type="range" min="10" max="100" step="5" value="${Math.round(g.opacity*100)}" data-wave-opacity="${g.id}" aria-label="Cluster ${g.id} waveform opacity"><output>${Math.round(g.opacity*100)}%</output></label>`).join('')}</div><p class="plot-note waveform-coverage-note"></p><p class="pair-member-status" role="status"></p><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="All individual member waveforms on shared axes"><defs><clipPath id="${clip}"><rect x="${box.l}" y="${box.t}" width="${box.iw}" height="${box.ih}"/></clipPath></defs>${box.markup}<image x="${box.l}" y="${box.t}" width="${box.iw}" height="${box.ih}" preserveAspectRatio="none" clip-path="url(#${clip})"/><g data-wave-samples clip-path="url(#${clip})"></g></svg><details class="evidence-details waveform-display-note"><summary>Plot details</summary><p>Actual recorded waveforms on shared time and amplitude axes. No means, SD bands, amplitude normalization or time shifting. Color and opacity change visibility only; brightness is not a spike count. The 24-spike sample is evenly spaced in recording-time order.</p></details>`;
  const image=host.querySelector('svg image'),sampleLayer=host.querySelector('[data-wave-samples]'),coverage=host.querySelector('[data-wave-members]'),displayControl=host.querySelector('[data-wave-display]'),mode=()=>displayControl?.value||'both';let timer;
  coverage.value=waveformMemberMode;
  const paint=()=>{
    if(!host.isConnected)return;
    const display=mode(),visible=groups.filter(g=>display==='both'||String(g.id)===display),sampled=coverage.value==='sample24';
    const count=g=>sampled?Math.min(24,g.n):g.n;
    if(sampled){
      const lines=[];
      for(const g of visible){const sample=g.sample,xs=sample?.x_ms,traces=sample?.traces;
        if(!Array.isArray(xs)||xs.length<2||!xs.every(Number.isFinite)||!Array.isArray(traces)||traces.length!==count(g)||traces.some(values=>!Array.isArray(values)||values.length!==xs.length||!values.every(Number.isFinite)))throw Error('Verified 24-spike sample is unavailable for Cluster '+g.id);
        for(const values of traces){const points=values.map((value,i)=>`${box.l+(xs[i]-state.x_min_ms)/(state.x_max_ms-state.x_min_ms)*box.iw},${box.t+(high-value)/(high-low)*box.ih}`).join(' ');lines.push(`<polyline data-sample-cluster="${g.id}" points="${points}" fill="none" stroke="${g.color}" stroke-opacity="${g.opacity}" stroke-width="0.7"/>`);}
      }
      sampleLayer.innerHTML=lines.join('');image.style.display='none';
    }else{
      sampleLayer.innerHTML='';image.style.display='';ctx.clearRect(0,0,1600,900);for(const g of [...visible].sort((a,b)=>b.n-a.n)){ctx.globalAlpha=g.opacity;ctx.drawImage(g.layer,0,0);}ctx.globalAlpha=1;image.setAttribute('href',canvas.toDataURL('image/png'));
    }
    const drawn=visible.reduce((n,g)=>n+count(g),0);
    host.querySelector('.waveform-coverage-note').textContent=sampled?`Chronological sample · up to 24 / cluster; may miss noise or outliers.`:visible.length===groups.length?`Shared axes · all recorded member waveforms.`:`All members of Cluster ${visible[0].id} · other cluster hidden.`;
    host.querySelector('.pair-member-status').textContent=groups.map(g=>visible.includes(g)?'Cluster '+g.id+': '+count(g).toLocaleString()+' / '+g.n.toLocaleString()+(sampled?' sampled':' drawn'):'Cluster '+g.id+': hidden ('+g.n.toLocaleString()+' available)').join(' · ');
    host.querySelector('svg').setAttribute('aria-label',sampled?'Up to 24 actual sampled waveforms per visible cluster on shared axes':'All individual member waveforms on shared axes');
    host.dataset.traceCount=drawn;host.dataset.totalTraceCount=total;host.dataset.visibleSourceCount=visible.includes(groups[0])?count(groups[0]):0;host.dataset.visibleTargetCount=groups[1]&&visible.includes(groups[1])?count(groups[1]):0;host.dataset.display=display;host.dataset.memberMode=coverage.value;host.dataset.sampled=String(sampled);host.dataset.ready='true';syncWaveformCoverageLabels(c);
  };
  const update=()=>{host.dataset.ready='false';clearTimeout(timer);timer=setTimeout(()=>{try{paint();}catch(error){host.dataset.ready='false';host.querySelector('.pair-member-status').textContent=error.message+'; switch to All waveforms.';}},70);};coverage.onchange=()=>{waveformMemberMode=coverage.value;try{localStorage.setItem('spike-review-waveform-members',waveformMemberMode);}catch{}update();};if(host.querySelector('[data-wave-display]'))host.querySelector('[data-wave-display]').onchange=update;for(const g of groups)host.querySelector(`[data-wave-opacity="${g.id}"]`).oninput=e=>{g.opacity=Number(e.target.value)/100;e.target.nextElementSibling.value=e.target.value+'%';update();};
  host.dataset.sourceCount=groups[0].n;host.dataset.targetCount=groups[1]?.n||0;host.dataset.sourceCluster=groups[0].id;host.dataset.targetCluster=groups[1]?.id||'';host.dataset.stateSha=c.state_sha256;host.dataset.low=low;host.dataset.high=high;host.dataset.mean='false';host.dataset.sdBand='false';host.dataset.sampled='false';host.dataset.roleColors=JSON.stringify(groups.map(g=>g.color));paint();attachPlotZoom(host);syncPlotTypography();
 }catch(error){if(!host.isConnected)return;host.dataset.ready='false';host.innerHTML=`<p class="analysis-message" role="alert">Could not load all member waveforms: ${escapeHtml(error.message)}. <button type="button" class="button small">Retry waveforms</button></p>`;host.querySelector('button').onclick=()=>{pairMemberStates.delete(c.dataset_id+'--'+c.state_sha256);pairMemberMasks.clear();host.dataset.started='false';host.innerHTML='<p class="pair-member-status" role="status">Loading all member waveforms…</p>';paintPairMembers(host,c,data);};}
}
function singleMemberWaveforms(data,c,u){return `<div class="pair-member-waveforms" data-case="${escapeHtml(c.id)}" data-single="true" data-source-unit="${escapeHtml(u.id)}"><p class="pair-member-status" role="status">Loading all ${u.spikes.toLocaleString()} recorded member waveforms of Cluster ${u.cluster_id}…</p></div>`;}

function pairWaveforms(data,c){return `<div class="pair-member-waveforms" data-case="${escapeHtml(c.id)}"><p class="pair-member-status" role="status">Loading all individual member waveforms for Cluster ${data.source_cluster} and Cluster ${data.target_cluster}…</p></div>`;}

function candidateISI(data){
  const isi=data.merged_isi,w=610,h=250,high=Math.max(...isi.counts,1);
  const box=axes(w,h,'ISI (ms)','Interval count',['0','12.5','25','37.5','50'],Array.from({length:5},(_,i)=>tickLabel(high*(1-i/4))));
  const bars=isi.counts.map((count,i)=>`<rect x="${box.l+i/isi.counts.length*box.iw}" y="${box.t+(1-count/high)*box.ih}" width="${box.iw/isi.counts.length}" height="${count/high*box.ih}" fill="#3769a0"/>`).join('');
  const marker=box.l+2/50*box.iw;
  return `<p class="plot-note">0–50 ms shown · 2 ms marker. Rate uses all ${isi.denominator.toLocaleString()} adjacent intervals (${isi.outside_display_count.toLocaleString()} above 50 ms).</p><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Recomputed candidate pair ISI histogram with 2 ms marker">${box.markup}<rect x="${box.l}" y="${box.t}" width="${marker-box.l}" height="${box.ih}" fill="#f6dcc0"/>${bars}<path d="M${marker} ${box.t}v${box.ih}" stroke="#8f3b16" stroke-dasharray="5 4" stroke-width="1.5"/></svg>`;
}
function renderPairComparison(c){
  const target=comparisonTarget(c);
  if(!target)return '';
  if(c.synthetic!==false){
    const units=[sourceUnit(c),c.units.find(u=>u.id===target)],w=610,h=250;
    const box=axes(w,h,'Time (ms)','Source units',['−0.5','0','0.5','1.0','1.5'],['60','5','−50','−105','−160']);
    const lines=units.map((u,i)=>`<polyline points="${tracePoints(u,box)}" fill="none" stroke="${u.color}" stroke-width="2.5" ${i?'stroke-dasharray="8 5"':''}/>`).join('');
    const key=units.map((u,i)=>`<span><i class="${i?'dashed-key':''}" style="--key-color:${u.color}"></i>${escapeHtml(u.title)}</span>`).join('');
    return plotCard(escapeHtml(comparisonName(c)),'Illustrative comparison · shared axes',`<div class="plot-key">${key}</div><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Illustrative pair waveform comparison">${box.markup}${lines}</svg><p class="plot-note">Synthetic demo only. No measured pair ISI or quality statistics are invented.</p>`);
  }
  const entry=reviewerAnalysis(c,c.source,target);
  if(!entry.data)return analysisPending(entry);
  const data=entry.data;
  if(!comparisonExposure.has(c.id))resetAnalysisExposure(c.id);
  comparisonExposure.get(c.id).add(target);
  const corr=data.pair.waveform_correlation;
  return plotCard('Waveforms','',pairWaveforms(data,c))+
    `<div class="pair-summary"><p><strong>Mean waveform correlation: ${corr==null?'Not defined':corr.toFixed(4)}</strong><span title="Pearson r of aligned mean waveforms. Similarity alone does not establish the same neuron.">Pearson r · means</span></p><p><strong>Candidate merged ISI violations: ${metricPercent(data.merged_isi.violation_rate)}</strong><span>${data.merged_isi.violation_count.toLocaleString()} / ${data.merged_isi.denominator.toLocaleString()} intervals below 2 ms. No spikes were reassigned.</span></p></div>`+
    plotCard('Candidate pair ISI · recomputed',`Cluster ${data.source_cluster} + ${data.target_cluster}`,candidateISI(data))+
    populationMetrics(data.units)+'<p class="tree-note">Candidate statistics only · no merge applied.</p>';
}
function renderQualityMetrics(c,u){
  const target=comparisonTarget(c),entry=reviewerAnalysis(c,target?c.source:u.id,target||null);
  if(!entry.data)return analysisPending(entry);
  if(target){if(!comparisonExposure.has(c.id))resetAnalysisExposure(c.id);comparisonExposure.get(c.id).add(target);}
  return `<div class="quality-heading"><h3>${escapeHtml(target?comparisonName(c):u.title)} · measured quality evidence</h3><p>All member spikes in this exact pre-decision state. Sampling rate: ${metricNumber(entry.data.sampling_rate_hz)} Hz. Undefined values are shown explicitly.</p></div>`+
    populationMetrics(entry.data.units)+
    '<div class="annotation"><p>These are descriptive measurements, not ground-truth quality labels. ISI violations can reflect contamination or spike detection effects; amplitude variation can reflect drift or mixed populations. No universal pass/fail cutoff is applied. SNR and spatial quality are unavailable because background noise and geometry were not imported.</p></div>';
}
function attachAnalysisControls(root=document.querySelector('#evidence-content')){
  const host=root.querySelector('.pair-member-waveforms');if(host){const c=currentCase(),single=host.dataset.single==='true',target=single?null:comparisonTarget(c),entry=reviewerAnalysis(c,single?host.dataset.sourceUnit:c.source,target);if(entry.data)paintPairMembers(host,c,entry.data);}
  const select=$('#compare-with');
  if(select)select.onchange=()=>selectComparison(select.value);
  $$('[data-retry-analysis]').forEach(button=>button.onclick=()=>{reviewerAnalyses.delete(button.dataset.retryAnalysis);renderEvidence();});
}
