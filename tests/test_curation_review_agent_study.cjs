const test=require('node:test');const assert=require('node:assert/strict');
const Study=require('../mockups/curation-review/dist/agent-study.js');
const catalog=[{id:'manual',source_kind:'initial',dataset_id:'other'},{id:'old',source_kind:'saved',dataset_id:'recording'},...Study.SLOTS.map(id=>({id,study_id:Study.ID,study_agent_id:id,source_kind:'comparison',dataset_id:'recording'}))];
test('default reviewer has only Agent 1 and Agent 2; reference is opt-in',()=>{
 assert.deepEqual(Study.available(catalog),['Agent_1','Agent_2']);
 assert.deepEqual(Study.available(catalog,true),Study.SLOTS);
 assert.equal(Study.label('Agent_1'),'Agent 1');assert.equal(Study.label('some-model'),'Historical agent');
});
test('agent catalog prioritizes matched cases and excludes legacy model data',()=>{
 assert.deepEqual(Study.visible(catalog,'Agent_1').map(b=>b.id),['Agent_1','manual']);
 assert.deepEqual(Study.visible(catalog,'Agent_2').map(b=>b.id),['Agent_2','manual']);
 assert.ok(!Study.visible(catalog,'Agent_3').some(b=>b.study_agent_id==='Agent_3'));
 assert.ok(!Study.visible(catalog,'Agent_3',true).some(b=>b.study_agent_id==='Agent_3'));
 assert.deepEqual(Study.available(catalog.filter(b=>b.study_agent_id!=='Agent_3'),true),Study.SLOTS);
 assert.equal(Study.choose(catalog,'Agent_3','recording'),null);
});
test('switching chooses genuine available agent data and matches reference identity',()=>{
 assert.equal(Study.choose(catalog,'Agent_2','recording').id,'Agent_2');
 assert.equal(Study.choose(catalog,'Agent_2','other').id,'Agent_2');
 assert.equal(Study.choose(catalog,'Agent_4','recording'),null);
 const cases=[{id:'a',comparison_case_id:'case-1'},{id:'b',comparison_case_id:'case-2'}];
 assert.equal(Study.matched(cases,'case-2').id,'b');assert.equal(Study.matched(cases,'unknown'),undefined);
});
test('default cluster tasks exclude every historical state and agent choice cannot multiply them',()=>{
 for(const agent of ['Agent_1','Agent_2'])assert.deepEqual(Study.queue(catalog,agent).map(b=>b.id),['manual']);
 assert.deepEqual(Study.queue(catalog,'Agent_1','history').map(b=>b.id),['Agent_1']);
 assert.deepEqual(Study.queue(catalog,'Agent_2','history').map(b=>b.id),['Agent_2']);
 assert.deepEqual(Study.queue(catalog.filter(b=>b.source_kind==='initial'),'Agent_1','history'),[]);
});
test('comparison counts are reviewer judgments, exclude legacy and distinguish defer',()=>{
 const row=(id,agree,action)=>({study_id:Study.ID,study_agent_id:id,expert_agree:agree,action});
 const summary=Study.summary({a:{review:row('Agent_1',true,'KEEP')},b:{review:row('Agent_2',false,'DISCARD')},c:{review:row('Agent_2',false,'DEFER')},old:{review:{agent_id:'other'}}});
 assert.equal(summary[0].agreed,1);assert.equal(summary[1].disagreed,1);assert.equal(summary[1].deferred,1);assert.equal(summary[1].reviewed,2);
});
