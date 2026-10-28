// Offline feedback-contract regressions. No server, datasets, browser, or model needed.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Core = require('../mockups/curation-review/dist/review-core.js');
const source = fs.readFileSync(path.join(__dirname, '../mockups/curation-review/dist/app.js'), 'utf8');
const cases = JSON.parse(vm.runInNewContext(source.split('let state =')[0] + '\nJSON.stringify(CASES)', {ReviewCore: Core}));
const keep = cases.find(c => c.ai === 'KEEP');
const pair = cases.find(c => c.pair);
const leaf = cases.find(c => c.kind === 'leaf');
const draft = overrides => ({agree: true, action: 'KEEP', target: '', reason: '', note: '', positive: '', disagreement_types: [], ...overrides});

test('reason autocomplete matches only the beginning, ignoring case and normalized whitespace', () => {
  const reasons = [{text:'Waveform contamination'}, {text:'Mixed waveform populations'}, {text:'Waveform artifacts'}];
  assert.deepEqual(Core.matchReasons(reasons, 'wAvE').map(r => r.text), ['Waveform contamination','Waveform artifacts']);
  assert.deepEqual(Core.matchReasons(reasons, 'form'), []);
  assert.deepEqual(Core.matchReasons(reasons, '  mixed   wave').map(r => r.text), ['Mixed waveform populations']);
  assert.deepEqual(Core.matchReasons(reasons, 'ＷＡＶＥ').map(r => r.text), ['Waveform contamination','Waveform artifacts']);
});

test('manual MAT review keeps AI fields null and permits KEEP without a reason', () => {
  const c={...keep,synthetic:false,review_mode:'manual',ai:null,rationale:null,agent_id:null,agent_kind:'none'};
  const d=draft({agree:null,positive:'Clear waveform'});
  assert.equal(Core.validate(c,d),null);
  const r=Core.buildReview(c,d);
  assert.equal(r.review_mode,'manual');assert.equal(r.ai_action,null);assert.equal(r.ai_source_cluster,null);
  assert.equal(r.agent_id,null);assert.deepEqual(r.model_visible_cluster_set,[]);
  assert.equal(r.expert_positive_comment,'Clear waveform');
  assert.match(Core.validate(c,{...d,action:'DISCARD'}),/reason or note/);
  assert.match(Core.validate(c,{...d,disagreement_types:['rationale']}),/no AI disagreement/);
});

test('agreement needs no comment and preserves the full AI rationale', () => {
  const r = Core.buildReview(keep, draft(), {timestamp: '2026-10-21T12:00:00Z'});
  assert.equal(r.expert_agree, true);
  assert.equal(r.expert_positive_comment, '');
  assert.equal(r.ai_rationale, keep.rationale);
  assert.equal(r.execution_status, 'not_executed');
});

test('rationale-only disagreement remains a disagreement after serialization and reload', () => {
  const r = Core.buildReview(keep, draft({agree: false, reason: 'The waveform alone does not establish isolation.', disagreement_types: ['rationale']}));
  assert.equal(r.ai_action, r.expert_action);
  assert.equal(r.expert_agree, false);
  assert.deepEqual(r.disagreement_types, ['rationale']);
  assert.equal(Core.validStoredReview(keep, JSON.parse(JSON.stringify(r))), true);
});

test('incomplete or contradictory judgments cannot be saved', () => {
  assert.match(Core.validate(keep, draft({agree: null})), /Agree or Disagree/);
  assert.match(Core.validate(keep, draft({agree: false, disagreement_types: ['rationale']})), /reason or note/);
  assert.match(Core.validate(keep, draft({agree: false, note: 'A correction'})), /mark rationale or evidence/);
  assert.match(Core.validate(keep, draft({action: 'DISCARD'})), /Agree must preserve/);
});

test('changing a merge target preserves distinct AI and expert pairs', () => {
  const r = Core.buildReview(pair, draft({agree: false, action: 'MERGE', target: 'U-034', reason: 'Compare the other template.'}));
  assert.deepEqual(r.ai_pair, [8, 21]);
  assert.deepEqual(r.expert_pair, [8, 34]);
  assert.deepEqual(r.disagreement_types, ['target']);
  assert.equal(Core.validStoredReview(pair, JSON.parse(JSON.stringify(r))), true);
  assert.equal(r.spike_counts[8] + r.spike_counts[21], pair.spikes);
});

