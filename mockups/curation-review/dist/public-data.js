/* Explicitly published full datasets. Same-origin downloads, checksums, no Runpod dependency. */
window.PublicData=(()=>{
 'use strict';const manifest=document.currentScript?.dataset.manifest;
 if(!manifest)return {configured:false};
 const allowed=new Set(['cM2-e004_004-006_CH3','cM2-e004_004-006_CH31']);let rows=[],pending=null;
 async function ready(){if(pending)return pending;pending=(async()=>{const response=await fetch(manifest,{cache:'no-cache',credentials:'omit'});if(!response.ok)throw Error('Public dataset catalog unavailable');const data=await response.json();
  if(data.schema!=='spikeagent-public-datasets-v1'||!Array.isArray(data.datasets)||data.datasets.length!==2)throw Error('Invalid public dataset catalog');
  rows=data.datasets.map(r=>{const url=new URL(r.url,location.href);if(!allowed.has(r.dataset_id)||url.origin!==location.origin||!/^\/public-data\/packages\/[A-Za-z0-9_.-]+\.bin$/.test(url.pathname)||!/^[a-f0-9]{64}$/.test(r.sha256)||!/^[a-f0-9]{64}$/.test(r.source_sha256)||!Number.isInteger(r.bytes)||r.bytes<1||r.bytes>256*1048576)throw Error('Invalid public package metadata');return {...r,url:url.pathname};});if(new Set(rows.map(r=>r.dataset_id)).size!==2)throw Error('Duplicate public dataset');return rows;
 })().catch(e=>{pending=null;throw e;});return pending;}
 async function packageData(dataset,onProgress=()=>{}){await ready();const row=rows.find(r=>r.dataset_id===dataset);if(!row)throw Error('Choose CH3 or CH31');const response=await fetch(row.url,{credentials:'omit'});if(!response.ok)throw Error('Public dataset download failed');const reader=response.body.getReader(),parts=[];let n=0;
  for(;;){const block=await reader.read();if(block.done)break;n+=block.value.length;if(n>row.bytes){await reader.cancel();throw Error('Public package size mismatch');}parts.push(block.value);onProgress(n,row.bytes);}
  const blob=new Blob(parts);if(n!==row.bytes||await CurationCore.digest(await blob.arrayBuffer())!==row.sha256)throw Error('Public package checksum mismatch');onProgress(n,row.bytes,'Verifying real waveform data…');
  const data=await new Response(blob.stream().pipeThrough(new DecompressionStream('gzip'))).json();
  if(data.dataset_id!==dataset||data.source_sha256!==row.source_sha256||!data.arrays?.waveforms||data.n_spikes!==row.n_spikes)throw Error('Public dataset provenance mismatch');
  data.public_package_sha256=row.sha256;return data;
 }
 return {configured:true,ready,packageData,get rows(){return rows;},has:dataset=>rows.some(r=>r.dataset_id===dataset)};
})();
