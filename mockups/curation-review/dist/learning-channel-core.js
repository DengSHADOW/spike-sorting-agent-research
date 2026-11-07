/* Expert feedback interchange only. Does not run or modify a curation agent. */
(function(root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./curation-core.js'), require('./activity-core.js'));
  else root.LearningChannelCore = factory(root.CurationCore, root.ActivityCore);
})(typeof globalThis !== 'undefined' ? globalThis : this, function(C, A) {
  'use strict';
  const SCHEMA = 'spikeagent-expert-learning-v1';
  const PROTOCOL = 'spikeagent-learning-channel-v1';
  const LIMIT = 16 * 1024 * 1024;
  const copy = C.clone;
  function canonical(value) {
    if (Array.isArray(value)) return '['+value.map(canonical).join(',')+']';
    if (value && typeof value === 'object') return '{'+Object.keys(value).sort().map(k => JSON.stringify(k)+':'+canonical(value[k])).join(',')+'}';
    return JSON.stringify(value);
  }
  function annotations(workspace, reviewer) {
    if (!workspace || workspace.reviewer !== reviewer) throw Error('Open your own working dataset before preparing feedback.');
    return A.working([workspace]).history.filter(row => row.event === 'choice_applied' && row.actor === reviewer);
  }
  function snapshot(state) {
    return {atom_labels: copy(state.labels), row_labels: copy(state.row_labels || {}), hierarchy_tree: copy(state.tree)};
  }
  async function build(input, workspace, reviewer) {
    if (!input || workspace.source_sha256 !== input.data.source_sha256 || workspace.dataset_id !== input.data.dataset_id) throw Error('Feedback and input dataset do not match.');
    const rows = annotations(workspace, reviewer);
    if (!rows.length) throw Error('Confirm at least one expert annotation first. Drafts and canceled actions are not included.');
    const operations = C.retainedOperations(workspace.events);
    if (operations.length !== workspace.undo?.length) throw Error('This session lacks the exact grouping snapshots needed for learning feedback.');
    const groups = {}, examples = [], verified = new WeakMap();
    async function addGrouping(state, expected) {
      if (!/^[a-f0-9]{64}$/.test(expected || '')) throw Error('An annotation is missing its grouping fingerprint.');
      if (!verified.has(state)) {
        C.validateRowLabels(input, state);
        verified.set(state, await C.stateHash(input, state));
      }
      if (verified.get(state) !== expected) throw Error('An annotation grouping failed its integrity check. No feedback was prepared.');
      if (!groups[expected]) groups[expected] = snapshot(state);
    }
    for (const row of rows) {
      const event = row.raw, index = operations.findIndex(e => e.id === event.id);
      if (index < 0 || typeof event.id !== 'string' || !Number.isInteger(event.version)) throw Error('An annotation is missing its saved identity or revision.');
      const before = workspace.undo[index], after = workspace.undo[index + 1] || workspace;
      await addGrouping(before, event.state_before);
      await addGrouping(after, event.state_after);
      const choice = event.choice;
      examples.push({
        annotation_id: event.id, version: event.version, timestamp: event.timestamp || null,
        reviewer_id: row.actor, origin_workspace_id: event.workspace_id || workspace.workspace_id,
        task: {source_cluster: event.task.source, target_cluster: event.task.target ?? null, context: event.task.target == null ? 'single' : 'pair'},
        human: {
          action: choice.action, reason: String(choice.reason || ''), note: String(choice.note || ''),
          positive_comment: String(choice.positive || ''), confidence: choice.confidence || null,
          agreement_with_reviewed_agent: choice.agree ?? null,
          discard_mode: choice.action === 'DISCARD' ? choice.discard_mode || 'remove' : null,
          manual_split_rows: choice.manual_rows ? copy(choice.manual_rows) : null
        },
        reviewed_agent: {id: choice.agent_id || null, reference_id: choice.agent_reference_id || null},
        evidence: {viewed_tabs: copy(choice.viewed_tabs || []), sufficiency: choice.sufficiency || 'unspecified', reference_viewed: !!choice.jacob_reference_viewed},
        before_state_sha256: event.state_before, after_state_sha256: event.state_after,
        counts_before: copy(event.counts_before || []), counts_after: copy(event.counts_after || [])
      });
    }
    const content = {
      schema: SCHEMA, purpose: 'expert_feedback_learning',
      source: {
        dataset_id: workspace.dataset_id, source_sha256: workspace.source_sha256,
        initial_state_sha256: input.data.initial_state_sha256 || await C.stateHash(input, C.initial(input, reviewer)),
        workspace_id: workspace.workspace_id, workspace_version: workspace.version, reviewer_id: reviewer,
        n_spikes: input.data.n_spikes, n_samples: input.data.n_samples,
        sampling_rate_hz: input.data.sampling_rate_hz, atom_count: input.data.atoms.length,
        waveforms_included: false, dataset_required: true
      },
      annotations: examples, groupings: groups
    };
    const encoded = new TextEncoder().encode(canonical(content));
    if (encoded.byteLength > LIMIT) throw Error('This feedback snapshot exceeds the 16 MiB channel limit.');
    return {...content, feedback_sha256: await C.digest(encoded)};
  }
  function origin(value) {
    let url; try { url = new URL(value); } catch { throw Error('Enter the learning service HTTPS origin.'); }
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    if (url.username || url.password || url.search || url.hash || url.pathname !== '/' || (!local && url.protocol !== 'https:') || (local && !['http:', 'https:'].includes(url.protocol))) throw Error('Use an HTTPS origin, or HTTP localhost for development, without a path or credentials.');
    return url.origin;
  }
  function capabilities(value) {
    if (value?.protocol !== PROTOCOL || value.update_mode !== 'new_candidate_version' || !Array.isArray(value.agents) || !value.agents.length || value.agents.length > 100) throw Error('The service does not support the separate candidate-version learning protocol.');
    const ids = new Set();
    for (const a of value.agents) {
      if (!a || typeof a.id !== 'string' || !/^[A-Za-z0-9_.-]{1,100}$/.test(a.id) || ids.has(a.id) || typeof a.name !== 'string' || a.name.length > 160 || typeof a.version !== 'string' || !a.version || a.version.length > 100) throw Error('The service returned an invalid learner list.');
      ids.add(a.id);
    }
    return {protocol: PROTOCOL, update_mode: value.update_mode, agents: value.agents.map(a => ({id:a.id, name:a.name, version:a.version}))};
  }
  function job(value, expected) {
    if (!value || value.request_id !== expected.request_id || value.job_id !== expected.request_id || value.feedback_sha256 !== expected.feedback_sha256 || value.agent_id !== expected.agent_id || !['queued','running','succeeded','failed','canceled'].includes(value.status)) throw Error('The learning response does not match this submitted feedback and agent.');
    const result = value.result;
    if (value.status === 'succeeded' && (!result || typeof result.candidate_version !== 'string' || !result.candidate_version || result.candidate_version.length > 120 || result.candidate_version === expected.base_version || result.activated !== false)) throw Error('The service did not report a separate, inactive candidate version.');
    return {job_id:value.job_id, request_id:value.request_id, feedback_sha256:value.feedback_sha256, agent_id:value.agent_id, status:value.status,
      message: typeof value.message === 'string' ? value.message.slice(0,2000) : '',
      result: value.status === 'succeeded' ? {candidate_version:result.candidate_version, activated:false, summary:String(result.summary || '').slice(0,4000)} : null};
  }
  class Client {
    constructor(endpoint, token = '', fetcher = (...args) => globalThis.fetch(...args)) { this.endpoint = origin(endpoint); this.token = token; this.fetcher = fetcher; }
    async request(path, body) {
      const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 20000);
      try {
        const response = await this.fetcher(this.endpoint + path, {method:body ? 'POST' : 'GET', credentials:'omit', cache:'no-store', referrerPolicy:'no-referrer', redirect:'error', signal:controller.signal,
          headers:{Accept:'application/json', ...(this.token ? {Authorization:'Bearer '+this.token} : {}), ...(body ? {'Content-Type':'application/json'} : {})}, ...(body ? {body:JSON.stringify(body)} : {})});
        if (!response.ok) throw Error('Learning service returned HTTP '+response.status+'.');
        const text = await response.text(); if (text.length > 256000) throw Error('Learning service response is too large.');
        return JSON.parse(text);
      } finally { clearTimeout(timer); }
    }
    async connect() { return capabilities(await this.request('/api/learning/capabilities')); }
    async submit(packet, expected) {
      return job(await this.request('/api/learning/jobs', {protocol:PROTOCOL, request_id:expected.request_id, agent_id:expected.agent_id, base_version:expected.base_version, update_mode:'new_candidate_version', feedback:packet}), expected);
    }
    async status(expected) { return job(await this.request('/api/learning/jobs/'+encodeURIComponent(expected.request_id)), expected); }
  }
  return {SCHEMA, PROTOCOL, LIMIT, canonical, annotations, build, origin, capabilities, job, Client};
});
