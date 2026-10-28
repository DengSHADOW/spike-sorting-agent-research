const test=require('node:test');const assert=require('node:assert/strict');
const Profiles=require('../mockups/curation-review/dist/reviewer-profiles.js');
const Audit=require('../mockups/curation-review/dist/review-audit.js');
const store=()=>{const rows=new Map();return {getItem:k=>rows.get(k)||null,setItem:(k,v)=>rows.set(k,v)}};
test('profile selection keeps immutable identities separate and renames in place',()=>{
 const s=store();Profiles.remember(s,'reviewer-0001','Alice');Profiles.remember(s,'reviewer-0002','Bob');Profiles.remember(s,'reviewer-0001','Alice updated');
 assert.deepEqual(Profiles.list(s),[{id:'reviewer-0001',name:'Alice updated'},{id:'reviewer-0002',name:'Bob'}]);
 assert.equal(Profiles.initials('张 三'),'张三');assert.equal(Profiles.initials(''),'ID');
 assert.throws(()=>Profiles.remember(s,'bad','name'),/Invalid/);
});
test('audit shows actual save, edit and undo field changes for the selected scope',()=>{
 const r={action:'KEEP',expert_note:'first',expert_agree:true,study_agent_id:'Agent_1'};
 const entries=Audit.entries([
  {type:'chart_viewed',case_id:'one'},
  {type:'review_saved',case_id:'one',revision:1,review:r},
  {type:'review_saved',case_id:'other',review:r},
  {type:'review_saved',case_id:'one',revision:2,review:{...r,expert_note:'second',expert_agree:false}},
  {type:'review_undone',case_id:'one',revision:3,review:null}
 ],new Set(['one']));
 assert.equal(entries.length,3);assert.deepEqual(entries.map(e=>e.revision),[3,2,1]);
 assert.equal(entries[0].changes.find(c=>c.field==='action').after,null);
 assert.deepEqual(entries[1].changes.find(c=>c.field==='expert_note'),{field:'expert_note',before:'first',after:'second'});
 assert.ok(entries[1].changes.some(c=>c.field==='expert_agree'));assert.equal(entries[0].agent_id,'Agent_1');
 assert.deepEqual(Audit.entries([],new Set(['one'])),[]);
});
test('legacy flattened demo events retain honest null revisions',()=>{
 const result=Audit.entries([{type:'review_saved',case_id:'demo',action:'KEEP'},{type:'review_undone',case_id:'demo'}],new Set(['demo']));
 assert.equal(result[0].revision,null);assert.equal(result[0].changes[0].after,null);
});
