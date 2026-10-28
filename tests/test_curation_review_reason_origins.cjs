const test=require('node:test'),assert=require('node:assert/strict');
const Core=require('../mockups/curation-review/dist/review-core.js');
test('Jacob source clusters come from quoted original actions, with discard sentinel excluded',()=>{
 const reason={text:'Evidence',sources:[{kind:'jacob',references:[{dataset_id:'file_CH3',raw_action:"'s 1'"},{dataset_id:'file_CH3',raw_action:"'m 0 211'"},{dataset_id:'file_CH31',raw_action:'m 31 211'}]}]};
 assert.deepEqual(Core.reasonClusters(reason),[{dataset_id:'file_CH3',cluster_ids:[1,211]},{dataset_id:'file_CH31',cluster_ids:[211,31]}]);
});
test('using a Jacob reason in a new review does not relabel its original source',()=>{
 const reason={text:'Evidence',sources:[{kind:'jacob',references:[{sheet:'file_CH3',raw_action:'s 1'}]}]};
 const reviews=[{dataset_id:'other',source_cluster:999,expert_reason:'Evidence'}];
 assert.deepEqual(Core.reasonClusters(reason,reviews),[{dataset_id:'file_CH3',cluster_ids:[1]}]);
});
test('reviewer reasons use matching saved reviews and preserve merge targets',()=>{
 const rows=[{dataset_id:'file_CH30',source_cluster:8,expert_reason:'Same shape',expert_pair:[8,21]},{dataset_id:'file_CH3',source_cluster:99,expert_reason:'Different reason'}];
 assert.deepEqual(Core.reasonClusters({text:' same   SHAPE ',sources:[{kind:'reviewer'}]},rows),[{dataset_id:'file_CH30',cluster_ids:[8,21]}]);
});
test('unknown or malformed provenance does not invent the current cluster',()=>{
 assert.deepEqual(Core.reasonClusters({text:'Unknown',sources:[]}),[]);
 assert.deepEqual(Core.reasonClusters({text:'Unknown',sources:[{kind:'jacob',references:[{sheet:'file',raw_action:'not an action'}]}]},[{source_cluster:999,expert_reason:'Unknown'}]),[]);
});
