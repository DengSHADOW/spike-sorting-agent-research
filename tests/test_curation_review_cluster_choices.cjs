const test=require('node:test'),assert=require('node:assert/strict');
const Core=require('../mockups/curation-review/dist/review-core.js');
const c={dataset_id:'recording',state_sha256:'current',agent_id:'Agent_1',units:[{id:'a',cluster_id:1},{id:'b',cluster_id:2}]};
const row=(extra={})=>({case_id:'review-b',dataset_id:'recording',state_sha256:'current',agent_id:'Agent_1',source_cluster:2,action:'KEEP',timestamp:'2026-10-26T10:00:00Z',...extra});
test('candidate choices prefer an exact state even when a later review exists elsewhere',()=>{
 const result=Core.findClusterReview([row({state_sha256:'other',timestamp:'2026-10-26T12:00:00Z'}),row()],c,'b');assert.equal(result.exact_state,true);assert.equal(result.review.state_sha256,'current');
});
test('older-state choices are explicitly distinguished and never treated as current',()=>{
 const result=Core.findClusterReview([row({state_sha256:'earlier'})],c,'b');assert.equal(result.exact_state,false);
});
test('other agents, recordings and merge-target-only appearances are not candidate source reviews',()=>{
 const records=[row({agent_id:'Agent_2'}),row({dataset_id:'different'}),row({source_cluster:1,expert_merge_target:2,expert_pair:[1,2]})];
 assert.equal(Core.findClusterReview(records,c,'b'),null);assert.equal(Core.findClusterReview([row()],c,'missing'),null);
});
