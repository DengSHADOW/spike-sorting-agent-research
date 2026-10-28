/* Pure feedback rules. This module never executes curation actions or calls a model. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ReviewCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SCHEMA = 'spikeagent-human-interface-mockup-v2';
  const PHASE_ACTIONS = {
    phase1: ['KEEP', 'SPLIT', 'DISCARD'],
    phase2: ['MERGE', 'NOT_MERGE', 'DISCARD']
  };
  const normalizeReason = text => String(text || '').trim().replace(/\s+/g, ' ');
  const reasonKey = text => normalizeReason(text).normalize('NFKC').toLocaleLowerCase();
  const matchReasons = (library, prefix) => library.filter(r => reasonKey(r.text).startsWith(reasonKey(prefix)));
  const unit = (c, id) => c.units.find(u => u.id === id);
  const candidates = c => c.units.filter(u => u.id !== c.source);
  const MISSING_EVIDENCE=['waveform','timing','amplitude','spatial','split_tree','recording_context','other'];
  function legacyAllowed(c) {
    return ['KEEP', ...(unit(c, c.source).split ? ['SPLIT'] : []), 'DISCARD',
      ...(candidates(c).length ? ['MERGE'] : []), ...(c.pair ? ['NOT_MERGE'] : []), 'DEFER'];
  }
  function judgmentScope(c,draft={}){return draft.scope??(c.pair?'pair':'single');}
  function agreementMatches(c,draft){return draft.scope===undefined||judgmentScope(c,draft)===(c.pair?'pair':'single')&&(draft.scope!=='pair'||draft.target===c.pair?.[1]);}
  function allowed(c,draft={}){
    if(draft.scope!==undefined){if(!['single','pair'].includes(draft.scope))return [];const valid=draft.scope==='single'||candidates(c).some(u=>u.id===draft.target);return valid?PHASE_ACTIONS[draft.scope==='pair'?'phase2':'phase1'].filter(a=>a!=='SPLIT'||unit(c,c.source).split).concat('DEFER'):[];}

    const pair=Array.isArray(c.pair)&&c.pair.length===2&&c.pair[0]!==c.pair[1]&&c.pair.every(id=>unit(c,id));
    return (PHASE_ACTIONS[c.phase_id]||[]).filter(action=>action==='SPLIT'?unit(c,c.source).split:!['MERGE','NOT_MERGE'].includes(action)||pair).concat('DEFER');
  }
  function differences(c, draft) {
    if(c.review_mode==='manual')return [];
    const result = new Set(draft.disagreement_types || []);
    if(!agreementMatches(c,draft))result.add('target');
    if (draft.action !== c.ai) result.add('action');
    if (['MERGE','NOT_MERGE'].includes(draft.action) && draft.action === c.ai && draft.target !== c.pair[1]) result.add('target');
    return [...result].filter(x => ['action', 'target', 'rationale', 'evidence'].includes(x));
  }
  function validate(c, draft, allowLegacy=false) {
    if(c.rationale_available===false&&(draft.disagreement_types||[]).includes('rationale'))return 'No retained rationale exists; review the action or evidence instead.';
    const manual=c.review_mode==='manual';
    const sufficiency=draft.sufficiency??'unspecified',missing=draft.missing??[],missingNote=draft.missing_note??'';
    if(draft.confidence!=null&&!['high','medium','low'].includes(draft.confidence))return 'Choose a valid confidence level.';
    if(!['unspecified','sufficient','insufficient'].includes(sufficiency))return 'Choose a valid evidence sufficiency.';
    if(!Array.isArray(missing)||missing.some(item=>!MISSING_EVIDENCE.includes(item)))return 'Choose valid missing evidence categories.';
    if(typeof missingNote!=='string'||missingNote.length>2000)return 'Missing evidence note is invalid or too long.';
    if(sufficiency!=='insufficient'&&(missing.length||missingNote.trim()))return 'Mark evidence as insufficient when requesting additional evidence.';
    if(sufficiency==='insufficient'&&!missing.length&&!missingNote.trim()&&!String(draft.note||'').trim())return 'Choose missing evidence or explain what is needed.';
    if(missing.includes('other')&&!missingNote.trim()&&!String(draft.note||'').trim())return 'Explain the other missing evidence.';
    if (manual?draft.agree!==null:typeof draft.agree !== 'boolean') return manual?'Manual review has no AI agreement.':'Choose Agree or Disagree first.';
    if (!Array.isArray(draft.disagreement_types) || draft.disagreement_types.some(t => !['action', 'target', 'rationale', 'evidence'].includes(t))) {
      return 'Invalid disagreement categories.';
    }
    if(manual&&draft.disagreement_types.length)return 'Manual reviews have no AI disagreement categories.';
    if (!(allowLegacy&&draft.scope===undefined?legacyAllowed(c):allowed(c,draft)).includes(draft.action)) return 'Choose an available expert action for this single-cluster or pair context.';
    if (draft.action === 'MERGE' && !candidates(c).some(u => u.id === draft.target)) {
      return 'Choose a different cluster in this recording as the merge target.';
    }
    if(draft.scope===undefined&&!allowLegacy&&c.phase_id==='phase2'&&draft.action==='NOT_MERGE'&&draft.target&&draft.target!==c.pair[1])return 'Keep separate applies to the recorded pair. Select that target or choose a different action.';
    if (draft.agree && (!agreementMatches(c,draft) || draft.action !== c.ai ||
        (c.ai === 'MERGE' && draft.target !== c.pair[1]) ||
        (draft.disagreement_types || []).length)) return 'Agree must preserve the AI action, target, and rationale.';
    if (((!draft.agree&&!manual)||(manual&&draft.action!=='KEEP')) && !normalizeReason(draft.reason) && !String(draft.note || '').trim() && !(draft.action==='DEFER'&&sufficiency==='insufficient'&&(missing.length||missingNote.trim()))) {
      return 'Add a reason or note for your disagreement.';
    }
    if (!manual && !draft.agree && !differences(c, draft).length) {
      return 'If the action and target are unchanged, mark rationale or evidence as the disagreement.';
    }
    return null;
  }
  function buildReview(c, draft, context = {}, allowLegacy=false) {
    if(c.rationale_available===false&&context.rationale_viewed)throw new Error('No retained rationale exists for this response.');
    const error = validate(c, draft, allowLegacy);
    if (error) throw new Error(error);
    const source = unit(c, c.source);
    const aiTarget = c.pair ? unit(c, c.pair[1]) : null;
    const expertTarget = draft.action === 'MERGE' ? unit(c, draft.target) : null;
    const viewed = [...new Set(context.viewed_cluster_ids || [c.source])].filter(id => unit(c, id));
    const visible = [...new Set(context.visible_cluster_ids || [c.source])].filter(id => unit(c, id));
    const modelIds = c.review_mode==='manual'?[]:c.pair || [c.source];
    const tabs = [...new Set(context.viewed_tabs || ['detail'])];
    const pairs=context.pair_comparisons||[];
    if(!Array.isArray(pairs)||pairs.length>c.units.length||pairs.some(pair=>!Array.isArray(pair)||pair.length!==2||pair[0]!==c.source||pair[0]===pair[1]||pair.some(id=>!unit(c,id))))throw new Error('Invalid compared cluster pair.');
    return {
      ...Object.fromEntries(['study_id','study_agent_id','comparison_case_id','rationale_available','response_sha256','evidence_scope','comparison_protocol_note'].filter(key=>key in c).map(key=>[key,c[key]])),
      schema: c.synthetic===false?'spikeagent-human-review-v3':SCHEMA, synthetic: c.synthetic!==false, review_mode: c.study_id?'blinded_historical_comparison':c.review_mode==='manual'?'manual':c.synthetic===false?'historical_model_review':'ai_assisted_review',
      case_id: c.id, dataset_id: c.dataset_id, recording_id: c.dataset_id,
      run_id: c.run_id||'synthetic-scripted-v2', step_id: c.step_id||c.id, phase: c.phase_id,
      state_id: c.state_id||`synthetic-${c.id}-v2`, state_sha256: c.state_sha256||null,
      request_sha256: c.request_sha256||null, image_sha256: c.image_sha256||null,
      current_cluster: source.cluster_id, source_cluster: source.cluster_id,
      channel: c.channel, agent_id: c.synthetic===false?c.agent_id:'Agent_1', agent_kind: c.agent_kind||'scripted-demo',
      ai_action: c.ai, ai_source_cluster: c.review_mode==='manual'?null:source.cluster_id,
      ai_merge_target: aiTarget ? aiTarget.cluster_id : null,
      ai_pair: aiTarget ? [source.cluster_id, aiTarget.cluster_id] : null,
      ai_rationale: c.rationale, expert_agree: draft.agree,
      disagreement_types: draft.agree ? [] : differences(c, draft),
      expert_action: draft.action === 'DEFER' ? null : draft.action,
      expert_merge_target: expertTarget ? expertTarget.cluster_id : null,
      expert_pair: expertTarget ? [source.cluster_id, expertTarget.cluster_id] : null,
      expert_reason: draft.agree ? '' : normalizeReason(draft.reason),
      expert_note: draft.agree ? '' : String(draft.note || '').trim(),
      expert_positive_comment: draft.agree||c.review_mode==='manual' ? String(draft.positive || '').trim() : '',
      expert_confidence:draft.confidence??null,evidence_sufficiency:draft.sufficiency??'unspecified',
      missing_evidence:[...new Set(draft.missing||[])],missing_evidence_note:String(draft.missing_note||'').trim(),
      review_status: draft.action === 'DEFER' ? 'deferred' : 'reviewed',
      action: draft.action, execution_status: 'not_executed',
      ...(draft.scope!==undefined?{judgment_context:draft.scope,judgment_pair:draft.scope==='pair'?[source.cluster_id,unit(c,draft.target).cluster_id]:null,context_contract_compatible:draft.action!=='DEFER'}:{}),
      phase_contract_compatible: PHASE_ACTIONS[c.phase_id].includes(draft.action),
      spike_counts: Object.fromEntries(c.units.map(u => [u.cluster_id, u.spikes])),
      displayed_trace_count: c.displayed_trace_count??24, waveform_units: c.waveform_units||'synthetic source units',
      evidence_id: c.evidence_id||`synthetic-${c.id}-v2`,
      visible_cluster_set: visible.map(id => unit(c, id).cluster_id),
      viewed_cluster_ids: viewed.map(id => unit(c, id).cluster_id),
      model_visible_cluster_set: modelIds.map(id => unit(c, id).cluster_id),
      quality_evidence:context.quality_evidence?JSON.parse(JSON.stringify(context.quality_evidence)):null,viewed_tabs: tabs, rationale_viewed: !!context.rationale_viewed,
      pair_comparisons:pairs.map(pair=>pair.map(id=>unit(c,id).cluster_id)),
      additional_evidence_viewed: Boolean(c.study_id)||tabs.some(t => ['overview', 'gallery', 'stability', 'pair', 'quality', 'correlation', 'pca'].includes(t)) || viewed.some(id => !modelIds.includes(id)),
      jacob_reference_viewed: Boolean(context.jacob_reference_viewed),
      timestamp: context.timestamp || new Date().toISOString(), annotator: String(context.annotator || '').trim() || null
    };
  }
  function rememberReason(library, text, context = {}) {
    const clean = normalizeReason(text);
    if (!clean || library.some(r => reasonKey(r.text) === reasonKey(clean))) return library.slice();
    return [...library, {text: clean, provenance: context.provenance||'reviewer-entered-in-synthetic-demo',
      case_id: context.case_id || null, created_at: context.timestamp || new Date().toISOString()}];
  }
  function reasonClusters(reason,reviews=[]) {
    const groups=new Map();
    function add(dataset,ids){
      const valid=ids.filter(id=>Number.isSafeInteger(id)&&id>=0);
      if(!valid.length)return;
      const key=typeof dataset==='string'?dataset:'';
      if(!groups.has(key))groups.set(key,new Set());
      valid.forEach(id=>groups.get(key).add(id));
    }
    const originals=(reason.sources||[]).filter(source=>source.kind==='jacob');
    for(const original of originals)for(const ref of original.references||[]){
      const action=normalizeReason(ref.raw_action).replace(/^['"]+|['"]+$/g,'').trim();
      const split=action.match(/^s\s+(\d+)$/i),merge=action.match(/^m\s+(\d+)\s+(\d+)$/i);
      if(split)add(ref.dataset_id||ref.sheet,[Number(split[1])]);
      if(merge)add(ref.dataset_id||ref.sheet,[Number(merge[2]),...(Number(merge[1])?[Number(merge[1])]:[])]);
    }
    if(!originals.length)for(const item of reviews){
      const review=item.review||item;
      if(reasonKey(review.expert_reason)!==reasonKey(reason.text))continue;
      add(review.dataset_id,review.judgment_pair||review.expert_pair||[review.source_cluster??review.current_cluster]);
    }
    return [...groups].map(([dataset_id,ids])=>({dataset_id,cluster_ids:[...ids]}));
  }
  function validStoredReview(c, r) {
    if (!r || r.schema !== SCHEMA || r.synthetic !== true || r.dataset_id !== c.dataset_id ||
        r.case_id !== c.id || r.phase !== c.phase_id || r.ai_action !== c.ai || r.ai_rationale !== c.rationale ||
        !(r.judgment_context===undefined?legacyAllowed(c):['KEEP','SPLIT','MERGE','NOT_MERGE','DISCARD','DEFER']).includes(r.action) || typeof r.timestamp !== 'string' || !Number.isFinite(Date.parse(r.timestamp)) ||
        !Array.isArray(r.disagreement_types) || r.expert_action !== (r.action === 'DEFER' ? null : r.action)) return false;
    const aiPair = c.pair ? c.pair.map(id => unit(c, id).cluster_id) : null;
    if (JSON.stringify(r.ai_pair) !== JSON.stringify(aiPair)) return false;
    const target = r.action === 'MERGE' ? c.units.find(u => u.cluster_id === r.expert_merge_target)?.id : null;
    const scopeTarget=r.judgment_context==='pair'?c.units.find(u=>u.cluster_id===r.judgment_pair?.[1])?.id:target;
    const expertPair = target ? [unit(c, c.source).cluster_id, unit(c, target).cluster_id] : null;
    if (JSON.stringify(r.expert_pair) !== JSON.stringify(expertPair)) return false;
    return !validate(c, {agree: r.expert_agree, action: r.action, target:scopeTarget,...(r.judgment_context!==undefined?{scope:r.judgment_context}:{}),
      reason: r.expert_reason, note: r.expert_note, disagreement_types: r.disagreement_types,
      confidence:r.expert_confidence,sufficiency:r.evidence_sufficiency,missing:r.missing_evidence,missing_note:r.missing_evidence_note},true);
  }
  function findClusterReview(reviews,c,targetId){
    const target=unit(c,targetId);if(!target)return null;
    const identity=c.study_agent_id||c.agent_id||null;
    const matches=reviews.map(item=>item.review||item).filter(r=>r.dataset_id===c.dataset_id&&
      (r.source_cluster??r.current_cluster)===target.cluster_id&&(r.study_agent_id||r.agent_id||null)===identity);
    const exact=matches.filter(r=>r.state_sha256===c.state_sha256);
    const rows=(exact.length?exact:matches).sort((a,b)=>String(b.timestamp||'').localeCompare(String(a.timestamp||'')));
    return rows.length?{review:rows[0],exact_state:exact.length>0}:null;
  }
  return {SCHEMA, PHASE_ACTIONS, MISSING_EVIDENCE, normalizeReason, reasonKey, matchReasons, candidates, allowed, judgmentScope, agreementMatches, differences,findClusterReview,
    validate, buildReview, rememberReason, reasonClusters, validStoredReview};
});
