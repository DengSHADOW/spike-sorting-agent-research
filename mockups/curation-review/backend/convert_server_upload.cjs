/* Offline validation/conversion of actual user files. No network or model calls. */
'use strict';
const fs=require('node:fs'),zlib=require('node:zlib'),crypto=require('node:crypto');
global.CurationCore=require('../dist/curation-core.js');
global.ReviewQuality=require('../dist/review-quality.js');
const Import=require('../dist/working-import.js'),C=global.CurationCore;
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
(async()=>{
 const manifest=JSON.parse(fs.readFileSync(process.argv[2])),files=manifest.files.map(f=>new File([fs.readFileSync(f.path)],f.name));
 let data;
 if(files.length===1&&files[0].name.endsWith('.gz')){const raw=zlib.gunzipSync(fs.readFileSync(manifest.files[0].path),{maxOutputLength:768*1024*1024});data=JSON.parse(raw);}
 else data=await Import.parse(files,manifest.recording_id);
 if(data.schema!=='spikeagent-working-dataset-v1')throw Error('Upload sorted-spike data, not a saved session');
 // Uploaded labels/reasons/Agent claims are not trusted historical study records.
 data={...Object.fromEntries(['schema','n_spikes','n_samples','sampling_rate_hz','atoms','tree','arrays','package_revision'].map(k=>[k,data[k]])),dataset_id:manifest.dataset_id,source_sha256:manifest.source_sha256,
  references:[],agent_suggestions:[],review_snapshots:[],metadata:{recording_id:manifest.recording_id,source_format:data.metadata?.source_format||'Uploaded working dataset',independence_status:'not assessed',waveform_source:'Actual uploaded per-spike waveforms'}};
 const p=C.prepare(data),wf=data.arrays.waveforms;
 if(!wf)throw Error('Full recorded waveforms are required; templates or sample-only packages are not usable');
 const bytes=Buffer.from(wf.data,'base64');if(bytes.length!==wf.length*(wf.dtype==='<f4'?4:8)||hash(bytes)!==wf.sha256)throw Error('Waveform checksum mismatch');
 const values=wf.dtype==='<f4'?new Float32Array(bytes.buffer,bytes.byteOffset,bytes.length/4):new Float64Array(bytes.buffer,bytes.byteOffset,bytes.length/8);
 if(!values.every(Number.isFinite))throw Error('Nonfinite waveform');
 for(const a of data.atoms){a.sum=Array(data.n_samples).fill(0);a.sumsq=Array(data.n_samples).fill(0);a.traces=[];}
 for(let row=0;row<data.n_spikes;row++){const a=data.atoms[p.index[row]];for(let j=0;j<data.n_samples;j++){const x=values[row*data.n_samples+j];a.sum[j]+=x;a.sumsq[j]+=x*x;}}
 data.initial_state_sha256=await C.stateHash(p,C.initial(p,'upload-validation'));
 fs.writeFileSync(manifest.output,zlib.gzipSync(JSON.stringify(data),{level:3}),{mode:0o600});
 console.log(JSON.stringify({n_spikes:data.n_spikes,n_clusters:C.active(p,C.initial(p,'check')).length,source_sha256:manifest.source_sha256}));
})().catch(e=>{console.log(JSON.stringify({error:String(e.message).slice(0,400)}));process.exitCode=1;});
