'use strict';

// Reviewer-only calculations. Nothing here executes a merge or calls a model.
const reviewerAnalyses=new Map(),comparisonChoices=new Map(),comparisonExposure=new Map();
function resetAnalysisExposure(id){comparisonExposure.set(id,new Set());}
function comparedPairs(c){return [...(comparisonExposure.get(c.id)||[])].map(target=>[c.source,target]);}
function comparisonTarget(c){
  const candidates=Core.candidates(c),valid=id=>candidates.some(u=>u.id===id);
  if(draft.action==='MERGE')return valid(draft.target)?draft.target:'';
  return valid(comparisonChoices.get(c.id))?comparisonChoices.get(c.id):c.pair?.[1]||'';
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
      entry.data=data;if(currentCase()===c&&['pair','quality'].includes(selectedTab))renderEvidence();
    }).catch(error=>{entry.error=error.message;if(currentCase()===c&&['pair','quality'].includes(selectedTab))renderEvidence();});
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
    ['Member spikes','All spikes assigned to this cluster in the current checkpoint.',u=>u.n_spikes.toLocaleString()],
    ['ISI violations','Adjacent sorted intervals < 2 ms / all N−1 intervals. This is not an estimated contamination fraction.',u=>`${metricPercent(u.isi.violation_rate)} (${u.isi.violation_count.toLocaleString()} / ${u.isi.denominator.toLocaleString()})`],
    ['Peak-to-trough amplitude','Mean ± population SD of per-spike peak-to-trough amplitude; source units.',u=>`${metricNumber(u.amplitude.mean_peak_to_trough)} ± ${metricNumber(u.amplitude.std_peak_to_trough)}`],
    ['Amplitude CV','Population amplitude SD / mean, using all spikes. No automatic acceptance cutoff.',u=>metricNumber(u.amplitude.cv)],
    ['Amplitude change','Last 20% vs first 20% of spikes by time. Unequal-duration windows; descriptive drift measure.',u=>u.drift.delta_percent==null?'Not defined':`${u.drift.delta_percent>=0?'+':''}${u.drift.delta_percent.toFixed(2)}%`]
  ];
  return `<div class="quality-table-wrap"><table class="quality-table"><thead><tr><th>Metric / interpretation</th>${units.map(u=>`<th>Cluster ${u.cluster_id}</th>`).join('')}</tr></thead><tbody>${rows.map(([label,note,value])=>`<tr><th>${label}<small>${note}</small></th>${units.map(u=>`<td>${value(u)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function pairWaveforms(data,c){
  const units=data.units,all=units.flatMap(u=>u.waveform.mean.flatMap((m,i)=>[m-u.waveform.std[i],m+u.waveform.std[i]]));
  let low=Math.min(...all),high=Math.max(...all);const pad=Math.max((high-low)*.08,1e-9);low-=pad;high+=pad;
  const xs=units[0].waveform.x_ms,x0=xs[0],span=xs[xs.length-1]-x0;
  const w=610,h=250,box=axes(w,h,'Time (ms)','Amplitude (source units)',Array.from({length:5},(_,i)=>tickLabel(x0+span*i/4)),Array.from({length:5},(_,i)=>tickLabel(high-(high-low)*i/4)));
  const points=values=>values.map((v,i)=>`${(box.l+(xs[i]-x0)/span*box.iw).toFixed(2)},${(box.t+(high-v)/(high-low)*box.ih).toFixed(2)}`).join(' ');
  const lines=units.map((u,index)=>{
    const color=c.units.find(item=>item.cluster_id===u.cluster_id).color,mean=u.waveform.mean,sd=u.waveform.std;
    const top=points(mean.map((m,i)=>m+sd[i])),bottom=points(mean.map((m,i)=>m-sd[i])).split(' ').reverse().join(' ');
    return `<polygon points="${top} ${bottom}" fill="${color}" fill-opacity=".10"/><polyline points="${points(mean)}" fill="none" stroke="${color}" stroke-width="2.5" ${index?'stroke-dasharray="8 5"':''}><title>Cluster ${u.cluster_id} · ${index?'candidate target':'review source'} mean</title></polyline>`;
  }).join('');
  const legend=units.map((u,i)=>`<span><i class="${i?'dashed-key':''}" style="--key-color:${c.units.find(item=>item.cluster_id===u.cluster_id).color}"></i>${i?'Dashed target':'Solid source'}: Cluster ${u.cluster_id} · n = ${u.n_spikes.toLocaleString()}</span>`).join('');
  return `<div class="plot-key">${legend}</div><p class="plot-note">Shared time and amplitude axes. Lines are all-member means; shaded bands are ±1 SD of individual spikes, not confidence intervals. No amplitude normalization or time shifting.</p><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Selected pair mean waveforms and one SD on shared axes">${box.markup}${lines}</svg>`;
}
function candidateISI(data){
  const isi=data.merged_isi,w=610,h=250,high=Math.max(...isi.counts,1);
  const box=axes(w,h,'ISI (ms)','Interval count',['0','12.5','25','37.5','50'],Array.from({length:5},(_,i)=>tickLabel(high*(1-i/4))));
  const bars=isi.counts.map((count,i)=>`<rect x="${box.l+i/isi.counts.length*box.iw}" y="${box.t+(1-count/high)*box.ih}" width="${box.iw/isi.counts.length}" height="${count/high*box.ih}" fill="#3769a0"/>`).join('');
  const marker=box.l+2/50*box.iw;
  return `<p class="plot-note">Pooled candidate spike times, sorted and differenced. Dashed line marks 2 ms. Only 0–50 ms are plotted; the rate denominator includes all ${isi.denominator.toLocaleString()} intervals, including ${isi.outside_display_count.toLocaleString()} above 50 ms.</p><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Recomputed candidate pair ISI histogram with 2 ms marker">${box.markup}<rect x="${box.l}" y="${box.t}" width="${marker-box.l}" height="${box.ih}" fill="#f6dcc0"/>${bars}<path d="M${marker} ${box.t}v${box.ih}" stroke="#8f3b16" stroke-dasharray="5 4" stroke-width="1.5"/></svg>`;
}
function renderPairComparison(c){
  const target=comparisonTarget(c),linked=draft.action==='MERGE';
  const controls=`<div class="pair-selector"><strong>Review source: ${escapeHtml(sourceUnit(c).title)}</strong><label for="pair-target">Compare with<select id="pair-target"><option value="">Choose a cluster…</option>${Core.candidates(c).map(u=>`<option value="${u.id}" ${u.id===target?'selected':''}>${escapeHtml(u.title)} · n = ${u.spikes.toLocaleString()}</option>`).join('')}</select></label><p>${linked?'Linked to your proposed merge target. Changing either selector updates both.':'Comparison only. Choose Merge in Your review to record a merge proposal.'}</p></div>`;
  if(!target)return controls+'<p class="tree-note">Choose a target to calculate shared-axis waveforms, quality metrics and candidate-pair ISI.</p>';
  const entry=reviewerAnalysis(c,c.source,target);
  if(!entry.data)return controls+analysisPending(entry);
  const data=entry.data;
  if(!comparisonExposure.has(c.id))resetAnalysisExposure(c.id);
  comparisonExposure.get(c.id).add(target);
  const corr=data.pair.waveform_correlation;
  return controls+plotCard(`${sourceUnit(c).title} + ${unitLabel(c,target)} · candidate comparison`,'Reviewer calculation · all member spikes',pairWaveforms(data,c))+
    `<div class="pair-summary"><p><strong>Mean waveform correlation: ${corr==null?'Not defined':corr.toFixed(4)}</strong><span>Pearson r, aligned samples. Similarity alone does not establish the same neuron.</span></p><p><strong>Candidate merged ISI violations: ${metricPercent(data.merged_isi.violation_rate)}</strong><span>${data.merged_isi.violation_count.toLocaleString()} / ${data.merged_isi.denominator.toLocaleString()} intervals below 2 ms. No spikes were reassigned.</span></p></div>`+
    plotCard('Candidate pair ISI · recomputed',`Cluster ${data.source_cluster} + ${data.target_cluster}`,candidateISI(data))+
    populationMetrics(data.units)+'<p class="tree-note">Computed for this exact candidate and checkpoint. Historical AI input images remain available in their original tabs. These statistics do not execute or automatically approve a merge.</p>';
}
function renderQualityMetrics(c,u){
  const entry=reviewerAnalysis(c,u.id);
  if(!entry.data)return analysisPending(entry);
  return `<div class="quality-heading"><h3>${escapeHtml(u.title)} · measured quality evidence</h3><p>All member spikes in this exact pre-decision state. Sampling rate: ${metricNumber(entry.data.sampling_rate_hz)} Hz. Undefined values are shown explicitly.</p></div>`+
    populationMetrics(entry.data.units)+
    '<div class="annotation"><p>These are descriptive measurements, not ground-truth quality labels. ISI violations can reflect contamination or spike detection effects; amplitude variation can reflect drift or mixed populations. No universal pass/fail cutoff is applied. SNR and spatial quality are unavailable because background noise and geometry were not imported.</p></div>';
}
function attachAnalysisControls(){
  const c=currentCase(),select=$('#pair-target');
  if(select)select.onchange=()=>{
    comparisonChoices.set(c.id,select.value);
    if(draft.action==='MERGE'){draft.target=select.value;isDirty=true;renderActions();}
    recordEvidence();renderEvidence();
  };
  $$('[data-retry-analysis]').forEach(button=>button.onclick=()=>{reviewerAnalyses.delete(button.dataset.retryAnalysis);renderEvidence();});
}
