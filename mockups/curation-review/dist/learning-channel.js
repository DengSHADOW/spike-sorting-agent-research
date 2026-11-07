/* An explicit connector for a future learner; no endpoint is configured by default. */
window.LearningChannel = (() => {
  'use strict';
  const L = LearningChannelCore, esc = escapeHtml;
  let packet = null, client = null, service = null, receipt = null, busy = false;
  const button = document.createElement('button');
  button.type = 'button'; button.id = 'learning-channel-button'; button.className = 'nav-item';
  button.title = 'Agent learning'; button.setAttribute('aria-label', 'Agent learning');
  button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m3 8 9-5 9 5-9 5-9-5Zm4 3v6l5 3 5-3v-6M21 8v8"/></svg><span>Learning</span>';
  document.querySelector('.rail-nav').append(button);
  const dialog = document.createElement('dialog'); dialog.id = 'learning-channel-dialog'; dialog.className = 'interface-dialog learning-channel-dialog';
  dialog.setAttribute('aria-labelledby', 'learning-channel-title');
  dialog.innerHTML = `<div class="modal-heading"><h2 id="learning-channel-title">Agent learning</h2><button type="button" class="icon-button" id="learning-close" aria-label="Close agent learning">×</button></div>
    <p>Create a separate candidate version from human expert annotations. Existing Agent 1 / Agent 2 and their saved results stay unchanged.</p>
    <section class="settings-section"><h3>Expert annotations</h3><div id="learning-summary">Open a working dataset to prepare feedback.</div>
      <p class="field-help">Current dataset and reviewer only. Confirmed actions, reasons and before/after grouping are included; drafts and canceled actions are excluded. Raw waveforms are not included: the learner needs the matching source dataset.</p>
      <div class="settings-actions"><button class="button small" id="learning-prepare">Refresh annotations</button><button class="button small" id="learning-export" disabled>Export feedback packet</button><button class="button primary" id="learning-start" disabled>Start learning</button></div>
      <details><summary>Preview annotations</summary><div id="learning-annotations"></div></details>
    </section>
    <section class="settings-section"><h3>Connect a learning agent</h3>
      <p class="field-help">Connect a compatible service when ready. Preparing and exporting feedback does not require a connection or start a learning job.</p>
      <label>Service address<input id="learning-endpoint" type="url" placeholder="https://your-learning-service.example" autocomplete="off" spellcheck="false"></label>
      <label>Access token (if required)<input id="learning-token" type="password" autocomplete="off" spellcheck="false"></label>
      <p class="field-help">The token is held only while this dialog is open. Check connection sends no annotations.</p>
      <button class="button small" id="learning-connect" disabled>Check connection</button>
      <label>Receiving agent<select id="learning-agent" disabled><option value="">Not connected</option></select></label>
      <div class="settings-actions"><button class="button small" id="learning-status" disabled>Refresh job status</button></div>
      <p class="field-help">Start learning sends the previewed feedback to the selected service. A successful job returns a new candidate version; this website does not activate it.</p>
    </section>
    <p id="learning-message" role="status">Not connected. No learning task has been started.</p><div id="learning-result"></div>`;
  document.body.append(dialog);
  const el = id => document.getElementById(id);
  function message(text) { el('learning-message').textContent = text; }
  function scopeMatches() {
    const s = WorkingReview.state;
    return !!packet && !!s && !WorkingReview.revision && s.reviewer === reviewerId && packet.source.reviewer_id === reviewerId && s.workspace_id === packet.source.workspace_id && s.version === packet.source.workspace_version;
  }
  function controls() {
    for (const id of ['learning-prepare','learning-endpoint','learning-token','learning-close']) el(id).disabled = busy;
    el('learning-export').disabled = busy || !packet;
    el('learning-connect').disabled = busy || !el('learning-endpoint').value.trim();
    el('learning-agent').disabled = busy || !service;
    el('learning-start').disabled = busy || !service || !scopeMatches() || !el('learning-agent').value;
    el('learning-start').textContent = receipt && !receipt.job && receipt.endpoint === client?.endpoint && receipt.agent_id === el('learning-agent').value && receipt.feedback_sha256 === packet?.feedback_sha256 ? 'Retry submission' : 'Start learning';
    el('learning-status').disabled = busy || !client || !receipt || receipt.endpoint !== client.endpoint;
  }
  function remember() {
    if (!receipt) return;
    try { localStorage.setItem('spikeagent-learning-receipt:'+reviewerId, JSON.stringify(receipt)); } catch { /* The server request ID remains visible. */ }
  }
  function showReceipt() {
    if (!receipt) { el('learning-result').replaceChildren(); return; }
    const result = receipt.job?.result;
    el('learning-result').innerHTML = '<p><strong>'+esc(receipt.job?.status || 'Submission status unknown')+'</strong> · '+esc(receipt.agent_id)+'</p><p class="field-help">Request '+esc(receipt.request_id)+'</p>'+
      (receipt.job?.message ? '<p>'+esc(receipt.job.message)+'</p>' : '')+
      (result ? '<p>Candidate version: <strong>'+esc(result.candidate_version)+'</strong> · not activated</p><p>'+esc(result.summary)+'</p>' : '');
  }
  function showPacket() {
    const s = packet?.source;
    el('learning-summary').textContent = s ? s.dataset_id+' · '+packet.annotations.length+' confirmed annotations · session v'+s.workspace_version : 'No feedback prepared.';
    el('learning-annotations').replaceChildren();
    if (packet) for (const a of packet.annotations) {
      const row = document.createElement('article'); row.className = 'learning-annotation';
      const title = document.createElement('strong'); title.textContent = 'v'+a.version+' · '+a.human.action+' · Cluster '+a.task.source_cluster+(a.task.target_cluster != null ? ' + '+a.task.target_cluster : '');
      const text = document.createElement('p'); text.textContent = a.human.reason || a.human.note || a.human.positive_comment || 'No written comment.';
      row.append(title, text); el('learning-annotations').append(row);
    }
    controls();
  }
  async function prepare() {
    const saved = await WorkingReview.learningSnapshot();
    const next = await L.build(saved.input, saved.state, saved.reviewer);
    if (WorkingReview.state?.workspace_id !== next.source.workspace_id || WorkingReview.state?.version !== next.source.workspace_version || reviewerId !== next.source.reviewer_id || WorkingReview.revision) throw Error('The annotations changed while preparing feedback. Refresh annotations again.');
    packet = next; showPacket(); return next;
  }
  async function attempt(fn) {
    if (busy) return; busy = true; controls();
    try { await fn(); } catch (e) { message(e.name === 'AbortError' ? 'The service timed out. A submitted job may still be running; use Refresh job status before starting another.' : e.message); }
    finally { busy = false; controls(); }
  }
  async function open() {
    if (busy) return;
    packet = null; service = null; client = null; receipt = null;
    el('learning-endpoint').value = ''; el('learning-token').value = ''; el('learning-agent').innerHTML = '<option value="">Not connected</option>';
    try {
      const saved = JSON.parse(localStorage.getItem('spikeagent-learning-receipt:'+reviewerId) || 'null');
      if (saved?.workspace_id === WorkingReview.state?.workspace_id && saved.reviewer_id === reviewerId) receipt = saved;
    } catch { /* No prior request receipt. */ }
    showPacket(); showReceipt(); message('Not connected. No learning task has been started in this dialog.'); dialog.showModal();
    await attempt(async () => { await prepare(); });
  }
  function disconnect() { client = null; service = null; el('learning-agent').innerHTML = '<option value="">Not connected</option>'; message('Not connected. No annotations have been sent to this address.'); controls(); }
  button.onclick = open;
  el('learning-close').onclick = () => dialog.close();
  dialog.addEventListener('cancel', e => { if (busy) e.preventDefault(); });
  dialog.addEventListener('close', () => { el('learning-token').value = ''; client = null; service = null; packet = null; });
  for (const id of ['learning-endpoint','learning-token']) el(id).addEventListener('input', disconnect);
  el('learning-agent').onchange = controls;
  el('learning-prepare').onclick = () => attempt(async () => { packet = null; showPacket(); await prepare(); message('Feedback refreshed locally. Nothing has been sent.'); });
  el('learning-export').onclick = () => attempt(async () => {
    const before = packet?.feedback_sha256; await prepare();
    if (before !== packet.feedback_sha256) throw Error('Annotations changed. Review the refreshed preview, then export again.');
    const blob = new Blob([JSON.stringify(packet,null,2)], {type:'application/json'}), link = document.createElement('a'), url = URL.createObjectURL(blob);
    link.href = url; link.download = packet.source.dataset_id+'.expert-feedback.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
    message('Feedback exported. No service was contacted and no learning task was started.');
  });
  el('learning-connect').onclick = () => attempt(async () => {
    client = null; service = null;
    const next = new L.Client(el('learning-endpoint').value.trim(), el('learning-token').value);
    const capability = await next.connect(); client = next; service = capability;
    el('learning-agent').innerHTML = service.agents.map(a => '<option value="'+esc(a.id)+'">'+esc(a.name)+' · '+esc(a.version)+'</option>').join('');
    message('Connected. No annotations have been sent. Start learning creates a separate candidate version.');
  });
  el('learning-start').onclick = () => attempt(async () => {
    if (!service || !client || !packet) throw Error('Prepare annotations and connect a learning service first.');
    const previous = packet.feedback_sha256; await prepare();
    if (packet.feedback_sha256 !== previous) throw Error('Annotations changed. Review the refreshed preview before starting learning.');
    const agent = service.agents.find(a => a.id === el('learning-agent').value); if (!agent) throw Error('Choose a connected receiving agent.');
    const sameRequest = receipt?.endpoint === client.endpoint && receipt.agent_id === agent.id && receipt.base_version === agent.version && receipt.feedback_sha256 === packet.feedback_sha256;
    if (sameRequest && receipt.job) {
      message('This feedback already has a request. Refresh its status instead of submitting it twice.'); return;
    }
    if (!sameRequest) receipt = {request_id:crypto.randomUUID(), agent_id:agent.id, base_version:agent.version, feedback_sha256:packet.feedback_sha256,
      endpoint:client.endpoint, reviewer_id:reviewerId, workspace_id:packet.source.workspace_id, workspace_version:packet.source.workspace_version, job:null};
    remember(); showReceipt(); message('Submitting the previewed annotations to '+client.endpoint+'…');
    receipt.job = await client.submit(packet, receipt); remember(); showReceipt();
    message('Feedback submitted. The receiving service owns this learning job; existing website agents remain unchanged.');
  });
  el('learning-status').onclick = () => attempt(async () => {
    if (!receipt || !client || client.endpoint !== receipt.endpoint) throw Error('Reconnect to the service used for this request.');
    receipt.job = await client.status(receipt); remember(); showReceipt(); message('Job status refreshed from the receiving service.');
  });
  return {open};
})();