test('self merges, missing targets, and clusters from another recording are rejected', () => {
  for (const target of [pair.source, '', keep.source, 'U-999']) {
    assert.match(Core.validate(pair, draft({agree: false, action: 'MERGE', target, reason: 'test'})), /different cluster/);
  }
  assert.match(Core.validate(pair, draft({action: 'MERGE', target: 'U-034'})), /Agree must preserve/);
});

test('pair reviews allow DISCARD; DEFER stays outside the backend action contract', () => {
  const r = Core.buildReview(pair, draft({agree: false, action: 'DISCARD', reason: 'Source is an artifact.'}));
  assert.equal(r.expert_action, 'DISCARD');
  assert.equal(r.phase_contract_compatible, true);
  assert.equal(r.expert_merge_target, null);
  const deferred = Core.buildReview(keep, draft({agree: false, action: 'DEFER', note: 'Need additional evidence.'}));
  assert.equal(deferred.review_status, 'deferred');
  assert.equal(deferred.expert_action, null);
  assert.equal(deferred.phase_contract_compatible, false);
  assert.equal(Core.validStoredReview(keep, deferred), true);
});

test('phase actions are enforced on new judgments while legacy feedback remains readable', () => {
  assert.equal(Core.allowed(leaf).includes('SPLIT'), false);
  assert.match(Core.validate(leaf, draft({action: 'SPLIT'})), /available expert action/);
  assert.deepEqual(Core.allowed(pair), ['MERGE','NOT_MERGE','DISCARD','DEFER']);
  assert.equal(Core.allowed(keep).includes('MERGE'), false);
  assert.match(Core.validate(pair,draft({agree:false,action:'KEEP',reason:'test'})),/available expert action/);
  const d=draft({agree: false, action: 'MERGE', target: keep.units[1].id, reason: 'Compare this other cluster.'});
  assert.throws(()=>Core.buildReview(keep,d,{allowLegacy:true}),/available expert action/);
  const r = Core.buildReview(keep,d,{},true);
  assert.equal(r.phase, 'phase1');
  assert.equal(r.phase_contract_compatible, false);
  assert.equal(r.execution_status, 'not_executed');
  assert.equal(Core.validStoredReview(keep,r),true);
  assert.match(Core.validate(pair,draft({agree:false,action:'NOT_MERGE',target:'U-034',reason:'test'})),/recorded pair/);
});

test('positive comments and reusable reasons keep their distinct purposes and provenance', () => {
  const r = Core.buildReview(keep, draft({positive: 'Particularly stable waveform.'}));
  assert.equal(r.expert_positive_comment, 'Particularly stable waveform.');
  assert.equal(r.expert_reason, '');
  const library = Core.rememberReason([], '  Mixed\n waveform  populations ', {case_id: keep.id});
  assert.equal(library[0].text, 'Mixed waveform populations');
  assert.equal(library[0].provenance, 'reviewer-entered-in-synthetic-demo');
  assert.equal(Core.rememberReason(library, 'mixed waveform populations').length, 1);
  assert.equal(Core.rememberReason(library, '').length, 1);
});

test('provenance distinguishes additional human evidence and does not invent real hashes', () => {
  const r = Core.buildReview(pair, draft({action: 'MERGE', target: 'U-021'}), {
    viewed_cluster_ids: pair.units.map(u => u.id), visible_cluster_ids: pair.units.map(u => u.id),
    viewed_tabs: ['detail', 'overview', 'gallery'], annotator: 'reviewer-1', rationale_viewed: true
  });
  assert.equal(r.additional_evidence_viewed, true);
  assert.deepEqual(r.model_visible_cluster_set, [8, 21]);
  assert.deepEqual(r.viewed_cluster_ids, [8, 21, 34]);
  assert.equal(r.state_sha256, null);
  assert.equal(r.request_sha256, null);
  assert.equal(r.agent_id, 'Agent_1');
  assert.equal(r.annotator, 'reviewer-1');
  assert.equal(Core.validStoredReview(pair, {...r, dataset_id: keep.dataset_id}), false);
});

