const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const crypto=require('node:crypto');
const Core=require('../mockups/curation-review/dist/review-core.js');
const source=fs.readFileSync(require('node:path').join(__dirname,'../mockups/curation-review/dist/static-share.js'),'utf8');
const c={id:'recording--step_1',synthetic:false,dataset_id:'recording',run_id:'saved-run',step_id:'step_1',state_id:'state',state_sha256:'state-sha',mat_sha256:'mat-sha',checkpoint_sha256:'checkpoint-sha',request_sha256:'request-sha',request_hash_status:'recorded',image_sha256:{waveform:'image-sha'},channel:'CH3',phase_id:'phase1',source:'U-1',pair:null,ai:'KEEP',rationale:'Saved rationale',agent_id:'model',agent_kind:'historical-model-response',units:[{id:'U-1',cluster_id:1,spikes:100,split:false},{id:'U-2',cluster_id:2,spikes:50,split:false}]};
const draft={agree:false,action:'DISCARD',target:'',reason:'Waveform mismatch',note:'',positive:'',disagreement_types:[],confidence:'low',sufficiency:'unspecified',missing:[],missing_note:''};
function make(storage=new Map(),records=[c]){
 const files={'/share/manifest.json':{bundles:[{id:'test',url:'/bundle.json'}]},'/bundle.json':{id:'test',cases:records},'/share/reasons.json':{version:'seed',n_source_rows:1,reasons:[{text:'Waveform mismatch',sources:[{sheet:'CH3',row:2}]}]}};
 const requests=[],context={window:{},ReviewCore:Core,location:{origin:'https://example.web.app'},navigator:{},URL,TextEncoder,crypto:crypto.webcrypto,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>{if(storage.fail)throw Error('Quota exceeded');storage.set(k,v);}},fetch:async(path,options)=>{requests.push({path,options});return {ok:!!files[path],json:async()=>files[path]};}};
 vm.runInNewContext(source,context);
 return {api:(path,body,reviewer='reviewer-1')=>context.window.StaticReview.api(path,body,reviewer),requests,storage};
}
const operation=(overrides={})=>({case_id:c.id,draft,context:{viewed_tabs:['detail'],pair_comparisons:[]},expected_revision:0,request_id:'request-0001',...overrides});

