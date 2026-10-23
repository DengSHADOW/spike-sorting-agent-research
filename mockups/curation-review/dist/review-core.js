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
  function allowed(c) {
    return ['KEEP', ...(unit(c, c.source).split ? ['SPLIT'] : []), 'DISCARD',
      ...(candidates(c).length ? ['MERGE'] : []), ...(c.pair ? ['NOT_MERGE'] : []), 'DEFER'];
  }
  function differences(c, draft) {
    if(c.review_mode==='manual')return [];
    const result = new Set(draft.disagreement_types || []);
    if (draft.action !== c.ai) result.add('action');
    if (draft.action === 'MERGE' && c.ai === 'MERGE' && draft.target !== c.pair[1]) result.add('target');
    return [...result].filter(x => ['action', 'target', 'rationale', 'evidence'].includes(x));
  }
  function validate(c, draft) {
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
    if (!allowed(c).includes(draft.action)) return 'Choose an available expert action.';
    if (draft.action === 'MERGE' && !candidates(c).some(u => u.id === draft.target)) {
      return 'Choose a different cluster in this recording as the merge target.';
    }
    if (draft.agree && (draft.action !== c.ai ||
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
  function buildReview(c, draft, context = {}) {
    const error = validate(c, draft);
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
      schema: c.synthetic===false?'spikeagent-human-review-v3':SCHEMA, synthetic: c.synthetic!==false, review_mode: c.review_mode==='manual'?'manual':c.synthetic===false?'historical_model_review':'ai_assisted_review',
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
      phase_contract_compatible: PHASE_ACTIONS[c.phase_id].includes(draft.action),
      spike_counts: Object.fromEntries(c.units.map(u => [u.cluster_id, u.spikes])),
      displayed_trace_count: c.displayed_trace_count??24, waveform_units: c.waveform_units||'synthetic source units',
      evidence_id: c.evidence_id||`synthetic-${c.id}-v2`,
      visible_cluster_set: visible.map(id => unit(c, id).cluster_id),
      viewed_cluster_ids: viewed.map(id => unit(c, id).cluster_id),
      model_visible_cluster_set: modelIds.map(id => unit(c, id).cluster_id),
      viewed_tabs: tabs, rationale_viewed: !!context.rationale_viewed,
      pair_comparisons:pairs.map(pair=>pair.map(id=>unit(c,id).cluster_id)),
      additional_evidence_viewed: tabs.some(t => ['overview', 'gallery', 'stability', 'pair', 'quality'].includes(t)) || viewed.some(id => !modelIds.includes(id)),
      timestamp: context.timestamp || new Date().toISOString(), annotator: String(context.annotator || '').trim() || null
    };
  }
  function rememberReason(library, text, context = {}) {
    const clean = normalizeReason(text);
    if (!clean || library.some(r => reasonKey(r.text) === reasonKey(clean))) return library.slice();
    return [...library, {text: clean, provenance: context.provenance||'reviewer-entered-in-synthetic-demo',
      case_id: context.case_id || null, created_at: context.timestamp || new Date().toISOString()}];
  }
  function validStoredReview(c, r) {
    if (!r || r.schema !== SCHEMA || r.synthetic !== true || r.dataset_id !== c.dataset_id ||
        r.case_id !== c.id || r.phase !== c.phase_id || r.ai_action !== c.ai || r.ai_rationale !== c.rationale ||
        !allowed(c).includes(r.action) || typeof r.timestamp !== 'string' || !Number.isFinite(Date.parse(r.timestamp)) ||
        !Array.isArray(r.disagreement_types) || r.expert_action !== (r.action === 'DEFER' ? null : r.action)) return false;
    const aiPair = c.pair ? c.pair.map(id => unit(c, id).cluster_id) : null;
    if (JSON.stringify(r.ai_pair) !== JSON.stringify(aiPair)) return false;
    const target = r.action === 'MERGE' ? c.units.find(u => u.cluster_id === r.expert_merge_target)?.id : null;
    const expertPair = target ? [unit(c, c.source).cluster_id, unit(c, target).cluster_id] : null;
    if (JSON.stringify(r.expert_pair) !== JSON.stringify(expertPair)) return false;
    return !validate(c, {agree: r.expert_agree, action: r.action, target,
      reason: r.expert_reason, note: r.expert_note, disagreement_types: r.disagreement_types,
      confidence:r.expert_confidence,sufficiency:r.evidence_sufficiency,missing:r.missing_evidence,missing_note:r.missing_evidence_note});
  }
  return {SCHEMA, PHASE_ACTIONS, MISSING_EVIDENCE, normalizeReason, reasonKey, matchReasons, candidates, allowed, differences,
    validate, buildReview, rememberReason, validStoredReview};
});
