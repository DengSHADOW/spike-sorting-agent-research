/* Authenticated, read-only Runpod data access. No credentials in this file. */
'use strict';
window.PrivateData=(()=>{
  const configuredURL=document.currentScript?.dataset.endpoint||'';
  const endpoint=configuredURL?new URL(configuredURL):null;
  if(endpoint&&(endpoint.protocol!=='https:'||endpoint.username||endpoint.password||endpoint.search||endpoint.hash||endpoint.pathname!=='/'))throw Error('Invalid private data endpoint');
  const storageKey='spikeagent-private-connection:'+configuredURL;
  let session=null;
  try{session=JSON.parse(sessionStorage.getItem(storageKey)||'null');}catch{}
  const connected=()=>!!session?.token&&session.until>Date.now();
  function forget(){session=null;try{sessionStorage.removeItem(storageKey);}catch{}window.dispatchEvent(new Event('private-data-connection'));}
  async function request(path,options={}){
    if(!endpoint)throw Error('Private data connection is not configured');
    const login=path==='/api/private/login';
    if(!login&&!connected()){forget();throw Error('Connect with your private access key to load data.');}
    let response;
    try{response=await fetch(new URL(path,endpoint),{...options,mode:'cors',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer',headers:{...options.headers,...(!login?{Authorization:'Bearer '+session.token}:{})}});}
    catch{throw Error('Runpod data service is unreachable. Check that the Pod is running, then retry.');}
    if(!response.ok){if(response.status===401&&!login)forget();let value;try{value=await response.json();}catch{}throw Error(value?.error||'Private data request failed ('+response.status+').');}
    return response;
  }
  async function connect(key){
    const r=await request('/api/private/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
    const value=await r.json();session={token:value.token,until:Date.now()+value.expires_in*1000,name:value.name};
    try{sessionStorage.setItem(storageKey,JSON.stringify(session));}catch{}
    window.dispatchEvent(new Event('private-data-connection'));
  }
  async function disconnect(){try{if(connected())await request('/api/private/logout',{method:'POST'});}finally{forget();}}
  async function datasets(){return (await (await request('/api/private/datasets')).json()).datasets;}
  async function packageBlob(dataset,progress=()=>{}){
    const r=await request('/api/private/package?dataset_id='+encodeURIComponent(dataset));
    const total=Number(r.headers.get('Content-Length')),expected=r.headers.get('X-Package-SHA256');
    if(!/^[a-f0-9]{64}$/.test(expected||'')||!Number.isSafeInteger(total)||total<=0||total>256*1024*1024)throw Error('Private package metadata is invalid.');
    const parts=[],reader=r.body.getReader();let count=0;
    for(;;){const {done,value}=await reader.read();if(done)break;count+=value.length;if(count>total){await reader.cancel();throw Error('Private package size mismatch.');}parts.push(value);progress(count,total);}
    if(count!==total)throw Error('Data transfer was interrupted. Retry without discarding your choice.');
    const blob=new Blob(parts,{type:'application/gzip'}),hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',await blob.arrayBuffer()))].map(x=>x.toString(16).padStart(2,'0')).join('');
    if(hash!==expected)throw Error('Private package integrity check failed.');
    return blob;
  }
  return {configured:!!endpoint,get connected(){return connected();},get name(){return connected()?session.name:'';},connect,disconnect,datasets,packageBlob};
})();