test('static saves persist locally, retain canonical provenance, and never POST evidence/feedback',async()=>{
 const app=make(),saved=await app.api('/api/reviews',operation());
 assert.equal(saved.revision,1);assert.equal(saved.review.mat_sha256,'mat-sha');assert.equal(saved.review.storage_mode,'browser');
 assert.equal(saved.review.reason_sources[0].kind,'jacob');assert.equal(saved.review.expert_confidence,'low');
 const reloaded=make(app.storage);assert.equal((await reloaded.api('/api/reviews')).reviews[c.id].review.expert_reason,draft.reason);
 assert.ok([...app.requests,...reloaded.requests].every(r=>(r.options?.method||'GET')==='GET'));
 assert.equal(app.requests.find(r=>r.path==='/share/manifest.json').options.cache,'no-cache');
});
test('reviewer histories and newly entered reasons are isolated; defaults remain available',async()=>{
 const app=make();await app.api('/api/reviews',operation({draft:{...draft,reason:'New browser reason'}}));
 assert.equal(Object.keys((await app.api('/api/reviews',undefined,'reviewer-2')).reviews).length,0);
 assert.equal((await app.api('/api/reasons?prefix=new',undefined,'reviewer-2')).reasons.length,0);
 assert.equal((await app.api('/api/reasons?prefix=wave',undefined,'reviewer-2')).reasons.length,1);
});
test('retry is idempotent, stale revisions conflict, and undo keeps its event',async()=>{
 const app=make();await app.api('/api/reviews',operation());await app.api('/api/reviews',operation());
 await assert.rejects(app.api('/api/reviews',operation({request_id:'request-0002'})),/another tab/);
 await app.api('/api/reviews/undo',operation({request_id:'request-0003',expected_revision:1}));
 const exported=await app.api('/api/export');assert.equal(exported.reviews.length,0);assert.equal(exported.events.length,2);
 assert.equal((await app.api('/api/reviews')).revisions[c.id],2);
});
test('failed browser quota write never reports success or creates a saved review',async()=>{
 const app=make();app.storage.fail=true;
 await assert.rejects(app.api('/api/reviews',operation()),/Quota exceeded/);
 assert.equal(Object.keys((await app.api('/api/reviews')).reviews).length,0);
});
test('backup restore validates provenance atomically and ignores fabricated reason sources',async()=>{
 const app=make();await app.api('/api/reviews',operation());const exported=await app.api('/api/export');
 exported.reviews[0].reason_sources=[{kind:'jacob',references:[{sheet:'FAKE'}]}];
 const preview=await app.api('/api/reviews/import',{payload:exported,preview:true},'reviewer-2');assert.equal(preview.available_to_import,1);
 const bad=JSON.parse(JSON.stringify(exported));bad.reviews[0].mat_sha256='wrong';
 await assert.rejects(app.api('/api/reviews/import',{payload:bad,preview:false},'reviewer-2'),/provenance/);
 assert.equal(Object.keys((await app.api('/api/reviews',undefined,'reviewer-2')).reviews).length,0);
 await app.api('/api/reviews/import',{payload:exported,preview:false},'reviewer-2');
 const restored=(await app.api('/api/export',undefined,'reviewer-2')).reviews[0];
 assert.equal(restored.reason_sources[0].references[0].sheet,'CH3');
 assert.equal((await app.api('/api/reviews/import',{payload:exported,preview:false},'reviewer-2')).added,0);
});
test('reason CSV supports quoted commas and no upload/import route is exposed',async()=>{
 const app=make();const result=await app.api('/api/reasons/import-file',{format:'csv',content:'text\n"New, quoted reason"\n'});
 assert.equal(result.added,1);assert.equal((await app.api('/api/reasons?prefix=new')).reasons[0].text,'New, quoted reason');
 await assert.rejects(app.api('/api/uploads',{filename:'data.mat'}),/unavailable/);
});
test('new cross-phase actions are rejected but a verified legacy backup is retained',async()=>{
 const app=make(),d={...draft,action:'MERGE',target:'U-2'};
 await assert.rejects(app.api('/api/reviews',operation({draft:d,context:{allowLegacy:true}})),/available expert action/);
 await app.api('/api/reviews',operation());
 const backup=await app.api('/api/export');
 backup.reviews[0]={...backup.reviews[0],...Core.buildReview(c,d,{},true)};
 await app.api('/api/reviews/import',{payload:backup,preview:false},'reviewer-2');
 const restored=(await app.api('/api/export',undefined,'reviewer-2')).reviews[0];
 assert.equal(restored.expert_action,'MERGE');assert.equal(restored.phase_contract_compatible,false);
});
test('anonymous agent reviews keep distinct identities, matching keys and missing-rationale provenance',async()=>{
 const a={...c,id:'matched--agent1',agent_id:'Agent_1',study_id:'anonymous-agents-v1',study_agent_id:'Agent_1',comparison_case_id:'matched-1',rationale_available:true};
 const b={...a,id:'matched--agent2',agent_id:'Agent_2',study_agent_id:'Agent_2',rationale_available:false,rationale:null};
 const app=make(new Map(),[a,b]);
 await app.api('/api/reviews',operation({case_id:a.id}));
 await app.api('/api/reviews',operation({case_id:b.id,request_id:'request-0002'}));
 const rows=(await app.api('/api/export')).reviews;
 assert.equal(rows.length,2);assert.deepEqual(Array.from(rows,r=>r.study_agent_id),['Agent_1','Agent_2']);
 assert.equal(rows[0].comparison_case_id,rows[1].comparison_case_id);assert.equal(rows[1].ai_rationale,null);
 await assert.rejects(app.api('/api/reviews',operation({case_id:b.id,expected_revision:1,request_id:'request-0003',draft:{...draft,disagreement_types:['rationale']}})),/No retained rationale/);
});
