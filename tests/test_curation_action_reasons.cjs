const test=require('node:test'),assert=require('node:assert/strict');
const A=require('../mockups/curation-review/dist/action-reasons.js');
test('recorded Jacob m 0 is discard, while nonzero merge and split retain their own actions',()=>{
 assert.equal(A.rawAction("'m 0 228'"),'DISCARD');assert.equal(A.rawAction('m 12 8'),'MERGE');assert.equal(A.rawAction('s 211'),'SPLIT');assert.equal(A.rawAction('noisy waveform'),null);
});
test('nested source records classify generalized reasons without inferring actions from text',()=>{
 const rows=[{text:'High variability.',kind:'jacob_generalized'},{text:'Similar waveforms.',kind:'jacob_generalized'},{text:'Unknown old reason'}];
 const source=[{text:'Cluster 1 has high variability.',sources:[{kind:'jacob',references:[{raw_action:'s 1'}]}]},{text:'Cluster 2 has high variability.',sources:[{references:[{raw_action:'m 0 2'}]}]},{text:'Similar waveforms.',sources:[{references:[{raw_action:'m 8 9'}]}]}];
 const result=A.catalog(rows,source);
 assert.deepEqual(new Set(result.find(r=>r.text==='High variability.').actions),new Set(['SPLIT','DISCARD']));
 assert.deepEqual(A.forAction(result,'MERGE').map(r=>r.text),['Similar waveforms.']);
 assert(!result.some(r=>r.text==='Unknown old reason'));assert(!A.forAction(result,'KEEP').some(r=>r.kind==='jacob_generalized'));
});
test('custom reasons persist only for actions actually saved, with legitimate multiple-action reuse',()=>{
 const map=new Map(),store={getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,v)};
 A.remember(store,'My observation','SPLIT');let rows=A.catalog([],[],A.read(store));assert(A.forAction(rows,'SPLIT').some(r=>r.text==='My observation'));assert(!A.forAction(rows,'MERGE').some(r=>r.text==='My observation'));
 A.remember(store,'My observation','MERGE');rows=A.catalog([],[],A.read(store));assert(A.forAction(rows,'MERGE').some(r=>r.text==='My observation'));assert.equal(A.read(store).length,1);assert(!A.forAction(rows,'DISCARD').some(r=>r.text==='My observation'));
});
test('saved review action supplies missing reason provenance and no action offers no templates',()=>{
 const rows=A.catalog([{text:'Old custom note'}],[],[{text:'Old custom note',action:'NOT_MERGE'}]);assert(A.forAction(rows,'NOT_MERGE').some(r=>r.text==='Old custom note'));assert(!A.forAction(rows,'SPLIT').some(r=>r.text==='Old custom note'));assert.deepEqual(A.forAction(rows,null),[]);
});
