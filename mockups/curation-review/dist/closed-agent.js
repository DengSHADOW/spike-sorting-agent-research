/* Live Agent 3 is an explicit, separate preview. No provider key is stored here. */
window.ClosedAgent=(function(){
  let status=null,snapshot=null,result=null,busy=false;
  const el=id=>document.getElementById(id);
  const message=text=>el('closed-connection-status').textContent=text;
  function base(){
    const url=new URL(el('closed-gateway-url').value.trim());
    const local=['localhost','127.0.0.1','[::1]'].includes(url.hostname);
    if(url.username||url.password||url.search||url.hash||url.pathname!=='/'||(!local&&url.protocol!=='https:')||local&&!['http:','https:'].includes(url.protocol))throw new Error('Use an HTTPS gateway origin, or a localhost gateway during development.');
    if(el('closed-gateway-token').value.length<32)throw new Error('Enter the gateway access token. Provider API keys belong on the server.');
    return url.origin;
  }
  async function request(path,body){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),55000);
    try{
      const response=await fetch(base()+path,{method:body?'POST':'GET',credentials:'omit',cache:'no-store',signal:controller.signal,
        headers:{Authorization:'Bearer '+el('closed-gateway-token').value,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
      const payload=await response.json();if(!response.ok)throw new Error(payload.error||'Gateway request failed.');return payload;
    }finally{clearTimeout(timer);}
  }
  function update(){
    const provider=status?.providers?.find(p=>p.id===el('closed-provider').value);
    el('closed-model').innerHTML=provider?.models?.length?provider.models.map(model=>`<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join(''):'<option value="">No model configured on gateway</option>';
    el('closed-model').disabled=busy||!provider?.configured;
    updateRun();
  }
  function updateRun(){
    const provider=status?.providers?.find(p=>p.id===el('closed-provider').value);
    el('closed-run-request').disabled=busy||!status?.enabled||!provider?.configured||!el('closed-model').value||!el('closed-paid-confirm').checked||!snapshot;
  }
  function open(){
    status=null;snapshot=null;result=null;
    el('closed-gateway-token').value='';el('closed-paid-confirm').checked=false;el('closed-result').hidden=true;
    const c=currentCase();
    if(c.synthetic!==false){message('Select a real-data case before requesting a live preview.');}
    else{
      snapshot={case_id:c.id,state_sha256:c.state_sha256,view:selectedTab,inspected_cluster_id:selectedUnitId,
        target_cluster_id:comparisonTarget(c)||null};
      if(c.working)snapshot.working_case={schema:'spikeagent-live-working-case-v1',id:c.id,dataset_id:c.dataset_id,state_sha256:c.state_sha256,mat_sha256:c.mat_sha256,source:c.source,units:c.units.map(u=>({id:u.id,cluster_id:u.cluster_id,spikes:u.spikes,split:!!u.split}))};message('Not connected. No model request has been sent.');
    }
    el('closed-case-context').textContent=snapshot?`${c.dataset_id} · ${c.working?'current private grouping':c.step_id} · visible ${selectedTab} plots. Only selected plots and cluster counts are sent to the gateway; no human decisions or reason library.`:'No real case selected.';
    el('closed-provider').value='openai';update();el('closed-agent-dialog').showModal();
  }
  async function connect(){
    try{status=null;message('Checking gateway configuration; this does not call a model…');status=await request('/api/closed/status');update();message(status.enabled?'Gateway connected. Choose the configured model and explicitly authorize one request.':'Gateway connected; live requests are disabled by its owner.');}
    catch(error){message('Not connected: '+error.message);update();}
  }
  async function capture(){
    const plots=[...document.querySelectorAll('#evidence-content svg,#evidence-content [data-main-wave] canvas,#evidence-content [data-before-wave] canvas,#evidence-content [data-pca-canvas]')].slice(0,3);
    if(!plots.length)throw new Error('Choose a waveform, PCA, ISI or ACG/CCG plot before requesting a judgment.');
    const images=[],descriptions=[];
    for(const svg of plots){
      if(svg.tagName.toLowerCase()==='canvas'){if(svg.dataset.complete!=='true')throw Error('Wait for the complete plot before requesting a judgment');images.push(svg.toDataURL('image/png'));descriptions.push((svg.closest('.plot-card')?.querySelector('.plot-heading')?.innerText||'PCA: all current members on shared axes').slice(0,4000));continue;}
      const clone=svg.cloneNode(true);clone.setAttribute('xmlns','http://www.w3.org/2000/svg');
      const originalNodes=[svg,...svg.querySelectorAll('*')],clonedNodes=[clone,...clone.querySelectorAll('*')];
      originalNodes.forEach((node,index)=>{const style=getComputedStyle(node);for(const key of ['fill','stroke','stroke-width','stroke-dasharray','font-size','font-family','font-weight','opacity','fill-opacity','stroke-opacity'])clonedNodes[index].style.setProperty(key,style.getPropertyValue(key));});
      const card=svg.closest('.plot-card');
      descriptions.push([svg.getAttribute('aria-label'),card?.querySelector('.plot-heading')?.innerText,...[...(card?.querySelectorAll('.plot-key,.plot-note')||[])].map(node=>node.innerText)].filter(Boolean).join('\n').slice(0,4000));
      const box=svg.viewBox.baseVal,w=Math.min(1200,Math.max(500,Math.round(box.width*1.5))),h=Math.min(1000,Math.max(200,Math.round(box.height*1.5)));
      for(const node of clone.querySelectorAll('image')){const href=node.getAttribute('href')||node.getAttribute('xlink:href');if(href?.startsWith('blob:')){const blob=await (await fetch(href)).blob();const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(blob);});node.setAttribute('href',data);}}
      clone.setAttribute('width',w);clone.setAttribute('height',h);
      const image=new Image(),serialized=new XMLSerializer().serializeToString(clone);
      const url=URL.createObjectURL(new Blob([serialized],{type:'image/svg+xml'}));
      try{
        image.src=url;await image.decode();const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
        const context=canvas.getContext('2d');context.fillStyle='#ffffff';context.fillRect(0,0,w,h);context.drawImage(image,0,0,w,h);images.push(canvas.toDataURL('image/png'));
      }finally{URL.revokeObjectURL(url);}
    }
    return {images,plot_descriptions:descriptions};
  }
  async function run(){
    if(busy||el('closed-run-request').disabled)return;
    const provider=el('closed-provider').value,model=el('closed-model').value;
    busy=true;result=null;el('closed-result').hidden=true;message('Preparing the current evidence plots…');updateRun();
    for(const id of ['closed-check-connection','closed-provider','closed-model','closed-gateway-url','closed-gateway-token','close-closed-agent'])el(id).disabled=true;
    try{
      if(!snapshot||currentCase().id!==snapshot.case_id||selectedTab!==snapshot.view||selectedUnitId!==snapshot.inspected_cluster_id||currentCase().state_sha256!==snapshot.state_sha256||(comparisonTarget(currentCase())||null)!==snapshot.target_cluster_id)throw new Error('The current evidence changed. Reopen the live window.');
      const plots=await capture();
      message('Sending one explicitly authorized request. Closing this window does not undo provider charges…');
      result=await request('/api/closed/request',{...snapshot,...plots,provider,model,request_id:crypto.randomUUID(),confirm_paid:true});
      if(snapshot.working_case)await WorkingReview.acceptLive(result);el('closed-result-text').textContent=JSON.stringify(result.decision,null,2);el('closed-result').hidden=false;
      message('Live preview received. No expert review was saved and no cluster assignments changed.');
    }catch(error){message('Preview not completed: '+error.message+'. No automatic retry was made.');}
    finally{busy=false;el('closed-paid-confirm').checked=false;for(const id of ['closed-check-connection','closed-provider','closed-model','closed-gateway-url','closed-gateway-token','close-closed-agent'])el(id).disabled=false;updateRun();}
  }
  function init(){
    el('close-closed-agent').onclick=()=>{if(!busy)el('closed-agent-dialog').close();};
    el('closed-agent-dialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
    el('closed-agent-dialog').addEventListener('close',()=>{el('closed-gateway-token').value='';status=null;snapshot=null;updateRun();});
    el('closed-check-connection').onclick=connect;el('closed-provider').onchange=update;el('closed-paid-confirm').onchange=updateRun;el('closed-model').onchange=updateRun;
    for(const id of ['closed-gateway-url','closed-gateway-token'])el(id).oninput=()=>{status=null;update();};
    el('closed-run-request').onclick=run;
    el('closed-export-response').onclick=()=>{if(result)downloadInputJSON(result,'spikeagent-agent3-live-preview.json');};
  }
  window.addEventListener('DOMContentLoaded',init,{once:true});
  return {open};
})();