test('malformed stored feedback is rejected before it can corrupt history or exports', () => {
  const r = Core.buildReview(pair, draft({action: 'MERGE', target: 'U-021'}));
  for (const altered of [
    {...r, ai_action: 'UNKNOWN'}, {...r, ai_pair: [8, 34]}, {...r, expert_pair: [8, 34]},
    {...r, disagreement_types: {}}, {...r, timestamp: 'invalid-date'}, {...r, expert_action: null}
  ]) assert.equal(Core.validStoredReview(pair, altered), false);
});

test('confidence, missing evidence and compared pairs round-trip without converting defer into disagreement certainty',()=>{
  const d=draft({agree:false,action:'DEFER',confidence:'low',sufficiency:'insufficient',missing:['timing'],missing_note:'Need pair evidence'});
  assert.equal(Core.validate(keep,d),null);
  const r=Core.buildReview(keep,d,{viewed_tabs:['pair','quality'],pair_comparisons:[[keep.source,keep.units[1].id]]});
  assert.equal(r.expert_confidence,'low');assert.equal(r.evidence_sufficiency,'insufficient');
  assert.deepEqual(r.missing_evidence,['timing']);assert.equal(r.expert_action,null);
  assert.equal(r.additional_evidence_viewed,true);
  assert.equal(Core.validStoredReview(keep,JSON.parse(JSON.stringify(r))),true);
});

test('invalid confidence and inconsistent missing-evidence requests cannot be saved',()=>{
  assert.match(Core.validate(keep,draft({confidence:'certain'})),/confidence/);
  assert.match(Core.validate(keep,draft({sufficiency:'sufficient',missing:['spatial']})),/insufficient/);
  assert.match(Core.validate(keep,draft({sufficiency:'insufficient',missing:['other']})),/other missing/);
  assert.throws(()=>Core.buildReview(keep,draft(),{pair_comparisons:[[keep.source,keep.source]]}),/compared cluster/);
});

test('explicit comparison context enables merge and keep-separate from a recorded single-cluster case',()=>{
 const target=keep.units.find(u=>u.id!==keep.source).id;
 for(const action of ['MERGE','NOT_MERGE']){
  const d=draft({agree:false,scope:'pair',target,action,reason:'Selected candidate pair judgment'}),r=Core.buildReview(keep,d);
  assert.equal(r.phase,'phase1');assert.equal(r.judgment_context,'pair');assert.deepEqual(r.judgment_pair,[keep.units.find(u=>u.id===keep.source).cluster_id,keep.units.find(u=>u.id===target).cluster_id]);assert.equal(r.phase_contract_compatible,false);assert.equal(r.context_contract_compatible,true);assert.equal(Core.validStoredReview(keep,r),true);
 }
 assert.match(Core.validate(keep,draft({agree:false,scope:'pair',target:keep.source,action:'MERGE',reason:'bad'})),/available expert action/);
});
test('clearing a recorded pair allows single-cluster judgments without rewriting recorded provenance',()=>{
 const r=Core.buildReview(pair,draft({agree:false,scope:'single',action:'KEEP',reason:'Inspect source alone'}));assert.equal(r.phase,'phase2');assert.equal(r.judgment_context,'single');assert.equal(r.judgment_pair,null);assert.deepEqual(r.ai_pair,[8,21]);assert.equal(Core.validStoredReview(pair,r),true);
 const d=draft({agree:true,scope:'pair',target:'U-034',action:'MERGE'});assert.match(Core.validate(pair,d),/Agree must preserve/);
});

test('same action on a different comparison object records an object correction',()=>{const c={...keep,ai:'DISCARD'},d=draft({agree:false,scope:'pair',target:keep.units[1].id,action:'DISCARD',reason:'Source judged with this comparison'}),r=Core.buildReview(c,d);assert.deepEqual(r.disagreement_types,['target']);assert.equal(r.judgment_context,'pair');});
